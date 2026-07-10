import os
import sys
from datetime import datetime, timedelta
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from routers import watchlist


def test_build_morning_status_body_has_explicit_price_instructions():
    body = watchlist._build_watchlist_status_body(
        [
            {
                "code": "002378",
                "name": "章源钨业",
                "current_price": 33.10,
                "pl_pct": 1.2,
                "computed_decision": "NEAR_TRIGGER",
                "computed_action": "接近触发：只等放量站稳，不提前追",
                "trigger_price": 33.64,
                "guard_price": 28.51,
                "operation_instruction": "超过33.64且放量站稳后再考虑小仓",
                "market_sentiment_label": "修复",
                "market_sentiment_score": 52.0,
                "portfolio_position_cap_pct": 40,
            }
        ],
        "morning",
    )

    assert "观察池晨间" not in body
    assert "不抢开盘" in body
    assert "站稳 >33.64" in body
    assert "跌破 <28.51" in body
    assert "超过33.64且放量站稳后再考虑小仓" in body


def test_theme_momentum_alert_detects_cluster_and_keeps_no_chase_wording():
    payload = watchlist._build_theme_momentum_alert([
        {"code": "002472", "name": "双环传动", "theme": "物理AI+人形机器人", "pct_chg": 9.99, "current_price": 47.01, "limit_up": 47.02},
        {"code": "002747", "name": "埃斯顿", "theme": "物理AI+人形机器人", "pct_chg": 10.0, "current_price": 44.77, "limit_up": 44.78},
        {"code": "601689", "name": "拓普集团", "theme": "物理AI+人形机器人", "pct_chg": 6.5, "current_price": 60.0, "limit_up": 66.0},
        {"code": "300124", "name": "汇川技术", "theme": "物理AI+人形机器人", "pct_chg": 5.2, "current_price": 72.0, "limit_up": 80.0},
    ])
    body = watchlist._build_theme_momentum_body(payload["alerts"], "09:35")

    assert payload["count"] == 1
    assert "物理AI+人形机器人" in body
    assert "题材异动预警，不是买入指令" in body
    assert "涨停/大涨不追" in body
    assert "拓普集团" in body


def test_send_theme_momentum_alert_can_render_without_notifying(monkeypatch):
    watch_df = pd.DataFrame([
        {"code": "002472", "name": "双环传动", "industry": "汽车配件", "theme": "物理AI+人形机器人", "watch_price": 42, "status": "WATCHING", "rise_logic": ""},
        {"code": "002747", "name": "埃斯顿", "industry": "机械基件", "theme": "物理AI+人形机器人", "watch_price": 40, "status": "WATCHING", "rise_logic": ""},
        {"code": "601689", "name": "拓普集团", "industry": "汽车配件", "theme": "物理AI+人形机器人", "watch_price": 58, "status": "WATCHING", "rise_logic": ""},
    ])
    snapshot = pd.DataFrame([
        {"code": "002472", "price": 47.01, "pct_chg": 9.99, "turnover": 9.3, "amount": 3_180_570_000, "limit_up": 47.02},
        {"code": "002747", "price": 44.77, "pct_chg": 10.0, "turnover": 15.8, "amount": 5_392_010_000, "limit_up": 44.78},
        {"code": "601689", "price": 61.37, "pct_chg": 8.14, "turnover": 3.2, "amount": 3_316_820_000, "limit_up": 67.5},
    ])

    monkeypatch.setattr(watchlist, "get_db_engine", lambda: object())
    monkeypatch.setattr(watchlist.pd, "read_sql", lambda *args, **kwargs: watch_df)

    import core.data as data
    monkeypatch.setattr(data, "get_market_snapshot", lambda: snapshot)
    monkeypatch.setattr(data, "is_snapshot_stale", lambda _snapshot: False)
    monkeypatch.setattr(data, "get_stale_cache", lambda _key: snapshot)
    monkeypatch.setattr(data, "format_freshness", lambda _snapshot: "行情：实时")

    result = watchlist.send_theme_momentum_alert(slot="09:35", notify=False)

    assert result["notification"] is False
    assert result["count"] == 1
    assert "双环传动" in result["body"]
    assert "不是买入指令" in result["body"]


def test_build_status_body_includes_execution_state():
    body = watchlist._build_watchlist_status_body(
        [
            {
                "code": "002378",
                "name": "章源钨业",
                "current_price": 35.00,
                "pl_pct": 6.1,
                "computed_decision": "TRIGGERED",
                "computed_action": "已触发目标价：待确认是否转入拟合实盘",
                "trigger_price": 33.64,
                "guard_price": 28.51,
                "execution_label": "高位不追",
                "execution_action": "已高出触发价3%以上，等待回踩确认",
                "market_sentiment_label": "修复",
                "market_sentiment_score": 52.0,
                "portfolio_position_cap_pct": 40,
            }
        ],
        "morning",
    )

    assert "执行状态：高位不追" in body
    assert "等待回踩确认" in body


def test_build_status_body_includes_theme_tracking_state():
    body = watchlist._build_watchlist_status_body(
        [
            {
                "code": "002472",
                "name": "双环传动",
                "current_price": 47.01,
                "pl_pct": 2.3,
                "computed_decision": "KEEP_WATCH",
                "computed_action": "继续观察：等待回踩/放量站稳或再次入选",
                "trigger_price": 48.30,
                "guard_price": 42.80,
                "theme_tracking_label": "等买点",
                "theme_tracking_action": "接近触发价，等待放量站稳，不提前追",
                "market_sentiment_label": "修复",
                "market_sentiment_score": 52.0,
                "portfolio_position_cap_pct": 40,
            }
        ],
        "noon",
    )

    assert "题材状态：等买点" in body
    assert "等待放量站稳，不提前追" in body
    assert "午间纪律" in body


def test_watchlist_status_body_orders_by_theme_state_performance_boost():
    items = [
        {
            "code": "000001",
            "name": "弱状态票",
            "current_price": 10.00,
            "pl_pct": 0.5,
            "computed_decision": "KEEP_WATCH",
            "computed_action": "继续观察",
            "trigger_price": 11.00,
            "guard_price": 9.20,
            "theme_tracking_state": "WAIT_PULLBACK",
            "theme_tracking_label": "涨幅偏高等回踩",
            "theme_tracking_action": "等待回踩不破支撑后再确认",
            "market_sentiment_label": "修复",
            "market_sentiment_score": 52.0,
            "portfolio_position_cap_pct": 40,
        },
        {
            "code": "000002",
            "name": "强状态票",
            "current_price": 10.00,
            "pl_pct": 0.1,
            "computed_decision": "KEEP_WATCH",
            "computed_action": "继续观察",
            "trigger_price": 11.00,
            "guard_price": 9.20,
            "theme_tracking_state": "PULLBACK_CONFIRMED",
            "theme_tracking_label": "回踩放量确认",
            "theme_tracking_action": "尾盘站稳确认价后再复核",
            "market_sentiment_label": "修复",
            "market_sentiment_score": 52.0,
            "portfolio_position_cap_pct": 40,
        },
    ]

    body = watchlist._build_watchlist_status_body(
        items,
        "noon",
        state_boosts={"PULLBACK_CONFIRMED": 25, "WAIT_PULLBACK": -25},
    )

    assert body.index("强状态票") < body.index("弱状态票")
    assert "状态复盘加权：回踩放量确认近5日表现较好" in body
    assert "状态复盘降权：涨幅偏高等回踩近5日表现偏弱" in body


def test_watch_execution_state_classifies_no_chase_and_faded_breakout():
    no_chase = watchlist._watch_execution_state(
        {
            "current_price": 35.00,
            "watch_price": 31.00,
            "trigger_price": 33.64,
            "guard_price": 28.51,
        }
    )
    faded = watchlist._watch_execution_state(
        {
            "current_price": 33.20,
            "watch_price": 31.00,
            "trigger_price": 33.64,
            "guard_price": 28.51,
            "intraday_high": 34.10,
        }
    )

    assert no_chase["state"] == "NO_CHASE"
    assert no_chase["label"] == "高位不追"
    assert faded["state"] == "FADED"
    assert faded["label"] == "冲高回落"


def test_no_chase_status_body_never_suggests_buying_above_trigger():
    body = watchlist._build_watchlist_status_body([{
        "code": "603949",
        "name": "雪龙集团",
        "current_price": 18.93,
        "pl_pct": 5.52,
        "computed_decision": "TRIGGERED",
        "computed_action": "已触发目标价：待确认是否转入拟合实盘",
        "execution_state": "NO_CHASE",
        "execution_label": "高位不追",
        "execution_action": "已高出触发价3%以上，等待回踩确认",
        "trigger_price": 18.13,
        "guard_price": 17.05,
        "operation_instruction": ">18.13: 可转拟合实盘/小仓试买",
    }], "late")

    assert "禁止转实盘或追价" in body
    assert "不以继续上涨作为买点" in body
    assert "可转拟合实盘/小仓试买" not in body


def test_build_status_body_blocks_new_position_during_retreat():
    body = watchlist._build_watchlist_status_body(
        [
            {
                "code": "300481",
                "name": "濮阳惠成",
                "current_price": 17.03,
                "computed_decision": "READY_WAIT",
                "computed_action": "结构就绪：仍需站稳触发价，未触发不买",
                "trigger_price": 17.04,
                "guard_price": 13.08,
                "market_sentiment_stage": "RETREAT",
                "market_sentiment_label": "退潮",
                "market_sentiment_score": 24.5,
                "portfolio_position_cap_pct": 10,
            }
        ],
        "late",
    )

    assert "市场：退潮 24.5分" in body
    assert "市场退潮，禁止新增仓位" in body
    assert "今日只观察，不买入" in body


def test_ready_setup_still_waits_for_price_trigger():
    decision = watchlist._watch_decision(
        {
            "pa_trade_action": "READY",
            "current_price": 17.03,
            "target_price": 17.04,
            "target_hit": False,
        }
    )

    assert decision["decision"] == "NEAR_TRIGGER"


def test_send_watchlist_status_report_skips_empty_watchlist(monkeypatch):
    monkeypatch.setattr(watchlist, "list_watchlist", lambda status="WATCHING": {"items": []})

    result = watchlist.send_watchlist_status_report("late")

    assert result == {"bark": False, "count": 0, "reason": "empty watchlist"}


def test_build_status_body_includes_triggered_section():
    """TRIGGERED 票应单独分区展示，与 WATCHING 票视觉分离。"""
    body = watchlist._build_watchlist_status_body(
        [
            {
                "code": "601138",
                "name": "工业富联",
                "current_price": 78.62,
                "pl_pct": 0.69,
                "computed_decision": "NEAR_TRIGGER",
                "computed_action": "接近触发：只等放量站稳，不提前追",
                "trigger_price": 79.50,
                "guard_price": 70.00,
                "market_sentiment_label": "修复",
                "market_sentiment_score": 52.0,
                "portfolio_position_cap_pct": 40,
            },
            {
                "code": "600460",
                "name": "士兰微",
                "current_price": 42.50,
                "pl_pct": 11.0,
                "computed_decision": "TRIGGERED",
                "computed_action": "已触发目标价：待确认是否转入拟合实盘",
                "trigger_price": 38.92,
                "guard_price": 32.09,
                "market_sentiment_label": "修复",
                "market_sentiment_score": 52.0,
                "portfolio_position_cap_pct": 40,
            },
        ],
        "morning",
    )

    assert "--- 已触发待确认 ---" in body
    assert "士兰微(600460)" in body
    assert "已触发目标价" in body
    # WATCHING 段在分隔线之前
    assert body.index("工业富联") < body.index("--- 已触发待确认 ---")
    # TRIGGERED 段在分隔线之后
    assert body.index("士兰微") > body.index("--- 已触发待确认 ---")


def test_send_watchlist_status_report_combines_watching_and_triggered(monkeypatch):
    """晨报应同时拉取 WATCHING 和 TRIGGERED 两个池，并正确计数。"""

    def fake_list_watchlist(status="WATCHING"):
        if status == "WATCHING":
            return {"items": [{"code": "601138", "name": "工业富联", "status": "WATCHING"}]}
        if status == "TRIGGERED":
            return {"items": [{"code": "600460", "name": "士兰微", "status": "TRIGGERED"}]}
        return {"items": []}

    monkeypatch.setattr(watchlist, "list_watchlist", fake_list_watchlist)
    # 跳过实时快照刷新（无交易时也能渲染）
    monkeypatch.setattr(
        watchlist,
        "_refresh_items_with_snapshot",
        lambda items, require_live_snapshot=False: items,
    )
    # 拦截 Bark 发送（notifier.send 是 async，mock 也须返回协程）
    async def fake_send(*args, **kwargs):
        return {"bark": True}

    monkeypatch.setattr(watchlist.notifier, "send", fake_send)

    result = watchlist.send_watchlist_status_report("morning")

    assert result["bark"] is True
    assert result["count"] == 2
    assert "士兰微" in result["body"]
    assert "工业富联" in result["body"]


def test_send_watchlist_status_report_can_render_without_notifying(monkeypatch):
    def fake_list_watchlist(status="WATCHING"):
        if status == "WATCHING":
            return {"items": [{"code": "601138", "name": "工业富联", "status": "WATCHING"}]}
        return {"items": []}

    monkeypatch.setattr(watchlist, "list_watchlist", fake_list_watchlist)
    monkeypatch.setattr(watchlist, "_refresh_items_with_snapshot", lambda items, require_live_snapshot=False: items)

    async def fail_send(*args, **kwargs):
        raise AssertionError("notifier.send should not be called")

    monkeypatch.setattr(watchlist.notifier, "send", fail_send)

    result = watchlist.send_watchlist_status_report("late", notify=False)

    assert result["bark"] is False
    assert result["notification"] is False
    assert result["count"] == 1
    assert "工业富联" in result["body"]


def test_build_status_body_with_only_triggered_items():
    """边界：WATCHING 池为空但 TRIGGERED 池有票时仍应正常渲染。"""
    body = watchlist._build_watchlist_status_body(
        [
            {
                "code": "600460",
                "name": "士兰微",
                "current_price": 42.50,
                "pl_pct": 11.0,
                "computed_decision": "TRIGGERED",
                "computed_action": "已触发目标价：待确认是否转入拟合实盘",
                "trigger_price": 38.92,
                "guard_price": 32.09,
                "market_sentiment_label": "修复",
                "market_sentiment_score": 52.0,
                "portfolio_position_cap_pct": 40,
            },
        ],
        "late",
    )

    assert "士兰微(600460)" in body
    assert "已触发目标价" in body


def test_refresh_snapshot_keeps_triggered_items_in_pending_bucket(monkeypatch):
    """TRIGGERED 票刷新实时价后仍应作为已触发待确认继续推送。"""

    class FakeSnapshot:
        empty = False

        def set_index(self, *_args, **_kwargs):
            return self

        def __getitem__(self, _key):
            return self

        def to_dict(self):
            return {"002440": 11.54}

    monkeypatch.setattr("core.data.get_market_snapshot", lambda: FakeSnapshot())
    monkeypatch.setattr("core.data.get_market_regime", lambda: {})
    monkeypatch.setattr(watchlist, "get_db_engine", lambda: object())
    monkeypatch.setattr("core.decision_layer.load_market_cycle_history", lambda _engine: [])
    monkeypatch.setattr(
        "core.decision_layer.build_market_decision_context",
        lambda *_args, **_kwargs: {
            "market_sentiment_label": "修复",
            "market_sentiment_score": 57.2,
            "portfolio_position_cap_pct": 40,
        },
    )

    items = watchlist._refresh_items_with_snapshot(
        [
            {
                "code": "002440",
                "name": "闰土股份",
                "status": "TRIGGERED",
                "watch_price": 10.99,
                "target_price": 11.00,
                "stop_price": 9.49,
            }
        ],
        require_live_snapshot=True,
    )

    assert items is not None
    assert items[0]["computed_decision"] == "TRIGGERED"
    assert "待确认是否转入拟合实盘" in items[0]["computed_action"]
    assert items[0]["operation_instruction"]


def test_old_triggered_item_is_marked_for_archive():
    item = {
        "status": "TRIGGERED",
        "updated_at": (datetime.now() - timedelta(days=8)).isoformat(),
        "execution_state": "NO_CHASE",
    }

    result = watchlist._triggered_archive_decision(item, max_triggered_days=5)

    assert result["should_archive"] is True
    assert "自动归档" in result["reason"]


def test_confirming_triggered_item_is_not_archived():
    item = {
        "status": "TRIGGERED",
        "updated_at": (datetime.now() - timedelta(days=8)).isoformat(),
        "execution_state": "CONFIRM",
    }

    result = watchlist._triggered_archive_decision(item, max_triggered_days=5)

    assert result == {"should_archive": False, "reason": ""}
