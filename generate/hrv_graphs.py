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
HRV_FILE = 'fitbit_hrv.csv'
try:
    profile_id = resolve_or_prompt_profile()
    hrv_csv = csv_path_for(profile_id, HRV_FILE)
    df = pd.read_csv(hrv_csv)
except FileNotFoundError:
    print(f"❌ Error: {hrv_csv} not found.")
    print("   Please run fetch_hrv_data.py first to generate the HRV data.")
    sys.exit(1)
except Exception as e:
    print(f"❌ Error loading HRV data: {e}")
    sys.exit(1)

# Rename and map columns to match expectations
df['timestamp'] = pd.to_datetime(df['date'])
df['rmssd'] = df['dailyRmssd']

# Extract components from timestamp
df['year'] = df['timestamp'].dt.year
df['month'] = df['timestamp'].dt.month

# Monthly and yearly averages for HRV (rmssd)
monthly_avg = df.groupby(['year', 'month'])['rmssd'].mean().reset_index()
yearly_avg = df.groupby('year')['rmssd'].mean().reset_index()

# Add a time index for trend analysis
monthly_avg['month_year'] = monthly_avg.apply(lambda x: f"{int(x['month']):02d}/{str(int(x['year']))[2:]}", axis=1)
monthly_avg['time_index'] = np.arange(len(monthly_avg))

# Linear regression to find trend
X = monthly_avg[['time_index']]
y = monthly_avg['rmssd']
model = LinearRegression()
model.fit(X, y)
monthly_avg['rmssd_trend'] = model.predict(X)

# Print monthly and yearly HRV values
print("Monthly average HRV (rmssd):")
current_year = None
for _, row in monthly_avg.iterrows():
    if current_year != row['year']:
        if current_year is not None:
            avg = yearly_avg[yearly_avg['year'] == current_year]['rmssd'].values[0]
            print(f"\nYear {current_year} average RMSSD: {avg:.2f}\n" + '-' * 40)
        current_year = row['year']
    print(f"{row['month_year']}: {row['rmssd']:.2f}")

# Final year avg
if current_year is not None:
    avg = yearly_avg[yearly_avg['year'] == current_year]['rmssd'].values[0]
    print(f"\nYear {current_year} average RMSSD: {avg:.2f}\n" + '-' * 40)

# Print linear regression model
print(f"\nLinear Regression Model: RMSSD = {model.coef_[0]:.4f} * time_index + {model.intercept_:.4f}")

# Plot RMSSD trend
plt.figure(figsize=(14, 7))
plt.plot(monthly_avg['month_year'], monthly_avg['rmssd'], marker='o', label='Average RMSSD')
plt.plot(monthly_avg['month_year'], monthly_avg['rmssd_trend'], color='red', label='Trend Line')
plt.title('Monthly Average HRV (RMSSD) with Trend')
plt.xlabel('Month/Year')
plt.ylabel('RMSSD')
plt.xticks(rotation=45)
plt.grid(True)
plt.legend()
plt.tight_layout()
plt.show()

# Plot yearly average bars
plt.figure(figsize=(10, 5))
bars = plt.bar(yearly_avg['year'], yearly_avg['rmssd'], color='skyblue')
for bar in bars:
    plt.text(bar.get_x() + bar.get_width() / 2, bar.get_height(), f'{bar.get_height():.2f}', ha='center', va='bottom')
plt.title('Yearly Average RMSSD')
plt.xlabel('Year')
plt.ylabel('RMSSD')
plt.grid(axis='y')
plt.tight_layout()
plt.show()

# Add day of week analysis for heatmap
# HRV readings are taken during sleep, so they represent recovery felt on that day
df['day_of_week'] = df['timestamp'].dt.day_name()
df['day_num'] = df['timestamp'].dt.dayofweek

# Calculate weekly averages by day of week
weekly_avg = df.groupby('day_of_week')['rmssd'].mean().reset_index()
weekly_avg['day_num'] = weekly_avg['day_of_week'].map({
    'Monday': 0, 'Tuesday': 1, 'Wednesday': 2, 'Thursday': 3, 
    'Friday': 4, 'Saturday': 5, 'Sunday': 6
})
weekly_avg = weekly_avg.sort_values('day_num')

# Heatmap of average HRV by day of the week
plt.figure(figsize=(10, 5))
day_avg_matrix = weekly_avg.pivot_table(index='day_of_week', values='rmssd', observed=False)

# Ensure proper day order for display
day_order = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday']
day_avg_matrix = day_avg_matrix.reindex(day_order)

sns.heatmap(day_avg_matrix, annot=True, cmap='coolwarm_r', fmt=".2f")
plt.title('Heatmap of Average HRV (RMSSD) by Day of the Week')
plt.xlabel('Day of the Week')
plt.ylabel('Average HRV (RMSSD)')
plt.show()

# Save summaries next to the input CSV
out_dir = os.path.dirname(hrv_csv)
monthly_avg.to_csv(os.path.join(out_dir, 'average_hrv_per_month.csv'), index=False)
yearly_avg.to_csv(os.path.join(out_dir, 'average_hrv_per_year.csv'), index=False)
