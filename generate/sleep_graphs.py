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

SLEEP_FILE = "fitbit_sleep.csv"
HRV_FILE = "fitbit_hrv.csv"


# Load and normalize the raw sleep CSV into a clean dataframe with parsed dates.
def load_sleep_df(path):
    df = pd.read_csv(path)
    if "date" not in df.columns and "dateOfSleep" in df.columns:
        df = df.rename(columns={"dateOfSleep": "date"})
    df["date"] = pd.to_datetime(df["date"], errors="coerce").dt.date
    if "efficiency" in df.columns:
        df["efficiency"] = pd.to_numeric(df["efficiency"], errors="coerce")
    return df.dropna(subset=["date"]).copy()


# Choose one main sleep per date, preferring Fitbit's main flag or longest duration.
def select_main_sleep(df):
    sdf = df.copy()
    if "isMainSleep" in sdf.columns and sdf["isMainSleep"].notna().any():
        sdf = sdf[sdf["isMainSleep"] == True]
    if sdf.empty:
        sdf = df.copy()
        if "minutesAsleep" in sdf.columns:
            sdf = sdf.sort_values(["date", "minutesAsleep"], ascending=[True, False]).drop_duplicates("date")
        else:
            sdf = sdf.sort_values(["date"]).drop_duplicates("date")
    else:
        sdf = sdf.sort_values(["date", "endTime"]).drop_duplicates("date", keep="last")
    return sdf.reset_index(drop=True)


# Compute stage percentage columns (deep/REM/light) relative to minutes asleep.
def add_stage_percentages(df):
    out = df.copy()
    for c in ["minutesDeep", "minutesREM", "minutesLight", "minutesWakeStages", "minutesAsleep", "timeInBed"]:
        if c not in out.columns:
            out[c] = np.nan
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out["pctDeep"] = np.where(out["minutesAsleep"] > 0, 100.0 * out["minutesDeep"] / out["minutesAsleep"], np.nan)
    out["pctREM"] = np.where(out["minutesAsleep"] > 0, 100.0 * out["minutesREM"] / out["minutesAsleep"], np.nan)
    out["pctLight"] = np.where(out["minutesAsleep"] > 0, 100.0 * out["minutesLight"] / out["minutesAsleep"], np.nan)
    return out


# Build monthly and yearly aggregates and a time index for trend modeling.
def monthly_yearly_aggregates(df):
    t = df.copy()
    t["timestamp"] = pd.to_datetime(t["date"])
    t["year"] = t["timestamp"].dt.year
    t["month"] = t["timestamp"].dt.month
    cols = [c for c in ["sleepScore", "minutesAsleep", "efficiency", "pctDeep", "pctREM", "pctLight"] if c in t.columns]
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


# Plot monthly average sleep score with optional linear trend overlay.
def plot_monthly_score(monthly):
    if "sleepScore" not in monthly.columns:
        return
    plt.figure(figsize=(14, 7))
    plt.plot(monthly["month_year"], monthly["sleepScore"], marker="o", label="Avg Sleep Score")
    if "sleepScore_trend" in monthly.columns:
        plt.plot(monthly["month_year"], monthly["sleepScore_trend"], label="Trend")
    plt.title("Monthly Average Sleep Score")
    plt.xlabel("Month/Year")
    plt.ylabel("Sleep Score")
    plt.xticks(rotation=45)
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.show()


# Plot bar chart of yearly average sleep scores with labels.
def plot_yearly_score(yearly):
    if "sleepScore" not in yearly.columns:
        return
    plt.figure(figsize=(10, 5))
    bars = plt.bar(yearly["year"], yearly["sleepScore"])
    for b in bars:
        plt.text(b.get_x() + b.get_width()/2, b.get_height(), f"{b.get_height():.1f}", ha="center", va="bottom")
    plt.title("Yearly Average Sleep Score")
    plt.xlabel("Year")
    plt.ylabel("Sleep Score")
    plt.grid(axis="y")
    plt.tight_layout()
    plt.show()


# Plot monthly average minutes asleep as a time series.
def plot_monthly_minutes(monthly):
    if "minutesAsleep" not in monthly.columns:
        return
    plt.figure(figsize=(14, 6))
    plt.plot(monthly["month_year"], monthly["minutesAsleep"], marker="o", label="Avg Minutes Asleep")
    plt.title("Monthly Average Minutes Asleep")
    plt.xlabel("Month/Year")
    plt.ylabel("Minutes")
    plt.xticks(rotation=45)
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.show()


# Plot monthly average percentages for deep, REM, and light sleep stages.
def plot_stage_lines(monthly):
    cols = [c for c in ["pctDeep", "pctREM", "pctLight"] if c in monthly.columns]
    if not cols:
        return
    plt.figure(figsize=(14, 6))
    for c in cols:
        plt.plot(monthly["month_year"], monthly[c], marker="o", label=c)
    plt.title("Monthly Average Sleep Stage Percentages")
    plt.xlabel("Month/Year")
    plt.ylabel("Percentage")
    plt.xticks(rotation=45)
    plt.grid(True)
    plt.legend()
    plt.tight_layout()
    plt.show()


# Plot a histogram of nightly sleep scores.
def plot_score_hist(df):
    if "sleepScore" not in df.columns:
        return
    plt.figure(figsize=(8, 5))
    plt.hist(df["sleepScore"].dropna(), bins=20)
    plt.title("Distribution of Nightly Sleep Scores")
    plt.xlabel("Sleep Score")
    plt.ylabel("Count")
    plt.tight_layout()
    plt.show()


# Load HRV CSV and normalize to have date and rmssd columns.
def load_hrv_df(path):
    if not os.path.exists(path):
        return None
    h = pd.read_csv(path)
    if "date" in h.columns:
        h["date"] = pd.to_datetime(h["date"], errors="coerce").dt.date
    if "dailyRmssd" in h.columns:
        h = h.rename(columns={"dailyRmssd": "rmssd"})
    return h.dropna(subset=["date"]) if "date" in h.columns else None


# Compute correlations between sleep score and HRV for same night and next day.
def hrv_correlations(sleep_df, hrv_df):
    if hrv_df is None:
        return None, None, None
    s = sleep_df[["date", "sleepScore"]].dropna()
    m_same = s.merge(hrv_df[["date", "rmssd"]], on="date", how="inner")
    h_shift = hrv_df[["date", "rmssd"]].copy()
    h_shift["date"] = pd.to_datetime(h_shift["date"]) - timedelta(days=1)
    h_shift["date"] = h_shift["date"].dt.date
    m_next = s.merge(h_shift, on="date", how="inner", suffixes=("", "_next"))
    p_same = m_same["sleepScore"].corr(m_same["rmssd"], method="pearson") if not m_same.empty else np.nan
    s_same = m_same["sleepScore"].corr(m_same["rmssd"], method="spearman") if not m_same.empty else np.nan
    p_next = m_next["sleepScore"].corr(m_next["rmssd"], method="pearson") if not m_next.empty else np.nan
    s_next = m_next["sleepScore"].corr(m_next["rmssd"], method="spearman") if not m_next.empty else np.nan
    return (m_same, p_same, s_same), (m_next, p_next, s_next), hrv_df


# Scatter plot with an optional fitted linear trend line.
def plot_scatter_with_trend(df, x, y, title, xlabel, ylabel):
    if df is None or df.empty:
        return
    plt.figure(figsize=(8, 6))
    plt.scatter(df[x], df[y])
    try:
        x_vals = df[x].values.reshape(-1, 1)
        y_vals = df[y].values
        model = LinearRegression()
        model.fit(x_vals, y_vals)
        x_sorted = np.sort(df[x].values)
        y_pred = model.predict(x_sorted.reshape(-1, 1))
        plt.plot(x_sorted, y_pred)
    except Exception:
        pass
    plt.title(title)
    plt.xlabel(xlabel)
    plt.ylabel(ylabel)
    plt.tight_layout()
    plt.show()


# Orchestrate data loading, aggregation, plotting, CSV outputs, and HRV analysis.
def main():
    profile_id = resolve_or_prompt_profile()
    sleep_csv = csv_path_for(profile_id, SLEEP_FILE)
    hrv_csv = csv_path_for(profile_id, HRV_FILE)
    if not os.path.exists(sleep_csv):
        print("❌ Error: Sleep CSV not found.")
        print("   Please run fetch_sleep_data.py first to generate the sleep data.")
        return
    df = load_sleep_df(sleep_csv)
    df = select_main_sleep(df)
    df = add_stage_percentages(df)
    monthly, yearly = monthly_yearly_aggregates(df)
    monthly = add_trend(monthly, "sleepScore")
    print("Monthly averages:")
    for _, r in monthly.iterrows():
        val = r["sleepScore"] if "sleepScore" in monthly.columns else np.nan
        print(f"{r['month_year']}: SleepScore={val:.2f}" if pd.notna(val) else f"{r['month_year']}: SleepScore=NA")
    if "sleepScore" in yearly.columns:
        print("\nYearly averages:")
        for _, r in yearly.iterrows():
            print(f"Year {int(r['year'])}: SleepScore={r['sleepScore']:.2f}")
    plot_monthly_score(monthly)
    plot_yearly_score(yearly)
    plot_monthly_minutes(monthly)
    plot_stage_lines(monthly)
    plot_score_hist(df)
    out_dir = os.path.dirname(sleep_csv)
    monthly.to_csv(os.path.join(out_dir, "average_sleep_per_month.csv"), index=False)
    yearly.to_csv(os.path.join(out_dir, "average_sleep_per_year.csv"), index=False)
    hrv_df = load_hrv_df(hrv_csv)
    msame, mnext, _ = hrv_correlations(df, hrv_df)
    if msame[0] is not None:
        print(f"\nSleepScore vs HRV (same night): pearson={msame[1]:.3f}, spearman={msame[2]:.3f}")
        plot_scatter_with_trend(msame[0], "sleepScore", "rmssd", "Sleep Score vs HRV (Same Night)", "Sleep Score", "RMSSD")
        msame[0].to_csv(os.path.join(out_dir, "sleep_hrv_same_night.csv"), index=False)
    if mnext[0] is not None:
        print(f"SleepScore vs Next-Day HRV: pearson={mnext[1]:.3f}, spearman={mnext[2]:.3f}")
        mtmp = mnext[0].rename(columns={"rmssd": "rmssd_next"})
        plot_scatter_with_trend(mtmp, "sleepScore", "rmssd_next", "Sleep Score vs HRV (Next Day)", "Sleep Score", "RMSSD (Next Day)")
        mnext[0].to_csv(os.path.join(out_dir, "sleep_score_vs_nextday_hrv.csv"), index=False)


if __name__ == "__main__":
    main()
