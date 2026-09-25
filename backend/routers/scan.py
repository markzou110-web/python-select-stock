"""
Scan router - market scanning and strategy analysis endpoints.

Extracted from api.py. Preserves all original logic exactly.
"""
from fastapi import APIRouter, HTTPException
from typing import Optional, Dict, Any, List, Literal
from datetime import datetime, timedelta, date
import time
import re
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from sqlalchemy import text

import akshare as ak

from core.logging_config import logger
from core.ws_manager import manager as ws_manager
from core.db import (
    _json_safe, get_db_engine, save_scan_results,
    get_scan_history_by_date, get_scan_dates, get_available_dates,
    load_from_db
)
from core.data import (
    get_cached_data, get_market_snapshot, get_index_hist, get_sector_map, get_stale_cache
)
from core.indicators import (
    calculate_indicators, calculate_pine_indicators,
    get_weekly_indicators, batch_calculate_indicators
)
from core.strategy import (
    check_strategy, check_pine_strategy, check_consensus_strategy,
    calculate_historical_win_rate, calculate_pine_win_rate, calculate_consensus_win_rate
)
from core.risk_constants import BACKTEST_STOP_LOSS_PCT, PRIMARY_TV_STRATEGY  # 与实盘硬止损同源，保证回测胜率反映真实规则
from core.celery_app import celery_app
from core.scan_preflight import build_scan_preflight
from core.audit_log import record_lifecycle_event, record_task_run
from core.trading_calendar import is_a_share_intraday_session

router = APIRouter(prefix="/api", tags=["scan"])

MANUAL_LIVE_TV_STRATEGIES = {PRIMARY_TV_STRATEGY, "tv_dual_strict", "tv_zp", "h2"}


def _selected_strategy_types(strategy_type: str, strategy_types: Optional[str]) -> List[str]:
    values = [item.strip() for item in str(strategy_types or "").split(",") if item.strip()]
    if not values:
        values = [strategy_type]
    return list(dict.fromkeys(values))


def _merge_strategy_results(
    results: List[Dict[str, Any]], selected_strategies: Optional[List[str]] = None,
    match_mode: str = "any",
) -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for item in results:
        raw_code = str(item.get("代码") or item.get("code") or "")
        if not raw_code:
            continue
        code = raw_code.zfill(6)
        strategy = str(item.get("strategy_type") or "")
        if code not in merged:
            merged[code] = dict(item)
            merged[code]["matched_strategies"] = [strategy] if strategy else []
            continue
        existing = merged[code]
        matches = existing["matched_strategies"]
        if strategy and strategy not in matches:
            matches.append(strategy)
        existing_rank = (
            bool(existing.get("trade_eligible")),
            float(existing.get("display_trade_score") or existing.get("final_trade_score") or existing.get("Score") or 0),
        )
        item_rank = (
            bool(item.get("trade_eligible")),
            float(item.get("display_trade_score") or item.get("final_trade_score") or item.get("Score") or 0),
        )
        if item_rank > existing_rank:
            merged[code] = {**item, "matched_strategies": matches}
    if match_mode == "all" and selected_strategies:
        required = set(selected_strategies)
        return [item for item in merged.values() if required.issubset(item["matched_strategies"])]
    return list(merged.values())


def manual_scan_requires_live_snapshot(
    strategy_type: str,
    data_date: Optional[str],
    now: Optional[datetime] = None,
) -> bool:
    return (
        strategy_type in MANUAL_LIVE_TV_STRATEGIES
        and not data_date
        and is_a_share_intraday_session(now)
    )


@router.get("/scan/preflight")
def scan_preflight(
    data_date: Optional[str] = None,
    min_stock_count: int = 1000,
    min_history_days: int = 120,
):
    """检查本地数据是否足够支撑一次专业扫描。"""
    return build_scan_preflight(
        get_db_engine(),
        data_date=data_date,
        min_stock_count=min_stock_count,
        min_history_days=min_history_days,
    )


@router.get("/scan/capabilities")
def scan_capabilities():
    return {"match_modes": ["any", "all"]}

@celery_app.task(name="scan.run_market_scan_task")
def run_market_scan_task(
    threshold: float = 0.12,
    vol_multiplier: float = 1.5,
    rsi_min: int = 55,
    use_macd_filter: bool = True,
    use_bb_sqz: bool = False,
    sqz_lookback: int = 10,
    use_weekly: bool = False,
    market_range: str = "全市场(除科创)",
    turnover_min: float = 3.0,
    mkt_cap_min: float = 0.0,
    use_rs_filter: bool = False,
    local_only: bool = True,
    data_date: Optional[str] = None,
    strategy_type: str = PRIMARY_TV_STRATEGY,
    pine_min_signals: int = 3,
    min_data_days: Optional[int] = None,
    weekly_ma_period: int = 20,  # 周线均线周期 (10/20/30/60)
    stop_loss_pct: float = BACKTEST_STOP_LOSS_PCT,
    require_live_snapshot: bool = False,
    include_scan_metadata: bool = False,
    strategy_types: Optional[str] = None,
    match_mode: str = "any",
):
    from core.scanner import perform_market_scan
    selected_strategies = _selected_strategy_types(strategy_type, strategy_types)
    combined_all = match_mode == "all" and len(selected_strategies) > 1
    scan_metadata = {
        "data_date": data_date,
        "data_mode": None,
        "as_of": None,
        "strategy_types": selected_strategies,
        "match_mode": match_mode,
    }
    all_results: List[Dict[str, Any]] = []
    primary_results: List[Dict[str, Any]] = []
    for current_strategy in selected_strategies:
        current_metadata: Dict[str, Any] = {}
        current_results = perform_market_scan(
            threshold=threshold,
            vol_multiplier=vol_multiplier,
            rsi_min=rsi_min,
            use_macd_filter=use_macd_filter,
            use_bb_sqz=use_bb_sqz,
            sqz_lookback=sqz_lookback,
            use_weekly=use_weekly,
            market_range=market_range,
            turnover_min=turnover_min,
            mkt_cap_min=mkt_cap_min,
            use_rs_filter=use_rs_filter,
            local_only=local_only,
            data_date=data_date,
            strategy_type=current_strategy,
            pine_min_signals=pine_min_signals,
            min_data_days=min_data_days,
            weekly_ma_period=weekly_ma_period,
            stop_loss_pct=stop_loss_pct,
            require_live_snapshot=require_live_snapshot,
            scan_context=current_metadata,
            publish_to_sentinel=current_strategy == PRIMARY_TV_STRATEGY and not combined_all,
        )
        all_results.extend(current_results or [])
        if current_strategy == PRIMARY_TV_STRATEGY:
            primary_results = current_results or []
        for key in ("data_date", "data_mode", "as_of"):
            if current_metadata.get(key) is not None:
                scan_metadata[key] = current_metadata[key]

    results = _merge_strategy_results(all_results, selected_strategies, match_mode)
    if primary_results and not combined_all:
        try:
            from core.sentinel import send_after_close_watchlist
            send_after_close_watchlist(primary_results, scan_date=primary_results[0].get("data_date"))
        except Exception as exc:
            logger.warning(f"After-close watchlist push skipped: {exc}")
    for item in results or []:
        record_lifecycle_event(
            "SCAN_RECOMMENDED",
            source="scan",
            code=item.get("代码") or item.get("code"),
            name=item.get("名称") or item.get("name"),
            strategy_type=item.get("strategy_type") or strategy_type,
            theme=item.get("题材") or item.get("行业") or item.get("industry"),
            payload={
                "score": item.get("Score") or item.get("score"),
                "matched_strategies": item.get("matched_strategies") or [],
            },
        )
    safe_results = _json_safe(results)
    if include_scan_metadata:
        return {"results": safe_results, "scan_meta": _json_safe(scan_metadata)}
    return safe_results

@router.get("/scan")
def scan_market(
    threshold: float = 0.12,
    vol_multiplier: float = 1.5,
    rsi_min: int = 55,
    use_macd_filter: bool = True,
    use_bb_sqz: bool = False,
    sqz_lookback: int = 10,
    use_weekly: bool = False,
    market_range: str = "全市场(除科创)",
    turnover_min: float = 3.0,
    mkt_cap_min: float = 0.0,
    use_rs_filter: bool = False,
    local_only: bool = True,
    data_date: Optional[str] = None,
    strategy_type: str = PRIMARY_TV_STRATEGY,
    pine_min_signals: int = 3,
    min_data_days: Optional[int] = None,
    weekly_ma_period: int = 20,  # 周线均线周期
    stop_loss_pct: float = BACKTEST_STOP_LOSS_PCT,
    strategy_types: Optional[str] = None,
    match_mode: Literal["any", "all"] = "any",
):
    """
    API Endpoint for market scan (Asynchronous via Celery)
    """
    data_date = data_date.strip() if data_date and data_date.strip() else None
    selected_strategies = _selected_strategy_types(strategy_type, strategy_types)
    strategy_type = selected_strategies[0]
    strategy_types = ",".join(selected_strategies)
    require_live_snapshot = any(
        manual_scan_requires_live_snapshot(item, data_date)
        for item in selected_strategies
    )
    if require_live_snapshot:
        local_only = False
        logger.info(
            "[SCAN API] Intraday TV scan forced to live snapshot; historical fallback disabled."
        )
    logger.info(f"[SCAN API] Submitting task: strategy_types={strategy_types}, match_mode={match_mode}, pine_min_signals={pine_min_signals}, min_data_days={min_data_days}, weekly_ma={weekly_ma_period}, stop_loss_pct={stop_loss_pct}")
    
    # 异步发送任务给 Celery Queue
    task = run_market_scan_task.delay(
        threshold, vol_multiplier, rsi_min, use_macd_filter,
        use_bb_sqz, sqz_lookback, use_weekly, market_range,
        turnover_min, mkt_cap_min, use_rs_filter, local_only, data_date, strategy_type, pine_min_signals, min_data_days,
        weekly_ma_period, stop_loss_pct, require_live_snapshot, True, strategy_types, match_mode
    )
    
    # 无 Redis 的兜底处理：任务已同步完成，直接把结果交给前端 (前端的 fallback 机制接收)
    if celery_app.conf.task_always_eager and task.state == 'SUCCESS':
        task_result = task.result
        if isinstance(task_result, dict) and "results" in task_result:
            return {
                "status": "SUCCESS",
                "results": task_result["results"],
                "scan_meta": task_result.get("scan_meta") or {},
                "message": "同步扫描完成",
            }
        return {"status": "SUCCESS", "results": task_result, "message": "同步扫描完成"}

    return {"task_id": task.id, "status": "PENDING", "message": "扫描任务已提交队列"}


@router.get("/scan/tasks")
def list_scan_tasks(limit: int = 20):
    engine = get_db_engine()
    if not engine:
        return []
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT task_id, task_name, status, started_at, finished_at, duration_sec, error_message
            FROM task_run_audits
            WHERE task_name = 'scan.run_market_scan_task'
            ORDER BY COALESCE(started_at, finished_at) DESC
            LIMIT :limit
        """), {"limit": min(max(limit, 1), 100)}).mappings().all()
    return [dict(row) for row in rows]


@router.post("/scan/cancel/{task_id}")
def cancel_scan_task(task_id: str):
    celery_app.control.revoke(task_id, terminate=True)
    record_task_run(task_id, "scan.run_market_scan_task", "REVOKED", finished_at=datetime.now())
    return {"task_id": task_id, "status": "REVOKED", "message": "扫描任务已取消"}

@router.get("/scan/status/{task_id}")
def get_scan_status(task_id: str):
    """查询扫描任务状态和结果"""
    # get async result
    task = celery_app.AsyncResult(task_id)
    if task.state == 'SUCCESS':
        # Result is either list of items or serialized JSON
        result = task.result
        if isinstance(result, dict) and "results" in result:
            return {
                "task_id": task_id,
                "status": task.state,
                "results": result["results"],
                "scan_meta": result.get("scan_meta") or {},
                "message": "扫描完成",
            }
        return {"task_id": task_id, "status": task.state, "results": result, "message": "扫描完成"}
    elif task.state == 'FAILURE':
        return {"task_id": task_id, "status": task.state, "message": str(task.info)}
    else:
        return {"task_id": task_id, "status": task.state, "message": "任务正在执行中..."}



@router.get("/scan/history")
async def get_history_results(date: str):
    """获取指定日期的历史选股结果并计算至今表现"""
    results = get_scan_history_by_date(date)
    if not results:
        return []
    
    # Dashboard initialization must not block on a full-market network fetch.
    try:
        snapshot = get_cached_data("market_snapshot", 300)
        if snapshot is None:
            snapshot = get_stale_cache("market_snapshot")
        if snapshot is not None and not snapshot.empty:
            for r in results:
                code = r["代码"]
                hist_price = float(r["现价"])
                match = snapshot[snapshot['code'] == code]
                if not match.empty:
                    curr_price = float(match.iloc[0]['price'])
                    pl_pct = (curr_price - hist_price) / hist_price * 100 if hist_price > 0 else 0
                    r["最新价"] = curr_price
                    r["表现%"] = round(pl_pct, 2)
                else:
                    r["最新价"] = hist_price
                    r["表现%"] = 0.0
    except Exception as e:
        logger.warning(f"Failed to fetch performance for history: {e}")
        
    return results


@router.get("/scan/dates")
async def get_history_dates():
    """获取历史扫描日期列表"""
    return get_scan_dates()


@router.get("/scan/available-dates")
async def get_available_dates_api() -> Dict[str, Any]:
    """获取可用于选股的数据日期列表"""
    dates = get_available_dates()
    return {"dates": dates}


@router.post("/scan/optimize")
def optimize_parameters(data: dict) -> Dict[str, Any]:
    """
    参数寻优：对指定股票和策略跑参数正交组合回测，返回胜率矩阵

    Args:
        data: {
            "code": "000001",
            "strategy": "squeeze",
            "param_x": "rsi_min",
            "param_x_values": [50, 55, 60, 65],
            "param_y": "stop_loss_pct",
            "param_y_values": [-5, -8, -10, -12]
        }
    """
    from core.strategy import run_optimization_grid

    code = data.get("code", "")
    if not validate_stock_code(code):
        raise HTTPException(status_code=400, detail="Invalid stock code")

    strategy = data.get("strategy", "squeeze")

    engine = get_db_engine()
    if not engine:
        raise HTTPException(status_code=500, detail="Database unavailable")

    # Load historical data
    df = load_from_db(code, (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d"), engine)
    if df.empty:
        raise HTTPException(status_code=404, detail="No historical data for this stock")

    # Calculate indicators
    enable_pine = strategy in ["pine", "both", "tv_zp"]
    df = calculate_indicators(df, enable_pine_indicators=enable_pine)
    if enable_pine and 'RF_Upward' not in df.columns:
        df = calculate_pine_indicators(df)

    return run_optimization_grid(
        df,
        strategy_type=strategy,
        param_x=data.get("param_x", "rsi_min"),
        param_x_values=data.get("param_x_values"),
        param_y=data.get("param_y", "stop_loss_pct"),
        param_y_values=data.get("param_y_values"),
    )
