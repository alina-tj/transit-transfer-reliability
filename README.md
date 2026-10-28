# Will I Miss My Transfer? Edmonton Transit Transfer Reliability

**Question:** Which Edmonton Transit (ETS) bus-to-bus connections are most likely to be missed, when, and how much buffer should a rider allow?

I built a data pipeline that collects ETS realtime feeds, matches live bus delays to the published schedule, and labels each scheduled transfer as **made** or **missed**. This is a **preliminary analysis of six weekdays** (see [Limitations](#limitations)).

**Dashboard:** [Tableau Public link: not published yet]

## Key findings (weekdays, same-line variants excluded)

3,466 observed transfers at 30 busy hub stops. Overall, **85.0%** were made (plausible range 83.8% to 86.2%).

### 1. The scheduled transfer window matters most

| Scheduled time to transfer | Transfers made | Plausible range | n |
|---|---|---|---|
| 1 to 2 min | 37.7% | 31.0% to 44.9% | 183 |
| 2 to 3 min | 56.3% | 49.3% to 63.0% | 199 |
| 3 to 5 min | 68.7% | 64.3% to 72.8% | 454 |
| 5 to 8 min | 81.5% | 77.7% to 84.8% | 460 |
| 8 to 12 min | 90.2% | 87.8% to 92.3% | 676 |
| 12 to 20 min | 97.5% | 96.2% to 98.3% | 879 |
| 20 to 30 min | 99.5% | 98.4% to 99.8% | 548 |

**Plan for about 8 to 12 minutes to be roughly 90% sure.** When a transfer is missed, the next bus on the connecting route comes about 20 minutes later on average (windows under 8 minutes).

### 2. Morning rush is the worst time to rely on a tight transfer

| Time of day (weekday) | Transfers made | Plausible range | n |
|---|---|---|---|
| AM peak (6 to 9) | 69.0% | 63.3% to 74.2% | 274 |
| Midday (9 to 15) | 88.9% | 87.3% to 90.3% | 1,727 |
| PM peak (15 to 18) | 81.3% | 78.7% to 83.7% | 930 |
| Evening (18 to 22) | 88.9% | 85.5% to 91.6% | 406 |

Part of the morning gap is that more morning transfers have short scheduled windows (about 37% are under 5 minutes, versus 22% at midday). But the gap remains when comparing the **same window size** (all 3,573 weekday outcomes, before excluding same-line pairs):

| Time of day | 1 to 5 min | 5 to 10 min | 10 to 20 min | 20 to 30 min |
|---|---|---|---|---|
| AM peak | 39% (n=103) | 71% (n=55) | 94% (n=99) | 100% (n=19) |
| Midday | 66% (n=396) | 88% (n=371) | 98% (n=647) | 100% (n=357) |
| PM peak | 60% (n=235) | 82% (n=284) | 91% (n=324) | 99% (n=124) |
| Evening | 56% (n=72) | 88% (n=83) | 99% (n=170) | 98% (n=97) |

In the morning and afternoon rush, even a 10 to 20 minute window is not fully safe.

### 3. Connections that need the most buffer

Route pairs with at least 20 observed transfers, lowest success rates first. **Buffer needed** is the scheduled window that would have made 90% of that pair's transfers succeed.

| Feeder to connector | n | Made | Plausible range | Buffer needed |
|---|---|---|---|---|
| 052 to 002 | 20 | 60.0% | 38.7% to 78.1% | 7.1 min |
| 915 to 205 | 27 | 63.0% | 44.2% to 78.5% | 7.6 min |
| 005 to 002 | 30 | 66.7% | 48.8% to 80.8% | 6.3 min |
| 722 to 713 | 20 | 70.0% | 48.1% to 85.5% | 17.0 min |
| 915 to 914 | 41 | 70.7% | 55.5% to 82.4% | 12.3 min |
| 004 to 002 | 33 | 72.7% | 55.8% to 84.9% | 7.1 min |
| 915 to 004 | 27 | 74.1% | 55.3% to 86.8% | 7.5 min |
| 004 to 915 | 73 | 76.7% | 65.8% to 84.9% | 8.4 min |

These are small samples. The ranges are wide so treat the ranking as a lead to investigate, not a verdict. Route 002 is the connector in three of the top six.

### 4. Hub stops (descriptive)

| Stop | n | Made |
|---|---|---|
| West Edmonton Mall Transit Centre (5212) | 1,253 | 93.6% |
| West Edmonton Mall Transit Centre (5840) | 317 | 79.8% |
| 178 Street & 89 Avenue (5381) | 368 | 76.9% |
| 178 Street & 89 Avenue (5690) | 103 | 91.3% |
| 169 Street & 87 Avenue (5186) | 299 | 73.2% |
| 104 Street & Jasper Avenue (1622) | 55 | 54.5% |

Stop-level rates reflect the schedule design (which routes meet there, and how long the planned windows are) as much as the stop itself. The same location can differ a lot between bays.

## How it works

1. **Collect.** `collector/collect.py` polls the ETS GTFS-Realtime TripUpdates and VehiclePositions feeds every 60 seconds into SQLite and logs every poll, including failures.
2. **Schedule.** `collector/load_static_gtfs.py` loads the static GTFS schedule.
3. **Candidate transfers.** `sql/01_transfer_candidates.sql` finds the 30 stops served by the most routes (at least 5). For each bus arriving at a hub, it takes the first scheduled departure of every other route at least 60 seconds later (within 30 minutes). Same-line variants (for example 918A and 918B) are flagged and can be excluded.
4. **Outcomes.** `sql/10_transfer_outcomes.sql` takes the last realtime prediction for each trip at each stop. A transfer is made if the connector's actual departure is at least 60 seconds after the feeder's actual arrival, where actual = scheduled + reported delay.
5. **Risk tables.** `analysis/risk_score.py` computes success rates, 95% Wilson intervals, buffer needed, and risk labels (only for groups with at least 20 outcomes). It writes CSVs for Tableau into `dashboard/`.

Data quality rules: a prediction only counts if it was made within 3 minutes of the event; delays beyond one hour are dropped; canceled trips and skipped stops are excluded.

## Limitations

- **Small sample.** Six weekdays (Oct 16 and 19 to 23, 2026), 3,466 outcomes after exclusions. Oct 13 to 15 were not collected.
- **Uneven coverage.** The collecting laptop slept at times so mornings are covered more thinly than afternoons. I expect gaps to reduce the number of results rather than bias them but I have not tested that.
- **Predictions, not observed arrivals.** Delays come from the feed's last prediction before the bus reached the stop. The feed mostly reports departure delays, so the feeder's departure delay stands in for its arrival delay.
- **Plausible ranges are optimistic.** Transfers from the same trips and delay incidents are not independent.
- **Same-stop transfers only.** Walking between bays or nearby stops is not modelled, and only 30 hub stops are covered.
- **Rates depend on window length.** Pair and stop success rates mix in how long the scheduled windows are, so buffer needed is the fairer comparison.
- **No passenger data.** This measures schedule and delay outcomes, not what riders actually did.
- **Risk labels are arbitrary.** Low risk is at least 90% made and High risk is under 75%. The numbers and ranges matter more than the labels.

## Reproduce

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python collector/load_static_gtfs.py path/to/gtfs.zip --db data/ets.db
python collector/collect.py --db data/ets.db --interval 60      # leave running for days
python scripts/run_sql.py sql/01_transfer_candidates.sql
python scripts/run_sql.py sql/10_transfer_outcomes.sql
python analysis/risk_score.py --weekday-only --exclude-same-line
```

## Data and attribution

Contains information licensed under the City of Edmonton Open Data Agreement. Schedule (GTFS) and realtime feeds are published by the City of Edmonton / ETS.

## Next steps for this analysis

- Collect several more weeks, especially morning rush, on an always-on machine.
- Group nearby bays into a single transfer point.
- Compare winter and fall.
