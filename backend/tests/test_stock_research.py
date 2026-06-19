import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core import stock_research
from core.data import CACHE


def setup_function():
    CACHE.clear()


def test_build_stock_research_signals_summarises_risk_and_opportunity(monkeypatch):
    monkeypatch.setattr(stock_research, "eastmoney_concept_blocks", lambda code: {"total": 1, "concept_tags": ["银行"]})
    monkeypatch.setattr(stock_research, "get_stock_money_flow", lambda code: {
        "status": "ok",
        "summary": {"main_net_5d_yi": 2.5, "consecutive_direction": "inflow", "consecutive_days": 3},
    })
    monkeypatch.setattr(stock_research, "dragon_tiger_board", lambda code, trade_date, look_back: {
        "records": [{"date": "2026-06-01", "reason": "涨幅偏离", "net_buy_wan": 3000}],
        "institution": {"net_amt_wan": 1200},
    })
    monkeypatch.setattr(stock_research, "ths_hot_reason", lambda trade_date: [
        {"code": "000001", "reason": "金融科技+银行", "change_pct": 5.5}
    ])
    monkeypatch.setattr(stock_research, "eastmoney_fund_flow_minute", lambda code: [
        {"time": "2026-06-05 14:55", "main_net": 30000000}
    ])
    monkeypatch.setattr(stock_research, "industry_comparison", lambda top_n: {
        "total": 10,
        "top": [
            {"rank": 1, "name": "银行", "change_pct": 2.2, "up_count": 30, "down_count": 2},
            {"rank": 10, "name": "煤炭", "change_pct": -2.0, "up_count": 2, "down_count": 30},
        ],
        "bottom": [],
    })
    monkeypatch.setattr(stock_research, "daily_dragon_tiger", lambda trade_date: {
        "date": trade_date,
        "total_records": 1,
        "stocks": [{"code": "000001", "net_buy_wan": 6500, "reason": "涨幅偏离"}],
    })
    monkeypatch.setattr(stock_research, "lockup_expiry", lambda code, trade_date, forward_days: {
        "history": [],
        "upcoming": [{"date": "2026-07-01", "ratio": 6.2}],
    })
    monkeypatch.setattr(stock_research, "margin_trading", lambda code, page_size: [
        {"date": "2026-06-05", "rzye": 2000000000},
        {"date": "2026-06-04", "rzye": 1900000000},
        {"date": "2026-06-03", "rzye": 1800000000},
        {"date": "2026-06-02", "rzye": 1700000000},
        {"date": "2026-06-01", "rzye": 1300000000},
    ])
    monkeypatch.setattr(stock_research, "block_trade", lambda code, page_size: [
        {"date": "2026-06-01", "premium_pct": -4.0},
        {"date": "2026-06-02", "premium_pct": -5.0},
    ])
    monkeypatch.setattr(stock_research, "holder_num_change", lambda code, page_size: [
        {"date": "2026-03-31", "change_ratio": -4.5},
    ])
    monkeypatch.setattr(stock_research, "dividend_history", lambda code, page_size: [{"bonus_rmb": 1.2}])
    monkeypatch.setattr(stock_research, "eastmoney_reports", lambda code, max_pages: [{"rating": "买入", "title": "深度"}])
    monkeypatch.setattr(stock_research, "eastmoney_stock_news", lambda code, page_size: [{"title": "公司收到问询函", "time": "2026-06-01"}])
    monkeypatch.setattr(stock_research, "cninfo_announcements", lambda code, page_size: [])

    result = stock_research.build_stock_research_signals("000001", trade_date="2026-06-05", force_refresh=True)

    assert result["status"] == "ok"
    assert "未来90天存在限售解禁" in result["summary"]["risk_flags"][0]
    assert any("近5日主力净流入" in item for item in result["summary"]["opportunity_flags"])
    assert result["summary"]["components"]["lockup"] == -6
    assert result["summary"]["components"]["holder_count"] == 2
    assert result["summary"]["components"]["hot_theme"] == 2.5
    assert result["summary"]["components"]["industry_rank"] == 1.5
    assert result["hot_theme"]["matched"] is True
    assert result["daily_dragon_tiger"]["matched"] is True


def test_apply_research_adjustment_updates_scan_candidate():
    stock = {"代码": "000001", "Score": 70, "final_trade_score": 72, "final_rank_score": 71}
    research = {
        "summary": {
            "score": 30,
            "score_delta": -5,
            "label": "事件风险",
            "components": {"lockup": -6},
            "risk_flags": ["未来90天存在限售解禁，最高解禁比例 6.20%"],
            "opportunity_flags": [],
        }
    }

    stock_research.apply_research_adjustment(stock, research)

    assert stock["research_label"] == "事件风险"
    assert stock["final_trade_score"] == 67
    assert stock["final_rank_score"] == 66
    assert "研究事件风险，降级观察" in stock["trade_blockers"]
