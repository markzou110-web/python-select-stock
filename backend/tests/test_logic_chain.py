import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from core.industry_prosperity import build_industry_prosperity, prosperity_text
from core.logic_chain import build_capital_evidence_line, build_logic_chain_line


def test_logic_chain_line_joins_market_sector_role():
    stock = {
        "market_sentiment_label": "修复 55分",
        "行业": "化工",
        "sector_phase": "SECTOR_CONFIRM",
        "sector_mainline": "MAIN",
        "sector_role": "CORE",
    }

    assert build_logic_chain_line(stock) == "逻辑：市场修复 55分 → 化工板块主升·主线 → 板块核心"


def test_logic_chain_line_skips_when_fields_missing():
    assert build_logic_chain_line({"代码": "000001"}) == ""
    # 只有一段信息不足以构成因果链，不渲染
    assert build_logic_chain_line({"market_sentiment_label": "修复"}) == ""


def test_capital_evidence_line_formats_flow_rps_limit_prosperity():
    stock = {
        "money_flow": {"main_net_inflow_yi": -2.13},
        "rps_120": 95.4,
        "sector_limit_count": 3,
        "industry_prosperity": {"label": "高景气"},
    }

    assert build_capital_evidence_line(stock) == (
        "资金：主力净流出2.1亿｜RPS120=95｜板块涨停3家｜行业高景气"
    )


def test_capital_evidence_line_inflow_and_empty_cases():
    assert build_capital_evidence_line({"money_flow": {"main_net_inflow_yi": 1.46}}) == (
        "资金：主力净流入1.5亿"
    )
    assert build_capital_evidence_line({}) == ""


def test_industry_prosperity_aggregates_medians_and_labels():
    rows = [
        {"行业": "白酒", "ROE": 18.0, "净利YOY": 25.0},
        {"行业": "白酒", "ROE": 12.0, "净利YOY": 35.0},
        {"行业": "白酒", "ROE": 10.0, "净利YOY": -5.0},
        {"行业": "煤炭", "ROE": 9.0, "净利YOY": -20.0},
        {"行业": "煤炭", "ROE": 7.0, "净利YOY": -10.0},
        # 样本不足的行业不输出，避免单票噪音被当成行业景气
        {"行业": "孤例", "ROE": 30.0, "净利YOY": 50.0},
    ]

    result = build_industry_prosperity(rows)

    assert set(result) == {"白酒", "煤炭"}
    baijiu = result["白酒"]
    assert baijiu["sample_count"] == 3
    assert baijiu["roe_median"] == 12.0
    assert baijiu["yoy_median"] == 25.0
    assert baijiu["positive_yoy_ratio"] == 66.7
    assert baijiu["prosperity_score"] == 73.0
    assert baijiu["label"] == "高景气"
    assert result["煤炭"]["label"] == "弱景气"


def test_prosperity_text_renders_readonly_summary():
    text = prosperity_text({"label": "高景气", "roe_median": 12.0, "yoy_median": 25.0, "sample_count": 3})

    assert "高景气" in text
    assert "ROE中位12.0%" in text
    assert "净利同比中位+25.0%" in text
    assert prosperity_text({}) == ""
