import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.linear_model import LinearRegression
from scipy.stats import pearsonr, spearmanr
import os
import sys

# Ensure repo root on sys.path for common imports when invoked from this folder
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.profile_paths import resolve_or_prompt_profile, csv_path_for

# Load data with error handling for the selected profile
HRV_FILE = 'fitbit_hrv.csv'
RHR_FILE = 'fitbit_rhr.csv'
try:
    profile_id = resolve_or_prompt_profile()
    hrv_path = csv_path_for(profile_id, HRV_FILE)
    rhr_path = csv_path_for(profile_id, RHR_FILE)
    hrv_df = pd.read_csv(hrv_path)
    rhr_df = pd.read_csv(rhr_path)
except FileNotFoundError as e:
    print(f"❌ Error: {e}")
    print("   Please ensure the CSV files exist for the selected profile or run the fetch scripts.")
    sys.exit(1)
except Exception as e:
    print(f"❌ Error loading data: {e}")
    sys.exit(1)

# Convert date columns
hrv_df["date"] = pd.to_datetime(hrv_df["date"])
rhr_df["date"] = pd.to_datetime(rhr_df["date"])

# Standardize column names
hrv_df = hrv_df.rename(columns={"dailyRmssd": "rmssd"})

# Merge on date
df = pd.merge(hrv_df[["date", "rmssd"]], rhr_df[["date", "resting_heart_rate"]], on="date", how="inner")

# Drop rows with missing values
df.dropna(subset=["rmssd", "resting_heart_rate"], inplace=True)

# --- Correlation ---
pearson_corr, _ = pearsonr(df["resting_heart_rate"], df["rmssd"])
spearman_corr, _ = spearmanr(df["resting_heart_rate"], df["rmssd"])

print(f"Pearson Correlation (RHR vs HRV): {pearson_corr:.4f}")
print(f"Spearman Correlation (RHR vs HRV): {spearman_corr:.4f}")

# --- Linear Regression ---
X = df[["resting_heart_rate"]]
y = df["rmssd"]
reg = LinearRegression().fit(X, y)
df["predicted_rmssd"] = reg.predict(X)
print(f"\nLinear Regression: rmssd = {reg.coef_[0]:.4f} * rhr + {reg.intercept_:.4f}")

# --- Plots ---
plt.figure(figsize=(10, 6))
sns.scatterplot(x="resting_heart_rate", y="rmssd", data=df, alpha=0.7)
plt.plot(df["resting_heart_rate"], df["predicted_rmssd"], color="red", label="Regression Line")
plt.title("HRV (RMSSD) vs Resting Heart Rate")
plt.xlabel("Resting Heart Rate")
plt.ylabel("HRV (RMSSD)")
plt.legend()
plt.grid(True)
plt.tight_layout()
plt.show()

# Time Series: Dual Y-Axis
fig, ax1 = plt.subplots(figsize=(14, 7))
ax1.plot(df["date"], df["rmssd"], label="HRV (RMSSD)", color="blue")
ax1.set_ylabel("HRV (RMSSD)", color="blue")
ax1.tick_params(axis='y', labelcolor='blue')

ax2 = ax1.twinx()
ax2.plot(df["date"], df["resting_heart_rate"], label="RHR", color="red", alpha=0.6)
ax2.set_ylabel("Resting Heart Rate", color="red")
ax2.tick_params(axis='y', labelcolor='red')

plt.title("HRV and Resting Heart Rate Over Time")
fig.tight_layout()
plt.show()

# Save correlation results next to the HRV CSV
out_dir = os.path.dirname(hrv_path)
with open(os.path.join(out_dir, "hrv_rhr_correlation_summary.txt"), "w") as f:
    f.write(f"Pearson Correlation: {pearson_corr:.4f}\n")
    f.write(f"Spearman Correlation: {spearman_corr:.4f}\n")
    f.write(f"Linear Regression: rmssd = {reg.coef_[0]:.4f} * rhr + {reg.intercept_:.4f}\n")
