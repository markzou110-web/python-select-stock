import os
import sys

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from routers.review import _build_real_trade_execution_review


def test_execution_review_groups_waiting_time():
    real = pd.DataFrame([{
        "entry_date": "2026-07-03",
        "entry_signal_date": "2026-07-01",
        "planned_entry_price": 10.0,
        "actual_entry_price": 10.1,
        "entry_slippage_pct": None,
        "plan_adherence": "COMPLIANT",
        "entry_source": "bark",
        "ret_5d": 2.0,
    }])

    report = _build_real_trade_execution_review(real, pd.DataFrame())

    assert report["delayed_trades_2d_plus"] == 1
    assert report["by_entry_delay_bucket"][0]["entry_delay_bucket"] == "延迟2日以上"
