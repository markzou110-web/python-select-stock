import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from .indicators import calculate_indicators
from .strategy import check_strategy
from .data import safe_ak_call, get_market_snapshot
from .db import load_from_db, save_to_db, get_db_engine

class BacktestEngine:
    def __init__(self, engine=None):
        self.engine = engine or get_db_engine()
        self.trade_log = []
        self.equity_curve = []
        self.initial_capital = 100000.0
        
    def fetch_data(self, code, start_date, end_date):
        """Fetches data from DB or AkShare"""
        # Load slightly earlier data for indicator warmup (250 days)
        warmup_start = (datetime.strptime(start_date, "%Y-%m-%d") - timedelta(days=250)).strftime("%Y%m%d")
        
        # Try local DB first (simplified for now, usually we might need to fetch fresh if range is new)
        df = load_from_db(code, warmup_start, self.engine)
        
        # If local data is insufficient/stale, fetch from online
        if df.empty or df.iloc[-1]['日期'] < end_date:
            print(f"🔄 Fetching fresh backtest data for {code}...")
            df_new = safe_ak_call("stock_zh_a_hist", symbol=code, period="daily", start_date=warmup_start, adjust="qfq")
            if not df_new.empty:
                df = df_new
                save_to_db(df, code, self.engine)
                
        # Ensure standard columns based on DB schema or AkShare
        return df

    def run(self, code, start_date, end_date, params):
        self.trade_log = []
        self.equity_curve = []
        
        df = self.fetch_data(code, start_date, end_date)
        if df.empty or len(df) < 100:
            return {"error": "Insufficient data"}
            
        # 1. Pre-calculate Indicators (Vectorized is fast)
        # We pass standard scanning params for indicator calculation if needed
        df = calculate_indicators(df)
        
        # 2. Slice to requested test period
        # Convert '日期' to datetime for comparison
        df['日期_dt'] = pd.to_datetime(df['日期'])
        test_df = df[(df['日期_dt'] >= pd.to_datetime(start_date)) & (df['日期_dt'] <= pd.to_datetime(end_date))].copy()
        
        if test_df.empty:
            return {"error": "No data in requested range"}

        cash = self.initial_capital
        position = 0 # Shares
        entry_price = 0
        entry_date = None
        
        # Simulation Loop
        # We need historical context for strategy check, so we iterate through original df indices
        # but only act within the test range.
        
        # Find integer index where test starts
        start_idx = test_df.index[0] 
        end_idx = test_df.index[-1]
        
        for i in range(start_idx, end_idx + 1):
            curr_row = df.loc[i]
            current_date_str = curr_row['日期']
            
            # Record Equity Daily
            current_close = curr_row['收盘']
            equity = cash + (position * current_close)
            self.equity_curve.append({
                "date": current_date_str,
                "equity": equity,
                "price": current_close
            })
            
            # Strategy Logic
            # Construct a sub-df for strategy check (up to today)
            # Optimization: check_strategy might be slow in loop. 
            # Ideally strategy check should be vectorized too, but for now we reuse the robust check_strategy.
            
            # We need at least ~60-100 days history for indicators
            if i < 100: continue
            
            # Slice window for strategy (last 120 days is usually enough for indicators)
            # Actually indicators are already calculated globally. check_strategy reads them.
            # We just need to pass a slice ending at i
            window_df = df.loc[i-120:i] 
            
            # Check Signals
            is_buy_signal = False
            
            # To simulate realistic trading:
            # We check strategy based on CLOSE (assuming we run analysis after close), 
            # and buy at NEXT OPEN. Or buy at Close if signal confirmed.
            # Let's assume Buy at Close for simplicity, or Buy Tomorrow Open.
            # "Buy at Close" aligns with the Scanner logic (which scans after 3pm or near close).
            
            match, stats = check_strategy(
                window_df, 
                threshold=params.get('threshold', 0.12),
                vol_multiplier=params.get('vol_multiplier', 1.5),
                rsi_min=params.get('rsi_min', 55),
                use_macd_filter=params.get('use_macd_filter', True),
                use_bb_sqz=params.get('use_bb_sqz', False),
                sqz_lookback=params.get('sqz_lookback', 10),
                use_rs_filter=params.get('use_rs_filter', True)
            )
            
            if match:
                is_buy_signal = True
                
            # --- Execution Logic ---
            
            # SELL Logic
            if position > 0:
                # 1. Stop Loss (Fixed 8%)
                if current_close < entry_price * 0.92:
                    self._sell(current_date_str, current_close, "Stop Loss", cash, position)
                    cash += position * current_close
                    position = 0
                
                # 2. Take Profit (Fixed 20% or if Strategy Invalidates?)
                # Simple Take Profit
                elif current_close > entry_price * 1.20:
                    self._sell(current_date_str, current_close, "Take Profit", cash, position)
                    cash += position * current_close
                    position = 0
                    
                # 3. Time Stop (Optional, e.g. 20 days no move)
                
            # BUY Logic
            elif position == 0 and is_buy_signal:
                # Buy at Close
                # Position Sizing: All In (Simple)
                shares = int(cash / current_close / 100) * 100
                if shares > 0:
                    cost = shares * current_close
                    fee = max(5, cost * 0.0003) # Commission
                    if cash >= cost + fee:
                        cash -= (cost + fee)
                        position = shares
                        entry_price = current_close
                        entry_date = current_date_str
                        self._log_trade("BUY", current_date_str, current_close, shares, fee, "Resonance Signal")

        return {
            "equity_curve": self.equity_curve,
            "trades": self.trade_log,
            "stats": self._calculate_stats()
        }

    def _sell(self, date, price, reason, cash_before, shares):
        amount = shares * price
        fee = max(5, amount * 0.0013) # Tax + Comm
        revenue = amount - fee
        profit = revenue - (self.trade_log[-1]['price'] * shares) # Approx
        pct = (price - self.trade_log[-1]['price']) / self.trade_log[-1]['price']
        
        self.trade_log.append({
            "action": "SELL",
            "date": date,
            "price": price,
            "shares": shares,
            "reason": reason,
            "profit": profit,
            "pct": pct
        })

    def _log_trade(self, action, date, price, shares, fee, reason):
        self.trade_log.append({
            "action": action,
            "date": date,
            "price": price,
            "shares": shares,
            "fee": fee,
            "reason": reason
        })

    def _calculate_stats(self):
        if not self.trade_log: return {}
        
        trades = pd.DataFrame(self.trade_log)
        sells = trades[trades['action'] == 'SELL']
        
        if sells.empty: return {"total_return": 0}
        
        wins = sells[sells['profit'] > 0]
        win_rate = len(wins) / len(sells) * 100
        
        total_return = (self.equity_curve[-1]['equity'] - self.initial_capital) / self.initial_capital * 100
        
        # Max Drawdown
        equity_series = pd.Series([e['equity'] for e in self.equity_curve])
        rolling_max = equity_series.cummax()
        drawdown = (equity_series - rolling_max) / rolling_max
        max_dd = drawdown.min() * 100
        
        return {
            "total_return_pct": round(total_return, 2),
            "win_rate": round(win_rate, 1),
            "max_drawdown": round(max_dd, 2),
            "total_trades": len(sells),
            "avg_profit_pct": round(sells['pct'].mean() * 100, 2)
        }
