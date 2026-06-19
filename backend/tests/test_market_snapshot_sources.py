import os
import sys
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core import data
from core.data import CACHE


def setup_function():
    CACHE.clear()


def _tencent_snapshot_line(code: str, name: str) -> str:
    parts = [""] * 50
    parts[1] = name
    parts[2] = code
    parts[3] = "10.50"
    parts[5] = "10.10"
    parts[6] = "1234"
    parts[32] = "5.00"
    parts[33] = "10.80"
    parts[34] = "10.00"
    parts[38] = "2.50"
    parts[39] = "8.90"
    parts[45] = "2500.00"
    return 'v_sz{}="{}";'.format(code, "~".join(parts))


def test_market_snapshot_prefers_tencent_direct_source(monkeypatch):
    response = MagicMock()
    response.status_code = 200
    response.text = "\n".join([
        _tencent_snapshot_line("000001", "平安银行"),
        _tencent_snapshot_line("000002", "万科A"),
    ])

    monkeypatch.setattr(data, "get_stock_basic_map", lambda: {
        "000001": {"name": "平安银行"},
        "000002": {"name": "万科A"},
    })
    monkeypatch.setattr(data.requests, "get", lambda *args, **kwargs: response)
    monkeypatch.setattr(
        data.ak,
        "stock_zh_a_spot_em",
        lambda: (_ for _ in ()).throw(AssertionError("Eastmoney akshare should not run")),
    )
    monkeypatch.setattr(
        data.ak,
        "stock_zh_a_spot",
        lambda: (_ for _ in ()).throw(AssertionError("Sina akshare should not run")),
    )

    snapshot = data.get_market_snapshot()

    assert list(snapshot["code"]) == ["000001", "000002"]
    assert snapshot.iloc[0]["price"] == 10.5
    assert snapshot.iloc[0]["pe"] == 8.9
    assert snapshot.iloc[0]["mkt_cap"] == 250000000000.0
