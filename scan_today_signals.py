
import sys
import os
import pandas as pd
from datetime import datetime, timedelta

# Ensure backend modules can be imported
sys.path.append(os.path.join(os.getcwd(), 'backend'))

from core.strategy import check_strategy, check_range_filter_strategy
from core.db import get_db_engine, load_from_db
from core.indicators import calculate_indicators

def scan_today_signals():
    engine = get_db_engine()
    print("📡 Querying all available stock codes in DB...")
    with engine.connect() as conn:
        from sqlalchemy import text
        res = conn.execute(text("SELECT DISTINCT code FROM daily_k LIMIT 500")).fetchall()
        codes = [r[0] for r in res]
    
    print(f"📊 Total stocks to check: {len(codes)}")
    
    resonance_signals = []
    range_filter_signals = []
    
    target_date = "2026-01-15"
    
    for code in codes:
        try:
            # Load enough history for indicators
            df = load_from_db(code, "2025-01-01", engine)
            if df.empty or len(df) < 120:
                continue
                
            # Check for today's existence
            if str(df.iloc[-1]['日期']) != target_date:
                continue
            
            df = calculate_indicators(df)
            
            # 1. Range Filter (Already crossover logic)
            rf_match, rf_stats = check_range_filter_strategy(df)
            if rf_match:
                range_filter_signals.append((code, rf_stats['名称'], rf_stats['Score']))
            
            # 2. Resonance (Check for breakout TODAY only)
            ma_values = [df['EMA5'], df['EMA10'], df['EMA20'], df['EMA60']]
            ma_df = pd.concat(ma_values, axis=1)
            ma_max_all = ma_df.max(axis=1)
            
            curr = df.iloc[-1]
            prev = df.iloc[-2]
            
            is_breakout = (curr['收盘'] > ma_max_all.iloc[-1]) and (curr['收盘'] > curr['开盘'])
            was_breakout = (prev['收盘'] > ma_max_all.iloc[-2])
            
            if is_breakout and not was_breakout:
                res_match, res_stats = check_strategy(df)
                if res_match:
                    resonance_signals.append((code, res_stats['名称'], res_stats['Score']))
                    
        except Exception as e:
            continue

    print("\n🚀 --- TODAY'S BUY SIGNALS (买点) ---")
    
    print("\n🎯 Resonance Breakouts (Today's first breakout):")
    for s in sorted(resonance_signals, key=lambda x: x[2], reverse=True)[:10]:
        print(f"   [{s[0]}] {s[1]} - Score: {s[2]}")
        
    print("\n🎢 Range Filter Crossovers:")
    for s in sorted(range_filter_signals, key=lambda x: x[2], reverse=True)[:10]:
        print(f"   [{s[0]}] {s[1]} - Score: {s[2]}")

if __name__ == "__main__":
    scan_today_signals()
