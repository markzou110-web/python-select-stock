import os
import sys
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.sentinel import (
    _attach_official_close_prices,
    _position_breakout_confirmation,
    _build_after_close_watchlist_body,
    _candidate_action_label,
    _candidate_brief_lines,
    _intraday_state_fingerprint,
    _mark_intraday_state_sent,
    _should_send_intraday_state,
    _format_market_line,
    _format_regime_line,
    _real_position_action,
    _select_after_close_watchlist,
    _select_intraday_push_stocks,
    _select_reference_push_stocks,
    send_after_close_watchlist,
    send_intraday_notification,
)
from core.notifier import BARK_BODY_MAX_BYTES, bark_encoded_body_size


def test_intraday_push_keeps_only_executable_candidates():
    stocks = [
        {"代码": "000001", "名称": "一号", "sop_grade": "A", "Score": 90, "trade_bucket": "TRADE", "trade_eligible": True},
        {"代码": "000002", "名称": "二号", "sop_grade": "B", "Score": 80},
        {"代码": "000003", "名称": "三号", "sop_grade": "C", "Score": 70, "sector_watch_only": True},
        {"代码": "000004", "名称": "四号", "sop_grade": "D", "Score": 95, "sector_watch_only": True},
    ]

    selected = _select_intraday_push_stocks(stocks, executable_limit=1, sector_watch_limit=1)

    assert [s["代码"] for s in selected] == ["000001"]


def test_intraday_push_limits_a_minus_trials_to_portfolio_cap():
    stocks = [
        {
            "代码": f"00000{idx}", "名称": f"试仓{idx}", "sop_grade": "B",
            "Score": 90 - idx, "trade_bucket": "TRADE", "trade_eligible": True,
            "a_minus_trial": True, "a_minus_trial_grade": "A-",
        }
        for idx in range(1, 4)
    ]

    selected = _select_intraday_push_stocks(stocks, executable_limit=5, sector_watch_limit=0)

    assert len(selected) == 2
    assert all(stock["a_minus_trial"] for stock in selected)


def test_intraday_push_limits_a_eod_trials_to_three_candidates():
    stocks = [
        {
            "代码": f"00001{idx}", "名称": f"A-EOD{idx}", "sop_grade": "B",
            "Score": 90 - idx, "trade_bucket": "TRADE", "trade_eligible": True,
            "a_eod_controlled_trial": True,
        }
        for idx in range(1, 5)
    ]

    selected = _select_intraday_push_stocks(stocks, executable_limit=5, sector_watch_limit=0)

    assert len(selected) == 3
    assert all(stock["a_eod_controlled_trial"] for stock in selected)


def test_candidate_brief_omits_verbose_observation_source():
    stock = {
        "代码": "000001", "名称": "双命中", "sop_grade": "B",
        "trade_bucket": "OBSERVE", "trade_eligible": False,
        "bark_selection_source_label": "TV宽松观察池",
    }

    body = "\n".join(_candidate_brief_lines(stock))

    assert "候选来源" not in body
    assert len(body.splitlines()) == 3


def test_intraday_reference_push_when_market_risk_blocks_all(monkeypatch):
    sent = []
    persisted_events = []
    persisted_snapshots = []
    created_intents = []
    stocks = [
        {
            "代码": "000001", "名称": "观察一", "sop_grade": "B", "Score": 82,
            "strategy_type": "tv_zp", "trade_bucket": "OBSERVE", "trade_eligible": False,
            "trade_blockers": ["CRITICAL市场默认禁止新仓，等待环境修复"],
        },
        {
            "代码": "000002", "名称": "观察二", "sop_grade": "B", "Score": 70,
            "strategy_type": "tv_zp", "trade_bucket": "OBSERVE", "trade_eligible": False,
            "trade_blockers": ["CRITICAL市场默认禁止新仓，等待环境修复"],
        },
        {
            "代码": "000003", "名称": "禁买", "sop_grade": "C", "Score": 99,
            "strategy_type": "tv_zp", "trade_bucket": "BLOCK", "trade_eligible": False,
        },
    ]
    monkeypatch.setattr("core.sentinel.is_a_share_intraday_session", lambda: True)
    monkeypatch.setattr("core.sentinel._load_recommendation_priority_adjustments", lambda: {})
    monkeypatch.setattr("core.sentinel._format_market_line", lambda *_: "退潮 35.2分 | 总仓上限 30% | 🛡️ 严格防守")
    monkeypatch.setattr("core.sentinel._should_send_intraday_state", lambda *_: True)
    monkeypatch.setattr(
        "core.sentinel._send_bark_message",
        lambda title, body, **kwargs: sent.append((title, body)) or True,
    )
    monkeypatch.setattr("core.sentinel._append_real_position_status", lambda *_: None)
    monkeypatch.setattr("core.sentinel._mark_intraday_state_sent", lambda *_: None)
    monkeypatch.setattr("core.data.get_market_regime", lambda: {"status": "CRITICAL"})
    monkeypatch.setattr("core.data.get_market_snapshot", lambda: {})
    monkeypatch.setattr("core.data.format_freshness", lambda *_: "测试快照")
    monkeypatch.setattr(
        "core.db.save_recommendation_events",
        lambda *args, **kwargs: persisted_events.append(args) or True,
    )
    monkeypatch.setattr(
        "core.signal_performance.save_intraday_signal_snapshots",
        lambda *args, **kwargs: persisted_snapshots.append(args) or 0,
    )
    monkeypatch.setattr(
        "core.execution_intents.create_bark_execution_intents",
        lambda *args, **kwargs: created_intents.append(args) or [],
    )

    # 冻结时间到上午：标题前缀依赖墙钟（14:20 后为"尾盘参考/决策"），不冻结则
    # 测试仅在尾盘窗口外的时段通过（存量墙钟脆弱，修复为确定性断言）
    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls):
            return datetime(2026, 9, 4, 10, 30, 0)

    monkeypatch.setattr("core.sentinel.datetime", _FrozenDatetime)

    body = send_intraday_notification(stocks)

    assert body
    assert len(sent) == 1
    title, pushed = sent[0]
    assert "盘中参考" in title
    assert "【策略信号｜仅供参考】" in pushed
    assert "市场风控禁新仓" in pushed
    assert "CRITICAL市场默认禁止新仓" in pushed
    assert "000001" in pushed and "000002" in pushed
    assert "000003" not in pushed
    assert persisted_events == []
    assert persisted_snapshots == []
    assert created_intents == []


def test_intraday_reference_push_dedupes_unchanged_state(monkeypatch):
    sent = []
    settings = {}
    stocks = [
        {
            "代码": "000001", "名称": "观察一", "sop_grade": "B", "Score": 82,
            "strategy_type": "tv_zp", "trade_bucket": "OBSERVE", "trade_eligible": False,
            "trade_blockers": ["CRITICAL市场默认禁止新仓，等待环境修复"],
        },
    ]
    monkeypatch.setattr("core.sentinel.is_a_share_intraday_session", lambda: True)
    monkeypatch.setattr("core.sentinel._load_recommendation_priority_adjustments", lambda: {})
    monkeypatch.setattr("core.sentinel._format_market_line", lambda *_: "测试行情")
    monkeypatch.setattr(
        "core.sentinel._send_bark_message",
        lambda title, body, **kwargs: sent.append((title, body)) or True,
    )
    monkeypatch.setattr("core.sentinel._append_real_position_status", lambda *_: None)
    monkeypatch.setattr("core.sentinel.get_setting", lambda key, default=None: settings.get(key, default))
    monkeypatch.setattr("core.sentinel.save_setting", lambda key, value: settings.update({key: value}) or True)
    monkeypatch.setattr("core.data.get_market_regime", lambda: {"status": "CRITICAL"})
    monkeypatch.setattr("core.data.get_market_snapshot", lambda: {})
    monkeypatch.setattr("core.data.format_freshness", lambda *_: "测试快照")

    first = send_intraday_notification(stocks)
    second = send_intraday_notification(stocks)

    assert first
    assert second == ""
    assert len(sent) == 1


def test_intraday_reference_push_disabled_rolls_back_to_silent(monkeypatch):
    sent = []
    stocks = [
        {
            "代码": "000001", "名称": "观察一", "sop_grade": "B", "Score": 82,
            "trade_bucket": "OBSERVE", "trade_eligible": False,
        },
    ]
    monkeypatch.setattr("core.sentinel.is_a_share_intraday_session", lambda: True)
    monkeypatch.setattr("core.sentinel._load_recommendation_priority_adjustments", lambda: {})
    monkeypatch.setattr("core.data.get_market_regime", lambda: {"status": "CRITICAL"})
    monkeypatch.setattr(
        "core.sentinel._send_bark_message",
        lambda title, body, **kwargs: sent.append((title, body)) or True,
    )
    monkeypatch.setattr("core.sentinel.REGIME_REFERENCE_PUSH_ENABLED", False)

    assert send_intraday_notification(stocks) is None
    assert sent == []


def test_reference_selection_excludes_block_and_ranks_by_score():
    stocks = [
        {"代码": "000001", "名称": "低分", "Score": 50, "trade_bucket": "OBSERVE"},
        {"代码": "000002", "名称": "高分", "Score": 90, "trade_bucket": "OBSERVE"},
        {"代码": "000003", "名称": "禁买", "Score": 99, "trade_bucket": "BLOCK"},
        {"代码": "000004", "名称": "板块观察", "Score": 80, "sector_watch_only": True},
    ]

    selected = _select_reference_push_stocks(stocks, limit=2)

    assert [s["代码"] for s in selected] == ["000002", "000001"]


def test_intraday_push_splits_oversized_bark_body(monkeypatch):
    sent = []
    stock = {
        "代码": "000001",
        "名称": "超长候选" * 350,
        "sop_grade": "B",
        "Score": 80,
        "strategy_type": "squeeze",
        "trade_bucket": "TRADE",
        "trade_eligible": True,
    }
    monkeypatch.setattr("core.sentinel.is_a_share_intraday_session", lambda: True)
    monkeypatch.setattr("core.sentinel._load_recommendation_priority_adjustments", lambda: {})
    monkeypatch.setattr("core.sentinel._format_market_line", lambda *_: "测试行情")
    monkeypatch.setattr("core.sentinel._should_send_intraday_state", lambda *_: True)
    monkeypatch.setattr(
        "core.sentinel._send_bark_message",
        lambda title, body, **kwargs: sent.append((title, body, kwargs)) or True,
    )
    monkeypatch.setattr("core.sentinel._append_real_position_status", lambda *_: None)
    monkeypatch.setattr("core.sentinel._mark_intraday_state_sent", lambda *_: None)
    monkeypatch.setattr("core.data.get_market_regime", lambda: {"status": "TEST"})
    monkeypatch.setattr("core.data.get_market_snapshot", lambda: {})
    monkeypatch.setattr("core.data.format_freshness", lambda *_: "测试快照")
    monkeypatch.setattr("core.db.save_recommendation_events", lambda *_args, **_kwargs: True)
    monkeypatch.setattr("core.db.get_db_engine", lambda: None)
    monkeypatch.setattr("core.signal_performance.save_intraday_signal_snapshots", lambda *_args, **_kwargs: 0)
    monkeypatch.setattr("core.execution_intents.create_bark_execution_intents", lambda *_args, **_kwargs: [])

    body = send_intraday_notification([stock])

    assert body
    assert len(sent) > 1
    assert all(bark_encoded_body_size(part) <= BARK_BODY_MAX_BYTES for _, part, _ in sent)
    assert sent[0][0].endswith(f"(1/{len(sent)})")
    assert all(kwargs["enqueue_failed"] is False for _, _, kwargs in sent)


def test_intraday_push_uses_outcome_adjustments_within_same_grade():
    stocks = [
        {
            "代码": "000001",
            "名称": "弱策略",
            "sop_grade": "A",
            "Score": 91,
            "strategy_type": "tv_dual",
            "trade_bucket": "TRADE",
            "trade_eligible": True,
        },
        {
            "代码": "000002",
            "名称": "强策略",
            "sop_grade": "A",
            "Score": 88,
            "strategy_type": "tv_dual_strict",
            "trade_bucket": "TRADE",
            "trade_eligible": True,
        },
    ]
    adjustments = {
        "source": {},
        "strategy": {
            "tv_dual": {"dimension": "策略", "value": "tv_dual", "score_delta": -5, "action": "DOWNWEIGHT"},
            "tv_dual_strict": {"dimension": "策略", "value": "tv_dual_strict", "score_delta": 4, "action": "BOOST"},
        },
    }

    selected = _select_intraday_push_stocks(stocks, executable_limit=2, priority_adjustments=adjustments)

    assert [s["代码"] for s in selected] == ["000002", "000001"]
    assert selected[0]["bark_priority_delta"] == 4
    assert "tv_dual_strict +4" in selected[0]["bark_priority_note"]


def test_bark_market_line_prefers_decision_context_over_offensive_regime():
    stocks = [{
        "market_sentiment_label": "退潮",
        "market_sentiment_score": 45.8,
        "portfolio_position_cap_pct": 10,
    }]
    regime = {
        "status": "OFFENSIVE",
        "indices": {
            "上证": {"chg_pct": -1.37},
            "创业": {"chg_pct": -3.84},
        },
    }

    line = _format_market_line(stocks, regime)

    assert line.startswith("退潮 45.8分 | 总仓上限 10%")
    assert "进攻模式" not in line
    assert "指数急跌" in line


def test_regime_line_does_not_show_offensive_during_sharp_index_drop():
    line = _format_regime_line({
        "status": "OFFENSIVE",
        "indices": {
            "上证": {"chg_pct": -1.37},
            "创业": {"chg_pct": -3.84},
        },
    })

    assert "进攻模式" not in line
    assert "指数急跌" in line


def test_sector_watch_action_is_observation_only():
    stock = {"sector_watch_only": True, "涨幅%": 8.2, "sop_grade": "C"}

    label = _candidate_action_label(stock)

    assert "不追" in label
    assert "买点" in label


def test_blocked_candidate_stays_out_of_interruptive_bark():
    stock = {
        "代码": "000005",
        "名称": "五号",
        "sop_grade": "A",
        "trade_bucket": "BLOCK",
        "trade_eligible": False,
        "trade_blockers": ["高开风险"],
    }

    selected = _select_intraday_push_stocks([stock], executable_limit=1, sector_watch_limit=1)
    label = _candidate_action_label(stock)

    assert selected == []
    assert "禁止追买" in label
    assert "高开风险" in label


def test_after_close_watchlist_keeps_observe_candidate_and_excludes_block():
    stocks = [
        {
            "代码": "300145",
            "名称": "南方泵业",
            "sop_grade": "M",
            "trade_bucket": "OBSERVE",
            "trade_eligible": False,
            "final_trade_score": 72,
            "现价": 5.62,
            "entry_price": 5.72,
            "pa_pullback_support_price": 5.36,
            "pa_pullback_confirmation_price": 5.72,
            "pa_pullback_invalidation_price": 4.31,
            "pa_pullback_status": "PENDING_CONFIRMATION",
            "pa_pullback_status_label": "回踩待确认",
            "plan_stop_price": 4.31,
            "trade_blockers": ["涨停/近涨停，等待隔日确认"],
        },
        {
            "代码": "000001",
            "名称": "风险样本",
            "sop_grade": "A",
            "trade_bucket": "BLOCK",
            "final_trade_score": 95,
        },
    ]

    selected = _select_after_close_watchlist(stocks)
    body = _build_after_close_watchlist_body(selected, "2026-06-09")

    assert [stock["代码"] for stock in selected] == ["300145"]
    assert "关键价：现价 5.62 | 确认 >5.72 | 有效失效 <4.31" in body
    assert "站稳 >5.72" in body
    assert "跌破 <4.31" in body
    assert "回踩状态: 回踩待确认：支撑暂守住，等放量站上确认价" in body
    assert "不追高；等待回踩后重新站上5.72，跌破4.31取消" in body
    assert "涨停/近涨停，等待隔日确认" in body
    assert "不是买入指令" in body


def test_after_close_watchlist_body_includes_outcome_adjustment_note():
    stocks = [
        {
            "代码": "000001",
            "名称": "弱策略",
            "sop_grade": "M",
            "trade_bucket": "OBSERVE",
            "final_trade_score": 73,
            "strategy_type": "tv_dual",
            "现价": 10,
        },
        {
            "代码": "000002",
            "名称": "强策略",
            "sop_grade": "M",
            "trade_bucket": "OBSERVE",
            "final_trade_score": 70,
            "strategy_type": "tv_dual_strict",
            "现价": 10,
        },
    ]
    adjustments = {
        "source": {},
        "strategy": {
            "tv_dual": {"dimension": "策略", "value": "tv_dual", "score_delta": -5, "action": "DOWNWEIGHT"},
            "tv_dual_strict": {"dimension": "策略", "value": "tv_dual_strict", "score_delta": 4, "action": "BOOST"},
        },
    }

    selected = _select_after_close_watchlist(stocks, limit=2, priority_adjustments=adjustments)
    body = _build_after_close_watchlist_body(selected, "2026-06-09")

    assert [s["代码"] for s in selected] == ["000002", "000001"]
    assert "闭环调权：策略 tv_dual_strict +4，仅影响推送排序" in body


def test_after_close_watchlist_body_includes_hot_sector_gap_summary():
    stock = {
        "代码": "300145",
        "名称": "南方泵业",
        "sop_grade": "M",
        "trade_bucket": "OBSERVE",
        "现价": 5.62,
        "entry_price": 5.72,
        "plan_stop_price": 4.31,
    }
    gaps = [
        {
            "industry": "机器人",
            "sector_momentum_score": 86,
            "scan_candidate_count": 2,
            "has_push_candidate": False,
            "primary_reason_label": "候选偏后排，暂不追",
        },
        {
            "industry": "创新药",
            "sector_momentum_score": 75,
            "scan_candidate_count": 0,
            "has_push_candidate": False,
            "primary_reason_label": "板块强，但扫描策略没有选出候选",
        },
    ]

    body = _build_after_close_watchlist_body([stock], "2026-06-09", gaps)

    assert "热门板块未推原因：" in body
    assert "机器人：候选偏后排，暂不追 | 板块分 86 | 候选 2只" in body
    assert "创新药：板块强，但扫描策略没有选出候选 | 板块分 75 | 候选 0只" in body


def test_intraday_candidate_line_shows_support_and_no_chase_rule():
    stock = {
        "代码": "300145",
        "名称": "南方泵业",
        "sop_grade": "M",
        "trade_bucket": "OBSERVE",
        "trade_eligible": False,
        "现价": 5.62,
        "entry_price": 5.72,
        "pa_pullback_support_price": 5.36,
        "plan_stop_price": 4.31,
        "涨幅%": 8.1,
        "trade_blockers": ["涨幅偏高且质量未确认，等待回踩/次日确认"],
    }

    body = "\n".join(_candidate_brief_lines(stock))

    assert "价格：现价5.62｜确认>5.72｜失效<4.31" in body
    assert "原因：涨幅偏高且质量未确认，等待回踩/次日确认" in body
    assert len(body.splitlines()) == 3


def test_intraday_candidate_uses_structure_label_and_capped_display_score():
    stock = {
        "代码": "603127",
        "名称": "昭衍新药",
        "sop_grade": "A",
        "trade_bucket": "OBSERVE",
        "trade_eligible": False,
        "final_trade_score": 132.02,
        "display_trade_score": 100,
        "现价": 43.22,
        "pa_entry_price": 43.23,
        "pa_stop_price": 39.34,
    }

    body = "\n".join(_candidate_brief_lines(stock))

    assert "禁止买入｜结构观察" in body
    assert "结构分" not in body
    assert "132.02" not in body


def test_intraday_candidate_shows_base_and_final_grade_reason():
    stock = {
        "代码": "603127",
        "名称": "昭衍新药",
        "sop_base_grade": "A",
        "sop_grade": "B",
        "sop_quality_score": 72,
        "sop_quality_gap_to_a": 0,
        "sop_grade_transition_reasons": ["历史信号复活按确认状态调整等级"],
        "trade_bucket": "OBSERVE",
        "trade_eligible": False,
        "现价": 43.22,
    }

    body = "\n".join(_candidate_brief_lines(stock))

    assert "质量分" not in body
    assert "等级调整" not in body
    assert "指令：不可交易｜禁止买入" in body


def test_bottom_discovery_bark_is_explicit_observation_not_trade():
    stock = {
        "代码": "000001", "名称": "底部票", "sop_grade": "C",
        "bottom_discovery_watch_only": True, "bottom_discovery_stage": "B1_REVERSAL",
        "trade_bucket": "OBSERVE", "trade_eligible": False, "现价": 10.5,
        "bottom_discovery_action": "起涨预警：等待板块与量价确认",
    }

    body = "\n".join(_candidate_brief_lines(stock))

    assert "指令：不可交易｜起涨预警｜B1止跌转强" in body
    assert "可交易" not in body.replace("不可交易", "")


def test_intraday_state_dedupe_only_sends_changed_state(monkeypatch):
    settings = {}
    monkeypatch.setattr("core.sentinel.get_setting", lambda key, default=None: settings.get(key, default))
    monkeypatch.setattr("core.sentinel.save_setting", lambda key, value: settings.update({key: value}) or True)
    stocks = [{"代码": "000001", "sop_grade": "B", "trade_bucket": "OBSERVE", "trade_eligible": False}]
    fingerprint = _intraday_state_fingerprint(stocks, {"status": "DEFENSIVE"})
    now = datetime(2026, 7, 10, 10, 0)

    assert _should_send_intraday_state(fingerprint, now) is True
    _mark_intraday_state_sent(fingerprint, now)
    assert _should_send_intraday_state(fingerprint, now) is False


def test_intraday_state_fingerprint_tracks_execution_review_changes():
    base = [{
        "代码": "000001", "sop_grade": "B", "trade_bucket": "OBSERVE",
        "trade_eligible": False, "execution_review_state": "OBSERVE",
        "confirmation_reachability": "REACHABLE_TODAY",
    }]
    changed = [{**base[0], "execution_review_state": "NEXT_DAY_REVIEW"}]

    assert _intraday_state_fingerprint(base, {"status": "DEFENSIVE"}) != _intraday_state_fingerprint(
        changed, {"status": "DEFENSIVE"},
    )


def test_after_close_watchlist_keeps_high_opportunity_d_grade_as_observation_only():
    stock = {
        "代码": "300263",
        "名称": "隆华科技",
        "sop_grade": "D",
        "trade_bucket": "OBSERVE",
        "trade_opportunity_score": 68,
        "trade_opportunity_label": "试错仓",
        "execution_instruction": "仅观察；站稳 8.20 且量能确认后复核",
    }

    selected = _select_after_close_watchlist([stock])
    body = _build_after_close_watchlist_body(selected, "2026-06-11")

    assert selected == [stock]
    assert "结构观察｜等待确认" in body
    assert "仅观察" in body


def test_after_close_watchlist_deduplicates_loose_row_when_strict_row_exists():
    loose = {
        "代码": "000001", "名称": "宽松发现", "strategy_type": "tv_dual",
        "sop_grade": "B", "trade_bucket": "OBSERVE", "trade_opportunity_score": 80,
    }
    strict = {
        **loose, "名称": "严格执行", "strategy_type": "tv_dual_strict",
        "trade_opportunity_score": 70,
    }

    selected = _select_after_close_watchlist([loose, strict])

    assert len(selected) == 1
    assert selected[0]["strategy_type"] == "tv_dual_strict"


def test_after_close_watchlist_prioritizes_backtested_a_eod_t1_plan():
    stock = {
        "代码": "000001", "名称": "次日计划", "strategy_type": "tv_dual_strict",
        "sop_grade": "M", "sop_vetoes": [], "trade_bucket": "BLOCK",
        "trade_eligible": False, "现价": 10.0, "price_action_score": 64,
        "pct_5d": 8.0, "pa_trade_action": "WATCH", "pa_entry_price": 10.2,
        "pa_stop_price": 9.4, "pa_target_price": 11.8,
        "market_sentiment_stage": "ADVANCE", "sector_phase": "SECTOR_CONFIRM",
    }

    selected = _select_after_close_watchlist([stock])
    body = _build_after_close_watchlist_body(selected, "2026-08-07")

    assert len(selected) == 1
    assert selected[0]["a_eod_t1_plan"] is True
    assert selected[0]["execution_review_state"] == "NEXT_DAY_REVIEW"
    assert selected[0]["trade_eligible"] is False
    assert "尾盘T1｜次日计划" in body
    assert "不是买入指令" in body


def test_after_close_watchlist_pushes_once_without_real_bark(monkeypatch):
    sent = []
    settings = {}
    stocks = [{
        "代码": "300145",
        "名称": "南方泵业",
        "sop_grade": "M",
        "trade_bucket": "OBSERVE",
        "现价": 5.62,
        "entry_price": 5.72,
        "plan_stop_price": 4.31,
        "strategy_type": "tv_dual",
    }]
    monkeypatch.setattr("core.sentinel.get_setting", lambda key, default=None: settings.get(key, default))
    monkeypatch.setattr("core.sentinel.save_setting", lambda key, value: settings.update({key: value}) or True)
    monkeypatch.setattr(
        "core.sentinel._send_bark_message",
        lambda title, body: sent.append((title, body)) or True,
    )
    monkeypatch.setattr("core.db.save_recommendation_events", lambda *args, **kwargs: True)
    monkeypatch.setattr("core.db.get_db_engine", lambda: None)
    monkeypatch.setattr("core.signal_performance.save_intraday_signal_snapshots", lambda *args, **kwargs: 0)

    first = send_after_close_watchlist(
        stocks,
        scan_date="2026-06-09",
        now=datetime(2026, 6, 9, 21, 30),
    )
    second = send_after_close_watchlist(
        stocks,
        scan_date="2026-06-09",
        now=datetime(2026, 6, 9, 21, 31),
    )

    assert first is not None
    assert second is None
    assert len(sent) == 1
    assert sent[0][0] == "Alpha Vision 收盘决策摘要 2026-06-09"
    assert "结果：正式1｜复活0｜动量0｜可交易0" in first


def test_after_close_watchlist_does_not_mark_failed_delivery_as_sent(monkeypatch):
    settings = {}
    snapshots = []
    stocks = [{
        "代码": "300145", "名称": "南方泵业", "sop_grade": "M",
        "trade_bucket": "OBSERVE", "现价": 5.62,
    }]
    monkeypatch.setattr("core.sentinel.get_setting", lambda key, default=None: settings.get(key, default))
    monkeypatch.setattr("core.sentinel.save_setting", lambda key, value: settings.update({key: value}) or True)
    monkeypatch.setattr("core.sentinel._send_bark_message", lambda *_: False)
    monkeypatch.setattr("core.db.save_recommendation_events", lambda *args, **kwargs: True)
    monkeypatch.setattr("core.db.get_db_engine", lambda: None)
    monkeypatch.setattr(
        "core.signal_performance.save_intraday_signal_snapshots",
        lambda *args, **kwargs: snapshots.append((args, kwargs)) or 1,
    )

    body = send_after_close_watchlist(
        stocks,
        scan_date="2026-06-09",
        now=datetime(2026, 6, 9, 21, 30),
    )

    assert body is None
    assert "after_close_watchlist_last_date" not in settings
    assert len(snapshots) == 1
    assert snapshots[0][1]["source"] == "bark_next_day"


def test_after_close_watchlist_push_is_one_compact_digest(monkeypatch):
    sent = []
    settings = {}
    stocks = [{
        "代码": "300145",
        "名称": "南方泵业",
        "行业": "机器人",
        "sop_grade": "M",
        "trade_bucket": "OBSERVE",
        "现价": 5.62,
        "entry_price": 5.72,
        "plan_stop_price": 4.31,
        "strategy_type": "tv_dual_strict",
        "sector_role": "FOLLOWER",
        "trade_blockers": ["强板块后排角色，等待转强为核心股"],
    }]
    monkeypatch.setattr("core.sentinel.get_setting", lambda key, default=None: settings.get(key, default))
    monkeypatch.setattr("core.sentinel.save_setting", lambda key, value: settings.update({key: value}) or True)
    monkeypatch.setattr(
        "core.sentinel._send_bark_message",
        lambda title, body: sent.append((title, body)) or True,
    )
    monkeypatch.setattr("core.db.save_recommendation_events", lambda *args, **kwargs: True)
    monkeypatch.setattr("core.db.get_db_engine", lambda: None)
    monkeypatch.setattr("core.signal_performance.save_intraday_signal_snapshots", lambda *args, **kwargs: 0)
    body = send_after_close_watchlist(
        stocks,
        scan_date="2026-06-09",
        now=datetime(2026, 6, 9, 21, 30),
    )

    assert body is not None
    assert "次日复核（合并摘要，Top 1）" in body
    assert "确认>5.72｜失效<4.31" in body
    assert len(body) < 800
    assert sent and sent[0][1] == body


def test_after_close_watchlist_uses_official_close_when_available():
    from sqlalchemy import create_engine, text

    from core.models import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO daily_k (code, date, close) "
                "VALUES ('300145', '2026-06-09', 5.88)"
            )
        )
    stocks = [{"代码": "300145", "名称": "南方泵业", "现价": 5.62}]

    stocks = _attach_official_close_prices(stocks, engine, "2026-06-09")
    body = _build_after_close_watchlist_body(stocks, "2026-06-09")

    assert stocks[0]["现价"] == 5.88
    assert stocks[0]["after_close_price_label"] == "正式收盘"
    assert "正式收盘 5.88" in body


def _position_df(last_close: float, last_volume: float) -> pd.DataFrame:
    rows = []
    for idx in range(21):
        rows.append({
            "日期": f"2026-06-{idx + 1:02d}",
            "开盘": 20.0 + idx * 0.05,
            "最高": 21.5 + idx * 0.03,
            "最低": 19.8 + idx * 0.04,
            "收盘": 20.3 + idx * 0.05,
            "成交量": 1000,
        })
    rows[-1].update({
        "开盘": 21.8,
        "最高": max(last_close + 0.1, 22.7),
        "最低": 21.7,
        "收盘": last_close,
        "成交量": last_volume,
    })
    return pd.DataFrame(rows)


def test_real_position_action_explains_breakout_confirmation_before_add():
    suggestion = _real_position_action(
        curr=22.0,
        entry=21.07,
        high_since_entry=22.0,
        risk={"active_stop_price": 21.28, "structure_stop_price": 19.17},
        pa={"pa_entry_price": 22.5},
        signals=[],
        df_hist=_position_df(last_close=22.0, last_volume=1100),
    )

    assert "持有观察" in suggestion
    assert "加仓确认" in suggestion
    assert "站上22.50" in suggestion
    assert "守22.16" in suggestion
    assert "指令" in suggestion
    assert ">22.50: 只确认不追，等价量收齐" in suggestion
    assert "22.16-22.50: 持有观察，不加仓" in suggestion
    assert "<22.16: 撤回加仓计划" in suggestion
    assert "<21.28: 减仓/收紧风控" in suggestion
    assert "<19.17: 结构失效，退出复核" in suggestion


def test_real_position_action_marks_confirmed_volume_breakout():
    suggestion = _real_position_action(
        curr=22.8,
        entry=21.07,
        high_since_entry=22.8,
        risk={"active_stop_price": 21.28, "structure_stop_price": 19.17},
        pa={"pa_entry_price": 22.5, "pa_volume_pattern": "放量突破"},
        signals=[],
        df_hist=_position_df(last_close=22.8, last_volume=2200),
    )

    assert "放量突破已确认" in suggestion
    assert "可小幅加仓" in suggestion
    assert "价✓" in suggestion
    assert "量✓" in suggestion
    assert "收✓" in suggestion
    assert ">22.50: 可小幅加仓" in suggestion
    assert "<22.16: 撤回加仓计划" in suggestion


def test_real_position_action_surfaces_high_confidence_eight_rule_risk():
    suggestion = _real_position_action(
        curr=22.0,
        entry=21.07,
        high_since_entry=22.8,
        risk={"active_stop_price": 21.28, "structure_stop_price": 19.17},
        pa={
            "pa_eight_rule_primary": {
                "label": "高位放量滞涨",
                "direction": "RISK",
                "confidence": 82,
                "invalidation_price": 21.7,
            }
        },
        signals=[],
        df_hist=_position_df(last_close=22.0, last_volume=2200),
    )

    assert "八诀风险触发：高位放量滞涨" in suggestion
    assert "停止加仓并复核减仓" in suggestion
    assert "八诀:高位放量滞涨 <21.7" in suggestion


def test_position_breakout_uses_price_action_dynamic_volume_threshold():
    plan = _position_breakout_confirmation(
        curr=22.5,
        entry=21.0,
        active_stop=20.0,
        pa={"pa_entry_price": 22.0, "pa_breakout_volume_threshold": 1.2},
        df_hist=_position_df(last_close=22.5, last_volume=1300),
    )

    assert plan["volume_ratio_threshold"] == 1.2
    assert plan["volume_ok"] is True


def test_candidate_brief_includes_logic_and_capital_lines():
    stock = {
        "代码": "000001", "名称": "测试", "sop_grade": "B",
        "trade_bucket": "TRADE", "trade_eligible": True,
        "market_sentiment_label": "修复 55分",
        "行业": "化工", "sector_phase": "SECTOR_CONFIRM", "sector_mainline": "MAIN",
        "sector_role": "CORE",
        "money_flow": {"main_net_inflow_yi": 2.13},
        "rps_120": 95.4,
        "sector_limit_count": 3,
    }

    body = "\n".join(_candidate_brief_lines(stock))

    assert "逻辑：市场修复 55分 → 化工板块主升·主线 → 板块核心" in body
    assert "资金：主力净流入2.1亿｜RPS120=95｜板块涨停3家" in body
