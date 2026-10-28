-- 02_sanity_checks.sql
-- Run after 01_transfer_candidates.sql. Look at these before trusting anything downstream.

SELECT COUNT(*) AS candidates FROM transfer_candidates;

-- Hub stops with names
SELECT h.stop_id, s.stop_name, h.n_routes
FROM hub_stops h
LEFT JOIN gtfs_stops s ON s.stop_id = h.stop_id
ORDER BY h.n_routes DESC
LIMIT 30;

-- Route pairs with the most scheduled transfer opportunities
SELECT feeder_route_name, connector_route_name,
       COUNT(*) AS n_candidates,
       COUNT(DISTINCT stop_id) AS n_stops,
       ROUND(AVG(sched_window_secs) / 60.0, 1) AS avg_window_min,
       ROUND(MIN(sched_window_secs) / 60.0, 1) AS min_window_min
FROM transfer_candidates
GROUP BY feeder_route_name, connector_route_name
ORDER BY n_candidates DESC
LIMIT 25;

-- How concentrated are the candidates? (a few pairs dominating is a warning sign)
SELECT COUNT(DISTINCT feeder_route_name || '>' || connector_route_name) AS distinct_route_pairs,
       COUNT(DISTINCT stop_id) AS stops_with_candidates
FROM transfer_candidates;

-- Distribution of scheduled transfer windows (minutes)
SELECT sched_window_secs / 60 AS window_min, COUNT(*) AS n
FROM transfer_candidates
GROUP BY window_min
ORDER BY window_min;
