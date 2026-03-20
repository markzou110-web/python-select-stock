import pandas as pd
from core.db import get_db_engine
from sqlalchemy import text

engine = get_db_engine()
query = text('SELECT date as "日期", vol as "成交量" FROM daily_k WHERE code = \'600000\' ORDER BY date DESC LIMIT 25')
with engine.connect() as conn:
    df = pd.read_sql(query, conn)

print("Volume history:")
print(df)
