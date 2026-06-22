from typing import Dict, Any, List, Optional

import pandas as pd
from sqlalchemy import text


def sector_phase(score: float, breadth: float, avg_pct: float, hot_ratio: float, history: Optional[Dict[str, Any]] = None) -> str:
    history = history or {}
    pct_3d = float(history.get('sector_3d_pct') or 0)
    pct_5d = float(history.get('sector_5d_pct') or 0)
    consecutive_up = int(history.get('sector_consecutive_up_days') or 0)
    slope = float(history.get('sector_trend_slope') or 0)

    if breadth < 45 or avg_pct < -0.5 or slope < -1.5:
        return "SECTOR_FADE"
    if (avg_pct >= 4 or hot_ratio >= 18) and (pct_3d >= 6 or pct_5d >= 10):
        return "SECTOR_CLIMAX"
    if score >= 75 and breadth >= 70 and hot_ratio >= 8 and (consecutive_up >= 2 or pct_3d >= 2):
        return "SECTOR_CONFIRM"
    if score >= 58 and breadth >= 60 and avg_pct > 0 and pct_5d < 8:
        return "SECTOR_EARLY"
    return "SECTOR_NEUTRAL"


def classify_sector_role(
    stock_pct: float,
    sector_avg_pct: float,
    rank_in_sector: Optional[int] = None,
    alignment_score: Optional[float] = None,
) -> str:
    """Classify a stock's role inside its sector: leader, core, follower, or laggard.

    改动 P0：原 LEADER 条件 `relative_pct >= 3.0` 不看 rank，导致板块排名第 7+ 的票
    只要跑赢板块均值 3% 就被判龙头（如 601138 rank=11 却标 LEADER）。修复：超额>=3% 时
    需配合 rank<=5（板块前 5 才有资格当龙头）；涨停级涨幅(pct>=9%)属绝对强势，不受 rank 约束。
    """
    relative_pct = float(stock_pct or 0) - float(sector_avg_pct or 0)
    rank = int(rank_in_sector or 0)
    alignment = float(alignment_score or 0)

    if stock_pct <= 0 and relative_pct < 0:
        return "LAGGARD"
    # LEADER: 板块前2且涨≥5%，或涨停级绝对强势(pct≥9%)，或超额≥3%且在板块前5
    if (rank and rank <= 2 and stock_pct >= 5) or stock_pct >= 9.0 or (relative_pct >= 3.0 and (not rank or rank <= 5)):
        return "LEADER"
    if (rank and rank <= 5 and stock_pct >= sector_avg_pct) or alignment >= 70 or relative_pct >= 1.0:
        return "CORE"
    if stock_pct > 0:
        return "FOLLOWER"
    return "LAGGARD"


def classify_mainline_sector(sector: Dict[str, Any]) -> str:
    """Classify sector tradability as mainline, secondary, rotation, fading, or non-main."""
    phase = sector.get("sector_phase")
    rank = int(sector.get("sector_rank") or 999)
    score = float(sector.get("sector_momentum_score") or 0)
    breadth = float(sector.get("sector_breadth") or 0)
    pct_5d = float(sector.get("sector_5d_pct") or 0)
    if phase == "SECTOR_FADE":
        return "FADING"
    if phase == "SECTOR_CONFIRM" and rank <= 3 and score >= 75 and breadth >= 65:
        return "MAIN"
    if phase in {"SECTOR_CONFIRM", "SECTOR_EARLY"} and rank <= 8 and score >= 58:
        return "SECONDARY"
    if score >= 50 and pct_5d > 0:
        return "ROTATION"
    return "NON_MAIN"


def _load_today_sector_breadth(engine) -> Dict[str, Dict[str, Any]]:
    """从 breadth_history 表读取最新一天的 SECTOR 宽度（盘中实时聚合写入）。

    返回 {industry: {advance_ratio, strong_ratio, weak_ratio, avg_return, total_count, bar_date}}。
    表空或读取失败时返回 {}（调用方回退到 daily_k）。
    """
    if engine is None:
        return {}
    try:
        df = pd.read_sql(text("""
            SELECT industry, advance_ratio, strong_ratio, weak_ratio, avg_return, total_count, bar_date
            FROM breadth_history
            WHERE scope = 'SECTOR' AND bar_date = (
                SELECT MAX(bar_date) FROM breadth_history WHERE scope = 'SECTOR'
            )
        """), engine)
    except Exception:
        return {}
    if df.empty:
        return {}
    result: Dict[str, Dict[str, Any]] = {}
    for _, row in df.iterrows():
        industry = str(row['industry'])
        result[industry] = {
            'advance_ratio': float(row['advance_ratio'] or 0),
            'strong_ratio': float(row['strong_ratio'] or 0),
            'weak_ratio': float(row['weak_ratio'] or 0),
            'avg_return': float(row['avg_return'] or 0),
            'total_count': int(row['total_count'] or 0),
            'bar_date': str(row['bar_date']),
        }
    return result


def build_sector_history_context(engine, sector_map: Dict[str, str], lookback: int = 6) -> Dict[str, Dict[str, Any]]:
    """Build recent 3/5-day sector context from local daily_k data.

    优先用 breadth_history 表的今日 SECTOR 行覆盖"最新一天"（修复 6/22 节后首日 bug：
    节后首日 daily_k 还是上个交易日数据，导致 slope/pct 用滞后值误判板块）。
    新表空时回退纯 daily_k 逻辑（向下兼容）。
    """
    if engine is None or not sector_map:
        return {}

    query = text("""
        WITH recent_dates AS (
            SELECT DISTINCT date
            FROM daily_k
            ORDER BY date DESC
            LIMIT :limit
        )
        SELECT code, date, close
        FROM daily_k
        WHERE date IN (SELECT date FROM recent_dates)
        ORDER BY code, date
    """)
    try:
        df = pd.read_sql(query, engine, params={"limit": max(lookback + 1, 6)})
    except Exception:
        df = pd.DataFrame()
    if not df.empty and 'code' in df.columns and 'close' in df.columns:
        df['code'] = df['code'].astype(str).str.zfill(6)
        df['industry'] = df['code'].map(sector_map).fillna('未知')
        df['close'] = pd.to_numeric(df['close'], errors='coerce')
        df = df.dropna(subset=['close'])
        df = df[df['industry'] != '未知']

    # ── 取今日实时 SECTOR 宽度（breadth_history），优先级最高 ──
    today_breadth = _load_today_sector_breadth(engine)

    if not df.empty:
        df = df.sort_values(['code', 'date'])
        df['pct'] = df.groupby('code')['close'].pct_change() * 100
        df = df.dropna(subset=['pct'])

    if df.empty:
        # daily_k 完全空（首次启动/测试环境）：仅用 breadth_history 今日数据构造最小 history
        if not today_breadth:
            return {}
        result: Dict[str, Dict[str, Any]] = {}
        for industry, b in today_breadth.items():
            avg = b['avg_return']
            result[industry] = {
                'sector_3d_pct': round(avg, 2),
                'sector_5d_pct': round(avg, 2),
                'sector_recent_breadth': round(b['advance_ratio'], 1),
                'sector_consecutive_up_days': 1 if avg > 0 else 0,
                'sector_trend_slope': round(avg, 2),  # 无历史时 slope = 今日值
                'sector_history_days': 1,
            }
        return result

    daily = (
        df.groupby(['industry', 'date'])
        .agg(avg_pct=('pct', 'mean'), breadth=('pct', lambda s: float((s > 0).mean() * 100)))
        .reset_index()
        .sort_values(['industry', 'date'])
    )

    result: Dict[str, Dict[str, Any]] = {}
    for industry, group in daily.groupby('industry'):
        recent = group.tail(5).copy()
        if recent.empty:
            continue
        avg_values = recent['avg_pct'].tolist()
        breadth_values = recent['breadth'].tolist()

        # ── 用 breadth_history 今日数据覆盖"最新一天"（若可用且比 daily_k 最新日期更新）──
        today = today_breadth.get(industry)
        if today:
            # 今日 avg_return 覆盖最后一天（avg_values[-1]）
            avg_values[-1] = today['avg_return']
            breadth_values[-1] = today['advance_ratio']

        last_3 = avg_values[-3:]
        last_5 = avg_values[-5:]
        consecutive_up = 0
        for value in reversed(avg_values):
            if value > 0:
                consecutive_up += 1
            else:
                break
        previous_mean = sum(avg_values[:-1]) / len(avg_values[:-1]) if len(avg_values) > 1 else 0
        result[industry] = {
            'sector_3d_pct': round(sum(last_3), 2),
            'sector_5d_pct': round(sum(last_5), 2),
            'sector_recent_breadth': round(float(breadth_values[-1]), 1),
            'sector_consecutive_up_days': consecutive_up,
            'sector_trend_slope': round(float(avg_values[-1] - previous_mean), 2),
            'sector_history_days': len(last_5),
        }
    return result


def build_sector_strength(
    snapshot_df: pd.DataFrame,
    sector_map: Dict[str, str],
    sector_trends: Dict[str, Dict[str, Any]],
    history_context: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Dict[str, Any]]:
    """Build intraday sector breadth/momentum stats from the realtime stock snapshot."""
    if snapshot_df is None or snapshot_df.empty or not sector_map:
        return {}

    df = snapshot_df.copy()
    if 'code' not in df.columns or 'pct_chg' not in df.columns:
        return {}

    df['code'] = df['code'].astype(str).str.zfill(6)
    df['industry'] = df['code'].map(sector_map).fillna('未知')
    df['pct_chg'] = pd.to_numeric(df['pct_chg'], errors='coerce').fillna(0)
    if 'turnover' in df.columns:
        df['turnover'] = pd.to_numeric(df['turnover'], errors='coerce').fillna(0)
    else:
        df['turnover'] = 0
    df = df[df['industry'] != '未知']
    if df.empty:
        return {}

    result: Dict[str, Dict[str, Any]] = {}
    for industry, group in df.groupby('industry'):
        total = int(len(group))
        if total < 3:
            continue

        up_count = int((group['pct_chg'] > 0).sum())
        strong_count = int((group['pct_chg'] >= 5).sum())
        limit_count = int((group['pct_chg'] >= 9.8).sum())
        breadth = up_count / total * 100
        # 改动 #2：板块均值改用成交额加权（过滤低活跃股）。
        # 原简单均值把僵尸股（日成交极低）和活跃股等权，拉低/扭曲板块基准，
        # 导致活跃股更容易"超额"被误判龙头。加权后基准反映板块真实的资金流向。
        # 优先用 amount（成交额）加权；缺 amount 时用 turnover（换手率）；都缺时退回简单均值。
        active = group[group['turnover'] > 0.1] if 'turnover' in group.columns else group
        if len(active) == 0:
            active = group  # 全部低换手时不过滤，避免空集
        if 'amount' in active.columns:
            amt = pd.to_numeric(active['amount'], errors='coerce').fillna(0)
            total_amt = float(amt.sum())
            if total_amt > 0:
                avg_pct = float((active['pct_chg'] * amt).sum() / total_amt)
            else:
                avg_pct = float(active['pct_chg'].mean())
        elif 'turnover' in active.columns:
            w = active['turnover'].clip(lower=0.01)
            total_w = float(w.sum())
            avg_pct = float((active['pct_chg'] * w).sum() / total_w) if total_w > 0 else float(active['pct_chg'].mean())
        else:
            avg_pct = float(group['pct_chg'].mean())
        median_pct = float(group['pct_chg'].median())
        hot_ratio = strong_count / total * 100
        turnover_avg = float(group['turnover'].mean()) if 'turnover' in group.columns else 0.0
        trend_info = sector_trends.get(industry, {})
        board_pct = float(trend_info.get('pct', avg_pct) or avg_pct)

        score = (
            min(35, max(0, breadth - 40) * 0.75)
            + min(25, max(0, avg_pct) * 8)
            + min(15, hot_ratio * 1.5)
            + min(10, limit_count * 2)
            + (8 if trend_info.get('trend') == 'LEAD' else 4 if trend_info.get('trend') == 'FOLLOW' else 0)
            + min(7, max(0, turnover_avg - 2) * 1.2)
        )
        score = round(max(0, min(100, score)), 1)
        history = (history_context or {}).get(industry, {})
        result[industry] = {
            'sector_momentum_score': score,
            'sector_breadth': round(breadth, 1),
            'sector_avg_pct': round(avg_pct, 2),
            'sector_median_pct': round(median_pct, 2),
            'sector_hot_count': strong_count,
            'sector_limit_count': limit_count,
            'sector_total_count': total,
            'sector_up_count': up_count,
            'sector_board_pct': round(board_pct, 2),
            'sector_turnover_avg': round(turnover_avg, 2),
            **history,
            'sector_phase': sector_phase(score, breadth, avg_pct, hot_ratio, history),
            'lead_stock': trend_info.get('lead_stock', ''),
            'trend': trend_info.get('trend', 'UNKNOWN'),
        }

    ranked = sorted(result.items(), key=lambda item: item[1]['sector_momentum_score'], reverse=True)
    for rank, (industry, data) in enumerate(ranked, start=1):
        data['sector_rank'] = rank
        data['sector_mainline'] = classify_mainline_sector(data)
    return result


def build_sector_leaders(
    engine,
    snapshot_df: Optional[pd.DataFrame],
    sector_map: Dict[str, str],
    sector_strength: Dict[str, Dict[str, Any]],
    lookback: int = 10,
    top_n: int = 5,
) -> Dict[str, List[Dict[str, Any]]]:
    """识别每个板块的"龙头股"——不是单日涨幅最大的票，而是近期持续领涨板块的票。

    评分维度（全部相对板块均值，避免牛熊周期失真）：
      - relative_strength_5d: 个股 5 日累计涨幅 - 板块 5 日累计涨幅（持续跑赢分）
      - lead_consistency:   最近 N 日里跑赢板块的天数占比（领涨稳定性）
      - today_momentum:     当日涨幅相对板块的超额（盘口即时强度）

    龙头 = 三者加权后得分最高的 top_n 只票。复用 daily_k 历史 + 当日快照，
    不新增任何表/依赖（遵循 Ponytail：标准库 + 已有数据源）。
    """
    if engine is None or not sector_map:
        return {}

    # 1) 取最近 lookback+1 个交易日的日线（多算 1 天用于 pct_change 基准），顺带取股票名称
    try:
        df = pd.read_sql(
            text("""
                WITH recent_dates AS (
                    SELECT DISTINCT date FROM daily_k ORDER BY date DESC LIMIT :limit
                )
                SELECT d.code, d.date, d.close, d.vol, b.name
                FROM daily_k d
                LEFT JOIN stock_basic b ON d.code = b.code
                WHERE d.date IN (SELECT date FROM recent_dates)
                ORDER BY d.code, d.date
            """),
            engine,
            params={"limit": lookback + 1},
        )
    except Exception:
        return {}
    if df.empty:
        return {}

    df['code'] = df['code'].astype(str).str.zfill(6)
    df['industry'] = df['code'].map(sector_map).fillna('未知')
    df['close'] = pd.to_numeric(df['close'], errors='coerce')
    df = df.dropna(subset=['close'])
    df = df[df['industry'] != '未知']
    if df.empty:
        return {}

    # 2) 每只票的日涨幅，并合并当日快照（盘中价/当日涨幅/名称）
    df = df.sort_values(['code', 'date'])
    df['daily_pct'] = df.groupby('code')['close'].pct_change() * 100

    snap: Dict[str, Dict[str, Any]] = {}
    if snapshot_df is not None and not snapshot_df.empty:
        snap_df = snapshot_df.copy()
        snap_df['code'] = snap_df['code'].astype(str).str.zfill(6)
        for _, r in snap_df.iterrows():
            snap[str(r.get('code', '')).zfill(6)] = {
                'price': float(r.get('price', 0) or 0),
                'pct_chg': float(r.get('pct_chg', 0) or 0),
                'name': str(r.get('name', '')),
            }

    # 3) 板块级日涨幅均值（用于"跑赢"判定）
    sector_daily = (
        df.dropna(subset=['daily_pct'])
        .groupby(['industry', 'date'])['daily_pct']
        .mean()
        .rename('sector_avg_pct')
        .reset_index()
    )
    df = df.merge(sector_daily, on=['industry', 'date'], how='left')

    result: Dict[str, List[Dict[str, Any]]] = {}
    for industry, group in df.groupby('industry'):
        # 板块 5 日累计涨幅（用板块均值收盘价折算）
        sec_series = group.groupby('date')['close'].mean().sort_index()
        if len(sec_series) < 2:
            continue
        sec_5d = float((sec_series.iloc[-1] / sec_series.iloc[-min(6, len(sec_series))] - 1) * 100) if len(sec_series) >= 6 else float((sec_series.iloc[-1] / sec_series.iloc[0] - 1) * 100)

        scored = []
        for code, g in group.groupby('code'):
            g = g.sort_values('date')
            if len(g) < 2:
                continue
            # 个股 5 日累计涨幅
            tail = g.tail(min(6, len(g)))
            stock_5d = float((tail['close'].iloc[-1] / tail['close'].iloc[0] - 1) * 100)
            relative_5d = stock_5d - sec_5d

            # 跑赢板块的天数占比
            valid = g.dropna(subset=['daily_pct', 'sector_avg_pct'])
            lead_days = int((valid['daily_pct'] > valid['sector_avg_pct']).sum())
            consistency = (lead_days / len(valid) * 100) if len(valid) else 0.0

            # 盘口即时超额
            today_info = snap.get(code, {})
            today_pct = today_info.get('pct_chg', float(g['daily_pct'].iloc[-1] if pd.notna(g['daily_pct'].iloc[-1]) else 0))
            sector_today = float(group.groupby('date')['daily_pct'].mean().iloc[-1]) if not group['daily_pct'].isna().all() else 0.0
            today_excess = today_pct - sector_today

            # 龙头综合分：持续跑赢(40%) + 领涨稳定(40%) + 盘口超额(20%)，clamp 到 [0,100]。
            # 领涨稳定性权重与相对强度持平——"龙头"的本质是持续领涨，而非单日跳涨，
            # 这样能避免把"一日游脉冲票"误判为龙头。
            score = (
                min(40.0, max(0.0, relative_5d * 4.0))
                + min(40.0, consistency * 0.4)
                + min(20.0, max(0.0, today_excess * 4.0))
            )

            role = classify_sector_role(today_pct, sector_today)
            # 名称优先用实时快照，缺失时回退 daily_k join 的 stock_basic.name
            name = today_info.get('name') or str(g['name'].dropna().iloc[-1]) if 'name' in g.columns and not g['name'].dropna().empty else ''
            scored.append({
                'code': code,
                'name': name,
                'price': round(today_info.get('price', float(g['close'].iloc[-1])), 2),
                'pct': round(today_pct, 2),
                'relative_strength_5d': round(relative_5d, 2),
                'lead_consistency': round(consistency, 1),
                'leader_score': round(score, 1),
                'role': role,
            })

        # 综合分排序取 top_n，复用既有 SectorLeader 契约（code/name/price/pct/role）+ 扩展字段
        scored.sort(key=lambda x: x['leader_score'], reverse=True)
        result[industry] = scored[:top_n]

    return result
