"""市场状态归因与退潮闸门规则回测（只读）。

背景（三基线报告 2026-09-30）：门控确认制回放显示策略族期望由年份/市场状态
主导——validation(2025) 全门皆正、train/test 全负，信号层门槛只能挪 0.5 个
百分点/笔。本脚本回答："哪些月份该不交易，能否用可量化特征提前识别"。

方法：复用 a_grade_kline_replay 的事件级重放（与 run_kline_replay 同一循环），
保留 filled 交易的 exec_entry_date / exec_return_pct，然后：
  1. 按入场月分桶（胜率/期望/PF）与市场代理月度特征并排
  2. 回测四条候选闸门规则（日线粒度，按 exec_entry_date 判定拦截）：
       R1 non_offensive   代理收盘 < EMA20（现有 offensive 定义取反）
       R2 drawdown>5%     代理距 20 日高点回撤超 5%
       R3 mom<-3%         代理近 10 日累计收益 < -3%
       R4 R1+R3 组合
     每条规则输出 train/validation/test 三段：拦截笔数、拦截笔的平均收益
     （应为负才是好规则）、保留笔的胜率/期望 vs 无闸门基线。

用法：python -m scripts.regime_attribution [--start-date 2022-05-12] [--out path]
"""
import argparse
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd

from core.a_grade_kline_replay import (
    _chunks,
    _load_codes,
    _load_price_chunk,
    _market_proxy,
    replay_stock,
)
from core.db import get_db_engine

SEGMENTS = {
    "train": ("2022-05-12", "2024-12-31"),
    "validation": ("2025-01-01", "2025-12-31"),
    "test": ("2026-01-01", "2099-12-31"),
}


def _segment_of(date: pd.Timestamp) -> str:
    for name, (start, end) in SEGMENTS.items():
        if pd.Timestamp(start) <= date <= pd.Timestamp(end):
            return name
    return "test"


def _bucket_stats(frame: pd.DataFrame) -> dict:
    if frame.empty:
        return {"filled": 0, "win_rate": None, "avg_return": None, "profit_factor": None}
    rets = pd.to_numeric(frame["exec_return_pct"], errors="coerce").dropna()
    if rets.empty:
        return {"filled": 0, "win_rate": None, "avg_return": None, "profit_factor": None}
    wins = rets[rets > 0]
    losses = rets[rets <= 0]
    gross_loss = abs(float(losses.sum()))
    return {
        "filled": int(len(rets)),
        "win_rate": round(float((rets > 0).mean() * 100), 2),
        "avg_return": round(float(rets.mean()), 3),
        "median_return": round(float(rets.median()), 3),
        "profit_factor": round(float(wins.sum() / gross_loss), 3) if gross_loss > 0 else None,
    }


def _market_features(market: pd.DataFrame) -> pd.DataFrame:
    market = market.sort_values("日期").reset_index(drop=True).copy()
    market["high_20d"] = market["收盘"].rolling(20).max()
    market["drawdown_pct"] = (market["收盘"] / market["high_20d"] - 1) * 100
    market["mom_10d_pct"] = market["收盘"].pct_change(10) * 100
    market["月"] = market["日期"].dt.strftime("%Y-%m")

    def _month_return(series: pd.Series) -> float:
        # _market_proxy 返回列不含 market_return，用代理收盘首尾比计算月收益
        return float(series.iloc[-1] / series.iloc[0] - 1) * 100 if len(series) > 1 else 0.0

    monthly = market.groupby("月").agg(
        month_return=("收盘", _month_return),
        offensive_ratio=("offensive", lambda s: float(s.mean()) * 100),
        max_drawdown_pct=("drawdown_pct", "min"),
        avg_mom_10d=("mom_10d_pct", "mean"),
    ).reset_index()
    return market, monthly


RULES = {
    "R1_non_offensive": lambda row: not bool(row["offensive"]),
    "R2_drawdown_5pct": lambda row: pd.notna(row["drawdown_pct"]) and row["drawdown_pct"] < -5.0,
    "R3_mom_neg_3pct": lambda row: pd.notna(row["mom_10d_pct"]) and row["mom_10d_pct"] < -3.0,
    "R4_combined": lambda row: (
        (not bool(row["offensive"]))
        or (pd.notna(row["drawdown_pct"]) and row["drawdown_pct"] < -5.0)
    ),
}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--start-date", default="2022-05-12")
    parser.add_argument("--chunk-size", type=int, default=200)
    parser.add_argument("--max-codes", type=int)
    parser.add_argument("--out", default="")
    parser.add_argument("--events-out", default="/tmp/regime_events.csv",
                        help="filled 事件缓存（重放 ~70min，缓存后规则迭代免重跑）")
    parser.add_argument("--events-in", default="",
                        help="已有事件缓存路径；给定则跳过重放")
    args = parser.parse_args()

    engine = get_db_engine()
    market = _market_proxy(engine, args.start_date)
    # 特征列（drawdown/mom）必须并入 market 本体：规则判定与月度聚合都从这里取列
    market, monthly_features = _market_features(market)
    market_lookup = market.set_index("日期")
    codes = _load_codes(engine, args.start_date, args.max_codes)
    print(f"universe={len(codes)} market_days={len(market)}", flush=True)

    t0 = time.time()
    if args.events_in:
        events = pd.read_csv(args.events_in)
        print(f"loaded cached events: {len(events)} rows from {args.events_in}", flush=True)
    else:
        rows = []
        for index, code_chunk in enumerate(_chunks(codes, max(1, int(args.chunk_size)))):
            prices = _load_price_chunk(engine, code_chunk, args.start_date)
            for code, group in prices.groupby("code", sort=False):
                rows.extend(replay_stock(str(code), group, market))
            if (index + 1) % 5 == 0:
                print(f"chunk {index + 1} elapsed={time.time() - t0:.0f}s events={len(rows)}", flush=True)
        events = pd.DataFrame(rows)
        Path(args.events_out).write_text(events.to_csv(index=False), encoding="utf-8")
        print(f"events cached -> {args.events_out} ({len(events)} rows)", flush=True)
    filled = events[events["exec_filled"].fillna(False).astype(bool)].copy()
    filled["exec_return_pct"] = pd.to_numeric(filled["exec_return_pct"], errors="coerce")
    filled["exec_entry_date"] = pd.to_datetime(filled["exec_entry_date"], errors="coerce")
    filled = filled.dropna(subset=["exec_entry_date", "exec_return_pct"])
    filled["segment"] = filled["exec_entry_date"].map(lambda d: _segment_of(d))

    # ── 1) 市场特征 daily 对齐 ──
    market_indexed = market.set_index("日期")

    def _row_market(entry_date: pd.Timestamp):
        ts = pd.Timestamp(entry_date).normalize()
        if ts in market_indexed.index:
            return market_indexed.loc[ts]
        return None

    # ── 2) 闸门规则回测（按入场日判定拦截）──
    rule_report = {}
    blocked_mask_cache = {}
    for rule_name, predicate in RULES.items():
        blocked = []
        for ts, group in filled.groupby("exec_entry_date"):
            row = _row_market(ts)
            if row is None:
                continue
            if predicate(row):
                blocked.extend(group.index.tolist())
        mask = pd.Series(False, index=filled.index)
        if blocked:
            mask.loc[sorted(set(blocked))] = True
        blocked_mask_cache[rule_name] = mask
        per_segment = {}
        for segment in ("train", "validation", "test"):
            seg_frame = filled[filled["segment"] == segment]
            seg_mask = mask[seg_frame.index]
            kept = seg_frame[~seg_mask]
            blocked_part = seg_frame[seg_mask]
            per_segment[segment] = {
                "baseline": _bucket_stats(seg_frame),
                "kept": _bucket_stats(kept),
                "blocked": _bucket_stats(blocked_part),
            }
        rule_report[rule_name] = {"per_segment": per_segment}

    # ── 3) 月度分桶 ──
    filled["月"] = filled["exec_entry_date"].dt.strftime("%Y-%m")
    monthly = {}
    for month, group in filled.groupby("月"):
        stats = _bucket_stats(group)
        feature = monthly_features[monthly_features["月"] == month]
        stats["market_month_return"] = float(feature["month_return"].iloc[0]) if len(feature) else None
        stats["offensive_ratio"] = float(feature["offensive_ratio"].iloc[0]) if len(feature) else None
        monthly[month] = stats

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "start_date": args.start_date,
        "events": int(len(events)),
        "filled": int(len(filled)),
        "monthly": monthly,
        "rules": rule_report,
    }
    payload = json.dumps(report, ensure_ascii=False, indent=2, default=str)
    print(payload, flush=True)
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
        print(f"saved -> {args.out}", flush=True)
    print(f"total elapsed {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
