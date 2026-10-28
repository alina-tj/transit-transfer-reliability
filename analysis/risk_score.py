"""
Turn labeled transfer outcomes into risk tables for the dashboard.

Usage (from the project folder):
    python analysis/risk_score.py --db data/ets.db --out dashboard
    python analysis/risk_score.py --weekday-only          # ignore weekends/holidays
    python analysis/risk_score.py --min-n 30              # stricter sample-size rule

Reads the table `transfer_outcomes` (built by sql/10_transfer_outcomes.sql) and writes
CSV files you can open in Tableau:

    outcomes.csv       one row per transfer outcome, with day type and time-of-day bucket
    pair_risk.csv      feeder route -> connector route
    stop_risk.csv      per hub stop
    time_risk.csv      per day type and time-of-day bucket
    buffer_curve.csv   success rate by scheduled transfer window

How to read the columns
-----------------------
n, made           number of transfers observed, and how many were made
success_rate      made / n
ci_low, ci_high   95% Wilson interval: the plausible range for the TRUE success rate.
                  Wide interval = not enough data.
buffer_needed_min scheduled window (minutes) that would have made 90% of these transfers
                  succeed. Assumes delay differences don't depend on the scheduled window.
avg_headway_after_min   average wait for the next connecting bus after missing one
expected_miss_cost_min  (1 - success_rate) * avg_headway_after_min
risk              Low / Medium / High risk, or "Not enough data" when n < min-n
"""

import argparse
import re
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

# ---- Settings you can change (and should explain in your README) ----
MIN_N = 20                # outcomes needed before a group gets a risk label
LOW_CUTOFF = 0.90         # success rate at or above this  -> Low risk
HIGH_CUTOFF = 0.75        # success rate below this        -> High risk
MIN_TRANSFER_SECS = 60    # must match min_transfer_secs in transfer_params
HOLIDAYS = {20261012}     # service dates running holiday service (Thanksgiving 2026)

TIME_BUCKETS = [          # (label, first hour inclusive, last hour exclusive); hours can pass 24
    ("1 Early (before 6)", 0, 6),
    ("2 AM peak (6-9)", 6, 9),
    ("3 Midday (9-15)", 9, 15),
    ("4 PM peak (15-18)", 15, 18),
    ("5 Evening (18-22)", 18, 22),
    ("6 Late night (22+)", 22, 99),
]
WINDOW_BINS = [60, 120, 180, 300, 480, 720, 1200, 1800]
WINDOW_LABELS = ["1-2 min", "2-3 min", "3-5 min", "5-8 min", "8-12 min", "12-20 min", "20-30 min"]


def wilson(made, n, z=1.96):
    """95% Wilson score interval for a success rate."""
    made = np.asarray(made, dtype=float)
    n = np.asarray(n, dtype=float)
    p = made / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * np.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return centre - half, centre + half


def time_bucket(secs):
    hour = secs // 3600
    for label, lo, hi in TIME_BUCKETS:
        if lo <= hour < hi:
            return label
    return TIME_BUCKETS[-1][0]


def day_type(service_date):
    if service_date in HOLIDAYS:
        return "Holiday"
    dow = pd.to_datetime(str(service_date), format="%Y%m%d").dayofweek
    return "Weekday" if dow < 5 else ("Saturday" if dow == 5 else "Sunday")


def route_base(name):
    """'918A' -> '918'. Used to flag A/B variants of the same line."""
    return re.sub(r"[A-Za-z]+$", "", str(name))


def risk_label(n, success_rate, min_n):
    if n < min_n:
        return "Not enough data"
    if success_rate >= LOW_CUTOFF:
        return "Low risk"
    if success_rate < HIGH_CUTOFF:
        return "High risk"
    return "Medium risk"


def summarise(df, keys, min_n):
    """One row per group with counts, success rate, interval, buffer and risk label."""
    g = df.groupby(keys, dropna=False)
    out = g.agg(
        n=("made", "size"),
        made=("made", "sum"),
        n_feeder_trips=("feeder_trip_id", "nunique"),
        avg_sched_window_min=("sched_window_secs", lambda s: s.mean() / 60),
        avg_headway_after_min=("headway_after_secs", lambda s: s.mean() / 60),
        needed_p90_secs=("needed_secs", lambda s: s.quantile(0.9)),
    ).reset_index()
    out["success_rate"] = out["made"] / out["n"]
    out["ci_low"], out["ci_high"] = wilson(out["made"], out["n"])
    out["buffer_needed_min"] = np.maximum(out["needed_p90_secs"], MIN_TRANSFER_SECS) / 60
    out["expected_miss_cost_min"] = (1 - out["success_rate"]) * out["avg_headway_after_min"]
    out["risk"] = [risk_label(n, p, min_n) for n, p in zip(out["n"], out["success_rate"])]
    out = out.drop(columns=["needed_p90_secs"])
    return out.round(3)


def show(title, df, cols, rows=10):
    print(f"\n=== {title} ===")
    if df.empty:
        print("(nothing to show)")
    else:
        print(df[cols].head(rows).to_string(index=False))


def main():
    ap = argparse.ArgumentParser(description="Build transfer risk tables for the dashboard.")
    ap.add_argument("--db", default="data/ets.db")
    ap.add_argument("--out", default="dashboard")
    ap.add_argument("--min-n", type=int, default=MIN_N, help="outcomes needed for a risk label")
    ap.add_argument("--weekday-only", action="store_true", help="drop weekends and holidays")
    ap.add_argument("--exclude-same-line", action="store_true",
                    help="drop pairs like 918A->918B (same route number, different letter)")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    df = pd.read_sql_query(
        """SELECT o.*, s.stop_name
           FROM transfer_outcomes o
           LEFT JOIN gtfs_stops s ON s.stop_id = o.stop_id""",
        conn,
    )
    conn.close()
    if df.empty:
        raise SystemExit("transfer_outcomes is empty. Run sql/10_transfer_outcomes.sql first.")

    # --- derived columns ---
    df["day_type"] = df["service_date"].map(day_type)
    df["time_bucket"] = (df["sched_arr_secs"]).map(time_bucket)
    df["pair"] = df["feeder_route_name"] + " -> " + df["connector_route_name"]
    df["same_line_flag"] = df["feeder_route_name"].map(route_base) == df["connector_route_name"].map(route_base)
    # scheduled window that this transfer would have needed in order to succeed
    df["needed_secs"] = MIN_TRANSFER_SECS + df["feeder_delay_secs"] - df["connector_delay_secs"]

    Path(args.out).mkdir(parents=True, exist_ok=True)
    df.to_csv(Path(args.out) / "outcomes.csv", index=False)

    # --- what data do we have? ---
    print("=== Data you have ===")
    per_date = (df.groupby(["service_date", "day_type"]).agg(outcomes=("made", "size"), made=("made", "sum"))
                .reset_index())
    per_date["success_rate"] = (per_date["made"] / per_date["outcomes"]).round(3)
    print(per_date.to_string(index=False))

    work = df.copy()
    if args.weekday_only:
        work = work[work["day_type"] == "Weekday"]
        print(f"\n(weekday-only: kept {len(work)} of {len(df)} outcomes)")
    if args.exclude_same_line:
        flagged = int(work["same_line_flag"].sum())
        work = work[~work["same_line_flag"]]
        print(f"(excluded {flagged} same-line outcomes such as 918A->918B)")
    if work.empty:
        raise SystemExit("No outcomes left after filters.")

    lo, hi = wilson(work["made"].sum(), len(work))
    print(f"\nOverall: {work['made'].sum()} of {len(work)} transfers made "
          f"({100 * work['made'].mean():.1f}%; plausible range {100 * lo:.1f}% to {100 * hi:.1f}%)")

    # --- the tables ---
    pair = summarise(work, ["feeder_route_name", "connector_route_name"], args.min_n)
    pair["same_line_flag"] = (pair["feeder_route_name"].map(route_base) == pair["connector_route_name"].map(route_base))
    pair = pair.sort_values(["n"], ascending=False)
    stop = summarise(work, ["stop_id", "stop_name"], args.min_n).sort_values("n", ascending=False)
    time = summarise(work, ["day_type", "time_bucket"], args.min_n).sort_values(["day_type", "time_bucket"])

    work = work.copy()
    work["window_band"] = pd.cut(work["sched_window_secs"], bins=WINDOW_BINS + [10**9],
                                 labels=WINDOW_LABELS + ["30+ min"], right=False)
    curve = summarise(work.dropna(subset=["window_band"]), ["window_band"], args.min_n)

    pair.to_csv(Path(args.out) / "pair_risk.csv", index=False)
    stop.to_csv(Path(args.out) / "stop_risk.csv", index=False)
    time.to_csv(Path(args.out) / "time_risk.csv", index=False)
    curve.to_csv(Path(args.out) / "buffer_curve.csv", index=False)

    # --- what can you honestly say? ---
    labelled = pair[pair["n"] >= args.min_n]
    print(f"\nRoute pairs: {len(pair)} seen, {len(labelled)} with at least {args.min_n} outcomes "
          f"({int(pair['same_line_flag'].sum())} look like same-line variants)")
    cols = ["feeder_route_name", "connector_route_name", "n", "success_rate", "ci_low", "ci_high",
            "buffer_needed_min", "risk"]
    if labelled.empty:
        print("-> No pair has enough data yet. Report pooled results (time of day, window size, stops) only.")
    else:
        show(f"Riskiest route pairs (n >= {args.min_n})",
             labelled.sort_values("success_rate"), cols)

    show("Hub stops", stop, ["stop_id", "stop_name", "n", "success_rate", "ci_low", "ci_high", "risk"], rows=12)
    show("Time of day", time, ["day_type", "time_bucket", "n", "success_rate", "ci_low", "ci_high", "risk"], rows=20)
    show("Success by scheduled window (pooled)", curve,
         ["window_band", "n", "success_rate", "ci_low", "ci_high", "avg_headway_after_min"], rows=10)

    print(f"\nCSV files written to {args.out}/")


if __name__ == "__main__":
    main()
