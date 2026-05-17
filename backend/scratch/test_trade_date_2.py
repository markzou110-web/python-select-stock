import akshare as ak
try:
    trade_dates = ak.tool_trade_date_hist_sina()
    print(trade_dates.head())
    print(trade_dates.info())
except Exception as e:
    print(e)
