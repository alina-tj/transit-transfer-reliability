-- 00_collection_health.sql
-- Run any time to see how well the collector is doing.
--  python scripts/run_sql.py sql/00_collection_health.sql --db data/ets.db
-- All times are shown in your computer's local time.

-- 1. Polls by feed and outcome (ok = 0 are failures)
SELECT feed, ok, COUNT(*) AS polls,
       datetime(MIN(fetched_at), 'unixepoch', 'localtime') AS first_poll,
       datetime(MAX(fetched_at), 'unixepoch', 'localtime') AS last_poll
FROM poll_log
GROUP BY feed, ok;

-- 2. Failure reasons
SELECT substr(error, 1, 90) AS error, COUNT(*) AS n
FROM poll_log
WHERE ok = 0
GROUP BY error
ORDER BY n DESC;

-- 3. Successful trip_updates polls per hour (a healthy hour has about 60 at a 60s interval)
SELECT strftime('%Y-%m-%d %H:00', fetched_at, 'unixepoch', 'localtime') AS hour_local,
       COUNT(*) AS polls
FROM poll_log
WHERE feed = 'trip_updates' AND ok = 1
GROUP BY hour_local
ORDER BY hour_local;

-- 4. Longest gaps between successful polls (minutes)
WITH p AS (
    SELECT fetched_at, LAG(fetched_at) OVER (ORDER BY fetched_at) AS prev
    FROM poll_log
    WHERE feed = 'trip_updates' AND ok = 1
)
SELECT ROUND((fetched_at - prev) / 60.0, 1) AS gap_min,
       datetime(prev, 'unixepoch', 'localtime') AS from_local,
       datetime(fetched_at, 'unixepoch', 'localtime') AS to_local
FROM p
WHERE prev IS NOT NULL
ORDER BY gap_min DESC
LIMIT 10;

-- 5. Do realtime trip_ids exist in the static schedule? (needs gtfs_trips loaded)
SELECT COUNT(*) AS realtime_trips,
       SUM(g.trip_id IS NOT NULL) AS matched_to_schedule
FROM (SELECT DISTINCT trip_id FROM trip_updates) u
LEFT JOIN gtfs_trips g ON g.trip_id = u.trip_id;

-- 6. Live predictions vs placeholder rows
--Rows with no vehicle or no delay may be schedule placeholders for trips that haven't started.
SELECT COUNT(*) AS rows_total,
       SUM(vehicle_id IS NOT NULL) AS with_vehicle,
       SUM(arrival_delay IS NOT NULL) AS with_arrival_delay,
       SUM(arrival_delay <> 0) AS nonzero_arrival_delay
FROM trip_updates;

-- 7. Size
SELECT (SELECT COUNT(*) FROM trip_updates) AS trip_update_rows,
       (SELECT COUNT(*) FROM vehicle_positions) AS vehicle_position_rows,
       ROUND(page_count * page_size / 1048576.0, 1) AS db_mb
FROM pragma_page_count(), pragma_page_size();
