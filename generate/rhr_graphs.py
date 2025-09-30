import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns
from sklearn.linear_model import LinearRegression
import os
import sys

# Ensure repo root on sys.path for common imports when invoked from this folder
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.profile_paths import resolve_or_prompt_profile, csv_path_for

# Resolve profile and load the CSV file with error handling
RHR_FILE = 'fitbit_rhr.csv'
try:
    profile_id = resolve_or_prompt_profile()
    rhr_csv = csv_path_for(profile_id, RHR_FILE)
    df = pd.read_csv(rhr_csv)
except FileNotFoundError:
    print(f"❌ Error: {rhr_csv} not found.")
    print("   Please run fetch_rhr_data.py first to generate the resting heart rate data.")
    sys.exit(1)
except Exception as e:
    print(f"❌ Error loading resting heart rate data: {e}")
    sys.exit(1)

# Convert date to datetime and rename
df['timestamp'] = pd.to_datetime(df['date'])
df['resting_hr'] = df['resting_heart_rate']

# Extract year and month
df['year'] = df['timestamp'].dt.year
df['month'] = df['timestamp'].dt.month

# Monthly and yearly averages
monthly_avg = df.groupby(['year', 'month'])['resting_hr'].mean().reset_index()
yearly_avg = df.groupby('year')['resting_hr'].mean().reset_index()

# Add a time index for trend analysis
monthly_avg['month_year'] = monthly_avg.apply(lambda x: f"{int(x['month']):02d}/{str(int(x['year']))[2:]}", axis=1)
monthly_avg['time_index'] = np.arange(len(monthly_avg))

# Linear regression to find trend
X = monthly_avg[['time_index']]
y = monthly_avg['resting_hr']
model = LinearRegression()
model.fit(X, y)
monthly_avg['rhr_trend'] = model.predict(X)

# Print monthly and yearly values
print("Monthly average Resting Heart Rate:")
current_year = None
for _, row in monthly_avg.iterrows():
    if current_year != row['year']:
        if current_year is not None:
            avg = yearly_avg[yearly_avg['year'] == current_year]['resting_hr'].values[0]
            print(f"\nYear {current_year} average RHR: {avg:.2f}\n" + '-' * 40)
        current_year = row['year']
    print(f"{row['month_year']}: {row['resting_hr']:.2f}")

# Final year avg
if current_year is not None:
    avg = yearly_avg[yearly_avg['year'] == current_year]['resting_hr'].values[0]
    print(f"\nYear {current_year} average RHR: {avg:.2f}\n" + '-' * 40)

# Print linear regression model
print(f"\nLinear Regression Model: RHR = {model.coef_[0]:.4f} * time_index + {model.intercept_:.4f}")

# Plot RHR trend
plt.figure(figsize=(14, 7))
plt.plot(monthly_avg['month_year'], monthly_avg['resting_hr'], marker='o', label='Average RHR')
plt.plot(monthly_avg['month_year'], monthly_avg['rhr_trend'], color='red', label='Trend Line')
plt.title('Monthly Average Resting Heart Rate with Trend')
plt.xlabel('Month/Year')
plt.ylabel('Resting Heart Rate')
plt.xticks(rotation=45)
plt.grid(True)
plt.legend()
plt.tight_layout()
plt.show()

# Plot yearly average bars
plt.figure(figsize=(10, 5))
bars = plt.bar(yearly_avg['year'], yearly_avg['resting_hr'], color='lightcoral')
for bar in bars:
    plt.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f'{bar.get_height():.2f}', ha='center', va='bottom')
plt.title('Yearly Average Resting Heart Rate')
plt.xlabel('Year')
plt.ylabel('Resting Heart Rate')
plt.grid(axis='y')
plt.tight_layout()
plt.show()

# Save summaries next to the input CSV
out_dir = os.path.dirname(rhr_csv)
monthly_avg.to_csv(os.path.join(out_dir, 'average_rhr_per_month.csv'), index=False)
yearly_avg.to_csv(os.path.join(out_dir, 'average_rhr_per_year.csv'), index=False)
