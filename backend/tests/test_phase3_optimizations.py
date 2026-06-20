"""Tests for Phase 3 optimizations: #7 自适应阈值 / #10 Sentinel 高频风控 / #12 日内亏损熔断."""
import os
import sys
from datetime import datetime, timedelta

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.models import Base
from core.market_regime import map_status_to_regime, get_adaptive_params


# ────────────────────────────────────────────────────────────────────
# #7 自适应阈值：status→regime 映射 + get_adaptive_params 按状态调参
# ────────────────────────────────────────────────────────────────────

def test_status_mapping_offensive_to_bull():
    assert map_status_to_regime("OFFENSIVE") == "bull"
    assert map_status_to_regime("offensive") == "bull"  # 大小写不敏感


def test_status_mapping_critical_to_bear():
    assert map_status_to_regime("CRITICAL") == "bear"


def test_status_mapping_defensive_to_volatile():
    assert map_status_to_regime("DEFENSIVE") == "volatile"


def test_status_mapping_unknown_defaults_volatile():
    assert map_status_to_regime("UNKNOWN") == "volatile"
    assert map_status_to_regime("") == "volatile"
    assert map_status_to_regime(None) == "volatile"


def test_status_mapping_idempotent_for_regime_values():
    """已是 bull/bear/volatile 时原样返回（幂等）。"""
    assert map_status_to_regime("bull") == "bull"
    assert map_status_to_regime("bear") == "bear"
    assert map_status_to_regime("volatile") == "volatile"


def test_adaptive_params_bear_strictest_threshold():
    """bear 市的 threshold 应最严（最小值），bear < volatile < bull。"""
    bear = get_adaptive_params("bear", "squeeze")
    volatile = get_adaptive_params("volatile", "squeeze")
    bull = get_adaptive_params("bull", "squeeze")
    # bear 收紧（threshold 更小，粘合更松才入选），bull 放宽（threshold 更大）
    assert bear["threshold"] <= volatile["threshold"] <= bull["threshold"]


def test_adaptive_params_offensive_status_works():
    """传入 core.data 的 status 词表也能正确解析（经 map_status_to_regime）。"""
    params = get_adaptive_params("OFFENSIVE", "squeeze")
    assert "threshold" in params and "vol_multiplier" in params
    # OFFENSIVE→bull，bull 的 squeeze 参数应生效
    assert params == get_adaptive_params("bull", "squeeze")


def test_adaptive_params_bear_tightens_stop_loss():
    """bear 市止损更紧（离场更快）：bear 止损幅度的绝对值 <= bull。

    REGIME_PARAMS 设计：bear stop=-5（更快止损）, bull stop=-6（给更多空间）。
    即熊市止损线更高（更接近入场价），实现"快进快出"。
    """
    bear_stop = abs(get_adaptive_params("bear", "squeeze")["stop_loss_pct"])
    bull_stop = abs(get_adaptive_params("bull", "squeeze")["stop_loss_pct"])
    # bear 止损幅度（绝对值）<= bull，即熊市更快止损（更紧）
    assert bear_stop <= bull_stop


# ────────────────────────────────────────────────────────────────────
# #10 Sentinel 风控独立高频检查
# ────────────────────────────────────────────────────────────────────

def test_wind_control_interval_default_30min():
    """Sentinel 默认风控间隔 30 分钟。"""
    from core.sentinel import IntradaySentinel
    s = IntradaySentinel()
    assert s.wind_control_interval_minutes == 30
    assert s.last_wind_control_dt is None


def test_should_run_wind_control_first_time_in_session(monkeypatch):
    """交易时段内首次（last_wind_control_dt=None）应触发。"""
    from core import sentinel as sentinel_mod
    s = sentinel_mod.IntradaySentinel()
    # 交易时段内（10:30）
    now = datetime(2026, 6, 16, 10, 30)
    monkeypatch.setattr(sentinel_mod, "is_a_share_intraday_session", lambda dt: True)
    assert s._should_run_wind_control(now) is True


def test_should_run_wind_control_throttled(monkeypatch):
    """距上次风控不足间隔时间 → 不触发。"""
    from core import sentinel as sentinel_mod
    s = sentinel_mod.IntradaySentinel()
    s.last_wind_control_dt = datetime(2026, 6, 16, 10, 30)
    monkeypatch.setattr(sentinel_mod, "is_a_share_intraday_session", lambda dt: True)
    # 10:50，距上次 20 分钟 < 30 分钟 → 不触发
    assert s._should_run_wind_control(datetime(2026, 6, 16, 10, 50)) is False
    # 11:01，距上次 31 分钟 >= 30 → 触发
    assert s._should_run_wind_control(datetime(2026, 6, 16, 11, 1)) is True


def test_should_run_wind_control_outside_session(monkeypatch):
    """非交易时段不触发风控。"""
    from core import sentinel as sentinel_mod
    s = sentinel_mod.IntradaySentinel()
    monkeypatch.setattr(sentinel_mod, "is_a_share_intraday_session", lambda dt: False)
    assert s._should_run_wind_control(datetime(2026, 6, 16, 22, 0)) is False


# ────────────────────────────────────────────────────────────────────
# #12 日内亏损熔断
# ────────────────────────────────────────────────────────────────────

def _setup_paper_engine():
    """内存 SQLite，建表，供日内亏损熔断测试。"""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    return engine


def _insert_closed_trade(engine, entry, close, shares=100, close_date=None):
    """写入一条 CLOSED 模拟交易（close_date 默认今天）。"""
    d = close_date or datetime.now().strftime("%Y-%m-%d")
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO paper_trading (code, name, entry_price, close_price, close_date,
                                       status, shares, trade_mode)
            VALUES (:code, :name, :e, :c, :d, 'CLOSED', :s, 'SIMULATED')
        """), {"code": "000001", "name": "测试", "e": entry, "c": close, "d": d, "s": shares})


def test_daily_loss_no_closes_is_ok():
    """今日无平仓 → status=ok, 不熔断。"""
    from core.portfolio_risk import evaluate_daily_loss_circuit_breaker
    engine = _setup_paper_engine()
    result = evaluate_daily_loss_circuit_breaker(engine)
    assert result["status"] == "ok"
    assert result["halted"] is False
    assert result["daily_loss_pct"] == 0.0


def test_daily_loss_within_limit_is_ok():
    """日内亏损在熔断线内 → 不熔断。"""
    from core.portfolio_risk import evaluate_daily_loss_circuit_breaker
    engine = _setup_paper_engine()
    # entry=10, close=9.8 → -2%，在 -5% 熔断线内
    _insert_closed_trade(engine, entry=10.0, close=9.8, shares=100)
    result = evaluate_daily_loss_circuit_breaker(engine)
    assert result["status"] == "ok"
    assert result["halted"] is False


def test_daily_loss_triggers_halt():
    """日内亏损超过熔断线 → status=halt, halted=True。"""
    from core.portfolio_risk import evaluate_daily_loss_circuit_breaker
    engine = _setup_paper_engine()
    # entry=10, close=9.2 → -8%，超过 -5% 熔断线
    _insert_closed_trade(engine, entry=10.0, close=9.2, shares=100)
    result = evaluate_daily_loss_circuit_breaker(engine)
    assert result["status"] == "halt"
    assert result["halted"] is True
    assert result["daily_loss_pct"] <= -5.0


def test_daily_loss_profit_never_halts():
    """日内盈利 → 永不熔断。"""
    from core.portfolio_risk import evaluate_daily_loss_circuit_breaker
    engine = _setup_paper_engine()
    # entry=10, close=11 → +10%
    _insert_closed_trade(engine, entry=10.0, close=11.0, shares=100)
    result = evaluate_daily_loss_circuit_breaker(engine)
    assert result["status"] == "ok"
    assert result["halted"] is False
    assert result["daily_loss_pct"] > 0


def test_daily_loss_custom_limit():
    """自定义更紧的熔断线（daily_loss_limit_pct=3）→ -4% 也触发。"""
    from core.portfolio_risk import evaluate_daily_loss_circuit_breaker
    engine = _setup_paper_engine()
    _insert_closed_trade(engine, entry=10.0, close=9.6, shares=100)  # -4%
    result = evaluate_daily_loss_circuit_breaker(engine, budget={"daily_loss_limit_pct": 3.0})
    assert result["status"] == "halt"
    assert result["halted"] is True


# ---------------------------------------------------------------------------
# 改动 B5：组合浮亏熔断（未实现亏损检测，与已实现亏损熔断互补）
# ---------------------------------------------------------------------------

def _insert_open_trade(engine, code="000001", entry=10.0, shares=1000):
    """写入一条 OPEN 持仓（供浮亏熔断测试用）。"""
    d = datetime.now().strftime("%Y-%m-%d")
    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO paper_trading (code, name, entry_price, shares, status, trade_mode, entry_date)
            VALUES (:code, '测试', :e, :s, 'OPEN', 'SIMULATED', :d)
        """), {"code": code, "e": entry, "s": shares, "d": d})


def test_floating_loss_triggers_on_deep_drawdown():
    """B5：组合浮亏超 -5% 时熔断（halt），阻止加仓。"""
    import pandas as pd
    from core.portfolio_risk import evaluate_floating_loss_circuit_breaker

    engine = _setup_paper_engine()
    for code in ["000001", "000002", "000003"]:
        _insert_open_trade(engine, code=code, entry=10.0, shares=1000)
    # 全部 -8% → 组合浮亏 -8% > 5% 熔断线
    snap = pd.DataFrame([
        {"code": "000001", "price": 9.2},
        {"code": "000002", "price": 9.2},
        {"code": "000003", "price": 9.2},
    ])
    result = evaluate_floating_loss_circuit_breaker(engine, snap)
    assert result["status"] == "halt"
    assert result["halted"] is True
    assert result["floating_loss_pct"] < -5.0


def test_floating_loss_ok_within_limit():
    """B5：浮亏在熔断线内（-2%）不触发。"""
    import pandas as pd
    from core.portfolio_risk import evaluate_floating_loss_circuit_breaker

    engine = _setup_paper_engine()
    for code in ["000001", "000002"]:
        _insert_open_trade(engine, code=code, entry=10.0, shares=1000)
    snap = pd.DataFrame([
        {"code": "000001", "price": 9.8},  # -2%
        {"code": "000002", "price": 9.8},
    ])
    result = evaluate_floating_loss_circuit_breaker(engine, snap)
    assert result["status"] == "ok"
    assert result["halted"] is False


def test_floating_loss_profit_never_triggers():
    """B5：盈利时永远不触发。"""
    import pandas as pd
    from core.portfolio_risk import evaluate_floating_loss_circuit_breaker

    engine = _setup_paper_engine()
    _insert_open_trade(engine, code="000001", entry=10.0, shares=1000)
    snap = pd.DataFrame([{"code": "000001", "price": 11.0}])  # +10%
    result = evaluate_floating_loss_circuit_breaker(engine, snap)
    assert result["status"] == "ok"
    assert result["floating_loss_pct"] > 0


def test_floating_loss_no_snapshot_returns_ok():
    """B5：无快照数据时不阻断（返回 ok）。"""
    from core.portfolio_risk import evaluate_floating_loss_circuit_breaker

    engine = _setup_paper_engine()
    result = evaluate_floating_loss_circuit_breaker(engine, snapshot=None)
    assert result["status"] == "ok"
    assert result["halted"] is False
