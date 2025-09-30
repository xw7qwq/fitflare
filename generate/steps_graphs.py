import os
import sys
import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
from sklearn.linear_model import LinearRegression
from datetime import timedelta

# Ensure repo root on sys.path for common imports when invoked from this folder
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.profile_paths import resolve_or_prompt_profile, csv_path_for

STEPS_FILE = "fitbit_activity.csv"


# Load and normalize the raw steps CSV into a clean dataframe with parsed dates.
def load_steps_df(path):
    df = pd.read_csv(path)
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    df["steps"] = pd.to_numeric(df["steps"], errors="coerce")
    return df.dropna(subset=["date", "steps"]).copy()


# Build monthly and yearly aggregates and a time index for trend modeling.
def monthly_yearly_aggregates(df):
    t = df.copy()
    t["timestamp"] = pd.to_datetime(t["date"])
    t["year"] = t["timestamp"].dt.year
    t["month"] = t["timestamp"].dt.month
    cols = ["steps"]
    monthly = t.groupby(["year", "month"], as_index=False)[cols].mean(numeric_only=True)
    yearly = t.groupby(["year"], as_index=False)[cols].mean(numeric_only=True)
    monthly["month_year"] = monthly.apply(lambda x: f"{int(x['month']):02d}/{str(int(x['year']))[2:]}", axis=1)
    monthly = monthly.sort_values(["year", "month"]).reset_index(drop=True)
    monthly["time_index"] = np.arange(len(monthly))
    return monthly, yearly


# Fit a linear trend on a monthly series using its time index.
def add_trend(df, target_col):
    z = df.copy()
    if target_col not in z.columns or z[target_col].dropna().empty:
        z[target_col + "_trend"] = np.nan
        return z
    m = z[["time_index", target_col]].dropna()
    model = LinearRegression()
    model.fit(m[["time_index"]], m[target_col])
    z[target_col + "_trend"] = model.predict(z[["time_index"]])
    return z


# Plot daily steps as a line graph over time.
def plot_daily_steps(df):
    if "steps" not in df.columns or df.empty:
        return
    plt.figure(figsize=(16, 8))
    plt.plot(df["date"], df["steps"], linewidth=1, alpha=0.7, color="#4CAF50")
    plt.title("Daily Steps Over Time", fontsize=16, fontweight="bold")
    plt.xlabel("Date", fontsize=12)
    plt.ylabel("Steps", fontsize=12)
    plt.xticks(rotation=45)
    plt.grid(True, alpha=0.3)
    plt.tight_layout()
    plt.show()


# Plot monthly average steps with optional linear trend overlay.
def plot_monthly_steps(monthly):
    if "steps" not in monthly.columns:
        return
    plt.figure(figsize=(14, 7))
    plt.plot(monthly["month_year"], monthly["steps"], marker="o", label="Avg Steps", linewidth=2, markersize=6)
    if "steps_trend" in monthly.columns:
        plt.plot(monthly["month_year"], monthly["steps_trend"], label="Trend", linewidth=2, linestyle="--", alpha=0.8)
    plt.title("Monthly Average Steps", fontsize=16, fontweight="bold")
    plt.xlabel("Month/Year", fontsize=12)
    plt.ylabel("Steps", fontsize=12)
    plt.xticks(rotation=45)
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.show()


# Plot bar chart of yearly average steps with labels.
def plot_yearly_steps(yearly):
    if "steps" not in yearly.columns:
        return
    plt.figure(figsize=(10, 6))
    bars = plt.bar(yearly["year"], yearly["steps"], color="#4CAF50", alpha=0.7)
    for b in bars:
        plt.text(b.get_x() + b.get_width()/2, b.get_height(), f"{b.get_height():.0f}", ha="center", va="bottom")
    plt.title("Yearly Average Steps", fontsize=16, fontweight="bold")
    plt.xlabel("Year", fontsize=12)
    plt.ylabel("Steps", fontsize=12)
    plt.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    plt.show()


# Plot a histogram of daily steps.
def plot_steps_hist(df):
    if "steps" not in df.columns:
        return
    plt.figure(figsize=(10, 6))
    plt.hist(df["steps"].dropna(), bins=30, color="#4CAF50", alpha=0.7, edgecolor="black")
    plt.title("Distribution of Daily Steps", fontsize=16, fontweight="bold")
    plt.xlabel("Steps", fontsize=12)
    plt.ylabel("Count", fontsize=12)
    plt.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    plt.show()


# Plot steps with 7-day rolling average overlay.
def plot_steps_with_rolling_average(df):
    if "steps" not in df.columns or df.empty:
        return
    df_sorted = df.sort_values("date").copy()
    df_sorted["rolling_avg"] = df_sorted["steps"].rolling(window=7, center=True).mean()
    
    plt.figure(figsize=(16, 8))
    plt.plot(df_sorted["date"], df_sorted["steps"], linewidth=1, alpha=0.6, color="#4CAF50", label="Daily Steps")
    plt.plot(df_sorted["date"], df_sorted["rolling_avg"], linewidth=3, color="#2E7D32", label="7-Day Rolling Average")
    plt.title("Daily Steps with 7-Day Rolling Average", fontsize=16, fontweight="bold")
    plt.xlabel("Date", fontsize=12)
    plt.ylabel("Steps", fontsize=12)
    plt.xticks(rotation=45)
    plt.grid(True, alpha=0.3)
    plt.legend()
    plt.tight_layout()
    plt.show()


# Plot steps by day of week.
def plot_steps_by_weekday(df):
    if "steps" not in df.columns or df.empty:
        return
    df_copy = df.copy()
    df_copy["timestamp"] = pd.to_datetime(df_copy["date"])
    df_copy["weekday"] = df_copy["timestamp"].dt.day_name()
    
    weekday_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    weekday_avg = df_copy.groupby("weekday")["steps"].mean().reindex(weekday_order)
    
    plt.figure(figsize=(10, 6))
    bars = plt.bar(weekday_avg.index, weekday_avg.values, color="#4CAF50", alpha=0.7)
    for b in bars:
        plt.text(b.get_x() + b.get_width()/2, b.get_height(), f"{b.get_height():.0f}", ha="center", va="bottom")
    plt.title("Average Steps by Day of Week", fontsize=16, fontweight="bold")
    plt.xlabel("Day of Week", fontsize=12)
    plt.ylabel("Average Steps", fontsize=12)
    plt.xticks(rotation=45)
    plt.grid(axis="y", alpha=0.3)
    plt.tight_layout()
    plt.show()


# Orchestrate data loading, aggregation, plotting, and CSV outputs.
def main():
    profile_id = resolve_or_prompt_profile()
    steps_csv = csv_path_for(profile_id, STEPS_FILE)
    if not os.path.exists(steps_csv):
        print("❌ Error: Steps CSV not found.")
        print("   Please run fetch_steps.py first to generate the steps data.")
        return
    
    df = load_steps_df(steps_csv)
    monthly, yearly = monthly_yearly_aggregates(df)
    monthly = add_trend(monthly, "steps")
    
    print("Monthly averages:")
    for _, r in monthly.iterrows():
        val = r["steps"] if "steps" in monthly.columns else np.nan
        print(f"{r['month_year']}: Steps={val:.0f}" if pd.notna(val) else f"{r['month_year']}: Steps=NA")
    
    if "steps" in yearly.columns:
        print("\nYearly averages:")
        for _, r in yearly.iterrows():
            print(f"Year {int(r['year'])}: Steps={r['steps']:.0f}")
    
    # Generate all plots
    plot_daily_steps(df)
    plot_monthly_steps(monthly)
    plot_yearly_steps(yearly)
    plot_steps_hist(df)
    plot_steps_with_rolling_average(df)
    plot_steps_by_weekday(df)
    
    # Save aggregated data to CSV
    out_dir = os.path.dirname(steps_csv)
    monthly.to_csv(os.path.join(out_dir, "average_steps_per_month.csv"), index=False)
    yearly.to_csv(os.path.join(out_dir, "average_steps_per_year.csv"), index=False)
    
    print(f"\n✅ Steps analysis complete!")
    print(f"   Monthly data saved to: {os.path.join(out_dir, 'average_steps_per_month.csv')}")
    print(f"   Yearly data saved to: {os.path.join(out_dir, 'average_steps_per_year.csv')}")


if __name__ == "__main__":
    main()
