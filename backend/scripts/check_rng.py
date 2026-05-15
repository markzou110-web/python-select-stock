import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from core.db import get_db_engine, load_from_db
from datetime import datetime, timedelta
import pandas as pd
import numpy as np

code = "002727"
engine = get_db_engine()
df = load_from_db(code, (datetime.now() - timedelta(days=400)).strftime("%Y-%m-%d"), engine)
close = df['收盘'].values
wper = 100
wper2 = 199
avgt = 3.0

abs_diff = np.abs(close - np.roll(close, 1))
abs_diff[0] = 0

smooth1 = pd.Series(abs_diff).ewm(span=wper, adjust=False).mean().values
rng = pd.Series(smooth1).ewm(span=wper2, adjust=False).mean().values * avgt

rf_filter = np.zeros(len(close))
rf_filter[0] = close[0]

for i in range(1, len(close)):
    curr_rng = rng[i] if not np.isnan(rng[i]) else 0
    if close[i] > rf_filter[i-1]:
        rf_filter[i] = max(rf_filter[i-1], close[i] - curr_rng)
    else:
        rf_filter[i] = min(rf_filter[i-1], close[i] + curr_rng)

df['RF'] = rf_filter
df['RNG'] = rng
print(df[['日期', '收盘', 'RF', 'RNG']].tail(40).to_string())
