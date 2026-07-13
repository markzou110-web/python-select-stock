from datetime import datetime, timedelta
from typing import Any, Dict, List

from core.backtest_lab import run_single_stock_backtest
from core.db import load_from_db
from core.indicators import calculate_indicators, calculate_pine_indicators


# ── 改动 #14：走查前推 / 样本外测试常量 ──
# train_ratio: 训练段（样本内）占比，默认前 70% 训练、后 30% 测试（样本外）。
# OVERFIT_WARNING_GAP: 样本内外胜率差（百分点）超过此值视为过拟合警告。
DEFAULT_TRAIN_RATIO = 0.7
OVERFIT_WARNING_GAP = 10.0


def build_rolling_windows(
    length: int,
    train_size: int,
    validation_size: int,
    test_size: int,
    step_size: int,
) -> List[Dict[str, slice]]:
    """Return non-overlapping train/validation/test slices rolled through time."""
    sizes = [max(1, int(value)) for value in (train_size, validation_size, test_size, step_size)]
    train_size, validation_size, test_size, step_size = sizes
    windows = []
    end_required = train_size + validation_size + test_size
    for start in range(0, max(0, length - end_required + 1), step_size):
        train_end = start + train_size
        validation_end = train_end + validation_size
        test_end = validation_end + test_size
        windows.append({
            "train": slice(start, train_end),
            "validation": slice(train_end, validation_end),
            "test": slice(validation_end, test_end),
        })
    return windows


def run_batch_experiment(engine, codes: List[str], strategy_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    days = min(max(int(payload.get("days") or 720), 120), 1500)
    start_date = payload.get("start_date") or (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    end_date = payload.get("end_date")
    params = {
        "threshold": payload.get("threshold", 0.12),
        "vol_multiplier": payload.get("vol_multiplier", 1.5),
        "rsi_min": payload.get("rsi_min", 55),
        "pine_min_signals": payload.get("pine_min_signals", 3),
        "stop_loss_pct": payload.get("stop_loss_pct", -8.0),
        "max_hold_days": payload.get("max_hold_days", 10),
        "entry_mode": payload.get("entry_mode", "next_open_confirm"),
        "max_open_gap_pct": payload.get("max_open_gap_pct", 3.0),
        "slippage_bps": payload.get("slippage_bps", 5.0),
        "position_pct": payload.get("position_pct", 1.0),
    }
    rows = []
    for code in codes:
        df = load_from_db(code, start_date, engine)
        if end_date and not df.empty:
            df = df[df["日期"].astype(str).str[:10] <= end_date]
        if df.empty:
            rows.append({"code": code, "status": "NO_DATA", "signal_count": 0})
            continue
        enable_pine = strategy_type in {"pine", "tv_zp"}
        df = calculate_indicators(df, enable_pine_indicators=enable_pine)
        if enable_pine and "RF_Upward" not in df.columns:
            df = calculate_pine_indicators(df)
        result = run_single_stock_backtest(df, strategy_type=strategy_type, params=params)
        summary = result["summary"]
        rows.append({
            "code": code,
            "status": "OK",
            "signal_count": summary["signal_count"],
            "win_rate": summary["win_rate"],
            "avg_return": summary["avg_return"],
            "total_return": summary["total_return"],
            "max_drawdown": summary["max_drawdown"],
            "profit_factor": summary["profit_factor"],
            "skipped_adjustment_gap": summary.get("skipped_adjustment_gap", 0),
        })
    effective = [row for row in rows if row.get("status") == "OK" and row.get("signal_count", 0) > 0]
    return {
        "meta": {"strategy_type": strategy_type, "start_date": start_date, "end_date": end_date, "requested": len(codes)},
        "summary": {
            "tested": len(rows),
            "effective": len(effective),
            "avg_win_rate": round(sum(row["win_rate"] for row in effective) / len(effective), 2) if effective else 0,
            "avg_total_return": round(sum(row["total_return"] for row in effective) / len(effective), 2) if effective else 0,
            "positive_count": sum(1 for row in effective if row["total_return"] > 0),
        },
        "items": sorted(rows, key=lambda row: row.get("total_return", -999), reverse=True),
    }


def _split_df_by_ratio(df, train_ratio: float):
    """按 train_ratio 把已算好指标的 df 按时间顺序拆成 train / test 两段（无重叠）。

    返回 (df_train, df_test)。train 段为前 train_ratio，test 段为剩余。
    两段都已包含完整指标（因为在全量上先算指标再切，保证 test 段指标窗口完整）。
    """
    n = len(df)
    if n < 80:
        return df, df.iloc[0:0]  # 数据不足，test 段为空
    split_idx = max(80, int(n * train_ratio))  # train 段至少 80 行（满足回测最小数据要求）
    df_train = df.iloc[:split_idx].reset_index(drop=True)
    df_test = df.iloc[split_idx:].reset_index(drop=True) if split_idx < n else df.iloc[0:0]
    return df_train, df_test


def _summary_subset(summary: Dict[str, Any]) -> Dict[str, Any]:
    """从回测 summary 提取走查前推对比所需的子集指标。"""
    return {
        "signal_count": summary.get("signal_count", 0),
        "win_rate": summary.get("win_rate", 0),
        "avg_return": summary.get("avg_return", 0),
        "total_return": summary.get("total_return", 0),
        "profit_factor": summary.get("profit_factor", 0),
        "max_drawdown": summary.get("max_drawdown", 0),
    }


def run_walk_forward_experiment(engine, codes: List[str], strategy_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """改动 #14：走查前推 / 样本外测试。

    把日期范围按 train_ratio 拆成训练段（样本内 IS）和测试段（样本外 OOS），
    分别回测，对比 IS/OOS 指标以暴露过拟合。
    关键：先在全量 df 上计算指标再按时间切分，保证 test 段指标窗口完整
    （否则 test 段开头会因指标 warm-up 缺失信号）。
    """
    days = min(max(int(payload.get("days") or 720), 120), 1500)
    start_date = payload.get("start_date") or (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    end_date = payload.get("end_date")
    train_ratio = float(payload.get("train_ratio", DEFAULT_TRAIN_RATIO))
    train_ratio = max(0.5, min(0.9, train_ratio))  # 限制在 [0.5, 0.9]
    params = {
        "threshold": payload.get("threshold", 0.12),
        "vol_multiplier": payload.get("vol_multiplier", 1.5),
        "rsi_min": payload.get("rsi_min", 55),
        "pine_min_signals": payload.get("pine_min_signals", 3),
        "stop_loss_pct": payload.get("stop_loss_pct", -8.0),
        "max_hold_days": payload.get("max_hold_days", 10),
        "entry_mode": payload.get("entry_mode", "next_open_confirm"),
        "max_open_gap_pct": payload.get("max_open_gap_pct", 3.0),
        "slippage_bps": payload.get("slippage_bps", 5.0),
        "position_pct": payload.get("position_pct", 1.0),
    }

    items = []
    for code in codes:
        df = load_from_db(code, start_date, engine)
        if end_date and not df.empty:
            df = df[df["日期"].astype(str).str[:10] <= end_date]
        if df.empty:
            items.append({"code": code, "status": "NO_DATA"})
            continue

        # 先在全量上算指标（保证 test 段指标窗口完整），再按时间切分
        enable_pine = strategy_type in {"pine", "tv_zp"}
        df_full = calculate_indicators(df, enable_pine_indicators=enable_pine)
        if enable_pine and "RF_Upward" not in df_full.columns:
            df_full = calculate_pine_indicators(df_full)

        df_train, df_test = _split_df_by_ratio(df_full, train_ratio)

        # 样本内（train）
        is_summary = {"signal_count": 0, "win_rate": 0, "total_return": 0, "profit_factor": 0, "max_drawdown": 0}
        if not df_train.empty:
            try:
                is_result = run_single_stock_backtest(df_train, strategy_type=strategy_type, params=params)
                is_summary = _summary_subset(is_result["summary"])
            except Exception:
                pass

        # 样本外（test）
        oos_summary = {"signal_count": 0, "win_rate": 0, "total_return": 0, "profit_factor": 0, "max_drawdown": 0}
        if not df_test.empty:
            try:
                oos_result = run_single_stock_backtest(df_test, strategy_type=strategy_type, params=params)
                oos_summary = _summary_subset(oos_result["summary"])
            except Exception:
                pass

        # overfit_gap = OOS胜率 - IS胜率。正值=样本外更差（过拟合）；负值=样本外更好（稳健）
        overfit_gap = round(oos_summary["win_rate"] - is_summary["win_rate"], 2)

        items.append({
            "code": code,
            "status": "OK",
            "in_sample": is_summary,
            "out_of_sample": oos_summary,
            "overfit_gap": overfit_gap,
            "overfit_warning": overfit_gap < -OVERFIT_WARNING_GAP,  # OOS 明显差于 IS → 过拟合
        })

    effective = [it for it in items if it.get("status") == "OK"]
    is_rates = [it["in_sample"]["win_rate"] for it in effective if it["in_sample"]["signal_count"] > 0]
    oos_rates = [it["out_of_sample"]["win_rate"] for it in effective if it["out_of_sample"]["signal_count"] > 0]
    gaps = [it["overfit_gap"] for it in effective]
    return {
        "meta": {
            "strategy_type": strategy_type,
            "start_date": start_date,
            "end_date": end_date,
            "train_ratio": train_ratio,
            "requested": len(codes),
        },
        "summary": {
            "tested": len(items),
            "effective": len(effective),
            "is_avg_win_rate": round(sum(is_rates) / len(is_rates), 2) if is_rates else 0,
            "oos_avg_win_rate": round(sum(oos_rates) / len(oos_rates), 2) if oos_rates else 0,
            "avg_overfit_gap": round(sum(gaps) / len(gaps), 2) if gaps else 0,
            "overfit_warning_count": sum(1 for it in effective if it.get("overfit_warning")),
        },
        "items": items,
    }


def run_rolling_walk_forward_experiment(engine, codes: List[str], strategy_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """Multi-window OOS evaluation with strictly separated train/validation/test periods."""
    days = min(max(int(payload.get("days") or 1500), 240), 3650)
    start_date = payload.get("start_date") or (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
    end_date = payload.get("end_date")
    train_size = int(payload.get("train_size") or 360)
    validation_size = int(payload.get("validation_size") or 60)
    test_size = int(payload.get("test_size") or 60)
    step_size = int(payload.get("step_size") or test_size)
    params = {
        "threshold": payload.get("threshold", 0.12),
        "vol_multiplier": payload.get("vol_multiplier", 1.5),
        "rsi_min": payload.get("rsi_min", 55),
        "pine_min_signals": payload.get("pine_min_signals", 3),
        "stop_loss_pct": payload.get("stop_loss_pct", -8.0),
        "max_hold_days": payload.get("max_hold_days", 10),
        "entry_mode": payload.get("entry_mode", "next_open_confirm"),
        "max_open_gap_pct": payload.get("max_open_gap_pct", 3.0),
        "slippage_bps": payload.get("slippage_bps", 5.0),
        "position_pct": payload.get("position_pct", 1.0),
    }
    items = []
    all_tests = []
    for code in codes:
        df = load_from_db(code, start_date, engine)
        if end_date and not df.empty:
            df = df[df["日期"].astype(str).str[:10] <= end_date]
        if df.empty:
            items.append({"code": code, "status": "NO_DATA", "windows": []})
            continue
        enable_pine = strategy_type in {"pine", "tv_zp"}
        full = calculate_indicators(df, enable_pine_indicators=enable_pine)
        if enable_pine and "RF_Upward" not in full.columns:
            full = calculate_pine_indicators(full)
        windows = []
        for index, slices in enumerate(build_rolling_windows(len(full), train_size, validation_size, test_size, step_size), 1):
            segments = {}
            for name, segment_slice in slices.items():
                # Keep causal warm-up history, but only allow signals inside this segment.
                segment = full.iloc[:segment_slice.stop].reset_index(drop=True)
                maturity_buffer = max(0, int(params["max_hold_days"]))
                if str(params.get("entry_mode")) == "next_open_confirm":
                    maturity_buffer += 1
                last_mature_signal_idx = segment_slice.stop - maturity_buffer - 1
                segment_params = {
                    **params,
                    "signal_start_date": str(full.iloc[segment_slice.start]["日期"])[:10],
                    "signal_end_date": (
                        str(full.iloc[last_mature_signal_idx]["日期"])[:10]
                        if last_mature_signal_idx >= segment_slice.start else "0000-00-00"
                    ),
                }
                result = run_single_stock_backtest(segment, strategy_type=strategy_type, params=segment_params)
                segments[name] = _summary_subset(result["summary"])
            test_summary = segments["test"]
            all_tests.append(test_summary)
            windows.append({
                "window": index,
                "periods": {
                    name: {
                        "start": str(full.iloc[value.start]["日期"])[:10],
                        "end": str(full.iloc[value.stop - 1]["日期"])[:10],
                    }
                    for name, value in slices.items()
                },
                **segments,
                "test_positive_expectancy": test_summary["total_return"] > 0,
            })
        items.append({"code": code, "status": "OK" if windows else "INSUFFICIENT_DATA", "windows": windows})

    total_signals = sum(row["signal_count"] for row in all_tests)
    weighted_win_rate = (
        sum(row["win_rate"] * row["signal_count"] for row in all_tests) / total_signals
        if total_signals else 0
    )
    positive_windows = sum(1 for row in all_tests if row["total_return"] > 0)
    weighted_avg_return = (
        sum(row["avg_return"] * row["signal_count"] for row in all_tests) / total_signals
        if total_signals else 0
    )
    weighted_profit_factor = (
        sum(row["profit_factor"] * row["signal_count"] for row in all_tests) / total_signals
        if total_signals else 0
    )
    return {
        "meta": {
            "strategy_type": strategy_type, "start_date": start_date, "end_date": end_date,
            "train_size": train_size, "validation_size": validation_size,
            "test_size": test_size, "step_size": step_size,
            "window_unit": "trading_rows", "params_frozen": True,
        },
        "summary": {
            "test_windows": len(all_tests), "test_signals": total_signals,
            "oos_weighted_win_rate": round(weighted_win_rate, 2),
            "oos_weighted_avg_return": round(weighted_avg_return, 4),
            "oos_weighted_profit_factor": round(weighted_profit_factor, 4),
            "positive_test_windows": positive_windows,
            "positive_window_ratio": round(positive_windows / len(all_tests), 3) if all_tests else 0,
        },
        "items": items,
    }
