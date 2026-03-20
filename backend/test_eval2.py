import pandas as pd
from sqlalchemy import create_engine, text

engine = create_engine("sqlite:///alpha_vision.db")
query = text("SELECT code, date as '日期', open as '开盘', high as '最高', low as '最低', close as '收盘', vol as '成交量' FROM daily_k WHERE code = '600000' ORDER BY date DESC LIMIT 5")
with engine.connect() as conn:
    df = pd.read_sql(query, conn)

print(df)

