import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.sector_push_analysis import build_hot_sector_push_gap_analysis


def test_hot_sector_with_trade_candidate_reports_has_push():
    sectors = [{
        "industry": "机器人",
        "sector_phase": "SECTOR_CONFIRM",
        "sector_momentum_score": 82,
        "sector_breadth": 76,
        "leaders": [{"code": "000001", "name": "核心A"}],
    }]
    scans = [{
        "代码": "000001",
        "名称": "核心A",
        "行业": "机器人",
        "现价": 12.34,
        "trade_bucket": "TRADE",
        "trade_eligible": True,
        "sector_role": "LEADER",
        "final_trade_score": 92,
    }]

    result = build_hot_sector_push_gap_analysis(sectors, scans)

    assert result[0]["has_push_candidate"] is True
    assert result[0]["primary_reason"] == "HAS_PUSH"
    assert result[0]["primary_reason_label"] == "已有可推候选"
    assert result[0]["representative_candidates"][0]["code"] == "000001"
    assert result[0]["representative_candidates"][0]["industry"] == "机器人"
    assert result[0]["representative_candidates"][0]["price"] == 12.34


def test_hot_sector_rear_candidate_explains_no_push_reason():
    sectors = [{
        "industry": "机器人",
        "sector_phase": "SECTOR_CONFIRM",
        "sector_momentum_score": 88,
        "sector_breadth": 80,
    }]
    scans = [{
        "代码": "000002",
        "名称": "后排B",
        "行业": "机器人",
        "trade_bucket": "OBSERVE",
        "trade_eligible": False,
        "sector_role": "FOLLOWER",
        "sector_rear_role_watch": True,
        "trade_blockers": ["强板块后排角色，等待转强为核心股"],
        "final_trade_score": 78,
    }]

    result = build_hot_sector_push_gap_analysis(sectors, scans)

    assert result[0]["has_push_candidate"] is False
    assert result[0]["primary_reason"] == "REAR_ROLE"
    assert result[0]["primary_reason_label"] == "候选偏后排，暂不追"
    assert result[0]["reason_counts"]["REAR_ROLE"] == 1


def test_hot_sector_without_scan_candidate_explains_strategy_miss():
    sectors = [{
        "industry": "创新药",
        "sector_phase": "SECTOR_EARLY",
        "sector_momentum_score": 68,
        "sector_breadth": 64,
    }]

    result = build_hot_sector_push_gap_analysis(sectors, [], limit=3)

    assert result[0]["scan_candidate_count"] == 0
    assert result[0]["primary_reason"] == "NO_SCAN_CANDIDATE"
    assert result[0]["primary_reason_label"] == "板块强，但扫描策略没有选出候选"


def test_hot_sector_classifies_extended_and_unconfirmed_buy_point():
    sectors = [{
        "industry": "AI硬件",
        "sector_phase": "SECTOR_CLIMAX",
        "sector_momentum_score": 91,
        "sector_breadth": 72,
    }]
    scans = [
        {
            "代码": "000003",
            "名称": "高位C",
            "行业": "AI硬件",
            "trade_bucket": "OBSERVE",
            "trade_blockers": ["涨停/近涨停，等待隔日确认"],
            "final_trade_score": 81,
        },
        {
            "代码": "000004",
            "名称": "待确认D",
            "行业": "AI硬件",
            "trade_bucket": "OBSERVE",
            "trade_blockers": ["未站上确认价，等待突破确认"],
            "final_trade_score": 75,
        },
    ]

    result = build_hot_sector_push_gap_analysis(sectors, scans)

    assert result[0]["primary_reason"] == "TOO_EXTENDED"
    assert result[0]["reason_counts"] == {"TOO_EXTENDED": 1, "NO_BUY_POINT": 1}
