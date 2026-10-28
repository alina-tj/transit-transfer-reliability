-- 10_transfer_outcomes.sql
-- Labels each scheduled transfer candidate as MADE or MISSED using the realtime data you collected.
-- Run after 01_transfer_candidates.sql:
--   python scripts/run_sql.py sql/10_transfer_outcomes.sql --db data/ets.db
--
-- How an outcome is decided (document this in your README):
--   * For each trip and stop, take the LAST prediction the collector saw (the poll just
--     before the bus reached/left that stop). That delay is our estimate of the actual delay.
--   * The feed often reports only departure delays, so the feeder's arrival delay falls back
--     to its departure delay. The connector uses its departure delay.
--   * actual window = scheduled window + connector delay - feeder delay
--   * MADE if actual window >= min_transfer_secs, otherwise MISSED.
--   * A prediction only counts if it was made shortly before the event (max_obs_lead_secs).
--     Gaps in collection (sleeping laptop, outages) therefore reduce coverage instead of
--     silently producing bad labels.
--   * Delays beyond +/- max_abs_delay_secs are treated as bad data and dropped.

INSERT OR IGNORE INTO transfer_params VALUES ('max_obs_lead_secs', 180);
INSERT OR IGNORE INTO transfer_params VALUES ('max_abs_delay_secs', 3600);

-- What kinds of rows did the collector store? (rows with no delay at all are not usable predictions)
SELECT (arrival_delay IS NOT NULL) AS has_arrival_delay,
       (departure_delay IS NOT NULL) AS has_departure_delay,
       (vehicle_id IS NOT NULL) AS has_vehicle,
       COUNT(*) AS n_rows
FROM trip_updates
GROUP BY 1, 2, 3
ORDER BY n_rows DESC;

-- Last prediction per (service date, trip, stop).
DROP TABLE IF EXISTS rt_last_obs;
CREATE TABLE rt_last_obs AS
SELECT start_date, trip_id, stop_id, stop_sequence, arrival_delay, departure_delay,
       COALESCE(arrival_time, departure_time) - fetched_at AS lead_secs
FROM (
    SELECT u.start_date, u.trip_id, u.stop_id, u.stop_sequence, u.arrival_delay, u.departure_delay,
           u.arrival_time, u.departure_time, u.fetched_at,
           ROW_NUMBER() OVER (
               PARTITION BY u.start_date, u.trip_id, u.stop_id
               ORDER BY u.fetched_at DESC
           ) AS rn
    FROM trip_updates u
    WHERE u.start_date IS NOT NULL
      AND (u.arrival_delay IS NOT NULL OR u.departure_delay IS NOT NULL)
      AND COALESCE(u.trip_schedule_relationship, 0) <> 3      -- not canceled
      AND COALESCE(u.stop_schedule_relationship, 0) = 0       -- stop not skipped / no-data
)
WHERE rn = 1;
CREATE INDEX ix_rt_last ON rt_last_obs(start_date, trip_id, stop_id);

-- Coverage: how many candidates have realtime observations for both buses?
SELECT c.service_date,
       COUNT(*) AS candidates,
       SUM(fo.trip_id IS NOT NULL) AS feeder_observed,
       SUM(co.trip_id IS NOT NULL) AS connector_observed,
       SUM(fo.trip_id IS NOT NULL AND co.trip_id IS NOT NULL) AS both_observed
FROM transfer_candidates c
LEFT JOIN rt_last_obs fo
       ON fo.start_date = CAST(c.service_date AS TEXT) AND fo.trip_id = c.feeder_trip_id AND fo.stop_id = c.stop_id
LEFT JOIN rt_last_obs co
       ON co.start_date = CAST(c.service_date AS TEXT) AND co.trip_id = c.connector_trip_id AND co.stop_id = c.stop_id
GROUP BY c.service_date
ORDER BY c.service_date;

DROP TABLE IF EXISTS transfer_outcomes;
CREATE TABLE transfer_outcomes AS
WITH joined AS (
    SELECT c.service_date, c.feeder_trip_id, c.feeder_route_name,
           c.connector_trip_id, c.connector_route_name, c.stop_id,
           c.sched_arr_secs, c.sched_dep_secs, c.sched_window_secs, c.headway_after_secs,
           COALESCE(fo.arrival_delay, fo.departure_delay) AS feeder_delay_secs,
           COALESCE(co.departure_delay, co.arrival_delay) AS connector_delay_secs,
           fo.lead_secs AS feeder_obs_lead_secs,
           co.lead_secs AS connector_obs_lead_secs
    FROM transfer_candidates c
    JOIN rt_last_obs fo
      ON fo.start_date = CAST(c.service_date AS TEXT) AND fo.trip_id = c.feeder_trip_id AND fo.stop_id = c.stop_id
    JOIN rt_last_obs co
      ON co.start_date = CAST(c.service_date AS TEXT) AND co.trip_id = c.connector_trip_id AND co.stop_id = c.stop_id
),
scored AS (
    SELECT j.*,
           j.sched_window_secs + j.connector_delay_secs - j.feeder_delay_secs AS actual_window_secs
    FROM joined j
    WHERE ABS(j.feeder_delay_secs)    <= (SELECT value FROM transfer_params WHERE key = 'max_abs_delay_secs')
      AND ABS(j.connector_delay_secs) <= (SELECT value FROM transfer_params WHERE key = 'max_abs_delay_secs')
      AND j.feeder_obs_lead_secs    BETWEEN -120 AND (SELECT value FROM transfer_params WHERE key = 'max_obs_lead_secs')
      AND j.connector_obs_lead_secs BETWEEN -120 AND (SELECT value FROM transfer_params WHERE key = 'max_obs_lead_secs')
)
SELECT s.*,
       s.actual_window_secs - (SELECT value FROM transfer_params WHERE key = 'min_transfer_secs') AS slack_secs,
       CASE WHEN s.actual_window_secs >= (SELECT value FROM transfer_params WHERE key = 'min_transfer_secs')
            THEN 1 ELSE 0 END AS made,
       CASE WHEN s.actual_window_secs >= (SELECT value FROM transfer_params WHERE key = 'min_transfer_secs') THEN NULL
            WHEN s.feeder_delay_secs > 0 AND s.connector_delay_secs < 0 THEN 'feeder late + connector early'
            WHEN s.feeder_delay_secs > 0 AND s.connector_delay_secs = 0 THEN 'feeder late'
            WHEN s.connector_delay_secs < 0 AND s.feeder_delay_secs <= 0 THEN 'connector early'
            ELSE 'both late, feeder later'
       END AS miss_cause
FROM scored s;
CREATE INDEX ix_outcomes_pair ON transfer_outcomes(feeder_route_name, connector_route_name);

-- Usable outcomes after quality filters, per date
SELECT service_date, COUNT(*) AS usable_outcomes, SUM(made) AS made,
       ROUND(100.0 * SUM(made) / COUNT(*), 1) AS pct_made
FROM transfer_outcomes
GROUP BY service_date
ORDER BY service_date;

-- Why transfers were missed
SELECT miss_cause, COUNT(*) AS n
FROM transfer_outcomes
WHERE made = 0
GROUP BY miss_cause
ORDER BY n DESC;
