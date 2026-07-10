import asyncio
from datetime import datetime
from typing import Any, Dict

from sqlalchemy import text

from core.data import format_freshness, get_market_snapshot, is_snapshot_stale
from core.db import get_db_engine
from core.notifier import _bark_key, notifier


def _count_scalar(engine, sql: str) -> int:
    if engine is None:
        return 0
    try:
        with engine.connect() as conn:
            return int(conn.execute(text(sql)).scalar() or 0)
    except Exception:
        return 0


def build_bark_self_check(engine=None) -> Dict[str, Any]:
    engine = engine if engine is not None else get_db_engine()
    bark_configured = bool(_bark_key())

    snapshot_status = "error"
    snapshot_rows = 0
    snapshot_stale = True
    freshness_line = "行情快照不可用"
    snapshot_source = "未知源"
    try:
        snapshot = get_market_snapshot()
        if snapshot is not None and not snapshot.empty:
            snapshot_rows = int(len(snapshot))
            snapshot_stale = is_snapshot_stale(snapshot)
            snapshot_status = "stale" if snapshot_stale else "ok"
            freshness_line = format_freshness(snapshot)
            snapshot_source = (getattr(snapshot, "attrs", {}) or {}).get("source") or "未知源"
    except Exception as exc:
        freshness_line = f"行情快照异常: {str(exc)[:80]}"

    watch_count = _count_scalar(engine, "SELECT COUNT(*) FROM watchlist WHERE status = 'WATCHING'")
    triggered_count = _count_scalar(engine, "SELECT COUNT(*) FROM watchlist WHERE status = 'TRIGGERED'")
    open_real_count = _count_scalar(engine, "SELECT COUNT(*) FROM paper_trading WHERE status = 'OPEN' AND trade_mode = 'REAL'")
    open_sim_count = _count_scalar(engine, "SELECT COUNT(*) FROM paper_trading WHERE status = 'OPEN' AND COALESCE(trade_mode, 'SIMULATED') != 'REAL'")

    blocking = (not bark_configured) or snapshot_status != "ok"
    status = "ok" if not blocking else "warn"
    summary = {
        "bark_configured": bark_configured,
        "snapshot_status": snapshot_status,
        "snapshot_rows": snapshot_rows,
        "snapshot_stale": snapshot_stale,
        "snapshot_source": snapshot_source,
        "freshness_line": freshness_line,
        "watching_count": watch_count,
        "triggered_count": triggered_count,
        "open_real_count": open_real_count,
        "open_simulated_count": open_sim_count,
    }

    lines = [
        f"时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"Bark：{'已配置' if bark_configured else '未配置'}",
        f"行情：{freshness_line}",
        f"快照：{snapshot_rows} 行 | {snapshot_source}",
        f"观察池：WATCHING {watch_count} | TRIGGERED {triggered_count}",
        f"持仓：实盘 {open_real_count} | 模拟 {open_sim_count}",
        "结论：可进入盘中盯盘" if status == "ok" else "结论：先处理配置或行情快照问题",
    ]
    return {"status": status, "summary": summary, "body": "\n".join(lines)}


def send_bark_self_check() -> Dict[str, Any]:
    payload = build_bark_self_check()
    body = payload["body"]
    try:
        result = asyncio.run(notifier.send("Alpha Vision Bark 实盘自检", body, channels=["bark"]))
    except RuntimeError:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            result = loop.run_until_complete(notifier.send("Alpha Vision Bark 实盘自检", body, channels=["bark"]))
        finally:
            loop.close()
    except Exception as exc:
        result = {"bark": False, "error": str(exc)}
    return {**payload, "notification": result}
