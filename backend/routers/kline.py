from fastapi import APIRouter, HTTPException
from typing import Dict, Any, List
import pandas as pd
from datetime import datetime, timedelta

from core.logging_config import logger
from core.db import get_db_engine, validate_stock_code
from core.operation_plan import operation_bands, safe_num
from core.risk_constants import TV_SIGNAL_WARMUP_DAYS
from sqlalchemy import text

router = APIRouter(prefix="/api", tags=["kline"])

@router.get("/kline/{code}")
def get_kline_data(code: str, days: int = 400, strategy_type: str = "squeeze"):
    """
    获取单只股票的 K 线数据，并根据当前选股策略计算前端图表所需的指标与买卖点标记。
    """
    logger.info(f"Fetching kline data for {code} over {days} days with strategy: {strategy_type}")
    if not validate_stock_code(code):
        raise HTTPException(status_code=400, detail="Invalid stock code format")
    days = max(60, min(int(days), 800))
    try:
        engine = get_db_engine()
        
        # 信号计算预热窗口与全市场扫描一致；图表展示范围仍由 days 控制。
        display_start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")
        start_date = (
            datetime.now() - timedelta(days=max(days, TV_SIGNAL_WARMUP_DAYS))
        ).strftime("%Y-%m-%d")
        
        query = text("""
            SELECT date as "日期", close as "收盘", open as "开盘",
                   high as "最高", low as "最低", vol as "成交量",
                   turnover as "换手率"
            FROM daily_k
            WHERE code = :code AND date >= :start_date
            ORDER BY date ASC
        """)
        
        with engine.connect() as conn:
            df = pd.read_sql(query, conn, params={"code": code, "start_date": start_date})
            
        if df.empty:
            raise HTTPException(status_code=404, detail="No historical data found for this stock.")

        # 当日 bar 校正：daily_k 当日行可能是午间半日部分数据，用实时快照覆盖最后一根，
        # 使 K线/指标/价格行为与头部实时价一致（快照不可用时保持原样，fail-open）
        from core.data import apply_snapshot_bar_to_frame, get_snapshot_daily_bar
        if apply_snapshot_bar_to_frame(df, get_snapshot_daily_bar(code)):
            logger.info(f"Applied live snapshot bar to today's candle for {code}")

        from core.chip_distribution import build_chip_distribution
        from core.data import ensure_turnover_history

        chip_frame = ensure_turnover_history(code, df, engine=engine)
        chip_distribution = build_chip_distribution(chip_frame)

        from core.indicators import calculate_indicators
        from core.price_action import build_price_action_annotations, build_chart_hints, build_trade_projection
        # Calculate all indicators including Pine Script indicators (Range Filter, QQE)
        df = calculate_indicators(df, enable_pine_indicators=True)
        
        # 核心防崩保障：对计算后的 DataFrame 按日期强制去重并按日期严格升序排列，规避任何指标合并导致的时序紊乱或重复
        df['日期'] = pd.to_datetime(df['日期'])
        df = df.drop_duplicates(subset=['日期']).sort_values('日期').reset_index(drop=True)
        df['日期'] = df['日期'].dt.strftime("%Y-%m-%d")
        
        # Prepare data for lightweight-charts
        candlestick_data = []
        rf_filter_data = []
        markers_data = []
        
        display_df = df[df["日期"] >= display_start_date]
        for index, row in display_df.iterrows():
            date_str = str(row['日期'])
            
            # 1. Candlestick
            candlestick_data.append({
                "time": date_str,
                "open": float(row['开盘']),
                "high": float(row['最高']),
                "low": float(row['最低']),
                "close": float(row['收盘']),
            })
            
            # 2. Range Filter
            if 'RF_Filter' in df.columns and pd.notna(row['RF_Filter']):
                rf_filter_data.append({
                    "time": date_str,
                    "value": float(row['RF_Filter'])
                })

        # 3. 动态加载策略特有的买卖点明细 (均线粘合、多指标共振或Azul共识突破)
        from core.strategy import get_signal_details
        signals = get_signal_details(df, strategy_type=strategy_type)

        strategy_sets = {}
        for overlay_strategy in ("squeeze", "tv_zp"):
            try:
                overlay_signals = get_signal_details(df, strategy_type=overlay_strategy)
                overlay_signals["buy_count"] = len(overlay_signals.get("buy_signals", []))
                overlay_signals["sell_count"] = len(overlay_signals.get("sell_signals", []))
                overlay_signals["strategy_type"] = overlay_strategy
                strategy_sets[overlay_strategy] = overlay_signals
            except Exception as exc:
                logger.warning(f"K-line overlay signals for {code} with {overlay_strategy} failed: {exc}")
                strategy_sets[overlay_strategy] = {
                    "buy_signals": [],
                    "sell_signals": [],
                    "trailing_stops": [],
                    "strategy_type": overlay_strategy,
                    "buy_count": 0,
                    "sell_count": 0,
                }
        
        # 记录已添加标记的日期，避免多线程重叠
        added_dates = set()

        for b in signals.get("buy_signals", []):
            time_str = b["time"]
            if time_str >= display_start_date and time_str not in added_dates:
                markers_data.append({
                    "time": time_str,
                    "position": "belowBar",
                    "color": "#2196F3", # Blue for Buy
                    "shape": "arrowUp",
                    "text": "long" if strategy_type == "tv_zp" else "买点"
                })
                added_dates.add(time_str)
                
        for s in signals.get("sell_signals", []):
            time_str = s["time"]
            if time_str >= display_start_date and time_str not in added_dates:
                # 止损标记红色，止盈/超时标记绿色
                color = "#e91e63" if "止损" in s["reason"] else "#4caf50"
                reason = s.get("reason", "")
                text_label = "short" if strategy_type == "tv_zp" else (
                    "回测移动止盈" if "移动止盈" in reason else ("回测止损" if "止损" in reason else "回测卖点")
                )
                markers_data.append({
                    "time": time_str,
                    "position": "aboveBar",
                    "color": color,
                    "shape": "arrowDown",
                    "text": text_label
                })
                added_dates.add(time_str)

        # 4. Al Brooks-style price action annotations
        price_action = build_price_action_annotations(df)
        try:
            from core.hot_stocks import get_hot_stock_chart
            from core.price_action_timeframes import build_intraday_price_action_context

            minute_chart = get_hot_stock_chart(code, period="minute")
            intraday_context = build_intraday_price_action_context(
                price_action.get("summary", {}),
                minute_chart.get("points", []),
                minute_chart.get("previous_close"),
            )
            price_action["summary"]["pa_mtf_state"] = intraday_context["state"]
            price_action["summary"]["pa_mtf_intraday"] = intraday_context
        except Exception as exc:
            logger.warning(f"Intraday price-action context for {code} failed: {exc}")
        for marker in price_action.get("markers", []):
            if str(marker.get("time", "")) >= display_start_date:
                markers_data.append(marker)

        # 确保 markers 严格按照时间升序排列，解决 lightweight-charts 的 Assertion failed 崩溃问题
        markers_data.sort(key=lambda x: x["time"])

        # 确保 移动风控线 严格按时间升序且日期唯一（若同一天有多条重叠轨迹，取最低保底止损价）
        trailing_stops_dict = {}
        for ts in signals.get("trailing_stops", []):
            t = ts["time"]
            val = ts["value"]
            if t >= display_start_date and (t not in trailing_stops_dict or val < trailing_stops_dict[t]):
                trailing_stops_dict[t] = val
        
        trailing_stops_data = [{"time": t, "value": v} for t, v in sorted(trailing_stops_dict.items())]
        chart_context: Dict[str, Any] = {}
        try:
            with engine.connect() as conn:
                scan_res = conn.execute(text("""
                    SELECT date, strategy_type, score, pa_trade_action, pa_trade_setup,
                           price_action_detail
                    FROM scan_history
                    WHERE code = :code
                    ORDER BY date DESC
                    LIMIT 1
                """), {"code": code}).fetchone()
                if scan_res:
                    detail = scan_res[5] or {}
                    if isinstance(detail, str):
                        try:
                            import json
                            detail = json.loads(detail)
                        except Exception:
                            detail = {}
                    chart_context = {
                        "bark_recommendation_date": str(scan_res[0]) if scan_res[0] else None,
                        "latest_scan_date": str(scan_res[0]) if scan_res[0] else None,
                        "latest_scan_strategy": scan_res[1],
                        "latest_scan_score": float(scan_res[2]) if scan_res[2] is not None else None,
                        "latest_scan_pa_action": scan_res[3],
                        "latest_scan_pa_setup": scan_res[4],
                        "sector_phase": detail.get("sector_phase"),
                        "sector_momentum_score": detail.get("sector_momentum_score"),
                        "sector_alignment_score": detail.get("sector_alignment_score"),
                    }
                paper_res = conn.execute(text("""
                    SELECT entry_price, high_since_entry, current_price
                    FROM paper_trading
                    WHERE code = :code AND status = 'OPEN'
                    LIMIT 1
                """), {"code": code}).fetchone()
                if paper_res:
                    from core.risk_engine import compute_paper_risk_levels_with_context

                    entry = safe_num(paper_res[0])
                    current = safe_num(paper_res[2]) or safe_num(df.iloc[-1]["收盘"])
                    high = max(safe_num(paper_res[1], entry), current)
                    risk = compute_paper_risk_levels_with_context(entry, high, current, None, code)
                    trigger = max(current * 1.02, high)
                    guard = max(safe_num(risk.get("active_stop_price")), trigger * 0.985)
                    chart_context["operation_bands"] = operation_bands(
                        trigger=trigger,
                        guard=guard,
                        active_stop=safe_num(risk.get("active_stop_price") or risk.get("stop_price")),
                        structure_stop=safe_num(risk.get("structure_stop_price") or risk.get("initial_stop_price")),
                    )
        except Exception as exc:
            logger.warning(f"Chart context fetch for {code} failed: {exc}")

        return {
            "code": code,
            "candlestick": candlestick_data,
            "rf_filter": rf_filter_data,
            "markers": markers_data,
            "trailing_stops": trailing_stops_data,
            "strategy_sets": strategy_sets,
            "price_action": price_action.get("summary", {}),
            "price_action_lines": price_action.get("lines", []),
            "trend_phases": price_action.get("phase_timeline", []),
            "chart_hints": build_chart_hints(price_action.get("summary", {})),
            "trade_projection": build_trade_projection(
                price_action.get("summary", {}),
                last_close=(candlestick_data[-1]["close"] if candlestick_data else None),
            ),
            "chart_context": chart_context,
            "chip_distribution": chip_distribution,
        }
        
    except Exception as e:
        logger.error(f"Error generating kline data for {code}: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
