"""Coverage audit for the point-in-time research warehouse."""
from typing import Any, Dict

from sqlalchemy import text


def build_point_in_time_coverage(engine) -> Dict[str, Any]:
    if engine is None:
        return {"status": "error", "summary": {}, "gaps": ["数据库不可用"]}
    with engine.connect() as conn:
        snapshot = conn.execute(text("""
            SELECT COUNT(DISTINCT dataset_version) versions, COUNT(DISTINCT CAST(as_of AS date)) dates,
                   COUNT(*) rows, MIN(as_of) first_as_of, MAX(as_of) last_as_of
            FROM point_in_time_stock_snapshots
        """)).mappings().first()
        audits = conn.execute(text("""
            SELECT COUNT(DISTINCT scan_date) dates, MIN(scan_date) first_date, MAX(scan_date) last_date,
                   SUM(CASE WHEN research_only=1 THEN 1 ELSE 0 END) research_only_scans
            FROM scan_audit_log
        """)).mappings().first()
        events = conn.execute(text("""
            SELECT COUNT(*) total, SUM(CASE WHEN verified=1 THEN 1 ELSE 0 END) verified,
                   MIN(published_at) first_event, MAX(published_at) last_event
            FROM event_catalysts
        """)).mappings().first()
    snapshot = dict(snapshot or {})
    audits = dict(audits or {})
    events = dict(events or {})
    dates = int(snapshot.get("dates") or 0)
    gaps = []
    if dates < 250:
        gaps.append(f"点时股票池仅覆盖{dates}个日期，尚不足1个交易年")
    if int(events.get("verified") or 0) < 30:
        gaps.append("官方验证事件样本不足30条")
    return {
        "status": "ok" if not gaps else "collecting",
        "summary": {"snapshots": snapshot, "scan_audits": audits, "events": events},
        "gaps": gaps,
        "target": {"trading_dates": 750, "years": 3, "verified_events": 100},
        "policy": "只持续采集真实点时数据，不用事后字段回填历史",
    }
