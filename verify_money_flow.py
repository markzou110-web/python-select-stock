import akshare as ak
import pandas as pd
import sys

def verify_money_flow(code='601579'):
    print(f"--- Verifying Money Flow for {code} ---")
    
    # 1. Get raw data from akshare
    try:
        # Determine market
        market = 'sz' if code.startswith('0') or code.startswith('3') else 'sh'
        df = ak.stock_individual_fund_flow(stock=code, market=market)
        
        if df.empty:
            print("❌ No data returned from akshare")
            return
            
        print("\nRaw Data (Last 5 days):")
        cols = ['日期', '主力净流入-净额', '超大单净流入-净额', '大单净流入-净额']
        print(df[cols].tail(5))
        
        # 2. Check units
        latest_main_flow = df['主力净流入-净额'].iloc[-1]
        print(f"\nLatest Main Flow (Raw): {latest_main_flow}")
        print(f"If in Yuan: {latest_main_flow / 10000:.2f} 万元")
        print(f"If in Wan Yuan: {latest_main_flow:.2f} 万元")
        
        # 3. Last 3 days sum
        last_3_days_sum = df['主力净流入-净额'].tail(3).sum()
        print(f"\nLast 3 days raw sum: {last_3_days_sum}")
        print(f"Last 3 days (normalized to 100M): {last_3_days_sum / 100000000:.4f} 亿元")
        
        # 4. Processed value (Backend current logic)
        processed_val_backend = (last_3_days_sum / 10000.0) # Backend converts to 万元
        print(f"Backend processed value (万元): {processed_val_backend:.2f}")
        
        # 5. Frontend display logic
        frontend_display = (processed_val_backend / 10000.0) # Frontend converts 万元 to 亿元
        print(f"Frontend display value (亿元): {frontend_display:.2f} 亿")

    except Exception as e:
        print(f"❌ Error: {e}")

if __name__ == "__main__":
    verify_money_flow()
