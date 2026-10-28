-- 01_transfer_candidates.sql  (multi-date version)
-- Builds scheduled transfer candidates at the busiest "hub" stops for EVERY service
-- date listed in transfer_dates.
--
-- By default transfer_dates is filled with every start_date found in your collected
-- trip_updates, so candidates exist for exactly the days you have realtime data for.
-- To add a date manually (e.g. a future weekday):
--   INSERT OR IGNORE INTO transfer_dates VALUES (20261013);
--
-- A candidate is: a feeder trip arriving at a hub stop, and for each OTHER route
-- (and direction) serving that same stop, the first scheduled departure that is at
-- least min_transfer_secs after the feeder's scheduled arrival (within max_window_secs).
-- Same-line variants (same route_short_name) are not counted as transfers.
--
-- Version 1 limitations (document these in the README):
--   * same-stop transfers only (no walking between bays or nearby stops)
--   * requires SQLite 3.25+ (window functions)

CREATE TABLE IF NOT EXISTS gtfs_calendar (
    service_id TEXT, monday INTEGER, tuesday INTEGER, wednesday INTEGER, thursday INTEGER,
    friday INTEGER, saturday INTEGER, sunday INTEGER, start_date TEXT, end_date TEXT
);
CREATE TABLE IF NOT EXISTS gtfs_calendar_dates (service_id TEXT, "date" TEXT, exception_type INTEGER);

CREATE TABLE IF NOT EXISTS transfer_params (key TEXT PRIMARY KEY, value INTEGER);
INSERT OR IGNORE INTO transfer_params VALUES ('min_transfer_secs', 60);
INSERT OR IGNORE INTO transfer_params VALUES ('max_window_secs', 1800);
INSERT OR IGNORE INTO transfer_params VALUES ('hub_min_routes', 5);
INSERT OR IGNORE INTO transfer_params VALUES ('hub_limit', 30);
-- (older versions used a single 'service_date' parameter; dates now come from transfer_dates)
DELETE FROM transfer_params WHERE key = 'service_date';

CREATE TABLE IF NOT EXISTS transfer_dates (service_date INTEGER PRIMARY KEY);
INSERT OR IGNORE INTO transfer_dates
SELECT DISTINCT CAST(start_date AS INTEGER)
FROM trip_updates
WHERE start_date GLOB '[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]';

-- Services running on each date (works with calendar.txt, calendar_dates.txt, or both).
DROP TABLE IF EXISTS active_services;
CREATE TABLE active_services AS
WITH d AS (
    SELECT service_date,
           CAST(service_date AS TEXT) AS ymd,
           strftime('%w', substr(CAST(service_date AS TEXT), 1, 4) || '-' ||
                          substr(CAST(service_date AS TEXT), 5, 2) || '-' ||
                          substr(CAST(service_date AS TEXT), 7, 2)) AS dow
    FROM transfer_dates
)
SELECT d.service_date, cd.service_id
FROM d
JOIN gtfs_calendar_dates cd ON cd."date" = d.ymd AND cd.exception_type = 1
UNION
SELECT d.service_date, c.service_id
FROM d
JOIN gtfs_calendar c ON c.start_date <= d.ymd AND c.end_date >= d.ymd
WHERE CASE d.dow WHEN '0' THEN c.sunday WHEN '1' THEN c.monday WHEN '2' THEN c.tuesday
                 WHEN '3' THEN c.wednesday WHEN '4' THEN c.thursday WHEN '5' THEN c.friday
                 WHEN '6' THEN c.saturday END = 1
  AND NOT EXISTS (
      SELECT 1 FROM gtfs_calendar_dates cd2
      WHERE cd2.service_id = c.service_id AND cd2."date" = d.ymd AND cd2.exception_type = 2
  );
CREATE INDEX ix_active ON active_services(service_id, service_date);

-- CHECK THIS: every date should have active_services well above 0 (0 = date outside the feed's range).
SELECT d.service_date, COUNT(a.service_id) AS active_services
FROM transfer_dates d
LEFT JOIN active_services a ON a.service_date = d.service_date
GROUP BY d.service_date
ORDER BY d.service_date;

-- Hub stops: served by many distinct routes.
DROP TABLE IF EXISTS hub_stops;
CREATE TABLE hub_stops AS
SELECT st.stop_id, COUNT(DISTINCT t.route_id) AS n_routes
FROM gtfs_stop_times st
JOIN gtfs_trips t ON t.trip_id = st.trip_id
GROUP BY st.stop_id
HAVING COUNT(DISTINCT t.route_id) >= (SELECT value FROM transfer_params WHERE key = 'hub_min_routes')
ORDER BY n_routes DESC
LIMIT (SELECT value FROM transfer_params WHERE key = 'hub_limit');

CREATE INDEX IF NOT EXISTS ix_hub_stop ON hub_stops(stop_id);

DROP TABLE IF EXISTS trip_bounds;
CREATE TABLE trip_bounds AS
SELECT trip_id, MIN(stop_sequence) AS first_seq, MAX(stop_sequence) AS last_seq
FROM gtfs_stop_times
GROUP BY trip_id;
CREATE INDEX ix_trip_bounds ON trip_bounds(trip_id);

-- Feeder arrivals at hubs, one row per (service date, trip, stop).
-- (a passenger can't "alight" at a trip's first stop)
DROP TABLE IF EXISTS tmp_feeder;
CREATE TABLE tmp_feeder AS
SELECT a.service_date, st.trip_id, st.stop_id, st.stop_sequence, st.arrival_secs AS arr_secs,
       t.route_id, COALESCE(r.route_short_name, t.route_id) AS route_name,
       COALESCE(t.direction_id, -1) AS direction_id
FROM gtfs_stop_times st
JOIN hub_stops h ON h.stop_id = st.stop_id
JOIN gtfs_trips t ON t.trip_id = st.trip_id
JOIN active_services a ON a.service_id = t.service_id
JOIN trip_bounds b ON b.trip_id = st.trip_id
LEFT JOIN gtfs_routes r ON r.route_id = t.route_id
WHERE st.stop_sequence > b.first_seq
  AND st.arrival_secs IS NOT NULL;
CREATE INDEX ix_feeder_stop ON tmp_feeder(service_date, stop_id);

-- Boardable departures at hubs (a passenger can't board at a trip's last stop).
-- next_dep_secs = the connector route's following departure, used to estimate the
-- wait after missing the connection.
DROP TABLE IF EXISTS tmp_connector;
CREATE TABLE tmp_connector AS
SELECT service_date, trip_id, stop_id, route_id, route_name, direction_id, dep_secs,
       LEAD(dep_secs) OVER (
           PARTITION BY service_date, route_id, direction_id, stop_id
           ORDER BY dep_secs
       ) AS next_dep_secs
FROM (
    SELECT a.service_date, st.trip_id, st.stop_id, st.departure_secs AS dep_secs,
           t.route_id, COALESCE(r.route_short_name, t.route_id) AS route_name,
           COALESCE(t.direction_id, -1) AS direction_id
    FROM gtfs_stop_times st
    JOIN hub_stops h ON h.stop_id = st.stop_id
    JOIN gtfs_trips t ON t.trip_id = st.trip_id
    JOIN active_services a ON a.service_id = t.service_id
    JOIN trip_bounds b ON b.trip_id = st.trip_id
    LEFT JOIN gtfs_routes r ON r.route_id = t.route_id
    WHERE st.stop_sequence < b.last_seq
      AND st.departure_secs IS NOT NULL
);
CREATE INDEX ix_conn_stop ON tmp_connector(service_date, stop_id, dep_secs);

DROP TABLE IF EXISTS transfer_candidates;
CREATE TABLE transfer_candidates AS
WITH pairs AS (
    SELECT f.service_date,
           f.trip_id AS feeder_trip_id,
           f.route_id AS feeder_route_id,
           f.route_name AS feeder_route_name,
           f.direction_id AS feeder_direction_id,
           c.trip_id AS connector_trip_id,
           c.route_id AS connector_route_id,
           c.route_name AS connector_route_name,
           c.direction_id AS connector_direction_id,
           f.stop_id,
           f.stop_sequence AS feeder_stop_sequence,
           f.arr_secs AS sched_arr_secs,
           c.dep_secs AS sched_dep_secs,
           c.dep_secs - f.arr_secs AS sched_window_secs,
           c.next_dep_secs - c.dep_secs AS headway_after_secs,
           ROW_NUMBER() OVER (
               PARTITION BY f.service_date, f.trip_id, f.stop_id, f.stop_sequence, c.route_id, c.direction_id
               ORDER BY c.dep_secs
           ) AS rn
    FROM tmp_feeder f
    JOIN tmp_connector c
      ON c.service_date = f.service_date
     AND c.stop_id = f.stop_id
     AND c.route_name <> f.route_name
     AND c.dep_secs >= f.arr_secs + (SELECT value FROM transfer_params WHERE key = 'min_transfer_secs')
     AND c.dep_secs <= f.arr_secs + (SELECT value FROM transfer_params WHERE key = 'max_window_secs')
)
SELECT service_date,
       feeder_trip_id, feeder_route_id, feeder_route_name, feeder_direction_id,
       connector_trip_id, connector_route_id, connector_route_name, connector_direction_id,
       stop_id, feeder_stop_sequence,
       sched_arr_secs, sched_dep_secs, sched_window_secs, headway_after_secs
FROM pairs
WHERE rn = 1;

CREATE INDEX ix_cand_feeder ON transfer_candidates(service_date, feeder_trip_id, stop_id);
CREATE INDEX ix_cand_connector ON transfer_candidates(service_date, connector_trip_id, stop_id);

-- Candidates per date
SELECT service_date, COUNT(*) AS candidates
FROM transfer_candidates
GROUP BY service_date
ORDER BY service_date;

-- Clean up intermediate tables.
DROP TABLE IF EXISTS tmp_feeder;
DROP TABLE IF EXISTS tmp_connector;
