"""Compare the production TV-or execution path with raw chart markers."""

from __future__ import annotations

import argparse
import json
import multiprocessing as mp
import os
import sys
import tempfile
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.a_grade_kline_replay import (
    DEFAULT_START_DATE,
    DEFAULT_TRAIN_END,
    DEFAULT_VALIDATION_END,
    _independent_position_mask,
    _load_codes,
    _load_price_chunk,
    _market_proxy,
    _tiered_v11_candidate_mask,
    build_gate_report,
    replay_stock,
    summarize_gate,
)
from core.db import get_db_engine


_WORKER_ENGINE = None
_WORKER_MARKET = None
_WORKER_OUTPUT_DIR = None


def _init_worker(market: pd.DataFrame, output_dir: str) -> None:
    global _WORKER_ENGINE, _WORKER_MARKET, _WORKER_OUTPUT_DIR
    _WORKER_ENGINE = get_db_engine()
    _WORKER_MARKET = market
    _WORKER_OUTPUT_DIR = output_dir


def _run_chunk(task: tuple[int, list[str]]) -> tuple[int, int, int, str]:
    part, codes = task
    prices = _load_price_chunk(_WORKER_ENGINE, codes, DEFAULT_START_DATE)
    rows: list[dict[str, Any]] = []
    for code, group in prices.groupby("code", sort=False):
        rows.extend(replay_stock(
            str(code),
            group,
            _WORKER_MARKET,
            require_both=False,
            include_chart_comparison=True,
        ))
    path = os.path.join(_WORKER_OUTPUT_DIR, f"part_{part:03d}.pkl")
    pd.DataFrame(rows).to_pickle(path)
    return part, len(codes), len(rows), path


def _model_frame(frame: pd.DataFrame, prefix: str | None = None) -> pd.DataFrame:
    result = frame.copy()
    if not prefix:
        return result
    for field in (
        "mature", "filled", "return_pct", "exit_reason",
        "entry_idx", "exit_idx", "hold_days",
    ):
        result[f"exec_{field}"] = result[f"{prefix}_exec_{field}"]
    return result


def _enriched_summary(frame: pd.DataFrame, candidate_mask: pd.Series) -> tuple[dict[str, Any], pd.Series]:
    selected_mask = _independent_position_mask(frame, candidate_mask)
    return summarize_gate(frame, selected_mask), selected_mask


def _segmented_model_report(frame: pd.DataFrame) -> dict[str, Any]:
    dates = pd.to_datetime(frame["signal_date"], errors="coerce")
    period_masks = {
        "train": dates.le(pd.Timestamp(DEFAULT_TRAIN_END)),
        "validation": dates.gt(pd.Timestamp(DEFAULT_TRAIN_END)) & dates.le(pd.Timestamp(DEFAULT_VALIDATION_END)),
        "test": dates.gt(pd.Timestamp(DEFAULT_VALIDATION_END)),
        "all": pd.Series(True, index=frame.index),
    }
    independent = _independent_position_mask(frame, pd.Series(True, index=frame.index))
    segments = {
        name: _enriched_summary(frame, independent & mask)[0]
        for name, mask in period_masks.items()
    }
    sources = {
        source: _enriched_summary(
            frame,
            independent & frame["signal_sources"].astype(str).eq(source),
        )[0]
        for source in sorted(frame["signal_sources"].dropna().astype(str).unique())
    }
    years = dates.dt.year
    yearly = {
        str(int(year)): _enriched_summary(frame, independent & years.eq(year))[0]
        for year in sorted(years.dropna().unique())
    }
    return {"segments": segments, "sources": sources, "yearly": yearly}


def run(*, workers: int, chunk_size: int, output_path: str, max_codes: int | None = None) -> dict[str, Any]:
    engine = get_db_engine()
    market = _market_proxy(engine, DEFAULT_START_DATE)
    codes = _load_codes(engine, DEFAULT_START_DATE, max_codes)
    tasks = [
        (part, codes[start:start + chunk_size])
        for part, start in enumerate(range(0, len(codes), chunk_size), 1)
    ]
    print(json.dumps({"stage": "start", "codes": len(codes), "tasks": len(tasks)}), flush=True)

    with tempfile.TemporaryDirectory(prefix="tv_or_backtest_") as output_dir:
        paths = []
        done_codes = 0
        done_signals = 0
        context = mp.get_context("spawn")
        with ProcessPoolExecutor(
            max_workers=max(1, workers),
            mp_context=context,
            initializer=_init_worker,
            initargs=(market, output_dir),
        ) as pool:
            futures = [pool.submit(_run_chunk, task) for task in tasks]
            for future in as_completed(futures):
                part, code_count, signal_count, path = future.result()
                paths.append((part, path))
                done_codes += code_count
                done_signals += signal_count
                print(json.dumps({
                    "stage": "progress",
                    "codes": done_codes,
                    "signals": done_signals,
                }), flush=True)

        signals = pd.concat(
            [pd.read_pickle(path) for _, path in sorted(paths)],
            ignore_index=True,
        )
    signals["signal_date"] = pd.to_datetime(signals["signal_date"], errors="coerce")
    system_gate_report = build_gate_report(signals)
    models = {
        "tiered_v11_system": _model_frame(signals[_tiered_v11_candidate_mask(signals)]),
        "system_confirmation_raw": _model_frame(signals),
        "chart_markers_with_stop": _model_frame(signals, "chart_protected"),
        "chart_markers_only": _model_frame(signals, "chart_points_only"),
    }
    report = {
        "meta": {
            "start_date": DEFAULT_START_DATE,
            "end_date": str(signals["signal_date"].max().date()),
            "train_end": DEFAULT_TRAIN_END,
            "validation_end": DEFAULT_VALIDATION_END,
            "codes": len(codes),
            "raw_signal_events": len(signals),
            "execution": (
                "signal at T close; buy T+1; sell marker at T close -> T+1 open; "
                "5bp slippage; A-share fees; stop-first on ambiguous OHLC"
            ),
            "system_entry": "price-action confirmation trigger with max 3% extension",
            "tiered_v11_entry": (
                "A=same-day MA+ZP at 1.0 risk unit; B=MA-only at 0.6 risk unit with "
                "PA>=60, non-AVOID and offensive market; C=ZP-only research, no execution"
            ),
            "chart_entry": (
                "next open after raw MA or ZP marker; ordinary gap allowed; "
                "limit-up remains unfilled"
            ),
        },
        "system_gate_report": system_gate_report,
        "models": {
            name: _segmented_model_report(model)
            for name, model in models.items()
        },
    }
    with open(output_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, default=str)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--chunk-size", type=int, default=100)
    parser.add_argument("--max-codes", type=int)
    parser.add_argument("--output", default="/tmp/tv_or_vs_chart_full.json")
    args = parser.parse_args()
    report = run(
        workers=args.workers,
        chunk_size=args.chunk_size,
        output_path=args.output,
        max_codes=args.max_codes,
    )
    print(json.dumps({
        "stage": "complete",
        "output": args.output,
        "signals": report["meta"]["raw_signal_events"],
        "selected_gate": report["system_gate_report"].get("selected_gate"),
    }), flush=True)


if __name__ == "__main__":
    main()
