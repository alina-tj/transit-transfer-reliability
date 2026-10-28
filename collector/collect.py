"""
ETS GTFS-Realtime collector.

Polls the Edmonton Transit System TripUpdates and VehiclePositions feeds on a
fixed interval and appends normalized rows to a SQLite database. Every poll
(success or failure) is logged in `poll_log`, so you can measure coverage and
gaps later -- important for honest analysis.

Usage:
    python collector/collect.py --db data/ets.db --interval 60

Stop with Ctrl+C (finishes the current poll cleanly).
"""

import argparse
import logging
import signal
import sqlite3
import time
from pathlib import Path

import requests
from google.transit import gtfs_realtime_pb2

TRIP_UPDATES_URL = "http://gtfs.edmonton.ca/TMGTFSRealTimeWebService/TripUpdate/TripUpdates.pb"
VEHICLE_POSITIONS_URL = "http://gtfs.edmonton.ca/TMGTFSRealTimeWebService/Vehicle/VehiclePositions.pb"

SCHEMA = """
CREATE TABLE IF NOT EXISTS poll_log (
    id            INTEGER PRIMARY KEY,
    fetched_at    INTEGER NOT NULL,      -- UTC epoch seconds when we polled
    feed          TEXT NOT NULL,         -- 'trip_updates' | 'vehicle_positions'
    ok            INTEGER NOT NULL,      -- 1 success, 0 failure
    rows_inserted INTEGER NOT NULL DEFAULT 0,
    feed_timestamp INTEGER,              -- header timestamp from the feed
    error         TEXT
);

CREATE TABLE IF NOT EXISTS trip_updates (
    id              INTEGER PRIMARY KEY,
    fetched_at      INTEGER NOT NULL,
    feed_timestamp  INTEGER,
    trip_id         TEXT,
    route_id        TEXT,
    direction_id    INTEGER,
    start_date      TEXT,                -- YYYYMMDD (service date)
    start_time      TEXT,                -- HH:MM:SS, can exceed 24h
    trip_schedule_relationship INTEGER,
    vehicle_id      TEXT,
    stop_sequence   INTEGER,
    stop_id         TEXT,
    arrival_delay   INTEGER,             -- seconds (positive = late)
    arrival_time    INTEGER,             -- predicted UTC epoch
    departure_delay INTEGER,
    departure_time  INTEGER,
    stop_schedule_relationship INTEGER
);

CREATE TABLE IF NOT EXISTS vehicle_positions (
    id              INTEGER PRIMARY KEY,
    fetched_at      INTEGER NOT NULL,
    feed_timestamp  INTEGER,
    vehicle_id      TEXT,
    trip_id         TEXT,
    route_id        TEXT,
    direction_id    INTEGER,
    start_date      TEXT,
    latitude        REAL,
    longitude       REAL,
    bearing         REAL,
    speed           REAL,
    current_stop_sequence INTEGER,
    stop_id         TEXT,
    current_status  INTEGER,
    vehicle_timestamp INTEGER,
    occupancy_status INTEGER
);
"""

log = logging.getLogger("ets-collector")
_running = True


def _stop(*_):
    global _running
    _running = False
    log.info("Shutdown requested; finishing current poll.")


def opt(msg, field):
    """Return a protobuf optional field's value, or None if it isn't set."""
    return getattr(msg, field) if msg.HasField(field) else None


def fetch_feed(url, timeout=20):
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(resp.content)
    return feed


def parse_trip_updates(feed, fetched_at, stops_per_trip):
    feed_ts = opt(feed.header, "timestamp")
    rows = []
    for entity in feed.entity:
        if not entity.HasField("trip_update"):
            continue
        tu = entity.trip_update
        trip = tu.trip
        vehicle_id = tu.vehicle.id if tu.HasField("vehicle") and tu.vehicle.HasField("id") else None
        base = (
            fetched_at, feed_ts,
            opt(trip, "trip_id"), opt(trip, "route_id"), opt(trip, "direction_id"),
            opt(trip, "start_date"), opt(trip, "start_time"),
            opt(trip, "schedule_relationship"), vehicle_id,
        )
        updates = list(tu.stop_time_update)
        if stops_per_trip > 0:
            updates = updates[:stops_per_trip]
        if not updates:
            # Trip with no stop updates (e.g. canceled): keep a row for it.
            rows.append(base + (None,) * 7)  # the 7 stop-level columns
            continue
        for stu in updates:
            arr = stu.arrival if stu.HasField("arrival") else None
            dep = stu.departure if stu.HasField("departure") else None
            rows.append(
                base
                + (
                    opt(stu, "stop_sequence"), opt(stu, "stop_id"),
                    opt(arr, "delay") if arr is not None else None,
                    opt(arr, "time") if arr is not None else None,
                    opt(dep, "delay") if dep is not None else None,
                    opt(dep, "time") if dep is not None else None,
                    opt(stu, "schedule_relationship"),
                )
            )
    return feed_ts, rows


def parse_vehicle_positions(feed, fetched_at):
    feed_ts = opt(feed.header, "timestamp")
    rows = []
    for entity in feed.entity:
        if not entity.HasField("vehicle"):
            continue
        v = entity.vehicle
        trip = v.trip if v.HasField("trip") else None
        pos = v.position if v.HasField("position") else None
        rows.append((
            fetched_at, feed_ts,
            v.vehicle.id if v.HasField("vehicle") and v.vehicle.HasField("id") else None,
            opt(trip, "trip_id") if trip is not None else None,
            opt(trip, "route_id") if trip is not None else None,
            opt(trip, "direction_id") if trip is not None else None,
            opt(trip, "start_date") if trip is not None else None,
            opt(pos, "latitude") if pos is not None else None,
            opt(pos, "longitude") if pos is not None else None,
            opt(pos, "bearing") if pos is not None else None,
            opt(pos, "speed") if pos is not None else None,
            opt(v, "current_stop_sequence"), opt(v, "stop_id"),
            opt(v, "current_status"), opt(v, "timestamp"), opt(v, "occupancy_status"),
        ))
    return feed_ts, rows


TU_INSERT = "INSERT INTO trip_updates (fetched_at, feed_timestamp, trip_id, route_id, direction_id, start_date, start_time, trip_schedule_relationship, vehicle_id, stop_sequence, stop_id, arrival_delay, arrival_time, departure_delay, departure_time, stop_schedule_relationship) VALUES (" + ",".join("?" * 16) + ")"
VP_INSERT = "INSERT INTO vehicle_positions (fetched_at, feed_timestamp, vehicle_id, trip_id, route_id, direction_id, start_date, latitude, longitude, bearing, speed, current_stop_sequence, stop_id, current_status, vehicle_timestamp, occupancy_status) VALUES (" + ",".join("?" * 16) + ")"


def poll_once(conn, name, url, parser, insert_sql, fetched_at):
    try:
        feed = fetch_feed(url)
        feed_ts, rows = parser(feed, fetched_at)
        conn.executemany(insert_sql, rows)
        conn.execute(
            "INSERT INTO poll_log (fetched_at, feed, ok, rows_inserted, feed_timestamp) VALUES (?,?,1,?,?)",
            (fetched_at, name, len(rows), feed_ts),
        )
        conn.commit()
        log.info("%s: %d rows", name, len(rows))
    except Exception as exc:  # network errors, parse errors, etc.
        conn.rollback()
        conn.execute(
            "INSERT INTO poll_log (fetched_at, feed, ok, rows_inserted, error) VALUES (?,?,0,0,?)",
            (fetched_at, name, str(exc)[:500]),
        )
        conn.commit()
        log.warning("%s failed: %s", name, exc)


def main():
    ap = argparse.ArgumentParser(description="Poll ETS GTFS-Realtime feeds into SQLite.")
    ap.add_argument("--db", default="data/ets.db")
    ap.add_argument("--interval", type=int, default=60, help="seconds between polls")
    ap.add_argument("--stops-per-trip", type=int, default=1,
                    help="store the next N stop updates per trip per poll (0 = all stops; large!)")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.db)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)

    log.info("Collecting every %ds into %s", args.interval, args.db)
    while _running:
        started = time.time()
        fetched_at = int(started)
        poll_once(conn, "trip_updates", TRIP_UPDATES_URL,
                  lambda f, t: parse_trip_updates(f, t, args.stops_per_trip), TU_INSERT, fetched_at)
        poll_once(conn, "vehicle_positions", VEHICLE_POSITIONS_URL,
                  parse_vehicle_positions, VP_INSERT, fetched_at)
        # Sleep in 1s steps so Ctrl+C is responsive.
        deadline = started + args.interval
        while _running and time.time() < deadline:
            time.sleep(1)

    conn.close()
    log.info("Stopped.")


if __name__ == "__main__":
    main()
