"""当前生产策略的全市场历史回测（只读，不写任何业务表）。

对 daily_k 全历史逐票跑各策略的官方回测引擎（_simulate_backtest：T+1 开盘
入场、板块感知涨停跳过、-9% 止损、ATR×2.2 移动止盈、10 日最大持有、摩擦
成本），聚合真实胜率、Wilson 95% 下界、期望收益与止损/时间止盈结构。

用法：
    python scripts/backtest_current_strategies.py --limit 200   # 冒烟
    python scripts/backtest_current_strategies.py               # 全市场

口径说明（与 quant-review 结论一致）：
- 未建模跳空击穿止损（低开 -15% 仍按 -9% 成交）→ 胜率偏乐观
- 信号窗口重叠（同票连续信号未去重）→ Wilson 显著性偏乐观
- 宇宙 = 库内 daily_k 现存票 → 轻度幸存者偏差
"""
import argparse
import json
import math
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pandas as pd
from sqlalchemy import text

from core.db import get_db_engine, load_from_db
from core.indicators import calculate_indicators
from core.strategy import (
    calculate_consensus_win_rate,
    calculate_historical_win_rate,
    calculate_pine_win_rate,
    calculate_tv_dual_win_rate,
    calculate_tv_zp_win_rate,
)

STRATEGY_RUNNERS = {
    "tv_dual": lambda df, code: calculate_tv_dual_win_rate(
        df, threshold=0.12, vol_multiplier=1.5, rsi_min=55, code=code),
    "tv_dual_strict": lambda df, code: calculate_tv_dual_win_rate(
        df, threshold=0.12, vol_multiplier=1.5, rsi_min=55,
        require_both=True, code=code),
    "tv_zp": lambda df, code: calculate_tv_zp_win_rate(df, code=code),
    "squeeze": lambda df, code: calculate_historical_win_rate(df, code=code),
    "pine": lambda df, code: calculate_pine_win_rate(df, code=code),
    "consensus": lambda df, code: calculate_consensus_win_rate(df, code=code),
}

MIN_BARS = 130  # 回测引擎最低预热


def wilson_lower(wins: float, trades: float, z: float = 1.96) -> float:
    if trades <= 0:
        return 0.0
    p = wins / trades
    denom = 1 + z * z / trades
    center = p + z * z / (2 * trades)
    margin = z * math.sqrt(p * (1 - p) / trades + z * z / (4 * trades * trades))
    return max(0.0, (center - margin) / denom)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=1500, help="回看自然日数")
    parser.add_argument("--limit", type=int, default=0, help="只跑前 N 只票（冒烟用）")
    parser.add_argument("--out", default="", help="结果 JSON 输出路径")
    args = parser.parse_args()

    engine = get_db_engine()
    if not engine:
        print("no db engine", flush=True)
        sys.exit(1)

    with engine.connect() as conn:
        codes = [row[0] for row in conn.execute(
            text("SELECT DISTINCT code FROM daily_k ORDER BY code")).fetchall()]
        (max_date,) = conn.execute(text("SELECT MAX(date) FROM daily_k")).fetchone()
    if args.limit:
        codes = codes[: args.limit]
    start_date = (datetime.now() - timedelta(days=args.days)).strftime("%Y-%m-%d")
    print(f"universe={len(codes)} codes, window=[{start_date}, {max_date}]", flush=True)

    # {strategy: {trades, wins, stop_hits, time_stopped, limit_up_skipped,
    #             vol_skipped, ret_sum, stocks_with_signals, pf_list, warn_count}}
    agg: dict = {}
    t0 = time.time()
    for index, code in enumerate(str(code).zfill(6) for code in codes):
        try:
            df = load_from_db(code, start_date, engine)
            if df is None or df.empty or len(df) < MIN_BARS:
                continue
            df = calculate_indicators(df)
        except Exception:
            continue
        for name, runner in STRATEGY_RUNNERS.items():
            try:
                result = runner(df, code)
            except Exception:
                continue
            trades = int(result.get("signal_count") or 0)
            if trades <= 0:
                continue
            bucket = agg.setdefault(name, {
                "trades": 0, "wins": 0, "stop_hits": 0, "time_stopped": 0,
                "limit_up_skipped": 0, "vol_skipped": 0, "ret_sum": 0.0,
                "stocks_with_signals": 0, "pf_list": [], "warn_count": 0,
            })
            bucket["trades"] += trades
            bucket["wins"] += int(result.get("win_count") or 0)
            bucket["stop_hits"] += int(result.get("stop_loss_hits") or 0)
            bucket["time_stopped"] += int(result.get("time_stopped") or 0)
            bucket["limit_up_skipped"] += int(result.get("limit_up_skipped") or 0)
            bucket["vol_skipped"] += int(result.get("vol_skipped") or 0)
            bucket["ret_sum"] += float(result.get("avg_return") or 0) * trades
            bucket["stocks_with_signals"] += 1
            pf = result.get("profit_factor")
            if isinstance(pf, (int, float)) and pf == pf:
                bucket["pf_list"].append(float(pf))
            if result.get("sample_warning"):
                bucket["warn_count"] += 1
        if (index + 1) % 300 == 0:
            print(f"progress {index + 1}/{len(codes)} elapsed={time.time() - t0:.0f}s", flush=True)

    report = {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "window_start": start_date,
        "data_max_date": str(max_date),
        "universe": len(codes),
        "min_bars": MIN_BARS,
        "strategies": {},
    }
    for name, bucket in sorted(agg.items()):
        trades = bucket["trades"]
        wins = bucket["wins"]
        pf_sorted = sorted(bucket["pf_list"])
        median_pf = pf_sorted[len(pf_sorted) // 2] if pf_sorted else None
        report["strategies"][name] = {
            "stocks_with_signals": bucket["stocks_with_signals"],
            "trades": trades,
            "win_rate": round(wins / trades * 100, 2) if trades else None,
            "wilson95_lower_pct": round(wilson_lower(wins, trades) * 100, 2) if trades else None,
            "avg_return_pct": round(bucket["ret_sum"] / trades, 3) if trades else None,
            "stop_loss_hit_pct": round(bucket["stop_hits"] / trades * 100, 2) if trades else None,
            "time_stopped_pct": round(bucket["time_stopped"] / trades * 100, 2) if trades else None,
            "limit_up_skipped": bucket["limit_up_skipped"],
            "vol_skipped": bucket["vol_skipped"],
            "median_stock_profit_factor": round(median_pf, 2) if median_pf else None,
            "stocks_with_sample_warning": bucket["warn_count"],
        }
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    print(payload, flush=True)
    if args.out:
        Path(args.out).write_text(payload, encoding="utf-8")
        print(f"saved -> {args.out}", flush=True)
    print(f"total elapsed {time.time() - t0:.0f}s", flush=True)


if __name__ == "__main__":
    main()
