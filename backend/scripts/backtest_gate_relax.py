"""门禁放宽回测：对比新旧 A 级门禁选出股票的真实未来收益。

回测逻辑：
  - 从 scan_history 取历史候选，按新旧门禁分别判定 A 级
  - 用 daily_k 的实际交易日序列，计算信号日收盘入场后 1/3/5/10 交易日的收益
  - 对照组：旧门禁A级 / 新门禁升级A级 / 所有B级（基准）

运行：cd backend && source venv_new/bin/activate && python scripts/backtest_gate_relax.py
"""
import os
import sys
from typing import Dict, Optional

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 复用新门禁的阈值常量（与 scanner.py 保持一致）
from core.risk_constants import (
    SOP_A_GRADE_HARD_MAX_5D_GAIN_PCT,
    SOP_A_GRADE_MAX_5D_GAIN_PCT,
    SOP_A_GRADE_5D_PENALTY_PER_PCT,
    SOP_A_GRADE_MIN_PRICE_ACTION_SCORE,
    SOP_A_GRADE_MIN_SCORE,
    SOP_A_GRADE_STRATEGIES,
)

HORIZONS = (1, 3, 5, 10)  # 交易日


def _is_new_a_grade(row) -> bool:
    """新门禁下的 A 级判定（与 scanner.py 一致）。"""
    strategy = str(row.get("strategy_type") or "")
    if strategy not in SOP_A_GRADE_STRATEGIES:
        return False
    pct_5d = float(row.get("pct_5d") or 0)
    pa = float(row.get("price_action_score") or 0)
    if pa < SOP_A_GRADE_MIN_PRICE_ACTION_SCORE:
        return False
    if pct_5d > SOP_A_GRADE_HARD_MAX_5D_GAIN_PCT:
        return False
    q = float(row.get("sop_quality_score") or 0)
    if pct_5d > SOP_A_GRADE_MAX_5D_GAIN_PCT:
        over = min(pct_5d - SOP_A_GRADE_MAX_5D_GAIN_PCT,
                   SOP_A_GRADE_HARD_MAX_5D_GAIN_PCT - SOP_A_GRADE_MAX_5D_GAIN_PCT)
        q -= over * SOP_A_GRADE_5D_PENALTY_PER_PCT
    return q >= SOP_A_GRADE_MIN_SCORE


def _is_old_a_grade(row) -> bool:
    """旧门禁：仅 tv_dual_strict + 5d<=10 硬否决 + 无递减扣分。"""
    strategy = str(row.get("strategy_type") or "")
    if strategy != "tv_dual_strict":
        return False
    pct_5d = float(row.get("pct_5d") or 0)
    if pct_5d > SOP_A_GRADE_MAX_5D_GAIN_PCT:
        return False
    pa = float(row.get("price_action_score") or 0)
    if pa < SOP_A_GRADE_MIN_PRICE_ACTION_SCORE:
        return False
    q = float(row.get("sop_quality_score") or 0)
    return q >= SOP_A_GRADE_MIN_SCORE


def load_candidates(data_dir: str = "/tmp/backtest") -> pd.DataFrame:
    """从 CSV 加载所有历史候选（聚焦 B 级 + A 级，含 5d 涨幅）。"""
    df = pd.read_csv(f"{data_dir}/candidates.csv", parse_dates=["data_date"])
    df["data_date"] = df["data_date"].dt.date
    df["pct_5d"] = df["pct_5d"].fillna(0)  # 缺失视为0（不追高），保守
    return df


def build_future_returns(data_dir: str = "/tmp/backtest", candidates: pd.DataFrame = None) -> pd.DataFrame:
    """为每只候选票计算入场后 N 交易日的收益率。

    用 daily_k 的实际交易日序列，避免日历日错误。
    """
    k = pd.read_csv(f"{data_dir}/daily_k.csv", parse_dates=["date"])
    k["date"] = k["date"].dt.date

    # 为每只票建立 date -> 序号 映射
    by_code: Dict[str, pd.DataFrame] = {code: g for code, g in k.groupby("code")}

    results = []
    for _, row in candidates.iterrows():
        code = row["code"]
        signal_date = row["data_date"]
        entry_price = row["entry_price"]
        if not entry_price or entry_price <= 0:
            continue
        g = by_code.get(code)
        if g is None or g.empty:
            continue
        # 找信号日之后的交易日（信号日收盘入场，次日开盘为准但这里用收盘简化）
        future = g[g["date"] > signal_date]
        if future.empty:
            continue
        future = future.reset_index(drop=True)
        rec = row.to_dict()
        for h in HORIZONS:
            if len(future) >= h:
                exit_close = float(future.iloc[h - 1]["close"])
                rec[f"ret_{h}d"] = round((exit_close - entry_price) / entry_price * 100, 2)
            else:
                rec[f"ret_{h}d"] = None
        results.append(rec)
    return pd.DataFrame(results)


def summarize_group(df: Optional[pd.DataFrame], label: str) -> Dict:
    """统计一组票的收益表现。"""
    if df is None or df.empty:
        return {"label": label, "n": 0, "note": "无样本"}
    out = {"label": label, "n": len(df)}
    for h in HORIZONS:
        col = f"ret_{h}d"
        s = df[col].dropna()
        if s.empty:
            out[f"ret_{h}d_mean"] = None
            out[f"ret_{h}d_winrate"] = None
            continue
        wins = (s > 0).sum()
        losses = (s < 0).sum()
        avg_win = s[s > 0].mean() if wins else 0
        avg_loss = abs(s[s < 0].mean()) if losses else 0
        out[f"ret_{h}d_mean"] = round(s.mean(), 2)
        out[f"ret_{h}d_median"] = round(s.median(), 2)
        out[f"ret_{h}d_winrate"] = round(wins / len(s) * 100, 1) if len(s) else 0
        out[f"ret_{h}d_pf"] = round((wins * avg_win) / (losses * avg_loss), 2) if losses and avg_loss else None
        out[f"ret_{h}d_max"] = round(s.max(), 1)
        out[f"ret_{h}d_min"] = round(s.min(), 1)
    return out


def main():
    print("=" * 70)
    print("门禁放宽回测：新旧 A 级门禁选出股票的真实未来收益对比")
    print("=" * 70)

    candidates = load_candidates()
    print(f"\n加载 {len(candidates)} 条候选（A+B级，信号日 <= 昨日）")
    print(f"  日期范围: {candidates['data_date'].min()} ~ {candidates['data_date'].max()}")
    print(f"  pct_5d 非零占比: {(candidates['pct_5d'] != 0).mean() * 100:.0f}%")

    # 计算未来收益
    print("\n计算未来收益（join daily_k，按实际交易日）...")
    df = build_future_returns(candidates=candidates)
    print(f"  有效样本: {len(df)} 条（能算出至少1日收益的）")

    if df.empty:
        print("无有效样本，退出")
        return

    # 分组：旧门禁A级 / 新门禁升级A级 / 所有B级基准
    df["is_old_a"] = df.apply(_is_old_a_grade, axis=1)
    df["is_new_a"] = df.apply(_is_new_a_grade, axis=1)
    # "新升级"= 新门禁A 但不是旧门禁A
    df["is_upgraded"] = df["is_new_a"] & ~df["is_old_a"]

    groups = {
        "旧门禁A级 (tv_dual_strict + 5d<=10)": df[df["is_old_a"]],
        "新门禁新增A级 (升级部分)": df[df["is_upgraded"]],
        "新门禁全部A级": df[df["is_new_a"]],
        "所有B级 (基准)": df[df["sop_grade"] == "B"],
    }

    print("\n" + "=" * 70)
    print("各组样本量")
    print("=" * 70)
    for label, g in groups.items():
        print(f"  {label}: {len(g)} 只")

    print("\n" + "=" * 70)
    print("收益对比（入场价=信号日收盘价）")
    print("=" * 70)
    summaries = []
    for label, g in groups.items():
        summaries.append(summarize_group(g, label))

    # 打印表格
    for h in HORIZONS:
        print(f"\n--- {h} 交易日收益 ---")
        print(f"{'组别':<35} {'样本':>5} {'均值%':>8} {'中位%':>8} {'胜率%':>7} {'PF':>6} {'最大%':>8} {'最小%':>8}")
        for s in summaries:
            if s.get(f"ret_{h}d_mean") is None:
                continue
            print(f"{s['label']:<35} {s['n']:>5} "
                  f"{s[f'ret_{h}d_mean']:>8} {s[f'ret_{h}d_median']:>8} "
                  f"{s[f'ret_{h}d_winrate']:>7} {str(s.get(f'ret_{h}d_pf') or '-'):>6} "
                  f"{s[f'ret_{h}d_max']:>8} {s[f'ret_{h}d_min']:>8}")

    # 明细：新升级A级的具体票
    upgraded = df[df["is_upgraded"]].copy()
    if not upgraded.empty:
        print("\n" + "=" * 70)
        print("新门禁升级为A级的股票明细（核心关注对象）")
        print("=" * 70)
        cols = ["data_date", "code", "name", "strategy_type", "sop_quality_score",
                "pct_5d", "entry_price", "ret_1d", "ret_3d", "ret_5d", "ret_10d"]
        cols = [c for c in cols if c in upgraded.columns]
        print(upgraded[cols].to_string(index=False))

    # 关键结论
    print("\n" + "=" * 70)
    print("关键结论")
    print("=" * 70)
    new_a = summaries[2]  # 新门禁全部A级
    b_base = summaries[3]  # B级基准
    if new_a.get("n", 0) > 0 and b_base.get("n", 0) > 0:
        for h in HORIZONS:
            na_mean = new_a.get(f"ret_{h}d_mean")
            b_mean = b_base.get(f"ret_{h}d_mean")
            na_wr = new_a.get(f"ret_{h}d_winrate")
            b_wr = b_base.get(f"ret_{h}d_winrate")
            if na_mean is not None and b_mean is not None:
                diff = na_mean - b_mean
                better = "优于" if diff > 0 else "劣于"
                print(f"  {h}日: 新A级均值 {na_mean}% vs B级 {b_mean}%（{better}基准 {diff:+.2f}%）"
                      f"｜胜率 {na_wr}% vs {b_wr}%")


if __name__ == "__main__":
    main()
