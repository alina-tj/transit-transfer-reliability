import pandas as pd
import matplotlib.pyplot as plt

df = pd.read_csv("data/transfer_outcomes.csv")

order = ["Very tight", "Tight", "Moderate", "Comfortable"]

summary = (
    df.groupby("risk_category")["made"]
    .agg(["count", "mean"])
    .reindex(order)
)

summary["success_rate"] = summary["mean"] * 100

print(summary[["count", "success_rate"]])

plt.figure(figsize=(8, 5))

plt.bar(
    summary.index,
    summary["success_rate"]
)

plt.ylim(0, 100)
plt.ylabel("Transfer success rate (%)")
plt.xlabel("Scheduled transfer window")
plt.title("Transfer Success Rate by Scheduled Transfer Window")

for i, value in enumerate(summary["success_rate"]):
    plt.text(
        i,
        value + 2,
        f"{value:.1f}%",
        ha="center"
    )

plt.tight_layout()
plt.savefig("data/transfer_success_by_risk.png", dpi=300)
plt.show()
