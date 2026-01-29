import sys
import os
import pandas as pd
import akshare as ak

# Add backend directory to sys.path
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from core.money_flow import get_individual_fund_flow

def verify_603198():
    code = "603198"
    print(f"🔍 Fetching money flow for {code}...")
    
    # Try fetching 5 days as shown in the image
    df = get_individual_fund_flow(code, days=5)
    
    if df.empty:
        print("❌ Failed to fetch data.")
        return

    print("📊 Recent Money Flow Data:")
    print(df)
    
    total_main = df['main_net_inflow'].sum()
    print(f"\n💰 Total Main Net Inflow (5 days): {total_main:.2f} 万元")
    print(f"💰 In Billion (亿): {total_main/10000.0:.2f} 亿")

if __name__ == "__main__":
    verify_603198()
