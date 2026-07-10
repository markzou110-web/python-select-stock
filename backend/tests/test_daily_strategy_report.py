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
