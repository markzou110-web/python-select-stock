# Walkthrough v5.0 - TradingView "Pro" Strategy Sync

I have successfully synchronized the Python stock screener with the TradingView "无门问禅：A股均线粘合战法 (Pro版)" and further enhanced it with advanced multi-factor resonance components.

## 🚀 Version 5.0 Key Enhancements

### 1. High-Fidelity Strategy Sync (TradingView Logic)
The core `check_strategy` has been upgraded to include the full suite of "Pro" components:
- **Weekly Trend Filter**: `EMA(10w) > EMA(30w)` to ensure we are trading in a long-term bull trend.
- **RSI Strength**: `RSI(14) > 55` to filter for stocks with internal momentum.
- **Relative Strength (RS)**: `RS > SMA(RS, 50)` compared against the SSE Index (000001), ensuring the stock is outperforming the broader market.
- **MACD Zero-Line**: `MACD_DIF > 0` condition to ensure the trend has shifted to bullish territory.
- **Squeeze Window Expansion**: The squeeze condition (MA cohesion) now looks back **10 days**, allowing for "breakouts after consolidation."
- **BB Width Squeeze**: Only selects stocks where the Bollinger Bandwidth is in the **lowest 20% quantile** of the past 120 days (extreme volatility contraction).

### 2. Advanced Multi-Factor Analytics
I've added a powerful post-scan analysis layer:
- **Historical Win Rate**: For every stock that passes the filters, the system automatically runs a 1-year historical backtest. It identifies past "squeeze & breakout" signals and calculates the percentage of times the price rose **>3% within 5 days**.
- **Northbound Fund Flow**: Integrated a placeholder for 3-day net flow (🔴/🟢) to visualize smart money movement.
- **Industry Hotspot Analysis**: Automatically maps stocks to their sectors to identify the day's leading industry themes.

### 3. Hyper-Premium UI Updates
- **Sidebar Form**: Added a dedicated "🔬 进阶筛选 (TV Pro 同步)" section with toggles for RSI, Weekly Trend, MACD, and BB Squeeze.
- **Interactive Results**: The results table now includes `Score`, `RSI`, `行业`, `历史胜率`, and `北向`.
- **Performance**: Optimized the execution pipeline to handle weekly data fetching and backtesting efficiently for Top 30 candidates.

## 🔍 Verification & Demonstration

I have verified that:
1.  **Multiple Factors Resonance**: Stocks now only appear if they satisfy technical (Cohesion), momentum (RSI), trend (EMA20/MACD/Weekly), and relative strength conditions simultaneously.
2.  **Backtest Accuracy**: The win rate logic correctly identifies historical signals and assesses future performance.
3.  **UI Integration**: All sidebar parameters correctly propagate to the scanning engine.

> [!TIP]
> Use the **"粘合回溯天数"** to adjust how far back the system looks for the consolidation phase. A setting of 10-15 days is recommended for "Pro" mode.

render_diffs(file:///Users/liangzou/Desktop/AI_Tools/python-select-stock/app.py)
