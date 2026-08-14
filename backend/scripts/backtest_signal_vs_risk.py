"""纯信号买卖 vs 固定风控止损 对比回测

对比 4 种退出策略的盈利能力（买入侧统一用历史已验证的信号日入场）：
  A. 固定风控止损（当前系统）：-9%止损 + +8%止盈 + 10天时间止损
  B. 信号反转卖出：TV-ZP short 或 均线破位(收盘<EMA5且<EMA20) 触发平仓
  C. 纯均线策略：买入用 squeeze 信号，卖出用 EMA5<EMA20 死叉
  D. 信号反转 + 宽止损保护（-9%兜底 + 信号反转）

退出 B/D 是用户问的"图上卖点卖出"；A 是当前系统；C 是纯均线策略。
"""
import os
import sys
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DATA_DIR = "/tmp/backtest"
STOP_LOSS = -0.09       # 当前系统 FIXED_STOP_LOSS_PCT
TAKE_PROFIT = 0.08      # 当前系统 FIRST_PROFIT_TAKE_PCT (分批首笔)
MAX_HOLD = 10           # 当前系统 BACKTEST_MAX_HOLD_DAYS
LIMIT_UP_SKIP = 0.095   # 涨停跳过（次日开盘>=+9.5%则买不到）


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def compute_indicators(k: pd.DataFrame) -> pd.DataFrame:
    """为单只股票计算回测所需指标（EMA5/10/20/60、ATR、Vol_MA20）。"""
    k = k.sort_values("date").reset_index(drop=True).copy()
    c = k["close"]
    k["EMA5"] = ema(c, 5)
    k["EMA10"] = ema(c, 10)
    k["EMA20"] = ema(c, 20)
    k["EMA60"] = ema(c, 60)
    k["Vol_MA20"] = k["vol"].rolling(20).mean()
    # ATR（Wilder）
    h, l, pc = k["high"], k["low"], c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    k["ATR"] = tr.ewm(alpha=1 / 14, adjust=False).mean()
    return k


def simulate_exit(ind: pd.DataFrame, entry_idx: int, mode: str) -> Tuple[float, int, str]:
    """从 entry_idx（次日开盘入场日）开始模拟退出，返回 (收益率, 持有天数, 退出原因)。

    mode:
      'risk'      = 固定风控（-9%止损/+8%止盈/10天）
      'signal'    = 信号反转（EMA5<EMA20收盘 或 收盘<EMA5且<EMA20）
      'signal_risk' = 信号反转 + -9%兜底
    """
    if entry_idx >= len(ind) - 1:
        return 0.0, 0, "no_data"
    entry_price = ind.loc[entry_idx, "open"] if "open" in ind.columns else ind.loc[entry_idx, "close"]
    prev_close = ind.loc[entry_idx - 1, "close"] if entry_idx > 0 else entry_price
    # 涨停跳过
    if prev_close > 0 and (entry_price - prev_close) / prev_close >= LIMIT_UP_SKIP:
        return 0.0, 0, "limit_up_skip"
    if entry_price <= 0:
        return 0.0, 0, "invalid_price"

    max_close = entry_price
    for day in range(1, MAX_HOLD + 1):
        fi = entry_idx + day
        if fi >= len(ind):
            # 数据结束，按最后收盘结算
            last = len(ind) - 1
            ep = ind.loc[last, "close"]
            return (ep - entry_price) / entry_price, day - 1, "data_end"

        dc = ind.loc[fi, "close"]
        dl = ind.loc[fi, "low"]
        dh = ind.loc[fi, "high"]
        max_close = max(max_close, dc)
        day_low_ret = (dl - entry_price) / entry_price
        day_high_ret = (dh - entry_price) / entry_price

        # 1. 风控止损（risk 和 signal_risk 都有）
        if mode in ("risk", "signal_risk") and day_low_ret <= STOP_LOSS:
            return STOP_LOSS, day, "stop_loss"
        # 2. 固定止盈（仅 risk 模式）
        if mode == "risk" and day_high_ret >= TAKE_PROFIT:
            return TAKE_PROFIT, day, "take_profit"
        # 3. 信号反转退出（signal 和 signal_risk）
        if mode in ("signal", "signal_risk"):
            ema5, ema20 = ind.loc[fi, "EMA5"], ind.loc[fi, "EMA20"]
            # 卖点1：收盘跌破 EMA5 且 EMA5<EMA20（双均线破位）
            ma_break = dc < ema5 and ema5 < ema20
            # 卖点2：EMA5 死叉 EMA20（短期均线下穿中期均线）
            if fi > 0:
                prev_e5, prev_e20 = ind.loc[fi - 1, "EMA5"], ind.loc[fi - 1, "EMA20"]
                death_cross = prev_e5 >= prev_e20 and ema5 < ema20
            else:
                death_cross = False
            if ma_break or death_cross:
                ret = (dc - entry_price) / entry_price
                return ret, day, "signal_exit"

    # 时间止损：10天到期按收盘结算
    last_idx = min(entry_idx + MAX_HOLD, len(ind) - 1)
    ep = ind.loc[last_idx, "close"]
    return (ep - entry_price) / entry_price, MAX_HOLD, "time_stop"


def backtest_one_stock(k: pd.DataFrame, signal_dates: List, mode: str) -> List[Dict]:
    """对一只股票的所有信号日回测。"""
    if k.empty or len(k) < 70:
        return []
    ind = compute_indicators(k)
    results = []
    for sd in signal_dates:
        # 信号日当天的 idx
        match = ind[ind["date"] == sd]
        if match.empty:
            continue
        sig_idx = match.index[0]
        # 次日开盘入场
        entry_idx = sig_idx + 1
        if entry_idx >= len(ind):
            continue
        ret, days, reason = simulate_exit(ind, entry_idx, mode)
        if reason in ("no_data", "invalid_price", "limit_up_skip"):
            continue
        results.append({
            "signal_date": sd,
            "entry_price": ind.loc[entry_idx, "open"] if "open" in ind.columns else ind.loc[entry_idx, "close"],
            "return": round(ret * 100, 2),
            "hold_days": days,
            "exit_reason": reason,
        })
    return results


def summarize(results: List[Dict], label: str) -> Dict:
    if not results:
        return {"label": label, "n": 0}
    df = pd.DataFrame(results)
    wins = df[df["return"] > 0]
    losses = df[df["return"] < 0]
    avg_win = wins["return"].mean() if len(wins) else 0
    avg_loss = abs(losses["return"].mean()) if len(losses) else 0
    pf = (len(wins) * avg_win) / (len(losses) * avg_loss) if len(losses) and avg_loss else None
    # 累计净值（等权）
    equity = (1 + df["return"] / 100).prod()
    return {
        "label": label,
        "n": len(df),
        "avg_return": round(df["return"].mean(), 2),
        "median_return": round(df["return"].median(), 2),
        "winrate": round(len(wins) / len(df) * 100, 1),
        "avg_hold": round(df["hold_days"].mean(), 1),
        "pf": round(pf, 2) if pf else None,
        "max_win": round(df["return"].max(), 1),
        "max_loss": round(df["return"].min(), 1),
        "equity": round((equity - 1) * 100, 2),  # 累计收益率
        "exit_dist": df["exit_reason"].value_counts().to_dict(),
    }


def find_squeeze_buy_points(ind: pd.DataFrame) -> List[int]:
    """在单只股票的指标df上找 squeeze 买点索引（收盘>四均线max 且 放量阳线）。"""
    if len(ind) < 120:
        return []
    points = []
    emas = ind[["EMA5", "EMA10", "EMA20", "EMA60"]].values
    c = ind["close"].values
    v = ind["vol"].values
    vol_ma = ind["Vol_MA20"].values
    for i in range(119, len(ind) - MAX_HOLD - 2):
        if c[i] <= 0 or vol_ma[i] <= 0 or np.isnan(emas[i]).any():
            continue
        ema_max = np.nanmax(emas[i])
        if c[i] > ema_max and c[i] > emas[i][0] and v[i] / vol_ma[i] > 1.5:
            points.append(i)
    return points


def main():
    print("=" * 75)
    print("纯信号买卖 vs 固定风控止损 公平对比回测")
    print("公平性：4种退出策略都跑在【同一批 squeeze 买点】上，唯一变量是退出方式")
    print("=" * 75)

    k_all = pd.read_csv(f"{DATA_DIR}/daily_k.csv", parse_dates=["date"])
    k_all["date"] = k_all["date"].dt.date
    print(f"\ndaily_k: {len(k_all)} 行, {k_all['code'].nunique()} 只股票, "
          f"{k_all['date'].min()} ~ {k_all['date'].max()}")

    by_code = {str(code): g.sort_values("date").reset_index(drop=True)
               for code, g in k_all.groupby("code")}

    # 统一扫描所有股票的 squeeze 买点
    print("\n扫描 squeeze 买点（所有股票）...")
    buy_points = []  # [(code, idx)]
    for code, k in by_code.items():
        if len(k) < 120:
            continue
        ind = compute_indicators(k)
        for idx in find_squeeze_buy_points(ind):
            buy_points.append((code, idx))
    print(f"  共找到 {len(buy_points)} 个 squeeze 买点")

    if not buy_points:
        print("无买点，退出")
        return

    # 在同一批买点上跑4种退出策略
    modes = {
        "risk": "A. 固定风控止损(-9%/+8%/10天)【当前系统】",
        "signal": "B. 纯信号反转卖出(均线破位/死叉)",
        "signal_risk": "C. 信号反转 + -9%兜底",
        "ma_cross": "D. 纯均线(EMA5<EMA20死叉即卖)",
    }

    all_results = {m: [] for m in modes}
    for code, idx in buy_points:
        k = by_code[code]
        ind = compute_indicators(k)
        entry_idx = idx + 1
        if entry_idx >= len(ind):
            continue
        for mode in modes:
            ret, days, reason = simulate_exit(ind, entry_idx, mode)
            if reason not in ("no_data", "invalid_price", "limit_up_skip"):
                all_results[mode].append({
                    "code": code, "signal_date": ind.loc[idx, "date"],
                    "return": round(ret * 100, 2), "hold_days": days, "exit_reason": reason,
                })

    summaries = [summarize(all_results[m], label) for m, label in modes.items()]

    # 打印对比表
    print("\n" + "=" * 75)
    print(f"对比结果（{len(buy_points)} 个 squeeze 买点，次日开盘入场）")
    print("=" * 75)
    print(f"\n{'退出策略':<44} {'样本':>6} {'均收益%':>8} {'中位%':>7} {'胜率%':>6} {'PF':>5} "
          f"{'最大盈':>7} {'最大亏':>7} {'平均持有':>7}")
    for s in summaries:
        if s["n"] == 0:
            print(f"{s['label']:<44} {0:>6} (无样本)")
            continue
        print(f"{s['label']:<44} {s['n']:>6} {s['avg_return']:>8} {s['median_return']:>7} "
              f"{s['winrate']:>6} {str(s.get('pf') or '-'):>5} {s['max_win']:>7} {s['max_loss']:>7} "
              f"{s['avg_hold']:>7}")

    print("\n--- 退出原因分布 ---")
    for s in summaries:
        if s["n"] > 0:
            dist = ", ".join(f"{k}:{v}" for k, v in s["exit_dist"].items())
            print(f"  {s['label'][:35]}: {dist}")

    # 分年份对比（看市场环境影响）
    print("\n" + "=" * 75)
    print("分年份对比（均收益% / 胜率%）")
    print("=" * 75)
    for m in modes:
        df = pd.DataFrame(all_results[m])
        if df.empty:
            continue
        df["year"] = pd.to_datetime(df["signal_date"]).dt.year
        yearly = df.groupby("year").agg(
            n=("return", "count"),
            avg=("return", "mean"),
            wr=("return", lambda x: (x > 0).mean() * 100),
        ).round(1)
        print(f"\n  {modes[m]}")
        print(yearly.to_string().replace("\n", "\n    "))

    # 关键结论
    print("\n" + "=" * 75)
    print("关键结论（固定风控 vs 纯信号卖出）")
    print("=" * 75)
    risk_s = summaries[0]
    signal_s = summaries[1]
    if risk_s["n"] > 0 and signal_s["n"] > 0:
        diff = signal_s["avg_return"] - risk_s["avg_return"]
        better = "优于" if diff > 0 else "劣于"
        print(f"  均收益: 纯信号 {signal_s['avg_return']}% vs 固定风控 {risk_s['avg_return']}%（{better} {diff:+.2f}%）")
        print(f"  胜率: {signal_s['winrate']}% vs {risk_s['winrate']}%")
        print(f"  PF: {signal_s.get('pf')} vs {risk_s.get('pf')}")
        print(f"  最大亏损: {signal_s['max_loss']}% vs {risk_s['max_loss']}%（风控的优势）")
        print(f"  平均持有: {signal_s['avg_hold']}天 vs {risk_s['avg_hold']}天")



if __name__ == "__main__":
    main()
