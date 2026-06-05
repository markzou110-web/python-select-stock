#!/usr/bin/env python3
"""Repair suspected ex-right adjustment gaps by refilling qfq daily bars.

Default mode is dry-run. Use --apply to update daily_k.
"""

import argparse
import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List

import pandas as pd
from sqlalchemy import text

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from core.data_source_quality import build_local_data_quality_report  # noqa: E402
from core.db import get_db_engine, validate_stock_code  # noqa: E402


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Refill qfq daily_k rows for suspected corporate-action gaps.")
    parser.add_argument("--target-date", default=None, help="Trading date to inspect, e.g. 2026-06-04. Default: latest date.")
    parser.add_argument("--threshold", type=float, default=25.0, help="Abnormal close jump threshold percent.")
    parser.add_argument("--lookback-days", type=int, default=250, help="History window to refill for each suspected code.")
    parser.add_argument("--limit", type=int, default=30, help="Maximum suspected codes to process.")
    parser.add_argument("--apply", action="store_true", help="Write repaired rows to daily_k. Omit for dry-run.")
    return parser.parse_args()


def _fetch_qfq_history(code: str, start_date: str, end_date: str) -> pd.DataFrame:
    import akshare as ak

    df = ak.stock_zh_a_hist(
        symbol=code,
        period="daily",
        start_date=start_date.replace("-", ""),
        end_date=end_date.replace("-", ""),
        adjust="qfq",
    )
    if df is None or df.empty:
        return pd.DataFrame()
    required = ["日期", "开盘", "最高", "最低", "收盘", "成交量"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError(f"AkShare result missing columns: {missing}")
    cleaned = df[required].copy()
    cleaned["日期"] = pd.to_datetime(cleaned["日期"]).dt.strftime("%Y-%m-%d")
    return cleaned


def _upsert_daily_k(engine: Any, code: str, df: pd.DataFrame) -> int:
    rows = []
    for item in df.to_dict("records"):
        rows.append({
            "code": code,
            "date": item["日期"],
            "open": float(item["开盘"]),
            "high": float(item["最高"]),
            "low": float(item["最低"]),
            "close": float(item["收盘"]),
            "vol": float(item["成交量"]),
        })
    if not rows:
        return 0

    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(text("""
                INSERT INTO daily_k (code, date, open, high, low, close, vol)
                VALUES (:code, CAST(:date AS DATE), :open, :high, :low, :close, :vol)
                ON CONFLICT (code, date) DO UPDATE SET
                    open = EXCLUDED.open,
                    high = EXCLUDED.high,
                    low = EXCLUDED.low,
                    close = EXCLUDED.close,
                    vol = EXCLUDED.vol
            """), rows)
        else:
            conn.execute(text("""
                INSERT INTO daily_k (code, date, open, high, low, close, vol)
                VALUES (:code, :date, :open, :high, :low, :close, :vol)
                ON CONFLICT (code, date) DO UPDATE SET
                    open = excluded.open,
                    high = excluded.high,
                    low = excluded.low,
                    close = excluded.close,
                    vol = excluded.vol
            """), rows)
    return len(rows)


def _suspected_samples(report: Dict[str, Any], limit: int) -> List[Dict[str, Any]]:
    samples = report.get("summary", {}).get("abnormal_move_samples") or []
    return [
        item for item in samples
        if item.get("likely_reason") == "suspected_corporate_action_gap" and validate_stock_code(item.get("code", ""))
    ][:limit]


def main() -> int:
    args = _parse_args()
    engine = get_db_engine()
    if engine is None:
        print("ERROR: database connection unavailable")
        return 2

    report = build_local_data_quality_report(
        engine,
        target_date=args.target_date,
        max_abnormal_move_pct=args.threshold,
    )
    selected_date = report.get("summary", {}).get("selected_date")
    if not selected_date:
        print("ERROR: no selected date found in local data quality report")
        return 2

    samples = _suspected_samples(report, args.limit)
    print(f"selected_date={selected_date} suspected_corporate_action_gap={len(samples)} mode={'apply' if args.apply else 'dry-run'}")
    if not samples:
        return 0

    end_dt = datetime.strptime(selected_date, "%Y-%m-%d")
    start_date = (end_dt - timedelta(days=max(args.lookback_days, 30))).strftime("%Y-%m-%d")
    total_rows = 0
    for item in samples:
        code = item["code"]
        name = item.get("name") or code
        try:
            df = _fetch_qfq_history(code, start_date, selected_date)
            print(f"{code} {name}: fetched={len(df)} jump={item.get('close_jump_pct')}% reason={item.get('likely_reason')}")
            if args.apply:
                total_rows += _upsert_daily_k(engine, code, df)
        except Exception as exc:
            print(f"{code} {name}: ERROR {str(exc)[:160]}")

    if args.apply:
        print(f"updated_rows={total_rows}")
    else:
        print("dry-run only; rerun with --apply to write repaired qfq rows")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
