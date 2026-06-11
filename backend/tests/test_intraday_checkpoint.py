import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core import tasks


def test_after_close_review_pushes_next_day_watchlist(monkeypatch):
    calls = []

    monkeypatch.setattr(tasks, "is_a_share_intraday_session", lambda now=None: True)
    monkeypatch.setattr("routers.paper_trade.check_operation_triggers", lambda notify=True, trade_mode="REAL": {"alerts": []})
    monkeypatch.setattr("routers.watchlist.refresh_watchlist_decisions", lambda: None)
    monkeypatch.setattr("routers.watchlist.auto_prune_watchlist", lambda max_watch_days=15: {"updated": 0})
    monkeypatch.setattr("routers.watchlist.check_watchlist_triggers", lambda notify=True: {"count": 0})
    monkeypatch.setattr("core.db.get_scan_history_by_date", lambda date_str, engine=None: [{"代码": "300145", "名称": "南方泵业", "sop_grade": "M", "trade_bucket": "OBSERVE"}])
    monkeypatch.setattr(
        "core.sentinel.send_after_close_watchlist",
        lambda scan_results, scan_date=None, now=None, limit=5: calls.append((scan_date, len(scan_results), now)) or "body",
    )

    result = tasks.intraday_monitor_checkpoint(slot="after_close_review")

    assert result["slot"] == "after_close_review"
    assert result["next_day_push"] == 1
    assert calls and calls[0][0] == datetime.now().strftime("%Y-%m-%d")
    assert calls[0][1] == 1
