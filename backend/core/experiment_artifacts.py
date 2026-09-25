"""Immutable, content-addressed artifacts for reproducible backtests."""
import hashlib
import json
import platform
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import pandas as pd
from sqlalchemy import bindparam, text


ARTIFACT_VERSION = "backtest-artifact-v1"
RAW_COLUMNS = ("code", "日期", "开盘", "最高", "最低", "收盘", "成交量")
SAFE_REQUEST_KEYS = {
    "code", "codes", "strategy_type", "start_date", "end_date", "days", "threshold",
    "vol_multiplier", "rsi_min", "pine_min_signals", "stop_loss_pct", "max_hold_days",
    "trailing_multiplier", "time_stop_days", "capital", "entry_mode", "max_open_gap_pct",
    "limit_up_gap_pct", "slippage_bps", "position_pct", "position_mode", "profit_exit_mode",
    "lot_size", "skip_adjustment_gaps",
    "adjustment_gap_pct", "train_ratio", "train_size", "validation_size", "test_size",
    "step_size", "window_unit", "params_frozen", "requested",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def current_code_version() -> str:
    root = Path(__file__).resolve().parents[2]
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=root, check=True, capture_output=True, text=True, timeout=3,
        ).stdout.strip()[:40] or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _load_data_slice(engine, request: Dict[str, Any]) -> pd.DataFrame:
    raw_codes = request.get("codes") or [request.get("code")]
    codes = [str(code) for code in raw_codes if code]
    if not codes:
        return pd.DataFrame()
    query = text("""
        SELECT code, date AS "日期", open AS "开盘", high AS "最高", low AS "最低",
               close AS "收盘", vol AS "成交量"
        FROM daily_k
        WHERE code IN :codes
          AND (:start_date IS NULL OR date >= :start_date)
          AND (:end_date IS NULL OR date <= :end_date)
        ORDER BY code, date
    """).bindparams(bindparam("codes", expanding=True))
    return pd.read_sql(query, engine, params={
        "codes": codes, "start_date": request.get("start_date"), "end_date": request.get("end_date"),
    })


def build_data_manifest(frame: pd.DataFrame) -> Dict[str, Any]:
    if frame is None or frame.empty:
        return {"rows": 0, "codes": [], "first_date": None, "last_date": None, "data_hash": _sha256([])}
    available = [column for column in RAW_COLUMNS if column in frame.columns]
    normalized = frame[available].copy().sort_values([column for column in ("code", "日期") if column in available])
    records = normalized.where(pd.notna(normalized), None).to_dict(orient="records")
    dates = pd.to_datetime(normalized.get("日期"), errors="coerce")
    codes = sorted(normalized.get("code", pd.Series(dtype=str)).astype(str).unique().tolist())
    return {
        "rows": int(len(normalized)), "codes": codes,
        "first_date": str(dates.min().date()) if dates.notna().any() else None,
        "last_date": str(dates.max().date()) if dates.notna().any() else None,
        "columns": available, "data_hash": _sha256(records),
    }


def record_backtest_experiment(
    engine, experiment_type: str, request: Dict[str, Any], result: Dict[str, Any], data_frame: pd.DataFrame | None = None,
) -> Dict[str, Any]:
    """Insert once by content hash; an existing artifact is never updated."""
    request = {key: value for key, value in request.items() if key in SAFE_REQUEST_KEYS}
    frame = data_frame if data_frame is not None else _load_data_slice(engine, request)
    manifest = build_data_manifest(frame)
    code_version = current_code_version()
    payload = {
        "artifact_version": ARTIFACT_VERSION,
        "experiment_type": experiment_type,
        "strategy_type": str(request.get("strategy_type") or "squeeze"),
        "request": request,
        "data_manifest": manifest,
        "code_version": code_version,
        "runtime": {"python": platform.python_version()},
        "result": result,
    }
    content_hash = _sha256(payload)
    experiment_id = f"exp_{content_hash[:20]}"
    json_value = ":value" if engine.dialect.name == "sqlite" else "CAST(:value AS JSON)"
    with engine.begin() as conn:
        inserted = conn.execute(text(f"""
            INSERT INTO backtest_experiments(
                experiment_id,content_hash,experiment_type,strategy_type,code_version,data_hash,
                request_payload,data_manifest,result_payload,created_at
            ) VALUES (
                :id,:hash,:kind,:strategy,:code_version,:data_hash,
                {json_value.replace(':value', ':request')},
                {json_value.replace(':value', ':manifest')},
                {json_value.replace(':value', ':result')},:created_at
            ) ON CONFLICT(content_hash) DO NOTHING
        """), {
            "id": experiment_id, "hash": content_hash, "kind": experiment_type,
            "strategy": payload["strategy_type"], "code_version": code_version,
            "data_hash": manifest["data_hash"], "request": _canonical(request),
            "manifest": _canonical(manifest), "result": _canonical(result), "created_at": datetime.now(),
        }).rowcount
    return {
        "experiment_id": experiment_id, "content_hash": content_hash,
        "data_hash": manifest["data_hash"], "code_version": code_version,
        "artifact_version": ARTIFACT_VERSION, "created": bool(inserted),
    }


def list_experiments(engine, limit: int = 50):
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT experiment_id,experiment_type,strategy_type,code_version,data_hash,content_hash,created_at
            FROM backtest_experiments ORDER BY created_at DESC LIMIT :limit
        """), {"limit": min(max(int(limit), 1), 200)}).mappings().all()
    return [dict(row) for row in rows]


def get_experiment(engine, experiment_id: str):
    with engine.connect() as conn:
        row = conn.execute(text("SELECT * FROM backtest_experiments WHERE experiment_id=:id"), {"id": experiment_id}).mappings().first()
    return dict(row) if row else None
