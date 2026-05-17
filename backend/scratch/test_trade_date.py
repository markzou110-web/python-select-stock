import akshare as ak
from datetime import datetime
try:
    trade_dates = ak.tool_trade_date_hist_sina()
    # trade_dates is a dataframe or series of dates
    # get the last date <= today
    today = datetime.now().date()
    dates_list = trade_dates['trade_date'].dt.date.tolist()
    past_dates = [d for d in dates_list if d <= today]
    latest_date = past_dates[-1] if past_dates else today
    date_str = latest_date.strftime("%Y%m%d")
    print(f"Latest trade date: {date_str}")
    
    df_pool = ak.stock_zt_pool_em(date=date_str)
    print("Limit up count:", len(df_pool))
    
    df_dt = ak.stock_zt_pool_dtgc_em(date=date_str)
    print("Limit down count:", len(df_dt))
except Exception as e:
    print("Error:", e)
