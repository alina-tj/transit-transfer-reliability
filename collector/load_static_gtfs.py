"""
Load a static GTFS zip (ETS schedule) into the project SQLite database.

Usage:
    python collector/load_static_gtfs.py path/to/gtfs.zip --db data/ets.db

Creates (or replaces) tables: gtfs_routes, gtfs_stops, gtfs_trips,
gtfs_stop_times, gtfs_calendar, gtfs_calendar_dates, gtfs_meta.

gtfs_stop_times gets two extra columns, arrival_secs and departure_secs
(seconds after midnight of the service day). GTFS times can exceed 24:00:00
for trips running past midnight, so they are stored as plain seconds rather
than clock times.
"""

import argparse
import csv
import io
import sqlite3
import time
import zipfile
from pathlib import Path

TABLES = {
    "routes": "routes.txt",
    "stops": "stops.txt",
    "trips": "trips.txt",
    "stop_times": "stop_times.txt",
    "calendar": "calendar.txt",
    "calendar_dates": "calendar_dates.txt",
}
REQUIRED = {"routes", "stops", "trips", "stop_times"}

INT_COLS = {
    "stop_sequence", "direction_id", "route_type", "location_type",
    "pickup_type", "drop_off_type", "exception_type", "timepoint",
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
    "wheelchair_boarding", "wheelchair_accessible",
}
REAL_COLS = {"stop_lat", "stop_lon", "shape_dist_traveled"}
BATCH = 50_000


def to_secs(value):
    """'HH:MM:SS' -> seconds after midnight (HH may be >= 24). Empty -> None."""
    if not value or not value.strip():
        return None
    h, m, s = value.strip().split(":")
    return int(h) * 3600 + int(m) * 60 + int(s)


def convert(col, value):
    value = value.strip()
    if value == "":
        return None
    try:
        if col in INT_COLS:
            return int(value)
        if col in REAL_COLS:
            return float(value)
    except ValueError:
        return value
    return value


def find_member(zf, filename):
    for name in zf.namelist():
        if name.split("/")[-1].lower() == filename:
            return name
    return None


def load_table(conn, zf, table, member):
    t0 = time.time()
    with zf.open(member) as raw:
        reader = csv.reader(io.TextIOWrapper(raw, encoding="utf-8-sig", newline=""))
        header = [h.strip() for h in next(reader)]
        is_st = table == "stop_times"
        cols = header + (["arrival_secs", "departure_secs"] if is_st else [])

        col_defs = []
        for c in cols:
            if c in INT_COLS or c in ("arrival_secs", "departure_secs"):
                col_defs.append(f'"{c}" INTEGER')
            elif c in REAL_COLS:
                col_defs.append(f'"{c}" REAL')
            else:
                col_defs.append(f'"{c}" TEXT')
        conn.execute(f"DROP TABLE IF EXISTS gtfs_{table}")
        conn.execute(f"CREATE TABLE gtfs_{table} ({', '.join(col_defs)})")

        insert = f"INSERT INTO gtfs_{table} VALUES ({','.join('?' * len(cols))})"
        arr_i = header.index("arrival_time") if is_st and "arrival_time" in header else None
        dep_i = header.index("departure_time") if is_st and "departure_time" in header else None

        batch, total = [], 0
        for row in reader:
            if not row:
                continue
            row = row + [""] * (len(header) - len(row))  # pad short rows
            values = [convert(c, v) for c, v in zip(header, row)]
            if is_st:
                arr = to_secs(row[arr_i]) if arr_i is not None else None
                dep = to_secs(row[dep_i]) if dep_i is not None else None
                if arr is None:
                    arr = dep
                if dep is None:
                    dep = arr
                values += [arr, dep]
            batch.append(values)
            if len(batch) >= BATCH:
                conn.executemany(insert, batch)
                total += len(batch)
                batch = []
        if batch:
            conn.executemany(insert, batch)
            total += len(batch)
    conn.commit()
    print(f"  gtfs_{table}: {total:,} rows ({time.time() - t0:.1f}s)")


def make_indexes(conn):
    stmts = [
        "CREATE INDEX IF NOT EXISTS ix_routes_id ON gtfs_routes(route_id)",
        "CREATE INDEX IF NOT EXISTS ix_stops_id ON gtfs_stops(stop_id)",
        "CREATE INDEX IF NOT EXISTS ix_trips_id ON gtfs_trips(trip_id)",
        "CREATE INDEX IF NOT EXISTS ix_trips_route ON gtfs_trips(route_id, service_id)",
        "CREATE INDEX IF NOT EXISTS ix_st_trip ON gtfs_stop_times(trip_id, stop_sequence)",
        "CREATE INDEX IF NOT EXISTS ix_st_stop ON gtfs_stop_times(stop_id, departure_secs)",
    ]
    for s in stmts:
        conn.execute(s)
    conn.commit()


def main():
    ap = argparse.ArgumentParser(description="Load static GTFS zip into SQLite.")
    ap.add_argument("zip_path")
    ap.add_argument("--db", default="data/ets.db")
    args = ap.parse_args()

    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(args.db)

    with zipfile.ZipFile(args.zip_path) as zf:
        print(f"Loading {args.zip_path} into {args.db}")
        for table, filename in TABLES.items():
            member = find_member(zf, filename)
            if member is None:
                if table in REQUIRED:
                    raise SystemExit(f"Required file missing from zip: {filename}")
                print(f"  (skipping optional {filename}: not in zip)")
                continue
            load_table(conn, zf, table, member)

    print("Building indexes...")
    make_indexes(conn)

    conn.execute("DROP TABLE IF EXISTS gtfs_meta")
    conn.execute("CREATE TABLE gtfs_meta (source TEXT, loaded_at INTEGER)")
    conn.execute("INSERT INTO gtfs_meta VALUES (?, ?)", (Path(args.zip_path).name, int(time.time())))
    conn.commit()
    conn.close()
    print("Done.")


if __name__ == "__main__":
    main()
