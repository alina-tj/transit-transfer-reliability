import sqlite3
import pandas as pd

DB_PATH = "data/ets.db"
conn = sqlite3.connect(DB_PATH)

query = """
SELECT
    service_date,
    feeder_route_name,
    connector_route_name,
    stop_id,
    sched_window_secs,
    headway_after_secs,
    feeder_delay_secs,
    connector_delay_secs,
    actual_window_secs,
    slack_secs,
    made,
    miss_cause
FROM transfer_outcomes
"""

df = pd.read_sql_query(query, conn)
conn.close()

#Convert seconds to minutes for easier interpretation.
df["sched_window_min"] = df["sched_window_secs"] / 60
df["actual_window_min"] = df["actual_window_secs"] / 60
df["feeder_delay_min"] = df["feeder_delay_secs"] / 60
df["connector_delay_min"] = df["connector_delay_secs"] / 60
df["slack_min"] = df["slack_secs"] / 60

#Create analytical transfer-risk categories.
def risk_category(minutes):
    if minutes <= 3:
        return "Very tight"
    elif minutes <= 6:
        return "Tight"
    elif minutes <= 14:
        return "Moderate"
    else:
        return "Comfortable"

df["risk_category"] = df["sched_window_min"].apply(risk_category)

#Create a readable route-pair column.
df["route_pair"] = (
    df["feeder_route_name"].astype(str)
    + " -> "
    + df["connector_route_name"].astype(str)
)

#Save the clean analysis dataset.
df.to_csv("data/transfer_outcomes.csv", index=False)

print("Analysis dataset created.")
print(f"Rows: {len(df)}")
print(f"Columns: {len(df.columns)}")
print()
print("Made:", int(df["made"].sum()))
print("Missed:", int((df["made"] == 0).sum()))
print(f"Success rate: {df['made'].mean() * 100:.1f}%")
print()
print("Risk categories:")
print(df["risk_category"].value_counts())
