import akshare as ak

try:
    print("Testing market sentiment data...")
    # 获取当日连板高度 (连板最高)
    df_pool = ak.stock_zt_pool_em(date="20260515") 
    print(df_pool.head())
    
    # 跌停池
    df_dt = ak.stock_zt_pool_dtgc_em(date="20260515")
    print("跌停:", len(df_dt))
except Exception as e:
    print(e)
