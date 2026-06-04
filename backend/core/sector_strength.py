from typing import Dict, Any, Optional

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
    """Classify a stock's role inside its sector: leader, core, follower, or laggard."""
    relative_pct = float(stock_pct or 0) - float(sector_avg_pct or 0)
    rank = int(rank_in_sector or 0)
    alignment = float(alignment_score or 0)

    if stock_pct <= 0 and relative_pct < 0:
        return "LAGGARD"
    if (rank and rank <= 2 and stock_pct >= 5) or relative_pct >= 3.0 or stock_pct >= 9.0:
        return "LEADER"
    if (rank and rank <= 5 and stock_pct >= sector_avg_pct) or alignment >= 70 or relative_pct >= 1.0:
        return "CORE"
    if stock_pct > 0:
        return "FOLLOWER"
    return "LAGGARD"


def build_sector_history_context(engine, sector_map: Dict[str, str], lookback: int = 6) -> Dict[str, Dict[str, Any]]:
    """Build recent 3/5-day sector context from local daily_k data."""
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
        return {}
    if df.empty or 'code' not in df.columns or 'close' not in df.columns:
        return {}

    df['code'] = df['code'].astype(str).str.zfill(6)
    df['industry'] = df['code'].map(sector_map).fillna('未知')
    df['close'] = pd.to_numeric(df['close'], errors='coerce')
    df = df.dropna(subset=['close'])
    df = df[df['industry'] != '未知']
    if df.empty:
        return {}

    df = df.sort_values(['code', 'date'])
    df['pct'] = df.groupby('code')['close'].pct_change() * 100
    df = df.dropna(subset=['pct'])
    if df.empty:
        return {}

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
            'sector_recent_breadth': round(float(recent['breadth'].iloc[-1]), 1),
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
    return result
