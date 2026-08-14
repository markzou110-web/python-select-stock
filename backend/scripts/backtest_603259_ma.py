"""药明康德(603259) 案例：EMA均线交叉B/S信号 vs 当前风控 公平对比

公平性：两种策略都在【同一批 EMA金叉买点】上入场，唯一变量是退出方式。
"""
import pandas as pd
from datetime import timedelta

STOP_LOSS = -0.09
TAKE_PROFIT = 0.08
MAX_HOLD = 10
LIMIT_UP_SKIP = 0.095


def load_k():
    df = pd.read_csv("/tmp/backtest/603259.csv", parse_dates=["date"])
    df["date"] = df["date"].dt.date
    c = df["close"]
    df["EMA5"] = c.ewm(span=5, adjust=False).mean()
    df["EMA20"] = c.ewm(span=20, adjust=False).mean()
    return df.sort_values("date").reset_index(drop=True)


def find_ma_signals(df):
    """EMA5/EMA20 金叉买、死叉卖。"""
    df["prev_e5"] = df["EMA5"].shift(1)
    df["prev_e20"] = df["EMA20"].shift(1)
    df["buy"] = (df["prev_e5"] <= df["prev_e20"]) & (df["EMA5"] > df["EMA20"])
    df["sell"] = (df["prev_e5"] >= df["prev_e20"]) & (df["EMA5"] < df["EMA20"])
    buys = df[df["buy"]].index.tolist()
    sells = df[df["sell"]].index.tolist()
    return buys, sells


def sim_signal_exit(df, buy_idx, sells):
    """纯信号卖出：次日开盘入场，持有到下一个死叉卖点。无上限持有期。"""
    ei = buy_idx + 1
    if ei >= len(df):
        return None
    entry = df.loc[ei, "open"]
    prev_c = df.loc[ei - 1, "close"]
    if prev_c > 0 and (entry - prev_c) / prev_c >= LIMIT_UP_SKIP:
        return None
    for si in sells:
        if si > ei:
            ret = (df.loc[si, "close"] - entry) / entry
            return {"entry": entry, "exit": df.loc[si, "close"], "ret": ret * 100,
                    "days": si - ei, "reason": "S卖点(死叉)", "exit_date": df.loc[si, "date"]}
    # 无卖点，持有至最新
    last = len(df) - 1
    ret = (df.loc[last, "close"] - entry) / entry
    return {"entry": entry, "exit": df.loc[last, "close"], "ret": ret * 100,
            "days": last - ei, "reason": "持有至最新", "exit_date": df.loc[last, "date"]}


def sim_risk_exit(df, buy_idx):
    """当前风控：次日开盘入场，-9%止损/+8%止盈/10天时间止损。"""
    ei = buy_idx + 1
    if ei >= len(df):
        return None
    entry = df.loc[ei, "open"]
    prev_c = df.loc[ei - 1, "close"]
    if prev_c > 0 and (entry - prev_c) / prev_c >= LIMIT_UP_SKIP:
        return None
    for day in range(1, MAX_HOLD + 1):
        fi = ei + day
        if fi >= len(df):
            last = len(df) - 1
            return {"entry": entry, "exit": df.loc[last, "close"],
                    "ret": (df.loc[last, "close"] - entry) / entry * 100,
                    "days": last - ei, "reason": "数据结束", "exit_date": df.loc[last, "date"]}
        dl, dh = df.loc[fi, "low"], df.loc[fi, "high"]
        if (dl - entry) / entry <= STOP_LOSS:
            return {"entry": entry, "exit": entry * (1 + STOP_LOSS), "ret": STOP_LOSS * 100,
                    "days": day, "reason": "止损-9%", "exit_date": df.loc[fi, "date"]}
        if (dh - entry) / entry >= TAKE_PROFIT:
            return {"entry": entry, "exit": entry * (1 + TAKE_PROFIT), "ret": TAKE_PROFIT * 100,
                    "days": day, "reason": "止盈+8%", "exit_date": df.loc[fi, "date"]}
    last = min(ei + MAX_HOLD, len(df) - 1)
    return {"entry": entry, "exit": df.loc[last, "close"],
            "ret": (df.loc[last, "close"] - entry) / entry * 100,
            "days": MAX_HOLD, "reason": "10天到期", "exit_date": df.loc[last, "date"]}


def main():
    df = load_k()
    buys, sells = find_ma_signals(df)
    print("=" * 80)
    print("药明康德(603259) 案例：EMA5/EMA20 均线交叉B/S信号 vs 当前风控策略")
    print("=" * 80)
    print(f"K线: {df['date'].min()} ~ {df['date'].max()}, 共 {len(df)} 天")
    print(f"金叉买点(B): {len(buys)} 个 | 死叉卖点(S): {len(sells)} 个")

    # 逐笔对比
    trades = []
    for bi in buys:
        sig = sim_signal_exit(df, bi, sells)
        risk = sim_risk_exit(df, bi)
        if sig is None or risk is None:
            continue
        trades.append({
            "buy_date": df.loc[bi, "date"], "entry": sig["entry"],
            "sig": sig, "risk": risk,
        })

    print(f"\n{'='*80}")
    print(f"逐笔对比（{len(trades)} 笔交易，次日开盘入场）")
    print(f"{'='*80}")
    print(f"\n{'买入日':<12}{'入场价':>7} │ {'── 纯信号卖出 ──':<32} │ {'── 当前风控 ──':<32} │ 差异")
    print(f"{'─'*12}{'─'*7} │ {'─'*32} │ {'─'*32} │ {'─'*7}")
    for t in trades:
        s, r = t["sig"], t["risk"]
        diff = s["ret"] - r["ret"]
        sign = "+" if diff >= 0 else ""
        print(f"{str(t['buy_date']):<12}{t['entry']:>7.2f} │ "
              f"{s['ret']:>+7.2f}% 持{s['days']:>3}天 {s['reason']:<11} │ "
              f"{r['ret']:>+7.2f}% 持{r['days']:>2}天 {r['reason']:<11} │ {sign}{diff:.2f}%")

    # 汇总
    sig_rets = pd.Series([t["sig"]["ret"] for t in trades])
    risk_rets = pd.Series([t["risk"]["ret"] for t in trades])
    sig_days = pd.Series([t["sig"]["days"] for t in trades])
    risk_days = pd.Series([t["risk"]["days"] for t in trades])

    def stat(s, d, label):
        wins = s[s > 0]
        losses = s[s < 0]
        aw = wins.mean() if len(wins) else 0
        al = abs(losses.mean()) if len(losses) else 0
        pf = (len(wins) * aw) / (len(losses) * al) if len(losses) and al else float("inf")
        nav = (1 + s / 100).prod()
        print(f"  {label}:")
        print(f"    均收益 {s.mean():+.2f}% | 中位 {s.median():+.2f}% | 胜率 {(s>0).mean()*100:.0f}% "
              f"({len(wins)}胜{len(losses)}亏) | PF {pf:.2f}")
        print(f"    最大盈 {s.max():+.1f}% | 最大亏 {s.min():+.1f}% | 平均持有 {d.mean():.0f}天 | "
              f"累计净值 {(nav-1)*100:+.1f}%（满仓复利）")

    print(f"\n{'='*80}")
    print(f"汇总对比（{len(trades)} 笔）")
    print(f"{'='*80}\n")
    stat(sig_rets, sig_days, "📈 纯B/S信号买卖（金叉买、死叉卖，持有到卖点）")
    print()
    stat(risk_rets, risk_days, "🛡️ 当前风控策略（-9%止损/+8%止盈/10天）")

    sig_nav = (1 + sig_rets / 100).prod()
    risk_nav = (1 + risk_rets / 100).prod()
    print(f"\n{'='*80}")
    print(f"核心结论")
    print(f"{'='*80}")
    print(f"  累计净值: 信号买卖 {(sig_nav-1)*100:+.1f}% vs 风控 {(risk_nav-1)*100:+.1f}%")
    better = "信号买卖" if sig_nav > risk_nav else "当前风控"
    print(f"  ➤ 胜出者: {better}（净值高 {(abs(sig_nav-risk_nav))*100:.1f}%）")
    print(f"  均收益: 信号 {sig_rets.mean():+.2f}%/笔 vs 风控 {risk_rets.mean():+.2f}%/笔")
    print(f"  胜率: 信号 {(sig_rets>0).mean()*100:.0f}% vs 风控 {(risk_rets>0).mean()*100:.0f}%")
    print(f"  最大单笔亏损: 信号 {sig_rets.min():+.1f}% vs 风控 {risk_rets.min():+.1f}%")
    # 信号策略靠哪几笔赚钱
    big_wins = sig_rets[sig_rets > 15]
    if len(big_wins):
        print(f"\n  ⚠️ 信号买卖的超额收益主要靠 {len(big_wins)} 笔大牛股(>15%)贡献:")
        for t in trades:
            if t["sig"]["ret"] > 15:
                print(f"     {t['buy_date']}: +{t['sig']['ret']:.1f}% (持{t['sig']['days']}天)")


if __name__ == "__main__":
    main()
