import akshare as ak
try:
    df_pool = ak.stock_zt_pool_em() 
    print(df_pool.head())
    print("Success without date")
except Exception as e:
    print(e)
