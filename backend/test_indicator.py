import pandas as pd
import numpy as np

dfs = []
for code in ['A', 'B']:
    df = pd.DataFrame({
        'code': [code]*5,
        '日期': pd.date_range('2023-01-01', periods=5),
        '收盘': np.random.rand(5)*10,
        '成交量': np.random.rand(5)*100
    })
    dfs.append(df)

master = pd.concat(dfs).reset_index(drop=True)
master = master.sample(frac=1, random_state=42) # jumble it
master = master.sort_values(['code', '日期'])

group = master.groupby('code', sort=False)
ema5 = group['收盘'].ewm(span=5, adjust=False).mean()
print("EWM Index:")
print(ema5.index)

ema5_reset = ema5.reset_index(level=0, drop=True)
print("EWM Reset Index:")
print(ema5_reset.index)

master['EMA5'] = ema5_reset
print(master[['code', '日期', '收盘', 'EMA5']])

