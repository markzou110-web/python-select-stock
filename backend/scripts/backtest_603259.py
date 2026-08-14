"""药明康德(603259)案例对比：纯B/S信号买卖 vs 当前风控策略

用系统真实的 strategy.py 算法扫描全部历史K线，提取：
  - B买点：squeeze均线突破 或 TV-ZP long 信号
  - S卖点：TV-ZP short 或 均线死叉(EMA5<EMA20)
然后逐笔对比"信号买卖"与"-9%止损/+8%止盈/10天"两种退出。
"""
import os
import sys
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from core.strategy import (
    _find_squeeze_signal_indices,
    _find_tv_zp_signal_indices,
    _tv_zp_range_filter_default,
)

STOP_LOSS = -0.09
TAKE_PROFIT = 0.08
MAX_HOLD = 10       # 当前系统的10天时间止损
MAX_HOLD_SIGNAL = 30  # 信号卖出模式允许更长持有期（让S卖点有机会触发）
LIMIT_UP_SKIP = 0.095


def load_k() -> pd.DataFrame:
    df = pd.read_csv("/tmp/backtest/603259.csv", parse_dates=["date"])
    df["date"] = df["date"].dt.date
    return df.sort_values("date").reset_index(drop=True)


def simulate_risk_exit(k: pd.DataFrame, entry_idx: int) -> Tuple[float, int, str]:
    """当前系统风控退出：-9%止损 / +8%止盈 / 10天时间止损。次日开盘入场。"""
    ei = entry_idx + 1
    if ei >= len(k):
        return 0.0, 0, "no_data"
    entry = k.loc[ei, "open"]
    prev_c = k.loc[ei - 1, "close"]
    if prev_c > 0 and (entry - prev_c) / prev_c >= LIMIT_UP_SKIP:
        return 0.0, 0, "limit_up_skip"
    if entry <= 0:
        return 0.0, 0, "invalid"
    for day in range(1, MAX_HOLD + 1):
        fi = ei + day
        if fi >= len(k):
            last = len(k) - 1
            return (k.loc[last, "close"] - entry) / entry, day - 1, "data_end"
        dl = k.loc[fi, "low"]
        dh = k.loc[fi, "high"]
        if (dl - entry) / entry <= STOP_LOSS:
            return STOP_LOSS, day, "止损-9%"
        if (dh - entry) / entry >= TAKE_PROFIT:
            return TAKE_PROFIT, day, "止盈+8%"
    last = min(ei + MAX_HOLD, len(k) - 1)
    return (k.loc[last, "close"] - entry) / entry, MAX_HOLD, "10天到期"


def find_signal_exit(k: pd.DataFrame, entry_idx: int, sell_dates: List) -> Tuple[float, int, str]:
    """纯信号卖出：持有到出现S卖点 或 最长30天到期。"""
    ei = entry_idx + 1
    if ei >= len(k):
        return 0.0, 0, "no_data"
    entry = k.loc[ei, "open"]
    prev_c = k.loc[ei - 1, "close"]
    if prev_c > 0 and (entry - prev_c) / prev_c >= LIMIT_UP_SKIP:
        return 0.0, 0, "limit_up_skip"
    sell_set = set(sell_dates)
    for day in range(1, MAX_HOLD_SIGNAL + 1):
        fi = ei + day
        if fi >= len(k):
            last = len(k) - 1
            return (k.loc[last, "close"] - entry) / entry, day - 1, "数据结束"
        d = k.loc[fi, "date"]
        if d in sell_set:
            ret = (k.loc[fi, "close"] - entry) / entry
            return ret, day, "S卖点卖出"
    last = min(ei + MAX_HOLD_SIGNAL, len(k) - 1)
    return (k.loc[last, "close"] - entry) / entry, MAX_HOLD_SIGNAL, f"{MAX_HOLD_SIGNAL}天到期(无卖点)"


def main():
    print("=" * 75)
    print("药明康德(603259) 案例对比：纯B/S信号买卖 vs 当前风控策略")
    print("=" * 75)

    k = load_k()
    print(f"\nK线: {k['date'].min()} ~ {k['date'].max()}, 共 {len(k)} 天")

    # ── 用系统真实算法提取 B/S 信号 ──
    # 构造 strategy.py 需要的 DataFrame 格式（中文列名）
    sdf = pd.DataFrame({
        "日期": k["date"], "开盘": k["open"], "最高": k["high"],
        "最低": k["low"], "收盘": k["close"], "成交量": k["vol"],
    })

    # 1. squeeze 买点（均线B共振）
    try:
        ma_buy_idx = _find_squeeze_signal_indices(sdf, threshold=0.12, vol_multiplier=1.5, rsi_min=55)
    except Exception as e:
        print(f"squeeze信号计算失败: {e}")
        ma_buy_idx = []
    # 2. TV-ZP long/short 信号
    try:
        zp_long, zp_short, _ = _find_tv_zp_signal_indices(sdf)
    except Exception as e:
        print(f"TV-ZP信号计算失败: {e}")
        zp_long, zp_short = [], []

    print(f"\n=== 系统算法扫描出的信号 ===")
    print(f"  squeeze均线买点(B): {len(ma_buy_idx)} 个")
    print(f"  TV-ZP long买点(B): {len(zp_long)} 个")
    print(f"  TV-ZP short卖点(S): {len(zp_short)} 个")

    # 合并买点（去重，取并集）
    all_buy = sorted(set(ma_buy_idx) | set(zp_long))
    print(f"  合并后买点(B): {len(all_buy)} 个")

    # 卖点 = TV-ZP short
    sell_idx = sorted(zp_short)
    sell_dates = [k.loc[i, "date"] for i in sell_idx if i < len(k)]

    # 打印最近1年的信号明细
    print(f"\n=== 最近12个月的所有B/S信号明细 ===")
    recent = k["date"].max()
    from datetime import date, timedelta
    cutoff = recent - timedelta(days=365)
    for i in all_buy:
        if i < len(k) and k.loc[i, "date"] >= cutoff:
            print(f"  B买点: {k.loc[i,'date']} 收盘价 {k.loc[i,'close']:.2f}")
    for i in sell_idx:
        if i < len(k) and k.loc[i, "date"] >= cutoff:
            print(f"  S卖点: {k.loc[i,'date']} 收盘价 {k.loc[i,'close']:.2f}")

    # ── 逐笔回测 ──
    print(f"\n{'='*75}")
    print(f"逐笔对比（次日开盘入场）")
    print(f"{'='*75}")
    print(f"\n{'信号日':<12} {'入场价':>7} {'持有天数':>8} │ {'信号卖出':>20} │ {'风控退出':>20} │ 差异%")
    print(f"{'─'*12} {'─'*7} {'─'*8} │ {'─'*20} │ {'─'*20} │ {'─'*7}")

    signal_trades = []
    risk_trades = []
    for idx in all_buy:
        if idx >= len(k) - 2:
            continue
        sig_ret, sig_days, sig_reason = find_signal_exit(k, idx, sell_dates)
        risk_ret, risk_days, risk_reason = simulate_risk_exit(k, idx)
        if sig_reason in ("no_data", "limit_up_skip", "invalid"):
            continue
        sig_date = k.loc[idx, "date"]
        entry = k.loc[idx + 1, "open"] if idx + 1 < len(k) else k.loc[idx, "close"]
        diff = (sig_ret - risk_ret) * 100
        sign = "+" if diff >= 0 else ""
        print(f"{str(sig_date):<12} {entry:>7.2f} {sig_days:>6}天 │ "
              f"{sig_ret*100:>7.2f}% ({sig_reason:<10}) │ "
              f"{risk_ret*100:>7.2f}% ({risk_reason:<10}) │ {sign}{diff:.2f}%")
        signal_trades.append(sig_ret * 100)
        risk_trades.append(risk_ret * 100)

    # ── 汇总对比 ──
    print(f"\n{'='*75}")
    print(f"汇总对比（{len(signal_trades)} 笔交易）")
    print(f"{'='*75}")
    st = pd.Series(signal_trades)
    rt = pd.Series(risk_trades)

    def stats(s, label):
        wins = s[s > 0]
        losses = s[s < 0]
        aw = wins.mean() if len(wins) else 0
        al = abs(losses.mean()) if len(losses) else 0
        pf = (len(wins) * aw) / (len(losses) * al) if len(losses) and al else None
        # 等权累计净值（单笔满仓，复利）
        nav = (1 + s / 100).prod()
        print(f"  {label}:")
        print(f"    均收益 {s.mean():+.2f}% | 中位 {s.median():+.2f}% | 胜率 {(s>0).mean()*100:.1f}% | "
              f"PF {pf or 0:.2f} | 最大盈 {s.max():+.1f}% | 最大亏 {s.min():+.1f}% | 累计净值 {(nav-1)*100:+.1f}%")

    print()
    stats(st, "📈 纯B/S信号买卖（图上卖点卖出）")
    stats(rt, "🛡️ 当前风控策略（-9%止损/+8%止盈/10天）")

    diff_nav = ((1 + st / 100).prod() - (1 + rt / 100).prod()) * 100
    print(f"\n  💡 净值差异: 信号买卖 vs 风控 = {diff_nav:+.1f}%（正=信号更优）")
    print(f"  💡 均收益差异: {st.mean() - rt.mean():+.2f}%/笔")


if __name__ == "__main__":
    main()
