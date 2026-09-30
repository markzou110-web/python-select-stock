"""行业资金流排名采集与证据门共振维度测试（借鉴 easy-stock 题材雷达）。

覆盖：东财列名归一（新旧 `--`/`-` 两种分隔）、fail-open、breadth_history
SECTOR 行落库与宽度列互不覆盖、映射读取的点内性、候选证据门的
sector_context 注入与 decision_memo 板块共振叙述。
"""
import os
import sys

import pandas as pd
from sqlalchemy import create_engine, text

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.candidate_evidence import build_candidate_evidence
from core.db import (
    init_db,
    load_sector_fund_flow_map,
    record_breadth_snapshot,
    save_sector_fund_flow_rank,
)
from core.models import Base
from core.sector_fund_flow import collect_sector_fund_flow_rank, normalize_sector_fund_flow

BAR_DATE = "2026-09-25"


def _engine():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    init_db(engine)
    return engine


def _rank_frame(separator: str) -> pd.DataFrame:
    return pd.DataFrame({
        "序号": [1, 2, 3],
        "名称": ["半导体", "证券", "银行"],
        "今日涨跌幅": [2.1, 1.2, 0.3],
        f"主力净流入{separator}净额": [5e9, -2e9, 8e8],
        f"主力净流入{separator}净占比": [7.1, -3.2, 1.1],
    })


def test_normalize_handles_both_column_styles_and_missing_rank():
    rows = normalize_sector_fund_flow(_rank_frame("--"))
    assert [(row["industry"], row["rank"], row["main_force_net"]) for row in rows] == [
        ("半导体", 1, 50.0), ("证券", 2, -20.0), ("银行", 3, 8.0),
    ]
    rows = normalize_sector_fund_flow(_rank_frame("-"))
    assert rows[0]["industry"] == "半导体"

    frame = _rank_frame("--").drop(columns=["序号"])
    rows = normalize_sector_fund_flow(frame)
    assert [row["rank"] for row in rows] == [1, 2, 3]  # 缺序号列时按行序补

    frame = _rank_frame("--").drop(columns=["主力净流入--净额"])
    assert normalize_sector_fund_flow(frame) == []
    assert normalize_sector_fund_flow(pd.DataFrame()) == []
    assert normalize_sector_fund_flow(None) == []


def test_save_and_load_roundtrip_is_point_in_time():
    engine = _engine()
    rows = [{"industry": "半导体", "rank": 1, "main_force_net": 50.0},
            {"industry": "证券", "rank": 2, "main_force_net": -20.0}]
    assert save_sector_fund_flow_rank(rows, engine=engine, bar_date=BAR_DATE) == 2

    mapping = load_sector_fund_flow_map(engine)
    assert mapping["半导体"] == {"rank": 1, "main_force_net": 50.0, "bar_date": BAR_DATE}
    # 点内读取：早于采集日的查询拿不到未来数据
    assert load_sector_fund_flow_map(engine, bar_date="2026-09-24") == {}

    # 无任何数据的引擎 fail-open
    assert load_sector_fund_flow_map(_engine()) == {}
    assert load_sector_fund_flow_map(None) == {}


def test_save_does_not_overwrite_breadth_aggregate_columns():
    engine = _engine()
    snapshot = pd.DataFrame([
        {"code": "600000", "pct_chg": 5.2, "industry": "证券"},
        {"code": "600001", "pct_chg": -1.0, "industry": "证券"},
    ])
    record_breadth_snapshot(snapshot, {}, engine=engine, bar_date=BAR_DATE)

    assert save_sector_fund_flow_rank(
        [{"industry": "证券", "rank": 2, "main_force_net": -20.0}],
        engine=engine, bar_date=BAR_DATE,
    ) == 1

    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT * FROM breadth_history WHERE scope='SECTOR' AND industry='证券' AND bar_date=:d"
        ), {"d": BAR_DATE}).mappings().one()
    assert row["fund_flow_rank"] == 2
    assert row["advance_ratio"] is not None  # 宽度聚合列未被覆盖为 NULL
    assert row["strong_ratio"] is not None


def test_collect_uses_injected_fetcher_and_fails_open():
    engine = _engine()
    result = collect_sector_fund_flow_rank(
        engine=engine, bar_date=BAR_DATE, fetcher=lambda: _rank_frame("--"),
    )
    assert result == {"saved": 3, "industries": 3, "errors": 0}
    assert "半导体" in load_sector_fund_flow_map(engine)

    def _boom():
        raise RuntimeError("em blocked")
    result = collect_sector_fund_flow_rank(engine=engine, bar_date=BAR_DATE, fetcher=_boom)
    assert result["errors"] == 1 and result["saved"] == 0


def _candidate_with_flow(rank, net=2.5):
    return {
        "代码": "600000",
        "名称": "测试股",
        "现价": 10.0,
        "strategy_type": "tv",
        "sector_mainline": "ACTIVE",
        "sector_fund_flow_rank": rank,
        "sector_main_force_net": net,
        "sector_flow_bar_date": BAR_DATE,
        "as_of": f"{BAR_DATE}T15:00:00",
    }


def test_evidence_sector_context_and_memo_include_fund_flow():
    bundle = build_candidate_evidence(_candidate_with_flow(3))
    items = bundle["domains"]["sector_context"]["items"]
    assert items["industry_fund_flow_rank"] == 3
    assert items["industry_main_force_net"] == 2.5
    assert items["industry_flow_bar_date"] == BAR_DATE
    memo_texts = [item["text"] for item in bundle["decision_memo"]["bull_case"]]
    assert any("资金流排名第 3" in text_ and "板块资金共振" in text_ for text_ in memo_texts)


def test_evidence_ignores_rank_outside_top_threshold():
    bundle = build_candidate_evidence(_candidate_with_flow(45))
    assert bundle["domains"]["sector_context"]["items"]["industry_fund_flow_rank"] == 45
    memo_texts = [item["text"] for item in bundle["decision_memo"]["bull_case"]]
    assert not any("板块资金共振" in text_ for text_ in memo_texts)


def test_evidence_without_flow_data_is_unchanged():
    bundle = build_candidate_evidence({"代码": "600000", "现价": 10.0, "as_of": f"{BAR_DATE}T15:00:00"})
    items = bundle["domains"]["sector_context"]["items"]
    assert items["industry_fund_flow_rank"] is None
    memo_texts = [item["text"] for item in bundle["decision_memo"]["bull_case"]]
    assert not any("板块资金共振" in text_ for text_ in memo_texts)
