# transit-transfer-reliability
# Edmonton Transit Delay & Reliability Analysis

Investigating when and where Edmonton Transit (ETS) bus delays are most likely to occur, using GTFS-Realtime data I collect myself.

> Status: data collection phase. Findings, SQL, charts and dashboard coming.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

## Run the collector

```bash
python collector/collect.py --db data/ets.db --interval 60
```

- Polls the ETS TripUpdates and VehiclePositions feeds every 60 seconds.
- Stores the next upcoming stop prediction per trip per poll (`--stops-per-trip 1`) to keep the database manageable.
- Every poll, successful or not, is recorded in `poll_log` so data coverage and gaps can be measured.

Quick health check:

```bash
sqlite3 data/ets.db "SELECT feed, ok, COUNT(*) FROM poll_log GROUP BY feed, ok;"
sqlite3 data/ets.db "SELECT COUNT(*) FROM trip_updates;"
```

## Load the static schedule and build transfer candidates

Download the ETS static GTFS zip from the City of Edmonton open data portal, then:

```bash
python collector/load_static_gtfs.py path/to/gtfs.zip --db data/ets.db
python scripts/run_sql.py sql/01_transfer_candidates.sql --db data/ets.db
python scripts/run_sql.py sql/02_sanity_checks.sql --db data/ets.db
```

- `transfer_params` holds the settings (minimum transfer time, maximum window, how many hub stops). Edit the values in the table and re-run to change them.
- Version 1 covers same-stop transfers at the busiest hub stops only.

## Planned structure

```
collector/   polling script
sql/         analysis queries
notebooks/   cleaning + exploration
dashboard/   Tableau extracts and screenshots
reports/     findings and recommendations
```

## Data and attribution

Contains information licensed under the City of Edmonton Open Data Agreement. Static schedule data (GTFS) and the realtime feeds are published by the City of Edmonton / ETS.

## Methodology notes (to be expanded)
Trip-update delays are the system's predictions, not confirmed arrival times.
"On time" definition, data coverage and known gaps will be documented here.
