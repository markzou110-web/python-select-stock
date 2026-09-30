"""行业资金流排名采集（借鉴 easy-stock 趋势题材雷达；东财行业资金流）。

一次全市场调用拿到全部行业的当日主力净流入排名，归一后由
db.save_sector_fund_flow_rank 写 breadth_history 的 SECTOR 行（只更新
fund_flow_rank / main_force_net，不覆盖盘中宽度聚合列）。排名供候选证据门
的 sector_context 做"独立走强 vs 板块共振"参考，不进策略判定。

东财接口列名随 akshare 版本在 `--`/`-` 间变化，列解析用关键字匹配防御；
接口失败时 fail-open 返回 errors=1，绝不阻断调用方。
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

import akshare as ak
import pandas as pd

from core.config import config
from core.db import get_db_engine, save_sector_fund_flow_rank
from core.logging_config import logger

config.setup_no_proxy()


def _column(frame: pd.DataFrame, *keywords: str) -> Optional[str]:
    """按关键字子串找列名（全部命中才算），找不到返回 None。"""
    for column in frame.columns:
        if all(keyword in str(column) for keyword in keywords):
            return str(column)
    return None


def normalize_sector_fund_flow(frame: Optional[pd.DataFrame]) -> List[Dict[str, Any]]:
    """把东财行业资金流排名 DataFrame 归一为 [{industry, rank, main_force_net}]。

    main_force_net 统一为亿元（源数据为元）。找不到名称列或主力净额列时返回
    []（fail-open，不写半结构数据污染 breadth_history）。
    """
    if frame is None or frame.empty:
        return []
    name_col = _column(frame, "名称")
    net_col = _column(frame, "主力", "净额")
    if not name_col or not net_col:
        logger.warning(f"sector fund flow: unexpected columns {list(frame.columns)}")
        return []
    rank_col = _column(frame, "序号")
    net_values = pd.to_numeric(frame[net_col], errors="coerce")
    names = frame[name_col].astype(str).str.strip()
    ranks = pd.to_numeric(frame[rank_col], errors="coerce") if rank_col else None
    rows: List[Dict[str, Any]] = []
    for position in range(len(frame)):
        industry = names.iloc[position]
        net = net_values.iloc[position]
        if not industry or pd.isna(net):
            continue
        rank = int(ranks.iloc[position]) if ranks is not None and pd.notna(ranks.iloc[position]) else position + 1
        rows.append({
            "industry": str(industry),
            "rank": int(rank),
            "main_force_net": round(float(net) / 1e8, 2),
        })
    return rows


def collect_sector_fund_flow_rank(
    engine=None, bar_date: Optional[str] = None, fetcher=None,
) -> Dict[str, int]:
    """拉取并落库当日行业资金流排名；fetcher 可注入便于测试。"""
    fetcher = fetcher or (
        lambda: ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")
    )
    engine = engine or get_db_engine()
    bar_date = bar_date or datetime.now().strftime("%Y-%m-%d")
    try:
        frame = fetcher()
    except Exception as exc:
        logger.warning(f"sector fund flow fetch failed for {bar_date}: {exc}")
        return {"saved": 0, "industries": 0, "errors": 1}
    rows = normalize_sector_fund_flow(frame)
    saved = save_sector_fund_flow_rank(rows, engine=engine, bar_date=bar_date)
    return {"saved": saved, "industries": len(rows), "errors": 0}
