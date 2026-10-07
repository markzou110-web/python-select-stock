import os
import sys
from datetime import datetime
from unittest.mock import MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core import data
import core.data as data
from core.data import CACHE
import core.direct_sources as ds


def setup_function():
    CACHE.clear()


def _allow_small_snapshot_samples(monkeypatch):
    original = data.resilient_fetch

    def fetch_without_row_floor(fetch_funcs, timeout=8, label="data", min_rows=0):
        return original(fetch_funcs, timeout=timeout, label=label, min_rows=0)

    monkeypatch.setattr(data, "resilient_fetch", fetch_without_row_floor)


def _tencent_snapshot_line(code: str, name: str) -> str:
    parts = [""] * 53
    parts[1] = name
    parts[3] = "10.50"
    parts[4] = "10.00"
    parts[5] = "10.10"
    parts[6] = "1234"
    parts[31] = "0.50"
    parts[32] = "5.00"
    parts[33] = "10.80"
    parts[34] = "10.00"
    parts[37] = "1300.00"
    parts[38] = "2.50"
    parts[39] = "8.90"
    parts[43] = "8.00"
    parts[44] = "2500.00"
    parts[45] = "2000.00"
    parts[46] = "1.25"
    parts[47] = "11.00"
    parts[48] = "9.00"
    parts[49] = "1.80"
    parts[52] = "9.10"
    return 'v_sz{}="{}";'.format(code, "~".join(parts))


def test_market_snapshot_prefers_tencent_direct_source(monkeypatch):
    _allow_small_snapshot_samples(monkeypatch)
    response = MagicMock()
    response.status_code = 200
    response.text = "\n".join([
        _tencent_snapshot_line("000001", "平安银行"),
        _tencent_snapshot_line("000002", "万科A"),
    ])

    monkeypatch.setattr(data, "get_stock_basic_map", lambda: {
        "000001": "银行",
        "000002": "房地产",
    })
    monkeypatch.setattr(data.requests, "get", lambda *args, **kwargs: response)
    monkeypatch.setattr(ds.requests, "get", lambda *args, **kwargs: response)
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
    assert snapshot.iloc[0]["float_mkt_cap"] == 200000000000.0
    assert snapshot.iloc[0]["pb"] == 1.25
    assert snapshot.iloc[0]["limit_up"] == 11.0
    assert snapshot.iloc[0]["limit_down"] == 9.0
    assert snapshot.iloc[0]["vol_ratio"] == 1.8
    assert snapshot.iloc[0]["industry"] == "银行"


def test_market_snapshot_force_refresh_skips_fresh_cache(monkeypatch):
    import pandas as pd

    cached_df = pd.DataFrame({"code": ["000001"], "name": ["旧"], "price": [9.0]})
    fresh_df = pd.DataFrame({"code": ["000001"], "name": ["新"], "price": [10.0]})
    data.set_cached_data("market_snapshot", cached_df)
    monkeypatch.setattr(data, "resilient_fetch", lambda *args, **kwargs: fresh_df)

    snapshot = data.get_market_snapshot(force_refresh=True)

    assert snapshot.iloc[0]["name"] == "新"
    assert snapshot.iloc[0]["price"] == 10.0


# ─────────────────────────────────────────────────────────────────────────────
# P0/P1/P2 新增测试：stale 兜底 / attrs 元数据 / 东财直连 / 新浪直连
# ─────────────────────────────────────────────────────────────────────────────

def test_market_snapshot_injects_attrs_metadata(monkeypatch):
    """P1：成功抓取后，DataFrame 应携带 fetched_at / source 元数据。"""
    import pandas as pd
    fake_df = pd.DataFrame({"code": ["000001"], "name": ["X"], "price": [10.0]})
    _allow_small_snapshot_samples(monkeypatch)

    monkeypatch.setattr(data, "get_stock_basic_map", lambda: {"000001": {"name": "X"}})
    monkeypatch.setattr(data.ak, "stock_zh_a_spot_em", lambda: fake_df)
    monkeypatch.setattr(
        data.ak, "stock_zh_a_spot",
        lambda: (_ for _ in ()).throw(AssertionError("sina akshare disabled")),
    )
    monkeypatch.setattr(data.requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("tencent disabled")))
    # 禁用东财/新浪直连，强制走 akshare 东财主源
    import core.direct_sources as ds
    monkeypatch.setattr(ds, "snapshot_from_eastmoney", lambda: (_ for _ in ()).throw(AssertionError("em direct disabled")))
    monkeypatch.setattr(ds, "snapshot_from_sina", lambda *a, **k: (_ for _ in ()).throw(AssertionError("sina direct disabled")))

    snapshot = data.get_market_snapshot()
    assert "fetched_at" in snapshot.attrs
    assert snapshot.attrs["data_date"] == data._expected_snapshot_date()
    assert snapshot.attrs["source"] == "akshare东财"


def test_market_snapshot_falls_back_to_stale_cache(monkeypatch):
    """P0：所有实时源失败时，应退回 stale 缓存而非空 DF。"""
    import pandas as pd
    import time as _time
    from core.risk_constants import STALE_SNAPSHOT_WARN

    # 预置一份 stale 缓存（时间戳设为 2 分钟前，绕过 60s TTL，确保走全失败→stale 分支）
    stale_df = pd.DataFrame({"code": ["000001"], "name": ["X"], "price": [9.0]})
    fixed_date = "2026-10-06"
    monkeypatch.setattr(data, "_expected_snapshot_date", lambda: fixed_date)
    stale_df.attrs = {
        "fetched_at": data.datetime.now() - data.timedelta(minutes=2),
        "data_date": fixed_date,
        "source": "腾讯",
    }
    with data._cache_lock:
        data.CACHE["market_snapshot"] = (stale_df, _time.time() - 120)

    # 所有源全部失败
    monkeypatch.setattr(data, "get_stock_basic_map", lambda: {"000001": {"name": "X"}})
    monkeypatch.setattr(data.ak, "stock_zh_a_spot_em", lambda: (_ for _ in ()).throw(Exception("em banned")))
    monkeypatch.setattr(data.ak, "stock_zh_a_spot", lambda: (_ for _ in ()).throw(Exception("sina banned")))
    monkeypatch.setattr(data.requests, "get", lambda *a, **k: (_ for _ in ()).throw(Exception("tencent banned")))
    import core.direct_sources as ds
    monkeypatch.setattr(ds, "snapshot_from_eastmoney", lambda: (_ for _ in ()).throw(Exception("em direct banned")))
    monkeypatch.setattr(ds, "snapshot_from_sina", lambda *a, **k: (_ for _ in ()).throw(Exception("sina direct banned")))
    monkeypatch.setattr("core.db.load_recent_point_in_time_snapshot", lambda max_age_minutes: pd.DataFrame())

    snapshot = data.get_market_snapshot()
    assert not snapshot.empty
    assert snapshot.attrs["source"] == STALE_SNAPSHOT_WARN


def test_market_snapshot_rejects_cross_day_stale_cache(monkeypatch):
    import pandas as pd
    import time as _time

    stale_df = pd.DataFrame({"code": ["000001"], "name": ["旧行情"], "price": [9.0]})
    stale_df.attrs = {
        "fetched_at": data.datetime(2026, 7, 20, 15, 0),
        "data_date": "2026-07-20",
        "source": "腾讯",
    }
    with data._cache_lock:
        data.CACHE["market_snapshot"] = (stale_df, _time.time() - 120)
    monkeypatch.setattr(data, "resilient_fetch", lambda *args, **kwargs: None)
    monkeypatch.setattr(data, "_expected_snapshot_date", lambda now=None: "2026-07-21")
    monkeypatch.setattr("core.db.load_recent_point_in_time_snapshot", lambda max_age_minutes: pd.DataFrame())

    snapshot = data.get_market_snapshot(force_refresh=True)

    assert snapshot.empty


def test_market_snapshot_falls_back_to_recent_persisted_snapshot(monkeypatch):
    """Different Celery workers can reuse a fresh auditable snapshot from the database."""
    import pandas as pd

    persisted = pd.DataFrame({
        "code": ["000001"], "name": ["X"], "price": [9.5], "pct_chg": [1.2],
    })
    persisted.attrs = {
        "fetched_at": data.datetime.now(),
        "data_date": data._expected_snapshot_date(),
        "source": "持久化短时快照·腾讯",
    }
    monkeypatch.setattr(data, "resilient_fetch", lambda *args, **kwargs: None)
    monkeypatch.setattr("core.db.load_recent_point_in_time_snapshot", lambda max_age_minutes: persisted)

    snapshot = data.get_market_snapshot(force_refresh=True)

    assert snapshot.iloc[0]["price"] == 9.5
    assert snapshot.attrs["source"].startswith("持久化短时快照")


def test_recent_persisted_snapshot_wins_over_older_in_process_cache(monkeypatch):
    import pandas as pd
    import time as _time

    stale = pd.DataFrame({"code": ["000001"], "name": ["旧"], "price": [9.0]})
    stale.attrs = {
        "fetched_at": data.datetime.now() - data.timedelta(minutes=10),
        "data_date": data._expected_snapshot_date(),
        "source": "腾讯",
    }
    with data._cache_lock:
        data.CACHE["market_snapshot"] = (stale, _time.time() - 120)
    persisted = pd.DataFrame({"code": ["000001"], "name": ["新"], "price": [10.0], "pct_chg": [1.0]})
    persisted.attrs = {
        "fetched_at": data.datetime.now(),
        "data_date": data._expected_snapshot_date(),
        "source": "持久化短时快照·腾讯",
    }
    monkeypatch.setattr(data, "resilient_fetch", lambda *args, **kwargs: None)
    monkeypatch.setattr("core.db.load_recent_point_in_time_snapshot", lambda max_age_minutes: persisted)

    snapshot = data.get_market_snapshot(force_refresh=True)

    assert snapshot.iloc[0]["name"] == "新"
    assert snapshot.attrs["source"].startswith("持久化短时快照")


def test_market_snapshot_uses_eastmoney_direct(monkeypatch):
    """P2：akshare 东财失败时，应降级到东财直连源。"""
    import pandas as pd
    _allow_small_snapshot_samples(monkeypatch)
    em_direct_df = pd.DataFrame({
        "code": ["600519"], "name": ["贵州茅台"], "price": [1500.0],
        "open": [1490.0], "high": [1510.0], "low": [1485.0], "pct_chg": [1.2],
        "vol": [30000.0], "turnover": [0.5], "mkt_cap": [1.8e12], "pe": [25.0],
    })

    monkeypatch.setattr(data, "get_stock_basic_map", lambda: {"600519": {"name": "贵州茅台"}})
    monkeypatch.setattr(data.ak, "stock_zh_a_spot_em", lambda: (_ for _ in ()).throw(Exception("akshare em banned")))
    monkeypatch.setattr(
        data.ak, "stock_zh_a_spot",
        lambda: (_ for _ in ()).throw(AssertionError("sina akshare disabled")),
    )
    monkeypatch.setattr(data.requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("tencent disabled")))
    import core.direct_sources as ds
    monkeypatch.setattr(ds, "snapshot_from_eastmoney", lambda: em_direct_df)
    monkeypatch.setattr(ds, "snapshot_from_sina", lambda *a, **k: (_ for _ in ()).throw(AssertionError("sina direct disabled")))

    snapshot = data.get_market_snapshot()
    assert snapshot.iloc[0]["code"] == "600519"
    assert snapshot.iloc[0]["price"] == 1500.0
    assert snapshot.attrs["source"] == "东财直连"


def test_snapshot_from_sina_parses_hq_response(monkeypatch):
    """P2：新浪直连源应正确解析 hq.sinajs.cn 的 GBK 响应。"""
    import core.direct_sources as ds

    # 模拟新浪返回：var hq_str_sh600519="贵州茅台,昨收,今开,现价,最高,最低,?,?,成交量(股),..."
    # 字段索引：0=名称 1=今开 2=昨收 3=现价 4=最高 5=最低 ... 8=成交量
    fake_text = (
        'var hq_str_sh600519="贵州茅台,1490.00,1485.00,1500.00,1510.00,1485.00,'
        'longname,10:30:00,3000000,4500000000,...";'
    )
    resp = MagicMock(status_code=200, text=fake_text)
    resp.encoding = "gbk"
    monkeypatch.setattr(ds.requests, "get", lambda *a, **k: resp)

    df = ds.snapshot_from_sina(["600519"])
    assert df.iloc[0]["code"] == "600519"
    assert df.iloc[0]["name"] == "贵州茅台"
    assert df.iloc[0]["price"] == 1500.0
    # 昨收 1485，现价 1500 → 涨幅约 1.01%
    assert abs(df.iloc[0]["pct_chg"] - round((1500 - 1485) / 1485 * 100, 2)) < 0.01
    # 成交量 3000000 股 → 30000 手
    assert df.iloc[0]["vol"] == 30000.0


def test_format_freshness_handles_stale_and_fresh(monkeypatch):
    """P1：format_freshness 对实时/滞后/stale/无attrs 四种情况的标注。"""
    import pandas as pd
    from datetime import datetime, timedelta
    from core.data import format_freshness
    from core.risk_constants import STALE_SNAPSHOT_WARN

    now = datetime.now()
    fresh = pd.DataFrame({"code": ["X"]}); fresh.attrs = {"fetched_at": now, "source": "akshare东财"}
    lagged = pd.DataFrame({"code": ["X"]}); lagged.attrs = {"fetched_at": now - timedelta(minutes=10), "source": "akshare东财"}
    stale = pd.DataFrame({"code": ["X"]}); stale.attrs = {"fetched_at": now - timedelta(minutes=2), "source": STALE_SNAPSHOT_WARN}
    noattrs = pd.DataFrame({"code": ["X"]})

    assert "⚠️" not in format_freshness(fresh)
    assert "实时" in format_freshness(fresh)
    assert "⚠️" in format_freshness(lagged)
    assert "⚠️" in format_freshness(stale)
    assert "未知" in format_freshness(noattrs)


def test_market_regime_uses_realtime_index_pct_for_wording(monkeypatch):
    """盘中日线源可能停在上一交易日，Bark 大盘涨跌应优先使用实时指数快照。"""
    import pandas as pd

    hist = pd.DataFrame({"close": [4120.281, 4027.265]})

    def fake_resilient_fetch(_funcs, timeout=8, label="data", min_rows=0):
        if label in {"regime_上证", "regime_创业"}:
            return hist
        return None

    monkeypatch.setattr(data, "get_index_data", lambda: {
        "上证": {"price": 4073.90, "pct": 1.16},
        "创业板": {"price": 4216.70, "pct": 0.54},
    })
    monkeypatch.setattr(data, "resilient_fetch", fake_resilient_fetch)

    regime = data.get_market_regime()

    assert regime["indices"]["上证"]["close"] == 4073.9
    assert regime["indices"]["上证"]["chg_pct"] == 1.16
    assert regime["indices"]["创业"]["close"] == 4216.7
    assert regime["indices"]["创业"]["chg_pct"] == 0.54
    assert regime["indices"]["创业"]["trend_label"] in {"站上EMA20", "低于EMA20"}
    assert isinstance(regime["indices"]["创业"]["ema20_gap_pct"], float)
    assert regime["trend_basis"] == "双指数相对EMA20"


def test_market_baseline_label_distinguishes_preopen_intraday_and_close(monkeypatch):
    monkeypatch.setattr("core.trading_calendar.is_a_share_trading_day", lambda _now: True)

    assert data._market_baseline_label(datetime(2026, 9, 8, 8, 30)) == "上一交易日收盘状态"
    assert data._market_baseline_label(datetime(2026, 9, 8, 10, 0)) == "盘中趋势参考"
    assert data._market_baseline_label(datetime(2026, 9, 8, 15, 30)) == "今日收盘状态"


def test_market_regime_route_uses_dashboard_market_pulse(monkeypatch):
    from routers import market

    pulse = {
        "status": "DEFENSIVE",
        "desc": "减仓观望：市场进入震荡/分化期",
        "indices": {"上证": {"trend": "BULL"}, "创业": {"trend": "BEAR"}},
        "limit_down_count": 2,
        "trend_basis": "双指数相对EMA20",
    }
    monkeypatch.setattr("core.data.get_market_regime", lambda: pulse)

    result = market.get_market_regime("pine")

    assert result["regime"]["regime"] == "volatile"
    assert result["regime"]["source"] == "market_pulse_dual_index_ema20"
    assert result["market_pulse"] == pulse
    assert result["recommended_params"]["pine_min_signals"] == 3
