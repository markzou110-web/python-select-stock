"""
Scan router - market scanning and strategy analysis endpoints.

Extracted from api.py. Preserves all original logic exactly.
"""
from fastapi import APIRouter, HTTPException
from typing import Optional, Dict, Any
from datetime import datetime, timedelta, date
import time
import re
import pandas as pd
from concurrent.futures import ThreadPoolExecutor, as_completed
from sqlalchemy import text

import akshare as ak

from core.logging_config import logger
from core.ws_manager import manager as ws_manager
from core.db import (
    get_db_engine, save_scan_results,
    get_scan_history_by_date, get_scan_dates, get_available_dates,
    load_from_db
)
from core.data import (
    get_market_snapshot, get_index_hist, get_sector_map
)
from core.indicators import (
    calculate_indicators, calculate_pine_indicators,
    get_weekly_indicators, batch_calculate_indicators
)
from core.strategy import (
    check_strategy, check_pine_strategy, check_consensus_strategy,
    calculate_historical_win_rate, calculate_pine_win_rate, calculate_consensus_win_rate
)
from core.celery_app import celery_app

router = APIRouter(prefix="/api", tags=["scan"])

@celery_app.task(name="scan.run_market_scan_task")
def run_market_scan_task(
    threshold: float = 0.12,
    vol_multiplier: float = 1.5,
    rsi_min: int = 55,
    use_macd_filter: bool = True,
    use_bb_sqz: bool = True,
    sqz_lookback: int = 10,
    use_weekly: bool = True,
    market_range: str = "全市场(除科创)",
    turnover_min: float = 3.0,
    mkt_cap_min: float = 0.0,
    use_rs_filter: bool = True,
    local_only: bool = True,
    data_date: Optional[str] = None,
    strategy_type: str = "squeeze",
    pine_min_signals: int = 3,
    min_data_days: Optional[int] = None,
    weekly_ma_period: int = 20  # 周线均线周期 (10/20/30/60)
):
    # 调试日志：确认接收到的策略类型
    logger.info(f"[RUN_MARKET_SCAN] strategy_type={strategy_type}, min_data_days={min_data_days}, weekly_ma={weekly_ma_period}")

    try:
        # 获取大盘环境以动态调整参数
        from core.data import get_market_regime
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
            mask = (bench_df['日期'] >= hist_start.strftime("%Y-%m-%d")) & (bench_df['日期'] <= hist_end.strftime("%Y-%m-%d"))
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
                params = {f"code_{j}": c for j, c in enumerate(chunk)}
                params["start_date"] = start_date_hist
                params["end_date"] = end_date_hist

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
                    chunk_df = pd.read_sql(query, conn, params=params)
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

            if snapshot_date > db_max_date and not candidates.empty:
                logger.info(f"Appending real-time snapshot data ({snapshot_date}) to historical data...")
                # 容错双重防御：防止因行情接口偶发缺陷导致缺失 high 或 low 字段
                for col in ['high', 'low']:
                    if col not in candidates.columns:
                        candidates[col] = candidates['price'] if 'price' in candidates.columns else candidates['open']
                
                snap_to_append = candidates[['code', 'open', 'high', 'low', 'price', 'vol']].copy()
                snap_to_append = snap_to_append.rename(columns={
                    'open': '开盘',
                    'high': '最高',
                    'low': '最低',
                    'price': '收盘',
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
            if strategy_type in ["pine", "both"]:
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
            if fail_reasons:
                logger.info(f"Rejection Summary: {fail_reasons}")

            # Pine 策略或 同时启用 策略额外统计
            if strategy_type in ["pine", "both"]:
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

        # 补充增强数据 (行业, 胜率) - 并发处理 Top 100
        logger.info(f"Parallel supplementing {len(results)} results (WinRate + Industry)...")
        sector_map = get_sector_map()

        def process_supplement(res):
            try:
                code = res['代码']
                # 1. 计算回测统计
                df_hist = hist_map.get(code)
                if df_hist is not None:
                    df_hist = df_hist.copy().reset_index(drop=True)
                # 并发中重新计算指标 (Top 100 规模可控)
                enable_pine = (strategy_type in ["pine", "both"])
                df_labeled = calculate_indicators(df_hist, bench_df=bench_slice, enable_pine_indicators=enable_pine)
                
                # 获取止损参数 (前端可配置)
                sl_pct = params.get("stop_loss_pct", -8.0)
                try:
                    sl_pct = float(sl_pct)
                except (TypeError, ValueError):
                    sl_pct = -8.0
                
                if strategy_type == "pine":
                    bt = calculate_pine_win_rate(df_labeled, stop_loss_pct=sl_pct)
                elif strategy_type == "both":
                    bt = calculate_pine_win_rate(df_labeled, stop_loss_pct=sl_pct)
                elif strategy_type == "consensus":
                    bt = calculate_consensus_win_rate(df_labeled, stop_loss_pct=sl_pct)
                else:
                    bt = calculate_historical_win_rate(df_labeled, stop_loss_pct=sl_pct)
                
                res['历史胜率'] = f"{bt['win_rate']}%"
                res['信号次数'] = bt['signal_count']
                res['回测统计'] = {
                    "avg_return": bt['avg_return'],
                    "max_drawdown": bt['max_drawdown'],
                    "profit_factor": bt['profit_factor'],
                    "avg_hold_days": bt['avg_hold_days'],
                    "stop_loss_hits": bt['stop_loss_hits'],
                }

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

                # 入场价 (当日最高价作为突破确认位) 和 止损价 (-8%)
                if df_hist is not None and not df_hist.empty:
                    res['entry_price'] = round(float(df_hist['最高'].iloc[-1]), 2)
                    res['stop_price'] = round(float(res['entry_price']) * 0.92, 2)
                else:
                    res['entry_price'] = res.get('现价', 0)
                    res['stop_price'] = round(float(res.get('现价', 0)) * 0.92, 2)

            except Exception as e:
                logger.error(f"Supplement error for {res.get('代码')}: {e}")
            return res

        # 使用线程池并发补充 100 只股票
        with ThreadPoolExecutor(max_workers=15) as executor:
            list(executor.map(process_supplement, results))

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
        from routers.market import fetch_mine_sweeper_data
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

        # --- SOP: 大盘-板块-个股联动过滤 ---
        from core.data import get_sector_trends, get_market_regime
        sector_trends = get_sector_trends()
        market_regime = get_market_regime()

        # 注入板块走势到每个结果
        for res in results:
            sector = res.get('行业', '')
            s_info = sector_trends.get(sector, {})
            res['sector_trend'] = s_info.get('trend', 'UNKNOWN')
            res['sector_pct'] = s_info.get('pct', 0)

        # 应用 SOP 等级评定
        _apply_sop_filter(results, market_regime, sector_trends)
        logger.info(f"SOP Grades: A={sum(1 for r in results if r.get('sop_grade')=='A')}, "
                    f"B={sum(1 for r in results if r.get('sop_grade')=='B')}, "
                    f"C={sum(1 for r in results if r.get('sop_grade')=='C')}, "
                    f"D={sum(1 for r in results if r.get('sop_grade')=='D')}")

        # 按 SOP 等级排序: A > B > C > D, 同等级内按 Score 排序
        grade_order = {'A': 0, 'B': 1, 'C': 2, 'D': 3}
        results = sorted(results, key=lambda x: (grade_order.get(x.get('sop_grade', 'D'), 3), -x.get('Score', 0)))

        # Update Sentinel memory (仅 A/B 级)
        from api import sentinel
        ab_results = [r for r in results if r.get('sop_grade') in ('A', 'B')]
        sentinel.last_top_5 = ab_results[:5] if ab_results else results[:5]

        # --- 持久化保存 ---
        save_scan_results(results, engine)

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
        # 允许 HTTPException 直接通过，不再包装成 500
        raise he
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


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

        # 板块下跌否决
        sector = res.get('行业', '')
        sector_info = sector_trends.get(sector, {})
        if sector_info.get('trend') == 'DOWN':
            vetoes.append("板块下跌")

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
        if strategy_type == "pine":
            min_days = 50
        elif strategy_type == "consensus":
            min_days = 130 # 需要 60 周或足够长的日线来模拟
        else: # squeeze or both
            min_days = 120
    else:
        min_days = min_data_days

    if len(df) < min_days:
        return {"reason": f"样本不足({len(df)})"}

    try:
        # 技术指标计算 - 如果预加载的数据已经包含指标，则跳过
        if 'RSI' not in df.columns:
            # Pine Script 策略或 同时启用 需要额外的指标
            enable_pine = (strategy_type in ["pine", "both"])
            df = calculate_indicators(df, current_price=price, current_vol=vol, current_open=open_price, bench_df=bench_df, enable_pine_indicators=enable_pine)

            # 如果包含 Pine 策略且指标已计算但缺少 Pine 特定指标，需要补充计算
            if enable_pine and 'RF_Upward' not in df.columns:
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


@router.get("/scan")
def scan_market(
    threshold: float = 0.12,
    vol_multiplier: float = 1.5,
    rsi_min: int = 55,
    use_macd_filter: bool = True,
    use_bb_sqz: bool = True,
    sqz_lookback: int = 10,
    use_weekly: bool = True,
    market_range: str = "全市场(除科创)",
    turnover_min: float = 3.0,
    mkt_cap_min: float = 0.0,
    use_rs_filter: bool = True,
    local_only: bool = True,
    data_date: Optional[str] = None,
    strategy_type: str = "squeeze",
    pine_min_signals: int = 3,
    min_data_days: Optional[int] = None,
    weekly_ma_period: int = 20  # 周线均线周期
):
    """
    API Endpoint for market scan (Asynchronous via Celery)
    """
    logger.info(f"[SCAN API] Submitting task: strategy_type={strategy_type}, pine_min_signals={pine_min_signals}, min_data_days={min_data_days}, weekly_ma={weekly_ma_period}")
    
    # 异步发送任务给 Celery Queue
    task = run_market_scan_task.delay(
        threshold, vol_multiplier, rsi_min, use_macd_filter,
        use_bb_sqz, sqz_lookback, use_weekly, market_range,
        turnover_min, mkt_cap_min, use_rs_filter, local_only, data_date, strategy_type, pine_min_signals, min_data_days,
        weekly_ma_period
    )
    
    # 无 Redis 的兜底处理：任务已同步完成，直接把结果交给前端 (前端的 fallback 机制接收)
    if celery_app.conf.task_always_eager and task.state == 'SUCCESS':
        return {"status": "SUCCESS", "results": task.result, "message": "同步扫描完成"}

    return {"task_id": task.id, "status": "PENDING", "message": "扫描任务已提交队列"}

@router.get("/scan/status/{task_id}")
def get_scan_status(task_id: str):
    """查询扫描任务状态和结果"""
    # get async result
    task = celery_app.AsyncResult(task_id)
    if task.state == 'SUCCESS':
        # Result is either list of items or serialized JSON
        result = task.result
        return {"task_id": task_id, "status": task.state, "results": result, "message": "扫描完成"}
    elif task.state == 'FAILURE':
        return {"task_id": task_id, "status": task.state, "message": str(task.info)}
    else:
        return {"task_id": task_id, "status": task.state, "message": "任务正在执行中..."}



@router.get("/scan/history")
async def get_history_results(date: str):
    """获取指定日期的历史选股结果并计算至今表现"""
    results = get_scan_history_by_date(date)
    if not results:
        return []
    
    # 获取实时快照，计算后续表现
    try:
        snapshot = get_market_snapshot()
        if not snapshot.empty:
            for r in results:
                code = r["代码"]
                hist_price = float(r["现价"])
                match = snapshot[snapshot['code'] == code]
                if not match.empty:
                    curr_price = float(match.iloc[0]['price'])
                    pl_pct = (curr_price - hist_price) / hist_price * 100 if hist_price > 0 else 0
                    r["最新价"] = curr_price
                    r["表现%"] = round(pl_pct, 2)
                else:
                    r["最新价"] = hist_price
                    r["表现%"] = 0.0
    except Exception as e:
        logger.warning(f"Failed to fetch performance for history: {e}")
        
    return results


@router.get("/scan/dates")
async def get_history_dates():
    """获取历史扫描日期列表"""
    return get_scan_dates()


@router.get("/scan/available-dates")
async def get_available_dates_api() -> Dict[str, Any]:
    """获取可用于选股的数据日期列表"""
    dates = get_available_dates()
    return {"dates": dates}


@router.post("/scan/optimize")
def optimize_parameters(data: dict) -> Dict[str, Any]:
    """
    参数寻优：对指定股票和策略跑参数正交组合回测，返回胜率矩阵

    Args:
        data: {
            "code": "000001",
            "strategy": "squeeze",
            "param_x": "rsi_min",
            "param_x_values": [50, 55, 60, 65],
            "param_y": "stop_loss_pct",
            "param_y_values": [-5, -8, -10, -12]
        }
    """
    from core.strategy import run_optimization_grid

    code = data.get("code", "")
    if not validate_stock_code(code):
        raise HTTPException(status_code=400, detail="Invalid stock code")

    strategy = data.get("strategy", "squeeze")

    engine = get_db_engine()
    if not engine:
        raise HTTPException(status_code=500, detail="Database unavailable")

    # Load historical data
    df = load_from_db(code, (datetime.now() - timedelta(days=365)).strftime("%Y-%m-%d"), engine)
    if df.empty:
        raise HTTPException(status_code=404, detail="No historical data for this stock")

    # Calculate indicators
    enable_pine = strategy in ["pine", "both"]
    df = calculate_indicators(df, enable_pine_indicators=enable_pine)
    if enable_pine and 'RF_Upward' not in df.columns:
        df = calculate_pine_indicators(df)

    return run_optimization_grid(
        df,
        strategy_type=strategy,
        param_x=data.get("param_x", "rsi_min"),
        param_x_values=data.get("param_x_values"),
        param_y=data.get("param_y", "stop_loss_pct"),
        param_y_values=data.get("param_y_values"),
    )
