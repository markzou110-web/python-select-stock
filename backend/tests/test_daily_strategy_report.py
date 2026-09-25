import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.daily_strategy_report import build_daily_strategy_report, build_daily_strategy_report_body


def test_daily_strategy_report_counts_buckets_and_actions():
    scans = [
        {"代码": "000001", "名称": "一号", "行业": "机器人", "trade_bucket": "TRADE", "trade_eligible": True, "final_trade_score": 91, "sop_grade": "A"},
        {"代码": "000002", "名称": "二号", "行业": "机器人", "trade_bucket": "EARLY", "early_trade_candidate": True, "final_trade_score": 82, "early_trade_grade": "A-"},
        {"代码": "000003", "名称": "三号", "行业": "创新药", "trade_bucket": "OBSERVE", "final_trade_score": 72, "sop_grade": "M"},
        {"代码": "000004", "名称": "四号", "行业": "AI硬件", "trade_bucket": "BLOCK", "final_trade_score": 88, "sop_grade": "D"},
    ]
    gaps = [{
        "industry": "AI硬件",
        "has_push_candidate": False,
        "primary_reason_label": "涨幅偏高，等回踩确认",
    }]

    report = build_daily_strategy_report(
        scans,
        scan_date="2026-07-07",
        sector_gap_analysis=gaps,
        bark_push_count=2,
    )

    assert report["summary"]["scan_count"] == 4
    assert report["summary"]["trade_count"] == 1
    assert report["summary"]["early_count"] == 1
    assert report["summary"]["observe_count"] == 1
    assert report["summary"]["block_count"] == 1
    assert report["summary"]["bark_push_count"] == 2
    assert report["summary"]["stance"] == "有可交易候选，仍需按确认价/量能小仓复核"
    assert report["sector_push_gaps"] == gaps
    assert report["top_candidates"][0]["code"] == "000001"
    assert "热门板块先看未推原因" in " ".join(report["next_actions"])


def test_daily_strategy_report_body_is_compact_for_bark():
    report = {
        "scan_date": "2026-07-07",
        "summary": {
            "scan_count": 4,
            "trade_count": 1,
            "early_count": 1,
            "observe_count": 1,
            "block_count": 1,
            "bark_push_count": 2,
            "watchlist_count": 0,
            "real_position_count": 0,
            "stance": "有可交易候选",
        },
        "sector_push_gaps": [{"industry": "机器人", "primary_reason_label": "候选偏后排，暂不追"}],
        "next_actions": ["TRADE候选只在站稳确认价且量能确认时小仓复核"],
    }

    body = build_daily_strategy_report_body(report)

    assert "扫描4只 | TRADE 1 | EARLY 1 | OBSERVE 1 | BLOCK 1" in body
    assert "机器人：候选偏后排，暂不追" in body
    assert "明日动作：" in body


def test_daily_report_uses_bounded_display_score_and_non_trade_action():
    report = build_daily_strategy_report([{
        "代码": "000001",
        "名称": "示例",
        "trade_bucket": "OBSERVE",
        "trade_eligible": False,
        "final_trade_score": 107.03,
        "display_opportunity_score": 69.3,
        "trade_opportunity_label": "试错仓",
    }])

    candidate = report["top_candidates"][0]
    assert candidate["score"] == 69.3
    assert candidate["action"] == "仅观察"


def test_daily_report_does_not_count_blocked_trade_bucket_as_trade():
    report = build_daily_strategy_report([{
        "代码": "000001",
        "trade_bucket": "TRADE",
        "trade_eligible": False,
        "display_opportunity_score": 0,
        "final_trade_score": 88,
    }])

    assert report["summary"]["trade_count"] == 0
    assert report["top_candidates"] == []


def _ai_review():
    return {
        "batch_id": "scheduled-20260707181000",
        "review_date": "2026-07-07",
        "model": "test-model",
        "source": "scheduled",
        "market_summary": "候选结构偏强，注意量能确认",
        "analyses": [
            {
                "code": "000001", "name": "一号", "action": "BUY", "confidence": 82,
                "summary": "可进入人工复核", "strategy_rank": 2, "ai_rank": 1,
                "positive_factors": ["板块主线", "量价确认", "风报比合格"],
            },
            {
                "code": "000003", "name": "三号", "action": "WAIT", "confidence": 55,
                "summary": "等回踩确认", "strategy_rank": 1, "ai_rank": 2,
                "positive_factors": ["趋势尚可"],
            },
        ],
    }


def test_daily_report_includes_ai_review_section():
    report = build_daily_strategy_report(
        [{"代码": "000001", "trade_bucket": "TRADE", "trade_eligible": True}],
        ai_review=_ai_review(),
    )

    assert report["ai_review"]["status"] == "available"
    assert report["ai_review"]["model"] == "test-model"
    assert report["ai_review"]["counts"] == {"BUY": 1, "WAIT": 1}
    assert report["ai_review"]["top_analyses"][0]["action"] == "BUY"
    assert len(report["ai_review"]["top_analyses"]) == 1

    body = build_daily_strategy_report_body(report)
    assert "AI复核（test-model）BUY 1 / WAIT 1 / AVOID 0" in body
    assert "BUY 一号(000001)｜策略#2 → AI#1｜82分" in body
    assert "依据：板块主线；量价确认；风报比合格" in body
    assert "WAIT 1只、AVOID 0只仅留档观察，不作为本次推送标的" in body
    assert "三号" not in body


def test_daily_report_without_ai_review_keeps_status_none():
    report = build_daily_strategy_report([{"代码": "000001"}])

    assert report["ai_review"] == {"status": "none"}
    assert "AI复核" not in build_daily_strategy_report_body(report)


def test_daily_report_includes_industry_prosperity_and_logic_digest():
    scans = [
        {
            "代码": "000001", "名称": "一号", "行业": "白酒", "trade_bucket": "TRADE",
            "trade_eligible": True, "final_trade_score": 91, "ROE": 18.0, "净利YOY": 25.0,
            "market_sentiment_label": "修复 55分", "sector_phase": "SECTOR_CONFIRM",
            "sector_mainline": "MAIN", "sector_role": "CORE",
            "money_flow": {"main_net_inflow_yi": 2.13},
        },
        {
            "代码": "000002", "名称": "二号", "行业": "白酒", "trade_bucket": "OBSERVE",
            "final_trade_score": 82, "ROE": 12.0, "净利YOY": 35.0,
        },
    ]

    report = build_daily_strategy_report(scans, scan_date="2026-09-17")
    body = build_daily_strategy_report_body(report)

    assert report["industry_prosperity"][0]["industry"] == "白酒"
    assert report["industry_prosperity"][0]["label"] == "高景气"
    assert "行业景气（候选池基本面聚合）" in body
    assert "白酒" in body and "ROE中位" in body
    digest = report["top_candidates"][0]
    assert digest["logic"] == "逻辑：市场修复 55分 → 白酒板块主升·主线 → 板块核心"
    assert "主力净流入2.1亿" in digest["capital"]
