"""题材热度看板采集与评分（借鉴 easy-stock 主题热点页）。

数据拼装（全部东财公开接口 + 库内已有数据，零新依赖）：
  1. 板块行情：概念/行业板块列表（名称、涨跌幅）
  2. 资金流：stock_sector_fund_flow_rank（今日/3日主力净额）
  3. 成分：top-N 热度候选板块的成分股（用于涨停统计/MA20 占比/成员列表）
  4. 涨停：limit_up_events 近 3 日按成分归组（连板梯队）
  5. 人气：stock_hot_rank_em top100 与成分的交集
  6. MA20：成分股近 22 日收盘 → 站上 MA20 占比
  7. 新闻证据：news_raw 近 3 日标题按主题关键词匹配（规则模板理由，无 LLM）

热度分 v1（SHADOW/研究用，公式见 heat_score；不进任何交易资格判定）：
  0.35*z(3日涨跌) + 0.30*z(3日主力净额) + 0.20*涨停热度 + 0.15*z(人气重叠)
  → 线性映射 0-100。接口失败逐项 fail-open（缺项按中性 50 分处理）。
"""
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

import akshare as ak
import numpy as np
import pandas as pd
from sqlalchemy import bindparam, text

from core.config import config
from core.db import get_db_engine, save_theme_heat_history
from core.logging_config import logger

config.setup_no_proxy()

TOP_THEMES = 30          # 抓成分与详情的板块数
MEMBER_CAP = 120         # 每主题保留成分上限
NEWS_LOOKBACK_DAYS = 3


def _column(frame: pd.DataFrame, *keywords: str) -> Optional[str]:
    for column in frame.columns:
        if all(keyword in str(column) for keyword in keywords):
            return str(column)
    return None


def _zscores(values: pd.Series) -> pd.Series:
    std = float(values.std(ddof=0))
    if not values.size or std == 0 or pd.isna(std):
        return pd.Series(50.0, index=values.index)
    z = (values - values.mean()) / std
    return (50 + 20 * z).clip(0, 100)


def heat_score(chg_norm: float, flow_norm: float, limit_up_heat: float, hot_norm: float) -> float:
    """四分量加权（分量均为 0-100 归一化）：0.35 涨跌 + 0.30 资金 + 0.20 涨停 + 0.15 人气。

    v1 研究评分（SHADOW）：权重为首版经验值，未经本库点内验证，不进任何
    交易资格判定；后续可用 outcome Calibration 调权。"""
    return round(
        0.35 * float(chg_norm or 50.0) + 0.30 * float(flow_norm or 50.0)
        + 0.20 * float(limit_up_heat or 0.0) + 0.15 * float(hot_norm or 50.0), 1)


def _rank_map(frame: pd.DataFrame, column: str) -> pd.Series:
    if frame.empty or column not in frame.columns:
        return pd.Series(dtype=float)
    normalized = _zscores(pd.to_numeric(frame[column], errors="coerce").fillna(0))
    return pd.Series(normalized.values, index=frame.index)


def _fetch_flow(engine_scope: str, indicator: str, fetcher=None, value_name: str = "flow") -> pd.DataFrame:
    fetcher = fetcher or ak.stock_sector_fund_flow_rank
    try:
        frame = fetcher(indicator=indicator, sector_type=engine_scope)
    except Exception as exc:
        logger.warning(f"theme heat flow fetch failed ({engine_scope}/{indicator}): {exc}")
        return pd.DataFrame()
    if frame is None or frame.empty:
        return pd.DataFrame()
    name_col = _column(frame, "名称")
    net_col = _column(frame, "主力", "净额")
    if not name_col or not net_col:
        return pd.DataFrame()
    net = pd.to_numeric(frame[net_col], errors="coerce")
    out = pd.DataFrame({"theme": frame[name_col].astype(str).str.strip()})
    out[value_name] = (net / 1e8).round(2)  # 亿元
    return out.dropna(subset=[value_name]).drop_duplicates("theme")


def _fetch_board_quotes(fetcher=None, industry: bool = False) -> pd.DataFrame:
    fetcher = fetcher or (ak.stock_board_industry_name_em if industry else ak.stock_board_concept_name_em)
    try:
        frame = fetcher()
    except Exception as exc:
        logger.warning(f"theme heat board quotes failed (industry={industry}): {exc}")
        return pd.DataFrame()
    if frame is None or frame.empty:
        return pd.DataFrame()
    name_col = _column(frame, "板块名称") or _column(frame, "名称")
    chg_col = _column(frame, "涨跌幅")
    if not name_col or not chg_col:
        return pd.DataFrame()
    return pd.DataFrame({
        "theme": frame[name_col].astype(str).str.strip(),
        "chg_today": pd.to_numeric(frame[chg_col], errors="coerce"),
    }).dropna()


def _fetch_hot_codes(fetcher=None) -> set:
    fetcher = fetcher or ak.stock_hot_rank_em
    try:
        frame = fetcher()
    except Exception as exc:
        logger.warning(f"theme heat hot rank failed: {exc}")
        return set()
    if frame is None or frame.empty:
        return set()
    code_col = _column(frame, "代码")
    return {str(v).zfill(6) for v in frame[code_col].head(100).tolist()} if code_col else set()


def _fetch_members(theme: str, fetcher=None, industry: bool = False) -> List[str]:
    fetcher = fetcher or (
        (lambda symbol: ak.stock_board_industry_cons_em(symbol=symbol)) if industry
        else (lambda symbol: ak.stock_board_concept_cons_em(symbol=symbol))
    )
    try:
        frame = fetcher(theme)
    except Exception as exc:
        logger.debug(f"theme cons unavailable for {theme}: {exc}")
        return []
    if frame is None or frame.empty:
        return []
    code_col = _column(frame, "代码") or _column(frame, "品种代码")
    if not code_col:
        return []
    codes = [str(v).zfill(6) for v in frame[code_col].tolist()]
    # 东财成分接口可能返回重复行（如 A+H 两地上市同一码），按序去重
    return list(dict.fromkeys(codes))[:MEMBER_CAP]


def _limit_up_stats(engine, members: List[str], bar_date: str) -> Dict[str, float]:
    if not members:
        return {"limit_up_count": 0, "max_streak": 0, "limit_up_3d": 0}
    member_set = set(members)
    try:
        frame = pd.read_sql(text(
            "SELECT code, event_date, status, limit_up_streak FROM limit_up_events "
            "WHERE event_date >= :start AND code IN :codes"
        ).bindparams(bindparam("codes", expanding=True)), engine, params={
            "start": (datetime.strptime(bar_date, "%Y-%m-%d") - timedelta(days=6)).strftime("%Y-%m-%d"),
            "codes": sorted(member_set) or [""],
        })
    except Exception as exc:
        logger.debug(f"limit_up stats query failed: {exc}")
        return {"limit_up_count": 0, "max_streak": 0, "limit_up_3d": 0}
    if frame.empty:
        return {"limit_up_count": 0, "max_streak": 0, "limit_up_3d": 0}
    today = frame[pd.to_datetime(frame["event_date"]).dt.strftime("%Y-%m-%d") == bar_date]
    sealed = today[today["status"] == "SEALED"]
    return {
        "limit_up_count": int(len(sealed)),
        "max_streak": int(pd.to_numeric(sealed["limit_up_streak"], errors="coerce").fillna(0).max() or 0),
        "limit_up_3d": int(frame["code"].nunique()),
    }


def _pct_above_ma20(engine, members: List[str]) -> Optional[float]:
    if not members:
        return None
    try:
        frame = pd.read_sql(text(
            "SELECT code, date, close FROM ("
            "  SELECT code, date, close,"
            "         ROW_NUMBER() OVER (PARTITION BY code ORDER BY date DESC) AS rn"
            "  FROM daily_k WHERE code IN :codes AND date >= :start"
            ") t WHERE rn <= 21"
        ).bindparams(bindparam("codes", expanding=True)), engine, params={
            "codes": sorted(set(members)) or [""],
            "start": (datetime.now() - timedelta(days=45)).strftime("%Y-%m-%d"),
        })
    except Exception as exc:
        logger.debug(f"ma20 query failed: {exc}")
        return None
    if frame.empty:
        return None
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    ma20 = frame.groupby("code")["close"].transform(lambda s: s.tail(20).mean())
    latest = frame.assign(ma20=ma20).sort_values("date").groupby("code").tail(1)
    above = latest[latest["close"] > latest["ma20"]]
    return round(len(above) / len(latest) * 100, 1) if len(latest) else None


def _news_evidence(engine, theme: str) -> List[str]:
    tokens = [t for t in str(theme).replace("（", "/").replace("(", "/").split("/") if len(t) >= 2][:3]
    if not tokens:
        return []
    try:
        frame = pd.read_sql(text(
            "SELECT title FROM news_raw WHERE publish_time >= :start AND title IS NOT NULL"
        ), engine, params={"start": (datetime.now() - timedelta(days=NEWS_LOOKBACK_DAYS)).strftime("%Y-%m-%d")})
    except Exception as exc:
        logger.debug(f"news evidence query failed: {exc}")
        return []
    if frame.empty:
        return []
    titles = frame["title"].astype(str)
    hits: List[str] = []
    for title in titles:
        if any(token in title for token in tokens) and title not in hits:
            hits.append(title[:80])
        if len(hits) >= 5:
            break
    return hits


def _narrative(stats: Dict[str, Any]) -> str:
    parts = []
    chg = stats.get("chg_3d")
    flow = stats.get("flow_3d")
    lu = stats.get("limit_up_count", 0)
    streak = stats.get("max_streak", 0)
    if flow is not None:
        parts.append(f"3日主力净流{'入' if flow >= 0 else '出'} {abs(flow):.1f} 亿")
    if chg is not None:
        parts.append(f"板块3日涨跌 {chg:+.2f}%")
    if lu:
        parts.append(f"当日涨停 {lu} 家" + (f"（最高 {streak} 板）" if streak > 1 else ""))
    ma20 = stats.get("pct_above_ma20")
    if ma20 is not None:
        parts.append(f"站上MA20成分 {ma20:.0f}%")
    if not parts:
        return "数据不足，暂无结构化理由（v1 规则模板，非 AI 生成）"
    return "；".join(parts) + "。热度为规则化研究评分，不构成交易依据。"




AI_THEME_MAX_CALLS = int(__import__("os").getenv("AI_THEME_MAX_CALLS", "20"))


def _post_chat_text(payload: Dict[str, Any]) -> tuple[str, Dict[str, Any]]:
    """纯文本 /chat/completions 薄客户端（ai_stock_analysis._post_chat 是 JSON 专用
    契约：返回解析后的 JSON 对象并对非 JSON 内容重试，叙述用不了它）。"""
    import requests as _requests

    from core.ai_stock_analysis import _chat_completions_url
    from core.config import config

    headers = {"Content-Type": "application/json"}
    if config.AI_API_KEY:
        headers["Authorization"] = f"Bearer {config.AI_API_KEY}"
    response = _requests.post(
        _chat_completions_url(config.AI_BASE_URL), headers=headers,
        json=payload, timeout=config.AI_TIMEOUT_SECONDS,
    )
    response.raise_for_status()
    body = response.json()
    choice = (body.get("choices") or [{}])[0]
    content = (choice.get("message") or {}).get("content")
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    return str(content or "").strip(), usage


def generate_narrative_llm(theme: str, stats: Dict[str, Any], evidence: List[str]) -> Optional[str]:
    """LLM 生成主题叙述（OpenAI 兼容 /chat/completions，复用 ai_stock_analysis 客户端）。

    未配置 AI（is_ai_analysis_configured=False）或任何异常时返回 None，
    调用方回落规则模板 narrative。输出为研究叙述，禁止买卖指令。"""
    try:
        from core.config import config

        if not config.is_ai_analysis_configured():
            return None
        payload = {
            "model": config.AI_MODEL,
            "messages": [
                {"role": "system", "content": (
                    "你是 A 股题材研究助手。基于给定的题材结构化数据与新闻标题，输出一段"
                    "不超过120字的中文研究叙述：先判断题材所处阶段（发酵/主线/分化/退潮），"
                    "引用资金与涨停梯队证据，最后一句给风险提示。禁止给出任何买卖指令或"
                    "具体操作建议，禁止编造数据中不存在的事实。直接输出叙述正文。"
                )},
                {"role": "user", "content": (
                    f"主题：{theme}\n"
                    f"热度数据：{__import__('json').dumps(stats, ensure_ascii=False)}\n"
                    f"新闻标题：{__import__('json').dumps(evidence, ensure_ascii=False)}"
                )},
            ],
            "temperature": 0.3,
            "max_tokens": 400,
        }
        text_out, _usage = _post_chat_text(payload)
        return text_out[:400] if text_out else None
    except Exception as exc:
        logger.debug(f"LLM narrative unavailable for {theme}: {exc}")
        return None


def _tier(stats: Dict[str, Any], heat: float) -> str:
    if heat >= 80 and (stats.get("limit_up_count") or 0) >= 3:
        return "主线（涨停+资金共振）"
    if heat >= 70:
        return "扩散（热度高，梯队未成型）"
    if heat >= 55:
        return "观察"
    return "冷门"


def collect_theme_heat(
    engine=None,
    scope: str = "CONCEPT",
    bar_date: Optional[str] = None,
    top_n: int = TOP_THEMES,
    fetchers: Optional[Dict[str, Any]] = None,
    use_llm: bool = True,
) -> Dict[str, Any]:
    """采集并落库当日题材热度（scope: CONCEPT / INDUSTRY）。fetcher 可注入便于测试。"""
    fetchers = fetchers or {}
    industry = scope == "INDUSTRY"
    engine = engine or get_db_engine()
    bar_date = bar_date or datetime.now().strftime("%Y-%m-%d")
    flow_scope = "行业资金流" if industry else "概念资金流"

    quotes = _fetch_board_quotes(fetchers.get("board"), industry=industry)
    flow_today = _fetch_flow(flow_scope, "今日", fetchers.get("flow"), value_name="flow_today")
    flow_3d = _fetch_flow(flow_scope, "3日", fetchers.get("flow"), value_name="flow")
    hot_codes = _fetch_hot_codes(fetchers.get("hot"))
    if quotes.empty:
        return {"saved": 0, "reason": "board_quotes_unavailable"}

    base = quotes.merge(flow_3d, on="theme", how="left").merge(flow_today, on="theme", how="left")
    base["chg_3d"] = base["chg_today"]  # v1 近似：3日涨跌用当日板块涨跌占位（东财板块单日接口）
    base["_chg_n"] = _zscores(base["chg_3d"].fillna(0))
    base["_flow_n"] = _zscores(base["flow"].fillna(0))
    # 先按可得字段（涨跌+资金）粗排，取 top_n 再补成分级数据
    base["_pre"] = (base["_chg_n"] * 0.5 + base["_flow_n"] * 0.5)
    candidates = base.sort_values("_pre", ascending=False).head(top_n)

    cand_rows: List[Dict[str, Any]] = []
    for _, row in candidates.iterrows():
        theme = str(row["theme"])
        members = _fetch_members(theme, fetchers.get("members"), industry=industry)
        member_set = set(members)
        lu_stats = _limit_up_stats(engine, members, bar_date) if engine else {"limit_up_count": 0, "max_streak": 0, "limit_up_3d": 0}
        hot_overlap = len(member_set & hot_codes)
        pct_ma20 = _pct_above_ma20(engine, members) if engine else None
        cand_rows.append({
            "theme": theme, "members": members, "chg_n": float(row["_chg_n"]),
            "flow_n": float(row["_flow_n"]), "chg_3d": row.get("chg_3d"),
            "flow_3d": row.get("flow"), "flow_today": row.get("flow_today"),
            "limit_up_count": lu_stats["limit_up_count"], "max_streak": lu_stats["max_streak"],
            "limit_up_3d": lu_stats["limit_up_3d"], "hot_overlap": hot_overlap,
            "pct_above_ma20": pct_ma20, "members_count": len(members),
        })

    # 涨停热度与人气在 top_n 子集内归一化后合成最终热度
    lu_heat = {
        r["theme"]: min(100.0, r["limit_up_count"] * 12 + r["max_streak"] * 8)
        for r in cand_rows
    }
    hot_norm_map = _zscores(pd.Series(
        [float(r["hot_overlap"]) for r in cand_rows],
        index=[r["theme"] for r in cand_rows],
    ))
    rows: List[Dict[str, Any]] = []
    for r in cand_rows:
        theme = r["theme"]
        score = heat_score(r["chg_n"], r["flow_n"], lu_heat.get(theme, 0.0),
                           float(hot_norm_map.get(theme, 50.0)))
        news = _news_evidence(engine, theme) if engine else []
        stats = {k: r.get(k) for k in ("chg_3d", "flow_3d", "limit_up_count", "max_streak", "pct_above_ma20")}
        rows.append({
            "bar_date": bar_date, "scope": scope, "theme": theme,
            "heat_score": score, "flow_3d": stats.get("flow_3d"),
            "flow_today": stats.get("flow_today"), "chg_today": row.get("chg_today"),
            "limit_up_count": r["limit_up_count"], "max_streak": r["max_streak"],
            "hot_overlap": r["hot_overlap"], "pct_above_ma20": r["pct_above_ma20"],
            "members_count": r["members_count"],
            "members": r["members"][:60],
            "narrative": _narrative(stats),
            "evidence": news,
            "tier": _tier(stats, score),
        })

    rows.sort(key=lambda r: r["heat_score"], reverse=True)
    for rank, row in enumerate(rows, start=1):
        row["rank"] = rank
    # LLM 叙述（top 主题，成本受 AI_THEME_MAX_CALLS 限制；未配置 AI 时全部回落规则模板）
    if use_llm:
        for row in rows[:max(0, AI_THEME_MAX_CALLS)]:
            row["narrative_llm"] = generate_narrative_llm(
                row["theme"],
                {k: row.get(k) for k in ("heat_score", "flow_3d", "chg_today",
                                          "limit_up_count", "max_streak",
                                          "pct_above_ma20", "hot_overlap")},
                row.get("evidence") or [],
            )
    saved = save_theme_heat_history(rows, engine=engine)
    return {"saved": saved, "scope": scope, "bar_date": bar_date, "themes": len(rows)}


def _trend_tag(rank_today: Optional[int], rank_prev: Optional[int]) -> str:
    if rank_prev is None:
        return "new"
    if rank_today is None:
        return "out"
    if rank_today < rank_prev:
        return "up"
    if rank_today > rank_prev:
        return "down"
    return "flat"


def load_theme_board(engine=None, scope: str = "CONCEPT", bar_date: Optional[str] = None) -> Dict[str, Any]:
    """读某日某 scope 的热度榜（含趋势标签与昨日 rank 对照）。"""
    engine = engine or get_db_engine()
    if not engine:
        return {"themes": []}
    bar_date = bar_date or datetime.now().strftime("%Y-%m-%d")
    try:
        today = pd.read_sql(text(
            "SELECT * FROM theme_heat_history WHERE scope=:s AND bar_date=:d ORDER BY heat_score DESC"
        ), engine, params={"s": scope, "d": bar_date})
        prev = pd.read_sql(text(
            "SELECT theme, rank FROM theme_heat_history WHERE scope=:s AND bar_date=("
            " SELECT MAX(bar_date) FROM theme_heat_history WHERE scope=:s2 AND bar_date < :d)"
        ), engine, params={"s": scope, "s2": scope, "d": bar_date})
    except Exception as exc:
        logger.warning(f"load_theme_board failed: {exc}")
        return {"themes": [], "bar_date": bar_date}
    prev_rank = {str(row["theme"]): int(row["rank"]) for _, row in prev.iterrows()} if not prev.empty else {}
    import json as _json

    themes = []
    for _, row in today.iterrows():
        rank = int(row["rank"]) if pd.notna(row.get("rank")) else None
        themes.append({
            "theme": str(row["theme"]), "heat": float(row["heat_score"]), "rank": rank,
            "flow_3d": row.get("flow_3d"), "flow_today": row.get("flow_today"),
            "chg_today": row.get("chg_today"), "limit_up_count": row.get("limit_up_count"),
            "max_streak": row.get("max_streak"), "hot_overlap": row.get("hot_overlap"),
            "pct_above_ma20": row.get("pct_above_ma20"), "members_count": row.get("members_count"),
            "narrative": row.get("narrative"),
            "narrative_llm": row.get("narrative_llm"),
            "evidence": _json.loads(row.get("evidence_json") or "[]"),
            "tier": row.get("tier"),
            "members": _json.loads(row.get("members_json") or "[]"),
            "trend": _trend_tag(rank, prev_rank.get(str(row["theme"]))),
        })
    return {"bar_date": bar_date, "scope": scope, "themes": themes}

def _market_env_facts(engine) -> Dict[str, Any]:
    """聚合市场环境结构化事实（R3 动量/涨停情绪/NH-NL 宽度/代理5日/题材top3）。"""
    from core.market_regime import (
        compute_limit_up_sentiment,
        compute_market_breadth_extremes,
        compute_market_state_gate,
    )

    facts: Dict[str, Any] = {}
    try:
        facts.update(compute_market_state_gate(engine))
    except Exception:
        pass
    try:
        zt = compute_limit_up_sentiment(engine)
        facts.update({k: zt.get(k) for k in ("sealed_count", "broken_count", "max_streak", "promotion_rate", "broken_rate")})
    except Exception:
        pass
    try:
        breadth = compute_market_breadth_extremes(engine)
        facts.update({k: breadth.get(k) for k in ("nh_count", "nl_count", "pct_above_ma50")})
    except Exception:
        pass
    try:
        proxy = pd.read_sql(text(
            "SELECT date, AVG(close / NULLIF(prev_close, 0) - 1) AS r FROM ("
            " SELECT date, code, close, LAG(close) OVER (PARTITION BY code ORDER BY date) AS prev_close"
            " FROM daily_k WHERE date >= :start) d"
            " WHERE prev_close > 0 AND close / prev_close BETWEEN 0.75 AND 1.25 GROUP BY date ORDER BY date"
        ), engine, params={"start": (datetime.now() - timedelta(days=20)).strftime("%Y-%m-%d")})
        if not proxy.empty:
            level = (1 + proxy["r"].fillna(0)).cumprod()
            facts["proxy_ret_5d_pct"] = round(float((level.iloc[-1] / level.iloc[-6] - 1) * 100), 2) if len(level) > 5 else None
    except Exception:
        pass
    try:
        board = load_theme_board(engine, scope="CONCEPT")
        facts["top_themes"] = [
            {"theme": t["theme"], "heat": t["heat"], "tier": t.get("tier"), "flow_3d": t.get("flow_3d")}
            for t in (board.get("themes") or [])[:3]
        ]
    except Exception:
        pass
    return facts


def generate_market_env_llm(engine) -> Optional[str]:
    """LLM 生成市场环境叙述（easy-stock 式：阶段判断 + 具体数字 + 风险提示）。

    结果缓存到 system_setting `market_env_summary_llm:{当日}`（每日一改，
    供 /api/themes/heat 与每日 AI 复盘复用）。未配置 AI 或异常返回 None。"""
    import json as _json

    from core.db import save_setting

    facts = _market_env_facts(engine)
    # 任一关键事实（闸门/涨停情绪/宽度）存在即可生成；全空库回落 None
    has_facts = any(facts.get(k) is not None for k in ("bar_date", "sealed_count", "nh_count", "broken_rate"))
    if not has_facts:
        return None
    try:
        from core.config import config

        if not config.is_ai_analysis_configured():
            return None
        payload = {
            "model": config.AI_MODEL,
            "messages": [
                {"role": "system", "content": (
                    "你是 A 股市场状态研究助手。基于给定的结构化市场事实，输出一段不超过140字的"
                    "中文市场环境叙述：先给阶段判断（主升/分歧/退潮/退潮后弱反抽/混沌，选最符合的），"
                    "必须引用至少三个具体数字（10日动量、炸板率、涨停家数/最高连板、MA50上方占比、"
                    "代理5日收益等），指出组合结构特征，最后一句风险提示。禁止买卖指令，"
                    "禁止编造数据中不存在的事实。直接输出叙述正文。"
                )},
                {"role": "user", "content": _json.dumps(facts, ensure_ascii=False)},
            ],
            "temperature": 0.3,
            "max_tokens": 400,
        }
        text_out, _usage = _post_chat_text(payload)
        if text_out:
            save_setting(f"market_env_summary_llm:{datetime.now().strftime('%Y-%m-%d')}", text_out[:500])
        return text_out[:500] if text_out else None
    except Exception as exc:
        logger.debug(f"market env LLM narrative unavailable: {exc}")
        return None


def load_cached_market_env_summary(engine, date: Optional[str] = None) -> Optional[str]:
    """读当日缓存的 AI 市场环境叙述（system_setting）。"""
    from core.db import get_setting

    try:
        key_date = date or datetime.now().strftime("%Y-%m-%d")
        return get_setting(f"market_env_summary_llm:{key_date}", "") or None
    except Exception:
        return None
