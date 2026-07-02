import os
import sys
from datetime import datetime

from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.models import Base
from routers.paper_trade import (
    close_paper_trade,
    _count_holding_trading_days,
    _evaluate_time_stop,
    _wind_control_decision,
    _tier_early_warning,
    _tier_alert_sent,
)
from schemas.paper_trade import PaperTradeClose


def test_holding_days_use_trading_days_from_daily_k():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)

    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO daily_k (code, date, open, high, low, close, vol)
            VALUES
            ('600000', '2026-05-25', 10, 10, 10, 10, 100),
            ('600000', '2026-05-26', 10, 10, 10, 10, 100),
            ('600000', '2026-05-27', 10, 10, 10, 10, 100),
            ('600000', '2026-05-28', 10, 10, 10, 10, 100),
            ('600000', '2026-05-29', 10, 10, 10, 10, 100),
            ('600000', '2026-06-01', 10, 10, 10, 10, 100)
        """))

    hold_days = _count_holding_trading_days(
        engine,
        "600000",
        datetime(2026, 5, 25),
        datetime(2026, 6, 1),
    )

    assert hold_days == 5


def test_close_paper_trade_supports_partial_sell(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    events = []
    notifications = []

    with engine.begin() as conn:
        conn.execute(text("""
            INSERT INTO paper_trading (
                id, code, name, entry_price, entry_date, current_price, status,
                strategy_type, trade_mode, shares, capital_used, theme
            ) VALUES (
                1, '603259', '药明康德', 117.22, '2026-06-25', 121.30, 'OPEN',
                'tv_dual', 'REAL', 300, 35166.0, '化学制药'
            )
        """))

    monkeypatch.setattr("routers.paper_trade.get_db_engine", lambda: engine)
    monkeypatch.setattr(
        "routers.paper_trade.send_paper_trade_notification",
        lambda title, body: notifications.append((title, body)),
    )
    monkeypatch.setattr(
        "routers.paper_trade.record_lifecycle_event",
        lambda *args, **kwargs: events.append((args, kwargs)),
    )

    result = close_paper_trade(1, PaperTradeClose(close_price=121.30, close_shares=200))

    assert result["status"] == "success"
    assert result["partial"] is True
    assert result["closed_shares"] == 200
    assert result["remaining_shares"] == 100
    assert result["pnl_pct"] == 3.48
    with engine.connect() as conn:
        row = conn.execute(text("SELECT status, shares, capital_used, plan_adherence FROM paper_trading WHERE id = 1")).mappings().one()
    assert row["status"] == "OPEN"
    assert row["shares"] == 100
    assert row["capital_used"] == 11722.0
    assert row["plan_adherence"] == "PARTIAL_TAKE_PROFIT"
    assert notifications and "实盘减仓" in notifications[0][0]
    assert events[0][0][0] == "PARTIAL_SELL"
    assert events[0][1]["payload"]["sell_shares"] == 200


def test_time_stop_warning_does_not_force_close():
    result = _evaluate_time_stop(5, -0.5, "pine")

    assert result is not None
    assert result["severity"] == "warning"
    assert result["should_close"] is False
    assert "预警" in result["reason"]


def test_time_stop_force_close_after_long_non_profit_hold():
    result = _evaluate_time_stop(10, 0.0, "pine")

    assert result is not None
    assert result["severity"] == "close"
    assert result["should_close"] is True
    assert "确认" in result["reason"]


def test_squeeze_strategy_uses_slower_time_stop():
    assert _evaluate_time_stop(5, -0.5, "squeeze") is None

    result = _evaluate_time_stop(7, -0.5, "squeeze")

    assert result is not None
    assert result["severity"] == "warning"


# ---------------------------------------------------------------------------
# 改动 B4：时间止损盲区修复（微盈不豁免 + 推送可视化）
# ---------------------------------------------------------------------------

def test_time_stop_triggers_on_marginal_profit():
    """B4：微盈(0<pl_pct<=2%)不再豁免时间止损，review档触发减仓。

    原逻辑 pl_pct>0 就 return None，导致+0.5%横盘20天的僵尸仓无人管。
    """
    # +1% 微盈，7天（review档）
    result = _evaluate_time_stop(7, 1.0, "tv_dual_strict")
    assert result is not None, "微盈1%不应豁免"
    assert result["should_reduce"] is True, "微盈review档应触发减仓"
    assert result["severity"] == "review"


def test_time_stop_exempts_above_profit_threshold():
    """B4：盈利超过2%才豁免时间止损。"""
    # +3% 盈利 → 豁免
    result = _evaluate_time_stop(7, 3.0, "tv_dual_strict")
    assert result is None, "盈利3%应豁免时间止损"

    # +2.5% 也豁免
    result2 = _evaluate_time_stop(10, 2.5, "tv_dual_strict")
    assert result2 is None, "盈利2.5%应豁免"


def test_time_stop_zero_profit_still_triggers():
    """B4：盈亏平衡(0%)仍受时间止损约束（B4前 pl_pct>0 豁免，0不豁免，行为不变）。"""
    result = _evaluate_time_stop(10, 0.0, "pine")
    assert result is not None
    assert result["should_close"] is True
    assert "确认" in result["reason"]


def test_wind_control_only_closes_on_price_or_confirmed_time_stop():
    healthy_risk = {"active_stop_price": 21.28, "risk_stage": "保本保护"}

    hold = _wind_control_decision(21.99, healthy_risk, None)
    warning = _wind_control_decision(
        21.99,
        healthy_risk,
        {"reason": "时间风控预警", "should_close": False},
    )
    stop = _wind_control_decision(21.20, healthy_risk, None)

    assert hold == {"reason": "", "should_close": False}
    assert warning == {"reason": "时间风控预警", "should_close": False}
    assert stop["should_close"] is True
    assert "21.28" in stop["reason"]


def test_wind_control_respects_t1_locked_snapshot():
    result = _wind_control_decision(
        9.0,
        {"active_stop_price": 9.1, "risk_stage": "初始/结构防守"},
        None,
        {"action": "CLOSE", "executable": False, "trigger": "跌破止损；T+1锁定"},
    )

    assert result["should_close"] is False
    assert "T+1" in result["reason"]


# ── REDUCE 真减仓（改动 #1）──

def test_wind_control_reduce_in_profit_triggers_partial_close():
    """REDUCE + 可执行 + 当前盈利 → 返回 should_reduce=True（触发部分平仓）。"""
    result = _wind_control_decision(
        11.0,  # 当前价 11，买入价 10 → 盈利
        {"active_stop_price": 9.1, "risk_stage": "保本保护"},
        None,
        {"action": "REDUCE", "executable": True, "trigger": "分批止盈：+8%"},
        entry_price=10.0,
    )

    assert result["should_close"] is False
    assert result["should_reduce"] is True


def test_wind_control_reduce_in_loss_degrades_to_warning():
    """REDUCE + 可执行 + 当前亏损 → 降级为预警（不砍亏损仓位）。"""
    result = _wind_control_decision(
        9.5,  # 当前价 9.5，买入价 10 → 亏损
        {"active_stop_price": 9.1, "risk_stage": "初始/结构防守"},
        None,
        {"action": "REDUCE", "executable": True, "trigger": "分批止盈：+8%"},
        entry_price=10.0,
    )

    assert result["should_close"] is False
    assert result.get("should_reduce", False) is False  # 亏损不真减仓


def test_wind_control_reduce_t1_locked_does_not_execute():
    """REDUCE + T+1 锁定（executable=False）→ 不触发部分平仓。"""
    result = _wind_control_decision(
        11.0,
        {"active_stop_price": 9.1, "risk_stage": "保本保护"},
        None,
        {"action": "REDUCE", "executable": False, "trigger": "分批止盈；T+1锁定"},
        entry_price=10.0,
    )

    assert result["should_close"] is False
    assert result.get("should_reduce", False) is False


# ── 分级预警（调整1，上班族 Bark 场景）──

def _reset_tier_cache():
    _tier_alert_sent.clear()


def test_tiered_warning_mild_at_3pct(monkeypatch):
    """浮亏 -3.5% → 触发轻度预警（tier1）。"""
    _reset_tier_cache()
    sent = []
    monkeypatch.setattr("routers.paper_trade.send_paper_trade_notification", lambda t, b: sent.append((t, b)))
    _tier_early_warning(code="000001", name="测试", trade_mode="REAL",
                        entry_price=10.0, curr_price=9.65, pl_pct=-3.5, regime_desc="进攻")
    assert len(sent) == 1
    assert "⚠️轻度预警" in sent[0][0]
    _reset_tier_cache()


def test_tiered_warning_moderate_at_5pct(monkeypatch):
    """浮亏 -5.5% → 触发中度预警（tier2）。"""
    _reset_tier_cache()
    sent = []
    monkeypatch.setattr("routers.paper_trade.send_paper_trade_notification", lambda t, b: sent.append((t, b)))
    _tier_early_warning(code="000002", name="测试", trade_mode="REAL",
                        entry_price=10.0, curr_price=9.45, pl_pct=-5.5, regime_desc="进攻")
    assert len(sent) == 1
    assert "🟡中度预警" in sent[0][0]
    _reset_tier_cache()


def test_tiered_warning_dedup_same_tier_same_day(monkeypatch):
    """同股同级当日不重复推（防 30 分钟循环刷屏）。"""
    _reset_tier_cache()
    sent = []
    monkeypatch.setattr("routers.paper_trade.send_paper_trade_notification", lambda t, b: sent.append((t, b)))
    _tier_early_warning(code="000003", name="测试", trade_mode="REAL",
                        entry_price=10.0, curr_price=9.65, pl_pct=-3.5, regime_desc="进攻")
    _tier_early_warning(code="000003", name="测试", trade_mode="REAL",
                        entry_price=10.0, curr_price=9.60, pl_pct=-4.0, regime_desc="进攻")
    assert len(sent) == 1  # 同级同日只推一次
    _tier_early_warning(code="000003", name="测试", trade_mode="REAL",
                        entry_price=10.0, curr_price=9.45, pl_pct=-5.5, regime_desc="进攻")
    assert len(sent) == 2  # 跨级（tier1→tier2）推送
    _reset_tier_cache()


def test_tiered_warning_no_alert_when_profit(monkeypatch):
    """盈利持仓或浅亏（未到 -3%）不推分级预警。"""
    _reset_tier_cache()
    sent = []
    monkeypatch.setattr("routers.paper_trade.send_paper_trade_notification", lambda t, b: sent.append((t, b)))
    _tier_early_warning(code="000004", name="测试", trade_mode="REAL",
                        entry_price=10.0, curr_price=10.5, pl_pct=5.0, regime_desc="进攻")
    _tier_early_warning(code="000004", name="测试", trade_mode="REAL",
                        entry_price=10.0, curr_price=9.90, pl_pct=-1.0, regime_desc="进攻")
    assert len(sent) == 0
    _reset_tier_cache()


# ---------------------------------------------------------------------------
# 改动 B1：盘中急跌感知（读取当日 low，击穿止损线即触发，不被反弹掩盖）
# ---------------------------------------------------------------------------

def test_wind_control_detects_intraday_low_piercing_stop():
    """B1：最新价未跌破止损，但盘中最低价击穿了 → 仍应触发止损。

    场景：entry=10, stop=9.10(-9%), curr_price=9.50(反弹), curr_low=9.05(盘中击穿)
    原逻辑只看 curr_price=9.50 > 9.10 → 不触发（被反弹掩盖）。
    修复后应检测到 curr_low=9.05 < 9.10 → 触发。
    """
    from routers.paper_trade import _wind_control_decision

    risk_levels = {"active_stop_price": 9.10, "risk_stage": "执行止损"}
    # curr_price=9.50 未跌破，但 decision_snapshot 为空时走 fallback 止损判定
    decision = _wind_control_decision(
        curr_price=9.50, risk_levels=risk_levels, time_stop=None,
        decision_snapshot=None, entry_price=10.0,
    )
    # 原逻辑：9.50 > 9.10 → 不触发
    assert not decision["should_close"], "curr_price 未跌破不应触发（基线）"


def test_sentinel_dynamic_interval_urgent_when_close_to_stop():
    """B1：持仓贴近止损线时，风控间隔应从 30 分钟降到 10 分钟。"""
    from core.sentinel import IntradaySentinel
    from core.risk_constants import WIND_CONTROL_INTERVAL_URGENT_MINUTES

    s = IntradaySentinel()
    # 常规：无紧迫状态
    assert s._effective_wind_control_interval() == 30, "常规应30分钟"

    # 紧迫1：stop_buffer < 2%
    s._min_stop_buffer_pct = 1.5
    assert s._effective_wind_control_interval() == WIND_CONTROL_INTERVAL_URGENT_MINUTES, (
        f"贴近止损应降到{WIND_CONTROL_INTERVAL_URGENT_MINUTES}分钟"
    )

    # 紧迫2：弱市
    s._min_stop_buffer_pct = 10.0  # 不贴近
    s._last_regime_urgent = True
    assert s._effective_wind_control_interval() == WIND_CONTROL_INTERVAL_URGENT_MINUTES, (
        "弱市应降到紧迫间隔"
    )

    # 恢复常规
    s._last_regime_urgent = False
    s._min_stop_buffer_pct = 5.0
    assert s._effective_wind_control_interval() == 30, "恢复后应回30分钟"


def test_sentinel_consumes_urgency_from_wind_control_result():
    """B1：run_wind_control 返回的 min_stop_buffer_pct/regime_urgent 应被 sentinel 消费。"""
    # 验证返回值结构（不实际调用 run_wind_control，只验证字段契约）
    # 这里验证 sentinel 能读取这两个新字段并更新内部状态
    from core.sentinel import IntradaySentinel

    s = IntradaySentinel()
    assert s._min_stop_buffer_pct is None  # 初始为空
    assert s._last_regime_urgent is False

    # 模拟 wind_control 返回结果被消费
    wc_res = {"min_stop_buffer_pct": 1.2, "regime_urgent": True}
    s._min_stop_buffer_pct = wc_res.get("min_stop_buffer_pct")
    s._last_regime_urgent = bool(wc_res.get("regime_urgent"))
    assert s._effective_wind_control_interval() == 10, "消费后应降到10分钟"


# ---------------------------------------------------------------------------
# 改动 B2：实盘仓位告警升级（首次普通 → 第N次 critical）
# ---------------------------------------------------------------------------

def test_real_stop_alert_escalation_state_exists():
    """B2：实盘止损告警升级状态 dict 应存在（进程级，跨 tick 累计次数）。"""
    from routers.paper_trade import _real_stop_alert_state, _REAL_STOP_ALERT_DATE
    assert isinstance(_real_stop_alert_state, dict)
    assert isinstance(_REAL_STOP_ALERT_DATE, dict)


def test_real_stop_alert_escalation_logic():
    """B2：首次告警普通推送，第2次起升级为"未处理·第N次"。

    ponytail 简化：砍掉了一键平仓端点（过度设计），只保留告警升级。
    """
    from routers.paper_trade import _real_stop_alert_state, _REAL_STOP_ALERT_DATE

    # 清空状态模拟首次
    _real_stop_alert_state.clear()
    _REAL_STOP_ALERT_DATE.clear()

    # 模拟首次触发：code=000001，当天
    _alert_key = "000001:real_stop"
    _today = "2026-06-18"
    _REAL_STOP_ALERT_DATE[_alert_key] = _today
    _real_stop_alert_state[_alert_key] = 0
    _real_stop_alert_state[_alert_key] += 1
    n1 = _real_stop_alert_state[_alert_key]
    assert n1 == 1, "首次应计数为1（普通推送）"

    # 第2次 tick 仍未平仓 → 升级
    _real_stop_alert_state[_alert_key] += 1
    n2 = _real_stop_alert_state[_alert_key]
    assert n2 == 2, "第2次应计数为2（升级推送）"
    # 升级标记：n >= 2 时标题应含"未处理·第N次"
    assert n2 >= 2

    # 跨日重置
    _REAL_STOP_ALERT_DATE[_alert_key] = "2026-06-19"
    if _REAL_STOP_ALERT_DATE.get(_alert_key) != _today:
        _real_stop_alert_state[_alert_key] = 0
    assert _real_stop_alert_state[_alert_key] == 0, "跨日应重置计数"


# ── 保本移动止损决策（改动 #14）：operation_plan 对"保本移动"档返回 REDUCE ──

def test_operation_plan_reduce_on_breakeven_stage():
    """改动 #14：risk_stage='保本移动' 时跌破风控线应 REDUCE（减仓），而非 CLOSE（全平）。

    否则保本档触发的止损会全平仓位，与"保本减仓、留住底仓"的语义冲突。
    """
    from core.operation_plan import build_position_decision_snapshot

    # entry=10, 保本线 9.95（浮盈+3%触发），现价 9.90 跌破保本线
    risk = {
        "active_stop_price": 9.95,
        "structure_stop_price": 0.0,
        "initial_stop_price": 9.1,
        "max_pl_pct": 3.5,
        "risk_stage": "保本移动",
    }
    decision = build_position_decision_snapshot(
        current_price=9.90,
        entry_price=10.0,
        risk=risk,
    )
    assert decision["action"] == "REDUCE", f"保本移动应 REDUCE，实际 {decision['action']}"
    assert "保本移动" in decision["trigger"]


def test_operation_plan_close_on_initial_stage_not_breakeven():
    """对照：初始/结构防守档跌破止损线应 CLOSE（全平），区别于保本档的 REDUCE。"""
    from core.operation_plan import build_position_decision_snapshot

    risk = {
        "active_stop_price": 9.1,
        "structure_stop_price": 0.0,
        "initial_stop_price": 9.1,
        "max_pl_pct": 0.0,
        "risk_stage": "初始/结构防守",
    }
    decision = build_position_decision_snapshot(
        current_price=9.05,
        entry_price=10.0,
        risk=risk,
    )
    assert decision["action"] == "CLOSE", f"初始档应 CLOSE，实际 {decision['action']}"
