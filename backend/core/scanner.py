"""
Alpha Vision - Core Market Scanning Engine.
Decoupled business logic from FastAPI Routers.
"""
import time
import os
import re
import pandas as pd
from datetime import datetime, timedelta, date
from typing import List, Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor, as_completed
from fastapi import HTTPException
import akshare as ak
from sqlalchemy import text

from core.logging_config import logger
from core.config import config
from core.ws_manager import manager as ws_manager
from core.db import (
    get_db_engine, save_scan_results, load_from_db, save_scan_audit_log
)
from core.data import (
    get_market_snapshot, get_index_hist, get_sector_map, get_sector_trends, get_market_regime
)
from core.indicators import (
    calculate_indicators, calculate_pine_indicators,
    get_weekly_indicators, batch_calculate_indicators
)
from core.strategy import (
    check_strategy, check_pine_strategy, check_tv_zp_strategy, check_tv_dual_strategy, check_consensus_strategy,
    calculate_historical_win_rate, calculate_pine_win_rate, calculate_tv_zp_win_rate, calculate_tv_dual_win_rate, calculate_consensus_win_rate,
    STRATEGY_LOGIC_VERSION, BACKTEST_ENGINE_VERSION, EXIT_RULE_VERSION
)
from core.price_action import analyze_price_action
from core.risk_engine import compute_paper_risk_levels
from core.sector_strength import build_sector_strength, build_sector_history_context, classify_sector_role
from routers.market import fetch_mine_sweeper_data


EXECUTABLE_PA_ACTIONS = {"READY", "WATCH"}
BLOCKED_PA_SETUPS = {"外包K", "交易区间假突破"}


def _pa_plan_action(res: Dict[str, Any]) -> str:
    plan = res.get('pa_trade_plan') or {}
    return str(plan.get('action') or res.get('pa_trade_action') or "").upper()


def _daily_limit_tolerance_pct(code: str) -> float:
    code = str(code or "")
    if code.startswith(("43", "83", "87", "88", "92")):
        return 30.5
    if code.startswith(("300", "301", "688", "689")):
        return 20.5
    return 10.5


def _is_abnormal_price_move(code: str, pct: Any) -> bool:
    try:
        pct_value = abs(float(pct or 0))
    except (TypeError, ValueError):
        return False
    return pct_value > _daily_limit_tolerance_pct(code)


def _brooks_rank_adjustment(res: Dict[str, Any]) -> float:
    """Translate Brooks price-action context into a bounded scan ranking adjustment."""
    adjustment = 0.0
    if (res.get('pa_trade_plan') or {}).get('action') == 'READY':
        adjustment += 6
    if res.get('pa_h2_quality') == '强':
        adjustment += 5
    elif res.get('pa_h2_quality') == '中':
        adjustment += 2
    if res.get('pa_volume_confirmed'):
        adjustment += 4
    if (res.get('pa_multi_timeframe_score') or 0) >= 25:
        adjustment += 4
    if (res.get('pa_always_in_strength') or 0) >= 70:
        adjustment += 3
    if res.get('pa_micro_channel') == '多头微型通道':
        adjustment += 2

    if (res.get('pa_trap_risk') or 0) >= 75:
        adjustment -= 8
    if (res.get('pa_failure_risk') or 0) >= 70:
        adjustment -= 6
    if res.get('pa_trend_damage') in {'跌破EMA20', '跌破EMA60', '短线低点破坏'}:
        adjustment -= 8
    if res.get('pa_failed_second_entry') == '失败H2':
        adjustment -= 6
    if (res.get('pa_gap_risk') or 0) >= 70:
        adjustment -= 4
    if (res.get('pa_multi_timeframe_score') or 0) <= -25:
        adjustment -= 4
    return max(-20.0, min(20.0, adjustment))


def _is_momentum_watch_candidate(res: Dict[str, Any], vetoes: List[str]) -> bool:
    """Keep strong movers visible without promoting them to executable A/B candidates."""
    if not vetoes:
        return False
    fatal_vetoes = {
        "地雷预警", "市值<30亿", "板块下跌", "上影线过长",
        "Brooks风险偏高", "异常价格跳变", "价格行为回避", "低质量价格结构",
    }
    if any(v in fatal_vetoes for v in vetoes):
        return False
    has_momentum_veto = any(v in {"涨幅>7%", "5日涨>15%"} for v in vetoes)
    if not has_momentum_veto:
        return False

    pa_score = float(res.get('price_action_score') or 0)
    plan_action = (res.get('pa_trade_plan') or {}).get('action')
    structure_ok = (
        plan_action == 'READY'
        or pa_score >= 65
        or res.get('pa_h2_quality') == '强'
        or res.get('pa_breakout_quality') == '强突破'
        or res.get('price_action_signal') == '强多头趋势K'
    )
    volume_ok = bool(res.get('pa_volume_confirmed')) or res.get('pa_volume_pattern') == '放量突破'
    trend_ok = res.get('price_action_regime') in {'向上突破', '多头趋势'} or res.get('pa_weekly_context') == '周线多头'
    return structure_ok and (volume_ok or trend_ok)


def _apply_trade_execution_profile(res: Dict[str, Any]) -> None:
    """Classify scan hits into executable, observation, or blocked trade buckets."""
    action = _pa_plan_action(res)
    setup = str(res.get('pa_trade_setup') or "")
    strategy_type = str(res.get('strategy_type') or "")
    blockers: List[str] = []

    if _is_abnormal_price_move(res.get('代码'), res.get('涨幅%')):
        blockers.append("异常价格跳变，排除交易")
    if action == "AVOID":
        blockers.append("价格行为建议回避")
    elif action and action not in EXECUTABLE_PA_ACTIONS:
        blockers.append("交易计划未确认")
    elif not action and strategy_type in {"tv_dual", "tv_dual_strict"}:
        blockers.append("缺少价格行为交易计划")
    if setup in BLOCKED_PA_SETUPS:
        blockers.append(f"{setup}结构不进入交易池")
    if res.get('sector_trend') == 'DOWN':
        blockers.append("板块下跌")
    if res.get('sector_phase') == 'SECTOR_FADE' and res.get('sector_alignment_score', 0) < 60:
        blockers.append("板块扩散转弱")
    if float(res.get('涨幅%', 0) or 0) >= 9.8:
        blockers.append("涨停/近涨停，等待隔日确认")
    elif float(res.get('涨幅%', 0) or 0) > 7:
        blockers.append("涨幅偏高，等待回踩确认")

    score = float(res.get('final_rank_score', res.get('Score', 0)) or 0)
    if action == "READY":
        score += 8
    elif action == "WATCH":
        score += 2
    elif action == "AVOID":
        score -= 20
    elif action:
        score -= 8

    if setup in BLOCKED_PA_SETUPS:
        score -= 12
    if res.get('sector_trend') == 'LEAD':
        score += 5
    if (res.get('sector_alignment_score') or 0) >= 75:
        score += 5
    if _is_abnormal_price_move(res.get('代码'), res.get('涨幅%')):
        score -= 25
    if float(res.get('涨幅%', 0) or 0) > 7:
        score -= 8

    grade = res.get('sop_grade')
    fatal_markers = ("回避", "结构不进入交易池", "异常价格跳变", "板块下跌")
    has_fatal_blocker = any(any(marker in b for marker in fatal_markers) for b in blockers)
    trade_eligible = grade in {"A", "B"} and not blockers and strategy_type != "sector_watch"
    if trade_eligible:
        bucket = "TRADE"
    elif has_fatal_blocker:
        bucket = "BLOCK"
    else:
        bucket = "OBSERVE"

    res['trade_eligible'] = trade_eligible
    res['trade_bucket'] = bucket
    res['trade_blockers'] = blockers
    res['final_trade_score'] = round(score, 2)
    if strategy_type == "pine":
        res['trade_timeframe'] = "SHORT_1_2D"
        res['exit_hint'] = "Pine信号按1-2个交易日短线管理，次日不强则降级观察"


def _apply_sop_filter(results, market_regime, sector_trends):
    """SOP 过滤引擎：对扫描结果应用硬性条件、一票否决、加分项，生成 A/B/C/D 等级"""
    regime_status = market_regime.get('status', 'UNKNOWN')

    for res in results:
        vetoes = []
        checks = []
        bonuses = []

        # ── 一票否决 ──
        if res.get('涨幅%', 0) > 7:
            vetoes.append("涨幅>7%")
        if res.get('影线比', 0) > 0.5:
            vetoes.append("上影线过长")
        if res.get('pct_5d', 0) > 15:
            vetoes.append("5日涨>15%")
        if res.get('warnings') and len(res['warnings']) > 0:
            vetoes.append("地雷预警")
        if 0 < res.get('mkt_cap_yi', 0) < 30:
            vetoes.append("市值<30亿")
        if _is_abnormal_price_move(res.get('代码'), res.get('涨幅%')):
            vetoes.append("异常价格跳变")
        if _pa_plan_action(res) == "AVOID":
            vetoes.append("价格行为回避")
        if str(res.get('pa_trade_setup') or "") in BLOCKED_PA_SETUPS:
            vetoes.append("低质量价格结构")

        # 板块下跌否决
        sector = res.get('行业', '')
        sector_info = sector_trends.get(sector, {})
        if sector_info.get('trend') == 'DOWN':
            vetoes.append("板块下跌")
        if res.get('sector_phase') == 'SECTOR_FADE' and res.get('sector_alignment_score', 0) < 60:
            vetoes.append("板块扩散转弱")

        # ── 硬性条件 ──
        win_rate_str = res.get('历史胜率', '0%')
        try:
            win_rate = float(str(win_rate_str).replace('%', ''))
        except (ValueError, TypeError):
            win_rate = 0
        pf = res.get('回测统计', {}).get('profit_factor', 0)
        try:
            pf = float(pf)
        except (ValueError, TypeError):
            pf = 0

        if win_rate >= 50:
            checks.append("胜率≥50%")
        if pf >= 1.5:
            checks.append("盈亏比≥1.5")
        if regime_status != "CRITICAL":
            checks.append("大盘安全")

        # ── 加分项 ──
        if res.get('共振') == "🔥 核心热点":
            bonuses.append("板块共振")
        if (res.get('ROE') or 0) >= 8:
            bonuses.append("ROE≥8%")
        if (res.get('净利YOY') or 0) >= 15:
            bonuses.append("业绩增长")
        if sector_info.get('trend') == 'LEAD':
            bonuses.append("板块领涨")
        if (res.get('sector_momentum_score') or 0) >= 75:
            bonuses.append("板块强共振")
        elif (res.get('sector_momentum_score') or 0) >= 58:
            bonuses.append("板块早期启动")
        if (res.get('sector_alignment_score') or 0) >= 75:
            bonuses.append("个股强于板块")
        if regime_status == "OFFENSIVE":
            bonuses.append("大盘进攻")

        # ── 综合评级 ──
        if vetoes:
            grade = "D"
        elif len(checks) >= 3 and len(bonuses) >= 2:
            grade = "A"
        elif len(checks) >= 2:
            grade = "B"
        else:
            grade = "C"

        res['sop_grade'] = grade
        res['sop_vetoes'] = vetoes
        res['sop_checks'] = checks
        res['sop_bonuses'] = bonuses
        brooks_adjustment = _brooks_rank_adjustment(res)
        res['brooks_rank_adjustment'] = brooks_adjustment
        res['final_rank_score'] = round(float(res.get('Score') or 0) + brooks_adjustment, 2)
        res['final_rank_score'] = round(res['final_rank_score'] + min(12, max(0, float(res.get('sector_alignment_score') or 0) - 50) * 0.24), 2)
        if brooks_adjustment >= 6:
            bonuses.append("Brooks结构加分")
        if brooks_adjustment <= -6:
            vetoes.append("Brooks风险偏高")
            res['sop_grade'] = "D" if grade in {"C", "D"} else "C"
        if res.get('early_watch_only'):
            res['sop_grade'] = "D" if vetoes else "C"
            res['sop_checks'].append("等待TV-ZP确认")
            res['sop_bonuses'].append("早期异动观察")
        elif res.get('sector_watch_only'):
            fatal_vetoes = {"地雷预警", "板块下跌", "板块扩散转弱", "Brooks风险偏高"}
            res['sop_grade'] = "D" if any(v in fatal_vetoes for v in vetoes) else "C"
            res['sop_checks'].append("等待TV买点")
            res['sop_bonuses'].append("板块趋势确认观察")
        elif _is_momentum_watch_candidate(res, vetoes):
            res['sop_grade'] = "M"
            res['sop_checks'].append("动量观察")
            res['sop_bonuses'].append("强势动量观察")
            res['momentum_watch_only'] = True
            res['momentum_watch_reason'] = "涨幅/短线涨幅偏高，不追买；保留观察回踩或次日确认"
        _apply_trade_execution_profile(res)


def _early_watch_quality(res: Dict[str, Any]) -> tuple[bool, List[str]]:
    """Quality gate for MA-led early watch candidates under TV strict mode."""
    reasons = []
    if res.get('tv_ma_signal') != 'B共振' or res.get('tv_zp_signal') != '无':
        reasons.append("不是均线领先信号")

    has_brooks = (
        res.get('pa_h2_quality') == '强'
        or bool(res.get('pa_volume_confirmed'))
        or res.get('pa_volume_pattern') == '放量突破'
        or res.get('pa_breakout_quality') == '强突破'
    )
    if not has_brooks:
        reasons.append("Brooks/H2/放量质量不足")

    has_fundamental = (res.get('ROE') or 0) >= 8 or (res.get('净利YOY') or 0) >= 15
    if not has_fundamental:
        reasons.append("缺少基本面加分")

    if (res.get('pa_trade_plan') or {}).get('action') == 'AVOID':
        reasons.append("价格行为建议暂不参与")

    return len(reasons) == 0, reasons


def _build_sector_watch_candidates(
    candidates: pd.DataFrame,
    existing_codes: set,
    hist_map: Dict[str, pd.DataFrame],
    sector_map: Dict[str, str],
    sector_strength: Dict[str, Dict[str, Any]],
    max_per_sector: int = 5,
) -> List[Dict[str, Any]]:
    """Add strong-sector leaders to observation only, even when no TV buy signal fired."""
    if candidates is None or candidates.empty or not sector_strength:
        return []

    df = candidates.copy()
    df['code'] = df['code'].astype(str).str.zfill(6)
    df['industry'] = df['code'].map(sector_map).fillna('未知')
    df['pct_chg'] = pd.to_numeric(df['pct_chg'], errors='coerce').fillna(0)
    df['price'] = pd.to_numeric(df['price'], errors='coerce').fillna(0)
    df = df[(df['pct_chg'] > 0) & (df['price'] > 0) & (df['industry'] != '未知')]

    watch: List[Dict[str, Any]] = []
    for industry, group in df.groupby('industry'):
        strength = sector_strength.get(industry, {})
        if strength.get('sector_phase') != 'SECTOR_CONFIRM':
            continue
        sector_avg = float(strength.get('sector_avg_pct') or group['pct_chg'].mean() or 0)
        leaders = group.sort_values('pct_chg', ascending=False).head(max_per_sector)
        for rank, (_, row) in enumerate(leaders.iterrows(), start=1):
            code = str(row.get('code', '')).zfill(6)
            if code in existing_codes:
                continue
            stock_pct = float(row.get('pct_chg') or 0)
            role = classify_sector_role(stock_pct, sector_avg, rank_in_sector=rank)
            if role not in {'LEADER', 'CORE'}:
                continue

            df_hist = hist_map.get(code)
            if df_hist is None or df_hist.empty:
                continue
            df_hist = df_hist.copy().reset_index(drop=True)
            pa = analyze_price_action(df_hist)
            plan = pa.get('pa_trade_plan') or {}
            if plan.get('action') == 'AVOID':
                continue

            latest = df_hist.iloc[-1]
            current_price = float(row.get('price') or latest.get('收盘') or 0)
            entry_price = float(pa.get('pa_entry_price') or latest.get('最高') or current_price)
            risk = compute_paper_risk_levels(entry_price, entry_price, current_price, pa)
            pct_5d = 0.0
            if len(df_hist) >= 6:
                close_now = float(df_hist['收盘'].iloc[-1])
                close_5d_ago = float(df_hist['收盘'].iloc[-6])
                if close_5d_ago > 0:
                    pct_5d = round((close_now - close_5d_ago) / close_5d_ago * 100, 2)

            item: Dict[str, Any] = {
                '代码': code,
                '名称': str(row.get('name') or code),
                '行业': industry,
                '现价': round(current_price, 2),
                '涨幅%': round(stock_pct, 2),
                'Score': 58.0,
                'RSI': round(float(latest.get('RSI') or 0), 1),
                'DIF': round(float(latest.get('DIF') or 0), 3),
                'BB': round(float(latest.get('BB') or 0), 4),
                '粘合度': round(float(latest.get('粘合度') or 0), 4),
                '历史胜率': '0%',
                '信号次数': 0,
                '北向': '---',
                '共振': '板块强势观察',
                '影线比': 0,
                'strategy_type': 'sector_watch',
                'signal': '板块强势观察',
                'reason': f'{industry}趋势确认，{role}进入观察；等待TV买点或回踩确认',
                'sector_watch_only': True,
                'sector_watch_reason': f'{industry}趋势确认，个股为{role}，但TV双策略买点未触发',
                'tv_ma_signal': '未触发',
                'tv_zp_signal': '未触发',
                'tv_match': '板块观察',
                'pct_5d': pct_5d,
                'entry_price': round(entry_price, 2),
                'stop_price': risk['active_stop_price'],
                'plan_stop_price': risk['active_stop_price'],
                'initial_stop_price': risk['initial_stop_price'],
                'structure_stop_price': risk['structure_stop_price'],
                'target_price': risk['take_profit_price'],
                'risk_reward': risk['risk_reward'],
                'risk_notes': risk['risk_notes'],
            }
            item.update(pa)
            watch.append(item)
            existing_codes.add(code)

    return watch


def single_stock_task(code, name, price, vol, open_price, threshold, vol_multiplier, rsi_min, use_macd_filter, use_bb_sqz, sqz_lookback, use_weekly, use_rs_filter=True, local_only=False, engine=None, preloaded_df=None, target_date=None, bench_df=None, strategy_type="squeeze", pine_min_signals=3, min_data_days=None, weekly_ma_period=20, fund_data=None):
    # Use provided target_date or default to now
    if target_date is None or target_date == "":
        target_date = datetime.now()
    elif isinstance(target_date, str):
        target_date = datetime.strptime(target_date, "%Y-%m-%d")

    # 优先使用预加载的数据
    if preloaded_df is not None and not preloaded_df.empty:
        df = preloaded_df.copy().reset_index(drop=True)
    else:
        df = load_from_db(code, (target_date - timedelta(days=360)).strftime("%Y-%m-%d"), engine)

    if df.empty:
        return {"reason": "数据库无此股票历史数据"}

    # 统一日期格式为字符串，确保计算和合并的一致性
    if pd.api.types.is_datetime64_any_dtype(df['日期']):
        df['日期'] = df['日期'].dt.strftime('%Y-%m-%d')
    else:
        df['日期'] = df['日期'].astype(str).str[:10]

    # 根据策略类型设置最小数据要求
    if min_data_days is None:
        if strategy_type in {"pine", "tv_zp", "tv_dual", "tv_dual_strict"}:
            min_days = 120
        elif strategy_type == "consensus":
            min_days = 130 # 需要 60 周或足够长的日线来模拟
        else: # squeeze or both
            min_days = 120
    else:
        min_days = min_data_days

    if len(df) < min_days:
        return {"reason": f"样本不足({len(df)})"}

    if len(df) >= 2:
        try:
            latest_close = float(df['收盘'].iloc[-1])
            prev_close = float(df['收盘'].iloc[-2])
            if prev_close > 0:
                daily_pct = (latest_close - prev_close) / prev_close * 100
                if _is_abnormal_price_move(code, daily_pct):
                    return {"reason": f"异常价格跳变({daily_pct:.2f}%)"}
        except (TypeError, ValueError, KeyError):
            pass

    try:
        # 技术指标计算 - 预加载数据可能已有通用指标，但仍缺少 Pine 专属指标。
        enable_pine = (strategy_type in ["pine", "both", "tv_zp", "tv_dual", "tv_dual_strict"])
        if 'RSI' not in df.columns:
            df = calculate_indicators(df, current_price=price, current_vol=vol, current_open=open_price, bench_df=bench_df, enable_pine_indicators=enable_pine)

        pine_cols = {'RF_Upward', 'ST_Signal', 'RQK_Up', 'HalfTrend_Up', 'QQE_Long'}
        if enable_pine and not pine_cols.issubset(df.columns):
            df = calculate_pine_indicators(df)

        # 根据策略类型选择不同的筛选逻辑
        if strategy_type == "pine":
            # Pine Script 多指标共振策略
            match, stats = check_pine_strategy(df, min_signals=pine_min_signals, fund_data=fund_data)
            if match:
                stats['代码'] = code
                stats['名称'] = name
                stats['strategy_type'] = "pine"
                return stats
            else:
                return stats
        elif strategy_type == "tv_zp":
            match, stats = check_tv_zp_strategy(df, fund_data=fund_data)
            if match:
                stats['代码'] = code
                stats['名称'] = name
                stats['strategy_type'] = "tv_zp"
                return stats
            else:
                return stats
        elif strategy_type in {"tv_dual", "tv_dual_strict"}:
            match, stats = check_tv_dual_strategy(
                df,
                threshold=threshold,
                vol_multiplier=vol_multiplier,
                rsi_min=rsi_min,
                use_macd_filter=use_macd_filter,
                sqz_lookback=sqz_lookback,
                require_both=(strategy_type == "tv_dual_strict"),
                fund_data=fund_data,
            )
            if not match and strategy_type == "tv_dual_strict":
                relaxed_match, relaxed_stats = check_tv_dual_strategy(
                    df,
                    threshold=threshold,
                    vol_multiplier=vol_multiplier,
                    rsi_min=rsi_min,
                    use_macd_filter=use_macd_filter,
                    sqz_lookback=sqz_lookback,
                    require_both=False,
                    fund_data=fund_data,
                )
                if (
                    relaxed_match
                    and relaxed_stats.get('tv_ma_signal') == 'B共振'
                    and relaxed_stats.get('tv_zp_signal') == '无'
                ):
                    relaxed_stats['代码'] = code
                    relaxed_stats['名称'] = name
                    relaxed_stats['strategy_type'] = strategy_type
                    relaxed_stats['early_watch_only'] = True
                    relaxed_stats['early_watch_reason'] = "均线B共振领先，等待TV-ZP long确认"
                    relaxed_stats['signal'] = "早期观察"
                    relaxed_stats['reason'] = "早期观察：均线B共振领先，等待TV-ZP long确认"
                    return relaxed_stats
            if match:
                stats['代码'] = code
                stats['名称'] = name
                stats['strategy_type'] = strategy_type
                return stats
            else:
                return stats
        elif strategy_type == "both":
            # 同时满足：均线粘合 + Pine Script 共振
            match_sqz, stats_sqz = check_strategy(
                df, threshold=threshold, vol_multiplier=vol_multiplier, rsi_min=rsi_min,
                use_macd_filter=use_macd_filter, use_bb_sqz=use_bb_sqz,
                sqz_lookback=sqz_lookback, use_rs_filter=use_rs_filter, fund_data=fund_data
            )
            if not match_sqz:
                return stats_sqz

            match_pine, stats_pine = check_pine_strategy(df, min_signals=pine_min_signals, fund_data=fund_data)
            if not match_pine:
                return stats_pine

            # 两者都满足，合并结果
            # 周线趋势过滤
            is_w_ok = True
            if use_weekly:
                is_w_ok = get_weekly_indicators(code, df=df, local_only=local_only, weekly_ma_period=weekly_ma_period)
                if not is_w_ok:
                    return {"reason": "周线波段未走强"}

            combined_stats = stats_pine.copy()
            combined_stats.update(stats_sqz)
            # 分数取平均
            combined_stats['Score'] = (stats_pine['Score'] + stats_sqz['Score']) / 2
            combined_stats['代码'] = code
            combined_stats['名称'] = name
            combined_stats['strategy_type'] = "both"
            combined_stats['reason'] = "双重策略共振"
            return combined_stats
        elif strategy_type == "consensus":
            # Azul "共识" 策略
            is_w_ok = True
            if use_weekly:
                is_w_ok = get_weekly_indicators(code, df=df, local_only=local_only, weekly_ma_period=weekly_ma_period)

            match, stats = check_consensus_strategy(df, is_weekly_ok=is_w_ok, vol_multiplier=vol_multiplier, fund_data=fund_data)
            if match:
                stats['代码'] = code
                stats['名称'] = name
                stats['strategy_type'] = "consensus"
                return stats
            else:
                return stats
        else:
            # 默认均线粘合策略
            match, stats = check_strategy(
                df,
                threshold=threshold,
                vol_multiplier=vol_multiplier,
                rsi_min=rsi_min,
                use_macd_filter=use_macd_filter,
                use_bb_sqz=use_bb_sqz,
                sqz_lookback=sqz_lookback,
                use_rs_filter=use_rs_filter,
                fund_data=fund_data
            )

            if match:
                # 周线趋势过滤
                if use_weekly:
                    if not get_weekly_indicators(code, df=df, local_only=local_only, weekly_ma_period=weekly_ma_period):
                        return {"reason": "周线波段未走强"}

                stats['代码'] = code
                stats['名称'] = name
                stats['strategy_type'] = "squeeze"
                return stats
            else:
                return stats
    except Exception as e:
        logger.error(f"[{code}] 分析异常: {str(e)}")
        return {"reason": "策略计算异常"}


def perform_market_scan(
    threshold: float = 0.12,
    vol_multiplier: float = 1.5,
    rsi_min: int = 55,
    use_macd_filter: bool = True,
    use_bb_sqz: bool = False,
    sqz_lookback: int = 10,
    use_weekly: bool = False,
    market_range: str = "全市场(除科创)",
    turnover_min: float = 3.0,
    mkt_cap_min: float = 0.0,
    use_rs_filter: bool = False,
    local_only: bool = True,
    data_date: Optional[str] = None,
    strategy_type: str = "tv_dual_strict",
    pine_min_signals: int = 3,
    min_data_days: Optional[int] = None,
    weekly_ma_period: int = 20,
    stop_loss_pct: float = -8.0
) -> List[Dict[str, Any]]:
    """
    Executes the main market scan logic.
    """
    logger.info(f"[RUN_MARKET_SCAN] strategy_type={strategy_type}, min_data_days={min_data_days}, weekly_ma={weekly_ma_period}")
    max_date = None
    scan_started_at = datetime.now()
    audit_payload: Dict[str, Any] = {
        "started_at": scan_started_at,
        "status": "SUCCESS",
        "strategy_type": strategy_type,
        "params_snapshot": {
            "threshold": threshold,
            "vol_multiplier": vol_multiplier,
            "rsi_min": rsi_min,
            "use_macd_filter": use_macd_filter,
            "use_bb_sqz": use_bb_sqz,
            "sqz_lookback": sqz_lookback,
            "use_weekly": use_weekly,
            "market_range": market_range,
            "turnover_min": turnover_min,
            "mkt_cap_min": mkt_cap_min,
            "use_rs_filter": use_rs_filter,
            "local_only": local_only,
            "data_date": data_date,
            "pine_min_signals": pine_min_signals,
            "min_data_days": min_data_days,
            "weekly_ma_period": weekly_ma_period,
            "stop_loss_pct": stop_loss_pct,
        },
        "version_snapshot": {
            "strategy_logic_version": STRATEGY_LOGIC_VERSION,
            "backtest_engine_version": BACKTEST_ENGINE_VERSION,
            "exit_rule_version": EXIT_RULE_VERSION,
        },
    }

    try:
        # 获取大盘环境以动态调整参数
        regime = get_market_regime()
        reg_status = regime.get("status", "UNKNOWN")
        
        # 动态调优：在大跌 (CRITICAL) 时收紧筛选，在进攻 (OFFENSIVE) 时适度放宽换手
        if reg_status == "CRITICAL":
            rsi_min = min(rsi_min + 5, 80)
            threshold = max(0.08, threshold - 0.04)
            logger.info(f"[SCAN] Market is {reg_status}. Tightening RSI to {rsi_min} and Threshold to {threshold}.")
        elif reg_status == "OFFENSIVE":
            turnover_min = max(2.5, turnover_min - 0.5)
            logger.info(f"[SCAN] Market is {reg_status}. Adjusting turnover requirement to {turnover_min}.")

        snapshot_df = pd.DataFrame()
        engine = get_db_engine()

        # 1. 如果不是强制本地，尝试联网获取快照
        if not local_only and data_date is None:
            try:
                snapshot_df = get_market_snapshot()
            except Exception:
                logger.debug("Network snapshot failed.")

        # 2. 如果数据为空（联网失败 或 强制本地），启用本地数据库兜底
        if snapshot_df.empty:
            logger.info(f"Switching to LOCAL DB mode (Local Only: {local_only}, Data Date: {data_date or 'Auto'})...")
            try:
                with engine.connect() as conn:
                    # 如果指定了日期，使用指定日期；否则查找有足够数据的最近日期
                    if data_date:
                        # 验证日期格式和存在性
                        date_check = conn.execute(
                            text("SELECT date, COUNT(DISTINCT code) as stock_count FROM daily_k WHERE date = :date GROUP BY date"),
                            {"date": data_date}
                        ).fetchone()
                        if not date_check:
                            raise HTTPException(
                                status_code=400,
                                detail=f"指定日期 {data_date} 没有数据或格式不正确。请使用 YYYY-MM-DD 格式。"
                            )
                        max_date = data_date
                        stock_count = date_check[1]
                        logger.info(f"Using specified date: {max_date} ({stock_count} stocks)")
                    else:
                        # 查找有足够数据的最近日期（至少 1000 只股票）
                        logger.debug("Querying DB for best available date...")
                        best_date_query = text("""
                            SELECT date, COUNT(DISTINCT code) as stock_count
                            FROM daily_k
                            GROUP BY date
                            HAVING COUNT(DISTINCT code) >= 1000
                            ORDER BY date DESC
                            LIMIT 1
                        """)
                        best_date_res = conn.execute(best_date_query).fetchone()
                        if best_date_res and best_date_res[0]:
                            max_date = best_date_res[0]
                            stock_count = best_date_res[1]
                            logger.info(f"Found best date in DB: {max_date} ({stock_count} stocks)")
                        else:
                            raise HTTPException(status_code=503, detail="数据库中没有足够的数据进行扫描")

                    # 使用参数化查询防止 SQL 注入
                    # pct_chg 使用与前一日收盘价对比 (日涨幅)，而非日内 open→close
                    # 使用 LAG 窗口函数高效获取前日收盘价
                    # 注意: 避免 :: 类型转换语法，SQLAlchemy 会将 :: 误解析为命名参数
                    query = text("""
                        WITH ranked AS (
                            SELECT code, date, close, open, high, low, vol,
                                   LAG(close) OVER (PARTITION BY code ORDER BY date) as prev_close
                            FROM daily_k
                            WHERE date <= CAST(:max_date AS date)
                              AND date >= CAST(CAST(:max_date AS date) - interval '7 days' AS date)
                        )
                        SELECT r.code, b.name, r.close as price, r.open, r.high, r.low, r.vol,
                               CASE WHEN r.prev_close > 0
                                   THEN ROUND(CAST((r.close - r.prev_close) / r.prev_close * 100 AS numeric), 2)
                                   ELSE 0
                               END as pct_chg,
                               NULL as turnover,
                               NULL as mkt_cap
                        FROM ranked r
                        LEFT JOIN stock_basic b ON r.code = b.code
                        WHERE r.date = CAST(:max_date AS date)
                    """)
                    snapshot_df = pd.read_sql(query, engine, params={"max_date": max_date})
                    logger.info(f"Loaded {len(snapshot_df)} rows from DB fallback.")
                    # 保存数据日期信息用于返回
                    if hasattr(snapshot_df, 'attrs'):
                        snapshot_df.attrs['data_date'] = max_date
                    # Fallback for name if join failed
                    if not snapshot_df.empty:
                        snapshot_df['name'] = snapshot_df['name'].fillna(snapshot_df['code'])
            except HTTPException:
                raise
            except Exception as e:
                logger.error(f"Local fallback error: {e}")
                raise HTTPException(status_code=500, detail=f"加载数据失败: {str(e)}")

        if snapshot_df.empty:
            detail_msg = "无法获取市场数据。"
            if local_only:
                detail_msg += "【离线模式】已开启，但本地数据库尚未同步今日数据。请先执行【数据管理 -> 同步当日数据】。"
            else:
                detail_msg += "联网请求超时且本地无缓存数据，请检查网络或刷新后再试。"
            raise HTTPException(status_code=503, detail=detail_msg)

        # 初始过滤 (核心优化：只分析当日上涨且满足换手率/市值要求的股票)
        total_snapshot = len(snapshot_df)
        audit_payload["total_snapshot"] = total_snapshot

        # SOP: 仅保留 沪深主板(60, 00)、创业板(30)、科创板(688)；剔除 ST、退市整理
        snapshot_df['code_str'] = snapshot_df['code'].astype(str)
        snapshot_df['name_str'] = snapshot_df['name'].astype(str)

        is_target_market = snapshot_df['code_str'].str.startswith(('60', '688', '00', '30'))
        is_not_st = ~snapshot_df['name_str'].str.contains('ST|退', case=False)

        # fallback 模式下 turnover/mkt_cap 可能为 NULL（本地DB无此数据），需特殊处理
        has_turnover = snapshot_df['turnover'].notna()
        has_mkt_cap = snapshot_df['mkt_cap'].notna()

        candidates = snapshot_df[
            (snapshot_df['pct_chg'] > 0) &
            is_target_market & is_not_st &
            (~has_mkt_cap | (snapshot_df['mkt_cap'] >= mkt_cap_min * 100000000)) &
            (~has_turnover | (snapshot_df['turnover'] >= turnover_min))
        ].copy()
        audit_payload["candidate_count"] = len(candidates)

        logger.info(f"Snapshot: {total_snapshot} stocks")
        logger.info(f"After SOP Filter (No ST/BJ/Delist, +%, TO>{turnover_min}%, MC>{mkt_cap_min}亿): {len(candidates)} candidates")

        # 1. 处理科创板过滤
        if "包含科创板" not in market_range:
            candidates = candidates[~candidates['code'].astype(str).str.startswith('688')]

        # 2. 处理成分股精确过滤
        index_map = {
            "沪深300": "000300",
            "上证50": "000016",
            "中证500": "000905",
            "中证1000": "000852"
        }

        target_index = None
        for key, val in index_map.items():
            if key in market_range:
                target_index = val
                break

        if target_index:
            try:
                import akshare as ak
                cons_df = ak.index_stock_cons(symbol=target_index)
                if not cons_df.empty:
                    cons_codes = cons_df['品种代码'].tolist()
                    candidates = candidates[candidates['code'].isin(cons_codes)]
            except Exception as e:
                logger.warning(f"{market_range} filter failed: {e}")

        # 无数量上限，用户可按需调整筛选条件
        logger.info(f"准备扫描 {len(candidates)} 只股票...")
        
        ws_manager.broadcast_threadsafe({
            "type": "scan_start",
            "message": f"准备扫描 {len(candidates)} 只股票..."
        })

        results = []
        engine = get_db_engine()

        # 核心优化：预拉取指数历史并过滤，避免在线程内重复查询和过滤
        bench_df = get_index_hist("000001")
        bench_slice = None
        if not bench_df.empty:
            # 预先过滤出需要的日期范围
            hist_end = datetime.now() if not data_date else datetime.strptime(data_date, "%Y-%m-%d")
            hist_start = hist_end - timedelta(days=365)
            bench_df = bench_df.copy()
            bench_df['日期'] = pd.to_datetime(bench_df['日期'], errors='coerce')
            mask = (bench_df['日期'] >= hist_start) & (bench_df['日期'] <= hist_end)
            bench_slice = bench_df.loc[mask, ['日期', '收盘']].copy()
            logger.info(f"Pre-filtered benchmark data: {len(bench_slice)} points.")

        # 核心优化：批量拉取所有候选标的的历史数据，并进行向量化指标计算
        logger.info(f"Pre-loading historical data for {len(candidates)} candidates in batch...")
        start_time = time.time()
        end_date_hist = datetime.now().strftime("%Y-%m-%d") if not data_date else data_date
        # 深度修复：延长历史数据预热期至 1000 天（约 4 年），以确保 100/200 周期的长效 EMA 完全收敛，精确对齐 TradingView
        start_date_hist = (datetime.strptime(end_date_hist, "%Y-%m-%d") - timedelta(days=1000)).strftime("%Y-%m-%d")
        candidate_codes = candidates['code'].tolist()

        dfs = []
        try:
            chunk_size = 1000
            for i in range(0, len(candidate_codes), chunk_size):
                chunk = candidate_codes[i:i + chunk_size]
                placeholders = ", ".join([f":code_{j}" for j in range(len(chunk))])
                query_params = {f"code_{j}": c for j, c in enumerate(chunk)}
                query_params["start_date"] = start_date_hist
                query_params["end_date"] = end_date_hist

                query = text(f"""
                    SELECT d.code, d.date as "日期", d.open as "开盘", d.high as "最高",
                           d.low as "最低", d.close as "收盘", d.vol as "成交量",
                           b.name
                    FROM daily_k d
                    LEFT JOIN stock_basic b ON d.code = b.code
                    WHERE d.code IN ({placeholders}) AND d.date >= :start_date AND d.date <= :end_date
                    ORDER BY d.code, d.date ASC
                """)
                with engine.connect() as conn:
                    chunk_df = pd.read_sql(query, conn, params=query_params)
                    if not chunk_df.empty:
                        dfs.append(chunk_df)

            if not dfs:
                logger.error("No historical data found for candidates.")
                raise HTTPException(status_code=404, detail="本地历史数据缺失，请先同步数据。")

            master_df = pd.concat(dfs).reset_index(drop=True)

            # --- 注入实盘快照数据 ---
            # snapshot_df 包含了我们要筛选的标的的实时数据
            # 如果是本地历史回测 (local_only 且 snapshot 从 db fallback 加载)，master_df 已经包含该日数据，不可重复添加
            # 我们通过判断 snapshot 的日期是否大于 master_df 中的最大日期来决定是否追加
            snapshot_date = getattr(snapshot_df, 'attrs', {}).get('data_date', datetime.now().strftime("%Y-%m-%d"))
            # 确保 snapshot_date 是字符串格式
            if isinstance(snapshot_date, date):
                snapshot_date = snapshot_date.strftime("%Y-%m-%d")
            else:
                snapshot_date = str(snapshot_date)[:10]

            db_max_date = master_df['日期'].max()
            if isinstance(db_max_date, pd.Timestamp):
                db_max_date = db_max_date.strftime("%Y-%m-%d")
            else:
                db_max_date = str(db_max_date)[:10]

            if snapshot_date > db_max_date:
                logger.info(f"Appending snapshot live data ({snapshot_date}) to historical database series ({db_max_date})...")
                snap_to_append = snapshot_df[snapshot_df['code'].isin(master_df['code'].unique())].copy()
                snap_to_append = snap_to_append.rename(columns={
                    'price': '收盘',
                    'open': '开盘',
                    'high': '最高',
                    'low': '最低',
                    'vol': '成交量'
                })
                snap_to_append['日期'] = snapshot_date
                master_df = pd.concat([master_df, snap_to_append], ignore_index=True)
                # 重新排序并重置索引，确保 batch calculation 的索引对齐逻辑正常工作
                master_df = master_df.sort_values(['code', '日期']).reset_index(drop=True)

            logger.info(f"Master dataframe loaded: {len(master_df)} rows. Calculating indicators...")

            # --- 向量化指标计算 ---
            master_df = batch_calculate_indicators(master_df, bench_df=bench_slice)

            # Pine Script 策略或 同时启用 策略需要额外的指标计算
            if strategy_type in ["pine", "both", "tv_zp", "tv_dual", "tv_dual_strict"]:
                logger.info("Calculating Pine Script indicators in parallel...")
                # 对每只股票单独计算 Pine 指标 (使用并行加速)
                groups = [group.copy() for _, group in master_df.groupby('code')]

                with ThreadPoolExecutor(max_workers=8) as executor:
                    pine_results = list(executor.map(calculate_pine_indicators, groups))

                if pine_results:
                    master_df = pd.concat(pine_results, ignore_index=True)
                logger.info(f"Parallel Pine Script indicators calculation completed.")

            logger.info(f"Batch indicator calculation completed in {time.time() - start_time:.2f}s.")

            # 按代码切分，供并发扫描使用
            hist_map = {code: group for code, group in master_df.groupby('code')}

        except Exception as e:
            logger.error(f"Batch processing failed: {e}")
            import traceback
            traceback.print_exc()
            raise HTTPException(status_code=500, detail=f"数据预处理失败: {str(e)}")

        # 加载基本面数据
        fund_map = {}
        try:
            with engine.connect() as conn:
                fund_res = conn.execute(text("SELECT code, roe, net_profit_yoy, revenue_yoy, label FROM stock_fundamentals")).fetchall()
                for r in fund_res:
                    # 强制使用字符串作为 Key，防止 pandas 类型推断导致 int/str 匹配失败
                    code_key = str(r[0])
                    fund_map[code_key] = {
                        "roe": float(r[1]) if r[1] is not None else 0.0,
                        "net_profit_yoy": float(r[2]) if r[2] is not None else 0.0,
                        "revenue_yoy": float(r[3]) if r[3] is not None else 0.0,
                        "label": str(r[4]) if r[4] is not None else ""
                    }
                logger.info(f"Loaded fundamentals for {len(fund_map)} stocks from database.")
        except Exception as e:
            logger.error(f"Failed to load fundamentals: {e}")

        # 并发扫描逻辑 - 执行策略筛选和周线确认
        workers = 24  # 向量化后主压力在周线重采样，可提高并发
        logger.info(f"Starting strategy scan for {len(candidates)} stocks (workers={workers})...")

        results = []
        fail_reasons = {}
        none_count = 0
        processed_count = 0

        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_stock = {
                executor.submit(
                    single_stock_task,
                    row['code'], row['name'], row['price'], row['vol'], row['open'],
                    threshold, vol_multiplier, rsi_min, use_macd_filter, use_bb_sqz, sqz_lookback, use_weekly, use_rs_filter,
                    local_only=local_only, engine=engine, preloaded_df=hist_map.get(row['code']), target_date=data_date,
                    bench_df=bench_slice, strategy_type=strategy_type, pine_min_signals=pine_min_signals, min_data_days=min_data_days,
                    weekly_ma_period=weekly_ma_period, fund_data=fund_map.get(str(row['code']))
                ): row for _, row in candidates.iterrows()
            }

            for future in as_completed(future_to_stock):
                processed_count += 1
                if processed_count % 100 == 0 or processed_count == len(future_to_stock):
                    logger.info(f"Scan Progress: {processed_count}/{len(future_to_stock)} stocks processed...")
                    ws_manager.broadcast_threadsafe({
                        "type": "scan_progress",
                        "current": processed_count,
                        "total": len(future_to_stock),
                        "message": f"扫描中... ({processed_count}/{len(future_to_stock)})"
                    })

                try:
                    res = future.result(timeout=60)
                    if isinstance(res, dict) and 'Score' in res:
                        results.append(res)
                    elif isinstance(res, dict):
                        reason = res.get('reason', '未知')
                        fail_reasons[reason] = fail_reasons.get(reason, 0) + 1
                    elif res is None:
                        none_count += 1
                except Exception as e:
                    fail_reasons[f"异常: {str(e)[:30]}"] = fail_reasons.get(f"异常: {str(e)[:30]}", 0) + 1

            logger.info(f"Scan Stats: Matches={len(results)}, Rejections={sum(fail_reasons.values())}")
            audit_payload["fail_reasons"] = fail_reasons
            if fail_reasons:
                logger.info(f"Rejection Summary: {fail_reasons}")

            # Pine 策略或 同时启用 策略额外统计
            if strategy_type in ["pine", "both", "tv_zp", "tv_dual", "tv_dual_strict"]:
                pine_stats = {}
                for reason, count in fail_reasons.items():
                    if "信号不足" in reason:
                        # 提取信号数，如 "信号不足 (2/3)"
                        match = re.search(r'\((\d+)/(\d+)\)', reason)
                        if match:
                            signals = int(match.group(1))
                            pine_stats[signals] = pine_stats.get(signals, 0) + count
                if pine_stats:
                    logger.info(f"Pine Strategy Signal Distribution: {pine_stats}")

        logger.info(f"Scan completed in {time.time() - start_time:.2f}s. Found {len(results)} matches.")

        # 排序并取 Top 100
        results = sorted(results, key=lambda x: x['Score'], reverse=True)[:100]

        sector_map = get_sector_map()
        sector_trends = get_sector_trends()
        market_regime = get_market_regime()
        sector_history = build_sector_history_context(engine, sector_map)
        sector_strength = build_sector_strength(snapshot_df, sector_map, sector_trends, sector_history)

        sector_watch = _build_sector_watch_candidates(
            candidates,
            {str(r.get('代码')) for r in results},
            hist_map,
            sector_map,
            sector_strength,
        )
        if sector_watch:
            logger.info(f"Added {len(sector_watch)} sector-watch candidates.")
            results.extend(sector_watch)

        # 补充增强 data (行业, 胜率) - 并发处理 Top 100 + 板块观察
        logger.info(f"Parallel supplementing {len(results)} results (WinRate + Industry)...")

        def process_supplement(res):
            try:
                if res.get('sector_watch_only'):
                    return res
                code = res['代码']
                # 1. 计算回测统计
                # 直接使用 hist_map 中已计算好指标的数据，避免重复计算
                df_hist = hist_map.get(code)
                if df_hist is not None:
                    df_hist = df_hist.copy().reset_index(drop=True)
                df_labeled = df_hist
                
                # 获取止损参数 (前端可配置)
                try:
                    sl_pct = float(stop_loss_pct)
                except (TypeError, ValueError):
                    sl_pct = -8.0
                
                if strategy_type == "pine":
                    bt = calculate_pine_win_rate(df_labeled, min_signals=pine_min_signals, stop_loss_pct=sl_pct)
                elif strategy_type == "tv_zp":
                    bt = calculate_tv_zp_win_rate(df_labeled, stop_loss_pct=sl_pct)
                elif strategy_type in {"tv_dual", "tv_dual_strict"}:
                    bt = calculate_tv_dual_win_rate(
                        df_labeled,
                        stop_loss_pct=sl_pct,
                        threshold=threshold,
                        vol_multiplier=vol_multiplier,
                        rsi_min=rsi_min,
                        use_macd_filter=use_macd_filter,
                        sqz_lookback=sqz_lookback,
                        require_both=(strategy_type == "tv_dual_strict"),
                    )
                elif strategy_type == "both":
                    bt = calculate_pine_win_rate(df_labeled, min_signals=pine_min_signals, stop_loss_pct=sl_pct)
                elif strategy_type == "consensus":
                    bt = calculate_consensus_win_rate(df_labeled, stop_loss_pct=sl_pct)
                else:
                    bt = calculate_historical_win_rate(
                        df_labeled,
                        stop_loss_pct=sl_pct,
                        threshold=threshold,
                        vol_multiplier=vol_multiplier,
                        rsi_min=rsi_min,
                        use_macd_filter=use_macd_filter,
                        use_bb_sqz=use_bb_sqz,
                        sqz_lookback=sqz_lookback,
                        use_rs_filter=use_rs_filter,
                    )
                
                res['历史胜率'] = f"{bt['win_rate']}%"
                res['信号次数'] = bt['signal_count']
                res['回测统计'] = {
                    "avg_return": bt['avg_return'],
                    "max_drawdown": bt['max_drawdown'],
                    "profit_factor": bt['profit_factor'],
                    "avg_hold_days": bt['avg_hold_days'],
                    "stop_loss_hits": bt['stop_loss_hits'],
                    "backtest_engine_version": BACKTEST_ENGINE_VERSION,
                    "exit_rule_version": EXIT_RULE_VERSION,
                }
                res['strategy_logic_version'] = STRATEGY_LOGIC_VERSION
                res['backtest_engine_version'] = BACKTEST_ENGINE_VERSION
                res['exit_rule_version'] = EXIT_RULE_VERSION

                # 2. 获取行业
                industry = sector_map.get(code, "未知")
                if industry == "未知":
                    try:
                        import akshare as ak
                        info_df = ak.stock_individual_info_em(symbol=code)
                        if not info_df.empty:
                            industry_val = info_df[info_df['item'] == '行业分类']['value'].values
                            if len(industry_val) > 0:
                                industry = industry_val[0]
                    except Exception: pass
                res['行业'] = industry

                # 3. SOP 新增字段
                if df_hist is not None and len(df_hist) >= 6:
                    close_now = float(df_hist['收盘'].iloc[-1])
                    close_5d_ago = float(df_hist['收盘'].iloc[-6])
                    res['pct_5d'] = round((close_now - close_5d_ago) / close_5d_ago * 100, 2)
                else:
                    res['pct_5d'] = 0.0

                # 候选计划价：突破确认位 + 结构失效/初始风控，而不是简单 -8%
                if df_hist is not None and not df_hist.empty:
                    pa = analyze_price_action(df_hist)
                    res.update(pa)
                    entry_price = float(pa.get('pa_entry_price') or df_hist['最高'].iloc[-1])
                    current_price = float(df_hist['收盘'].iloc[-1])
                    risk = compute_paper_risk_levels(entry_price, entry_price, current_price, pa)
                    res['entry_price'] = round(entry_price, 2)
                    res['stop_price'] = risk['active_stop_price']
                    res['plan_stop_price'] = risk['active_stop_price']
                    res['initial_stop_price'] = risk['initial_stop_price']
                    res['structure_stop_price'] = risk['structure_stop_price']
                    res['target_price'] = risk['take_profit_price']
                    res['risk_reward'] = risk['risk_reward']
                    res['risk_notes'] = risk['risk_notes']
                    if not res.get('结构') and pa.get('price_action_pattern') not in (None, "无明确形态"):
                        res['结构'] = pa.get('price_action_pattern')
                else:
                    res['entry_price'] = res.get('现价', 0)
                    risk = compute_paper_risk_levels(float(res.get('现价', 0)), float(res.get('现价', 0)), float(res.get('现价', 0)))
                    res['stop_price'] = risk['active_stop_price']
                    res['plan_stop_price'] = risk['active_stop_price']
                    res['target_price'] = risk['take_profit_price']

            except Exception as e:
                logger.error(f"Supplement error for {res.get('代码')}: {e}")
            return res

        # 使用线程池并发补充 100 只股票
        with ThreadPoolExecutor(max_workers=15) as executor:
            list(executor.map(process_supplement, results))

        early_drop_count = 0
        for res in results:
            if res.get('early_watch_only'):
                ok, reasons = _early_watch_quality(res)
                res['early_watch_quality_ok'] = ok
                res['early_watch_quality_reasons'] = reasons
                if not ok:
                    early_drop_count += 1
                    res['_drop_early_watch'] = True
        if early_drop_count:
            logger.info(f"Early watch quality filter dropped {early_drop_count} candidates.")
            results = [r for r in results if not r.get('_drop_early_watch')]

        # --- SOP: 板块共振 (Sector Resonance) 计算 ---
        industry_counts = {}
        for res in results:
            ind = res.get('行业', '未知')
            industry_counts[ind] = industry_counts.get(ind, 0) + 1

        for res in results:
            ind = res.get('行业', '未知')
            if industry_counts.get(ind, 0) > 1 and ind != '未知':
                res['共振'] = "🔥 核心热点"
            else:
                res['共振'] = "独苗"

        # --- SOP: 地雷监测 (Mine Sweeper) ---
        mine_data = fetch_mine_sweeper_data()
        for res in results:
            code = res['代码']
            warnings = []
            if code in mine_data["earnings"]: warnings.append("📅 财报")
            if code in mine_data["unlocks"]: warnings.append("🔒 解禁")
            if code in mine_data["reductions"]: warnings.append("⚠️ 减持")
            res['warnings'] = warnings

        # --- SOP: 注入流通市值 (从快照数据) ---
        snap_mkt_map = {}
        if not snapshot_df.empty and 'mkt_cap' in snapshot_df.columns:
            for _, row in snapshot_df.iterrows():
                snap_mkt_map[str(row['code'])] = row.get('mkt_cap', 0)
        for res in results:
            mkt_raw = snap_mkt_map.get(res['代码'], 0)
            res['mkt_cap_yi'] = round(float(mkt_raw) / 1e8, 1) if mkt_raw else 0

        # 注入板块走势到每个结果
        for res in results:
            sector = res.get('行业', '')
            s_info = sector_trends.get(sector, {})
            res['sector_trend'] = s_info.get('trend', 'UNKNOWN')
            res['sector_pct'] = s_info.get('pct', 0)
            strength = sector_strength.get(sector, {})
            res.update(strength)
            stock_pct = float(res.get('涨幅%', 0) or 0)
            sector_avg = float(strength.get('sector_avg_pct', res.get('sector_pct', 0)) or 0)
            relative_pct = round(stock_pct - sector_avg, 2)
            res['sector_relative_pct'] = relative_pct
            momentum = float(strength.get('sector_momentum_score', 0) or 0)
            breadth = float(strength.get('sector_breadth', 0) or 0)
            leader_bonus = 15 if relative_pct >= 3 else 8 if relative_pct >= 1 else 0
            res['sector_alignment_score'] = round(min(100, momentum * 0.45 + breadth * 0.25 + leader_bonus + min(15, max(0, stock_pct) * 1.5)), 1)
            res['sector_role'] = classify_sector_role(
                stock_pct,
                sector_avg,
                alignment_score=res['sector_alignment_score'],
            )

        # 应用 SOP 等级评定
        _apply_sop_filter(results, market_regime, sector_trends)
        logger.info(f"SOP Grades: A={sum(1 for r in results if r.get('sop_grade')=='A')}, "
                    f"B={sum(1 for r in results if r.get('sop_grade')=='B')}, "
                    f"M={sum(1 for r in results if r.get('sop_grade')=='M')}, "
                    f"C={sum(1 for r in results if r.get('sop_grade')=='C')}, "
                    f"D={sum(1 for r in results if r.get('sop_grade')=='D')}")

        # 按 SOP 等级排序: A > B > M > C > D, 同等级内按 Brooks/板块调整后的 Score 排序
        grade_order = {'A': 0, 'B': 1, 'M': 2, 'C': 3, 'D': 4}
        results = sorted(results, key=lambda x: (grade_order.get(x.get('sop_grade', 'D'), 4), -x.get('final_rank_score', x.get('Score', 0))))

        # Update Sentinel memory (仅 A/B 级)
        from core.sentinel import sentinel, _select_intraday_push_stocks
        ab_results = [r for r in results if r.get('sop_grade') in ('A', 'B')]
        sentinel.last_top_5 = _select_intraday_push_stocks(results) if results else ab_results[:5]

        # --- 持久化保存 ---
        save_scan_results(results, engine)
        audit_payload.update({
            "scan_date": str(max_date) if max_date else datetime.now().strftime("%Y-%m-%d"),
            "finished_at": datetime.now(),
            "duration_sec": round(time.time() - start_time, 2),
            "result_count": len(results),
        })
        save_scan_audit_log(audit_payload, engine)

        ws_manager.broadcast_threadsafe({
            "type": "scan_end",
            "matches": len(results),
            "message": f"扫描完成！A级{sum(1 for r in results if r.get('sop_grade')=='A')}只 B级{sum(1 for r in results if r.get('sop_grade')=='B')}只"
        })

        # 在结果中注入数据日期
        scan_data_date = str(max_date) if max_date else datetime.now().strftime("%Y-%m-%d")
        for res in results:
            res['data_date'] = scan_data_date

        return results
    except HTTPException as he:
        engine = get_db_engine()
        audit_payload.update({
            "scan_date": str(max_date) if max_date else datetime.now().strftime("%Y-%m-%d"),
            "finished_at": datetime.now(),
            "duration_sec": round((datetime.now() - scan_started_at).total_seconds(), 2),
            "status": "FAILED",
            "error_message": str(he.detail),
        })
        save_scan_audit_log(audit_payload, engine)
        raise he
    except Exception as e:
        logger.error(f"Scanner fatal error: {e}")
        import traceback
        traceback.print_exc()
        engine = get_db_engine()
        audit_payload.update({
            "scan_date": str(max_date) if max_date else datetime.now().strftime("%Y-%m-%d"),
            "finished_at": datetime.now(),
            "duration_sec": round((datetime.now() - scan_started_at).total_seconds(), 2),
            "status": "FAILED",
            "error_message": str(e)[:500],
        })
        save_scan_audit_log(audit_payload, engine)
        raise HTTPException(status_code=500, detail=f"扫描执行失败: {str(e)}")
