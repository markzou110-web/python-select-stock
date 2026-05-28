"""
Stock router - individual stock data endpoints.

Extracted from api.py.
"""
from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from datetime import datetime, timedelta
import akshare as ak
import pandas as pd

from core.logging_config import logger
from core.db import get_db_engine, validate_stock_code, load_from_db, save_to_db
from core.indicators import calculate_indicators, calculate_pine_indicators
from core.strategy import get_signal_details, run_optimization_grid
from core.risk_constants import (
    FIXED_STOP_LOSS_RATIO, TRAILING_STOP_RATIO, TAKE_PROFIT_RATIO
)

router = APIRouter(prefix="/api/stock", tags=["stock"])


@router.get("/{code}/kline")
async def get_stock_kline(code: str, local_only: bool = False):
    """
    获取个股 K 线数据供前端绘图

    Args:
        code: Stock code (validated)
        local_only: If True, only use local data
    """
    if not validate_stock_code(code):
        raise HTTPException(status_code=400, detail="Invalid stock code format")

    engine = get_db_engine()
    target_date = datetime.now()
    start_date_str = (target_date - timedelta(days=300)).strftime("%Y-%m-%d")

    df = load_from_db(code, start_date_str, engine)

    if df.empty and not local_only:
        try:
            logger.debug(f"API: Fetching K-line for {code}...")
            start_fetch = (target_date - timedelta(days=300)).strftime("%Y%m%d")
            df = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_fetch, adjust="qfq")
            if not df.empty:
                save_to_db(df, code, engine)
        except Exception as e:
            logger.warning(f"K-line fetch error for {code}: {e}")

    if df.empty:
        return {"code": code, "data": []}

    df = calculate_indicators(df, periods=[20, 120, 250])

    mapping = {
        '日期': 'time', '开盘': 'open', '最高': 'high', '最低': 'low', '收盘': 'close', '成交量': 'value'
    }
    ak_mapping = {
        '日期': 'time', '开盘': 'open', '最高': 'high', '最低': 'low', '收盘': 'close', '成交量': 'volume'
    }

    col_map = mapping if '收盘' in df.columns else ak_mapping
    plot_df = df.rename(columns=col_map)
    plot_df['time'] = plot_df['time'].astype(str)

    cols = ['time', 'open', 'high', 'low', 'close', 'value' if 'value' in plot_df.columns else 'volume', 'EMA20', 'EMA120', 'EMA250']
    records = plot_df[cols].tail(200).to_dict('records')

    return {
        "code": code,
        "name": df.iloc[0]['name'] if 'name' in df.columns else "未知",
        "data": records
    }


def fetch_stock_data_with_indicators(code: str):
    engine = get_db_engine()
    target_date = datetime.now()
    start_db = (target_date - timedelta(days=365)).strftime("%Y-%m-%d")
    df = load_from_db(code, start_db, engine)

    is_stale = True
    if not df.empty and '日期' in df.columns:
        last_date_str = str(df.iloc[-1]['日期'])
        try:
            last_date = datetime.strptime(last_date_str, "%Y-%m-%d")
            if (target_date - last_date).days <= 2:
                is_stale = False
        except:
            pass

    if is_stale or df.empty:
        try:
            start_date = (target_date - timedelta(days=365)).strftime("%Y%m%d")
            df_new = ak.stock_zh_a_hist(symbol=code, period="daily", start_date=start_date, adjust="qfq")
            if not df_new.empty:
                df = df_new
                save_to_db(df, code, engine)
        except Exception as e:
            logger.error(f"Fetch error for {code}: {e}")

    if df.empty:
        return df

    df = calculate_indicators(df, periods=[5, 10, 20, 60])
    return df


@router.get("/detail")
def get_stock_detail(code: str):
    """
    获取单只股票详情 (k线指标 + 资金面) 用于 AI Deep Dive
    """
    if not validate_stock_code(code):
        raise HTTPException(status_code=400, detail="Invalid stock code format")

    try:
        df = fetch_stock_data_with_indicators(code)
        if df.empty:
            raise HTTPException(status_code=404, detail="未找到该股票的历史数据")

        plot_df = df.tail(60).copy()
        plot_df['time'] = plot_df['日期'].astype(str)

        records = []
        for _, row in plot_df.iterrows():
            records.append({
                "time": row['time'],
                "open": float(row['开盘']),
                "high": float(row['最高']),
                "low": float(row['最低']),
                "close": float(row['收盘']),
                "value": float(row['成交量']),
                "EMA5": float(row.get('EMA5', 0)),
                "EMA20": float(row.get('EMA20', 0)),
                "EMA60": float(row.get('EMA60', 0)),
                "RSI": float(row.get('RSI', 0)),
                "MACD": float(row.get('MACD_HIST', 0))
            })

        # --- 增补计算该股票的完整元数据 ---
        engine = get_db_engine()
        from core.strategy import check_strategy, calculate_historical_win_rate
        
        is_paper_trade = False
        buy_price = 0.0
        stop_price = 0.0
        take_profit_price = 0.0
        paper_remark = ""
        
        with engine.connect() as conn:
            basic_res = conn.execute(text("SELECT name, industry FROM stock_basic WHERE code = :code"), {"code": code}).fetchone()
            name = basic_res[0] if basic_res else "未知"
            industry = basic_res[1] if basic_res else "未知"
            
            # 检查是否有持仓中的拟合实盘记录
            paper_res = conn.execute(text("""
                SELECT entry_price, high_since_entry, status, remark
                FROM paper_trading 
                WHERE code = :code AND status = 'OPEN'
                LIMIT 1
            """), {"code": code}).fetchone()
            
            if paper_res:
                entry_price = float(paper_res[0])
                high_since_entry = float(paper_res[1]) if paper_res[1] is not None else entry_price
                # NaN 防御
                import math
                if math.isnan(high_since_entry):
                    high_since_entry = entry_price
                is_paper_trade = True
                buy_price = round(entry_price, 2)
                # 固定止损 OR 移动止损，取其高者
                stop_price = round(max(entry_price * FIXED_STOP_LOSS_RATIO, high_since_entry * TRAILING_STOP_RATIO), 2)
                take_profit_price = round(entry_price * TAKE_PROFIT_RATIO, 2)
                paper_remark = str(paper_res[3] or "")
            
        _, stats = check_strategy(df)
        bt = calculate_historical_win_rate(df)
        
        high = float(df.iloc[-1]['最高'])
        low = float(df.iloc[-1]['最低'])
        close = float(df.iloc[-1]['收盘'])
        open_p = float(df.iloc[-1]['开盘'])
        shadow_ratio = round((high - max(close, open_p)) / (high - low) if (high - low) > 0 else 0.0, 2)
        
        stock_info = {
            "代码": code,
            "名称": name,
            "行业": industry,
            "现价": round(close, 2),
            "涨幅%": round(float((df.iloc[-1]['收盘'] - df.iloc[-2]['收盘']) / df.iloc[-2]['收盘'] * 100), 2) if len(df) > 1 else 0.0,
            "Score": round(float(stats.get('Score', 50.0)), 1),
            "RSI": round(float(df.iloc[-1].get('RSI', 50.0)), 1),
            "DIF": round(float(df.iloc[-1].get('MACD_DIF', 0.0)), 3),
            "BB": round(float(df.iloc[-1].get('BB', 0.0)), 3),
            "粘合度": round(float(df.iloc[-1].get('MA_GLUE', 0.0)), 4) if 'MA_GLUE' in df.columns else 0.0,
            "历史胜率": f"{bt['win_rate']}%",
            "信号次数": int(bt['signal_count']),
            "共振": "独苗",
            "影线比": shadow_ratio,
            "strategy_type": "squeeze",
            "roe": None,
            "净利YOY": None,
            "warnings": [],
            "is_paper_trade": is_paper_trade,
            "buy_price": buy_price,
            "stop_price": stop_price,
            "take_profit_price": take_profit_price,
            "paper_remark": paper_remark,
            "回测统计": {
                "avg_return": bt['avg_return'],
                "max_drawdown": bt['max_drawdown'],
                "profit_factor": bt['profit_factor'],
                "avg_hold_days": bt['avg_hold_days'],
                "stop_loss_hits": bt['stop_loss_hits']
            }
        }

        return {
            "code": code,
            "data": records,
            "stock_info": stock_info,
            "indicators": {
                "rsi": float(df.iloc[-1].get('RSI', 0)),
                "dif": float(df.iloc[-1].get('MACD_DIF', 0)),
                "dea": float(df.iloc[-1].get('MACD_DEA', 0)),
                "hist": float(df.iloc[-1].get('MACD_HIST', 0)),
                "ema5": float(df.iloc[-1].get('EMA5', 0)),
                "ema20": float(df.iloc[-1].get('EMA20', 0)),
                "ema60": float(df.iloc[-1].get('EMA60', 0))
            }
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching stock detail for {code}: {e}")
        raise HTTPException(status_code=500, detail="Internal server error")


# ═══════════════════════════════════════════════════════
# Full-Analysis Endpoint — 全量分析接口
# ═══════════════════════════════════════════════════════

def _fetch_stock_concepts(code: str) -> list:
    """获取个股所属概念板块 (24小时缓存)"""
    from core.data import get_cached_data, set_cached_data
    cache_key = f"concepts_{code}"
    cached = get_cached_data(cache_key, 86400)
    if cached is not None:
        return cached

    concepts = []
    try:
        # 获取个股所属概念板块
        df_concepts = ak.stock_board_concept_name_em()
        if df_concepts is not None and not df_concepts.empty:
            # 遍历概念板块，找出包含该股票的板块
            from concurrent.futures import ThreadPoolExecutor, as_completed
            import random, time as _time

            top_concepts = df_concepts.head(30)  # 限制查询数量避免超时

            def check_concept(row):
                try:
                    _time.sleep(random.uniform(0.1, 0.3))
                    name = row['板块名称']
                    df_cons = ak.stock_board_concept_cons_em(symbol=name)
                    if df_cons is not None and not df_cons.empty:
                        if code in df_cons['代码'].values:
                            pct = float(row.get('涨跌幅', 0))
                            return {"name": name, "pct": round(pct, 2)}
                except Exception:
                    pass
                return None

            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = [executor.submit(check_concept, row) for _, row in top_concepts.iterrows()]
                for f in as_completed(futures, timeout=15):
                    try:
                        result = f.result(timeout=3)
                        if result:
                            concepts.append(result)
                    except Exception:
                        continue
    except Exception as e:
        logger.warning(f"Concept fetch for {code} failed: {e}")

    # 兜底：至少返回行业信息
    if not concepts:
        engine = get_db_engine()
        try:
            with engine.connect() as conn:
                res = conn.execute(text("SELECT industry FROM stock_basic WHERE code = :code"), {"code": code}).fetchone()
                if res and res[0]:
                    concepts = [{"name": res[0], "pct": 0.0}]
        except Exception:
            pass

    set_cached_data(cache_key, concepts)
    return concepts


def _fetch_financials(code: str) -> dict:
    """获取个股财务数据"""
    engine = get_db_engine()
    financials = {
        "roe": None, "pe_ttm": None, "pe_percentile": "未知",
        "net_profit_yoy": None, "revenue_yoy": None,
        "label": "未知", "mkt_cap_yi": None
    }

    try:
        with engine.connect() as conn:
            res = conn.execute(text("""
                SELECT roe, pe_ttm, pe_percentile, net_profit_yoy, revenue_yoy, label
                FROM stock_fundamentals WHERE code = :code
            """), {"code": code}).fetchone()

            if res:
                financials["roe"] = round(float(res[0]), 2) if res[0] else None
                financials["pe_ttm"] = round(float(res[1]), 2) if res[1] else None
                pe_pct = float(res[2]) if res[2] else None
                if pe_pct is not None:
                    if pe_pct <= 30:
                        financials["pe_percentile"] = "低估"
                    elif pe_pct <= 70:
                        financials["pe_percentile"] = "中位"
                    else:
                        financials["pe_percentile"] = "高估"
                financials["net_profit_yoy"] = round(float(res[3]), 2) if res[3] else None
                financials["revenue_yoy"] = round(float(res[4]), 2) if res[4] else None
                financials["label"] = str(res[5]) if res[5] else "未知"
    except Exception as e:
        logger.warning(f"Financial data fetch for {code}: {e}")

    # 尝试获取市值
    try:
        from core.data import get_cached_data
        snapshot = get_cached_data('market_snapshot', 300)
        if snapshot is not None and hasattr(snapshot, 'loc'):
            match = snapshot[snapshot['code'] == code]
            if not match.empty:
                mkt_cap = match.iloc[0].get('mkt_cap')
                if mkt_cap and float(mkt_cap) > 0:
                    financials["mkt_cap_yi"] = round(float(mkt_cap) / 1e8, 1)
    except Exception:
        pass

    return financials


def _compute_risk_assessment(df, code: str, financials: dict) -> dict:
    """计算风险评估"""
    risk = {
        "volatility": "未知", "liquidity": "未知",
        "sector_risk": "未知", "market_regime": "UNKNOWN",
        "warnings": [], "risk_level": "中"
    }

    try:
        # 波动性评估 (20日ATR占价格比)
        if len(df) >= 20:
            recent = df.tail(20)
            high = recent['最高'].astype(float)
            low = recent['最低'].astype(float)
            close_prev = recent['收盘'].astype(float).shift(1)
            tr = pd.concat([
                high - low,
                (high - close_prev).abs(),
                (low - close_prev).abs()
            ], axis=1).max(axis=1)
            atr = tr.mean()
            close = float(df.iloc[-1]['收盘'])
            atr_pct = (atr / close) * 100 if close > 0 else 0

            if atr_pct > 5:
                risk["volatility"] = "高"
            elif atr_pct > 3:
                risk["volatility"] = "中等"
            else:
                risk["volatility"] = "低"

        # 流动性评估 (平均成交量)
        if len(df) >= 10:
            avg_vol = df.tail(10)['成交量'].astype(float).mean()
            if avg_vol > 500000:
                risk["liquidity"] = "充裕"
            elif avg_vol > 100000:
                risk["liquidity"] = "良好"
            else:
                risk["liquidity"] = "偏弱"
                risk["warnings"].append("近10日平均成交量偏低，流动性风险")

        # 大盘状态
        try:
            from core.data import get_market_regime
            regime = get_market_regime()
            risk["market_regime"] = regime.get("status", "UNKNOWN")
            if regime.get("status") == "CRITICAL":
                risk["warnings"].append("大盘处于严格防守模式，系统性风险较高")
        except Exception:
            pass

        # 板块趋势风险
        try:
            from core.data import get_sector_trends
            sector_trends = get_sector_trends()
            engine = get_db_engine()
            with engine.connect() as conn:
                res = conn.execute(text("SELECT industry FROM stock_basic WHERE code = :code"), {"code": code}).fetchone()
                if res and res[0]:
                    industry = res[0]
                    sector_data = sector_trends.get(industry)
                    if sector_data:
                        trend = sector_data.get("trend", "")
                        if trend == "DOWN":
                            risk["sector_risk"] = "高"
                            risk["warnings"].append(f"所属板块{industry}当日下跌，板块拖累风险")
                        elif trend == "FLAT":
                            risk["sector_risk"] = "中"
                        else:
                            risk["sector_risk"] = "低"
        except Exception:
            pass

        # 财务风险提示
        if financials.get("pe_ttm") and financials["pe_ttm"] > 100:
            risk["warnings"].append(f"动态PE {financials['pe_ttm']}倍，估值偏高")
        if financials.get("net_profit_yoy") and financials["net_profit_yoy"] < -20:
            risk["warnings"].append(f"净利润同比下降{abs(financials['net_profit_yoy'])}%，盈利恶化")

        # 技术面风险
        if len(df) >= 5:
            pct_5d = ((float(df.iloc[-1]['收盘']) - float(df.iloc[-5]['收盘'])) / float(df.iloc[-5]['收盘'])) * 100
            if pct_5d > 20:
                risk["warnings"].append(f"近5日涨幅{pct_5d:.1f}%，短期追高风险")

        rsi = float(df.iloc[-1].get('RSI', 50))
        if rsi > 80:
            risk["warnings"].append(f"RSI {rsi:.1f} 进入超买区间")
        elif rsi < 30:
            risk["warnings"].append(f"RSI {rsi:.1f} 进入超卖区间")

        # 综合风险等级
        warning_count = len(risk["warnings"])
        if warning_count >= 3 or risk["market_regime"] == "CRITICAL":
            risk["risk_level"] = "高"
        elif warning_count >= 1:
            risk["risk_level"] = "中"
        else:
            risk["risk_level"] = "低"

    except Exception as e:
        logger.warning(f"Risk assessment error for {code}: {e}")

    return risk


def _generate_ai_suggestion(df, stock_info: dict, risk: dict) -> dict:
    """基于规则引擎生成 AI 操作建议"""
    suggestion = {
        "action": "HOLD",
        "confidence": 0.5,
        "reasoning": [],
        "action_label": "📊 等待 — 数据不足"
    }

    if not stock_info.get("is_paper_trade") or len(df) < 20:
        suggestion["reasoning"] = ["该股不在拟合实盘持仓中或数据不足，暂无操作建议"]
        return suggestion

    close = float(df.iloc[-1]['收盘'])
    buy_price = stock_info.get("buy_price", close)
    stop_price = stock_info.get("stop_price", buy_price * 0.91)
    tp_price = stock_info.get("take_profit_price", buy_price * 1.15)
    rsi = float(df.iloc[-1].get('RSI', 50))
    macd_hist = float(df.iloc[-1].get('MACD_HIST', 0))
    ema5 = float(df.iloc[-1].get('EMA5', close))
    ema20 = float(df.iloc[-1].get('EMA20', close))
    ema60 = float(df.iloc[-1].get('EMA60', close))

    # 计算关键距离
    stop_buffer = ((close - stop_price) / close) * 100 if close > 0 else 0
    tp_distance = ((tp_price - close) / close) * 100 if close > 0 else 0
    pl_pct = ((close - buy_price) / buy_price) * 100 if buy_price > 0 else 0

    reasons = []
    score = 0  # 正 = 看多, 负 = 看空

    # 1. 趋势判断
    if ema5 > ema20 > ema60:
        reasons.append("✅ 趋势：EMA5 > EMA20 > EMA60，三线多头排列")
        score += 2
    elif ema5 > ema20:
        reasons.append("✅ 趋势：短期均线多头，中期趋势待确认")
        score += 1
    elif ema5 < ema20 < ema60:
        reasons.append("⚠️ 趋势：三线空头排列，趋势走弱")
        score -= 2
    else:
        reasons.append("➖ 趋势：均线交织，方向不明")

    # 2. 动能判断
    if 50 <= rsi <= 70:
        reasons.append(f"✅ 动能：RSI {rsi:.1f} 处于强势区间")
        score += 1
    elif rsi > 80:
        reasons.append(f"⚠️ 动能：RSI {rsi:.1f} 超买，注意回调风险")
        score -= 1
    elif rsi < 30:
        reasons.append(f"⚠️ 动能：RSI {rsi:.1f} 超卖，可能反弹")
        score += 1
    else:
        reasons.append(f"➖ 动能：RSI {rsi:.1f}，动能中性")

    # 3. MACD
    macd_prev = float(df.iloc[-2].get('MACD_HIST', 0)) if len(df) > 1 else 0
    if macd_hist > 0 and macd_prev <= 0:
        reasons.append("✅ MACD：红柱翻多，金叉确认")
        score += 2
    elif macd_hist > 0 and macd_hist > macd_prev:
        reasons.append("✅ MACD：红柱放大，多头加速")
        score += 1
    elif macd_hist > 0 and macd_hist < macd_prev:
        reasons.append("⚠️ MACD：红柱缩短，多头动能减弱")
    elif macd_hist < 0 and macd_prev >= 0:
        reasons.append("⚠️ MACD：绿柱翻空，死叉信号")
        score -= 2
    elif macd_hist < 0:
        reasons.append("⚠️ MACD：绿柱运行中，空头氛围")
        score -= 1

    # 4. 风控距离
    reasons.append(f"📏 风控：距止损 {stop_buffer:.1f}%，距止盈 {tp_distance:.1f}%")
    if stop_buffer < 2:
        reasons.append("🚨 警告：距止损位极近，建议立即关注")
        score -= 3
    elif stop_buffer < 5:
        reasons.append("⚠️ 注意：距止损位较近")
        score -= 1

    if tp_distance < 2 and tp_distance > 0:
        reasons.append("🎯 即将触达止盈目标")
        score += 1
    elif tp_distance <= 0:
        reasons.append("🎯 已超越止盈目标，考虑分批减仓")
        score -= 1

    # 5. 大盘环境
    regime = risk.get("market_regime", "UNKNOWN")
    regime_labels = {"OFFENSIVE": "🚀 进攻模式", "DEFENSIVE": "⚠️ 防守观望", "CRITICAL": "🛡️ 严格防守"}
    reasons.append(f"🌍 大盘：{regime_labels.get(regime, '未知')}")
    if regime == "CRITICAL":
        score -= 2
    elif regime == "OFFENSIVE":
        score += 1

    # 6. 持仓盈亏
    reasons.append(f"💰 当前盈亏：{'+' if pl_pct >= 0 else ''}{pl_pct:.2f}%")

    # 最终决策
    action_map = {
        "CLOSE": ("🔴 平仓", "text-rose-600"),
        "REDUCE": ("🟡 减仓", "text-amber-600"),
        "HOLD": ("📊 等待", "text-slate-600"),
        "ADD": ("🟢 加仓", "text-emerald-600"),
    }

    if stop_buffer < 2 or regime == "CRITICAL":
        action = "CLOSE"
        label_suffix = "风控触发，建议平仓保护本金"
    elif score <= -3:
        action = "CLOSE"
        label_suffix = "多项指标转空，建议及时止盈止损"
    elif score <= -1 or rsi > 80 or tp_distance <= 0:
        action = "REDUCE"
        label_suffix = "动能减弱或目标达成，建议分批减仓"
    elif score >= 3 and stop_buffer > 10:
        action = "ADD"
        label_suffix = "多头共振+安全边际充足，可考虑加仓"
    elif score >= 2 and rsi < 60 and macd_hist > 0 and macd_prev <= 0:
        action = "ADD"
        label_suffix = "MACD金叉+趋势多头，可把握加仓机会"
    else:
        action = "HOLD"
        label_suffix = "趋势健康但信号不足，保持仓位静待确认"

    confidence = min(max(abs(score) / 6.0, 0.3), 0.95)
    emoji, _ = action_map.get(action, ("📊 等待", ""))

    suggestion["action"] = action
    suggestion["confidence"] = round(confidence, 2)
    suggestion["reasoning"] = reasons
    suggestion["action_label"] = f"{emoji} — {label_suffix}"

    return suggestion


@router.get("/full-analysis")
def get_stock_full_analysis(code: str):
    """
    全量个股分析接口 — 返回K线、信号、概念板块、财务画像、风险评估、AI操作建议
    用于拟合实盘中的全屏股票详情页
    """
    import pandas as pd

    if not validate_stock_code(code):
        raise HTTPException(status_code=400, detail="Invalid stock code format")

    try:
        # 1. K线数据 + 技术指标
        df = fetch_stock_data_with_indicators(code)
        if df.empty:
            raise HTTPException(status_code=404, detail="未找到该股票的历史数据")

        # Ensure df has strictly unique ascending chronological dates
        if '日期' in df.columns:
            df = df.drop_duplicates(subset=['日期']).sort_values('日期').reset_index(drop=True)

        # 200 日 K线
        kline_df = df.tail(200).copy()
        kline_df['time'] = kline_df['日期'].astype(str)
        kline_records = []
        for _, row in kline_df.iterrows():
            kline_records.append({
                "time": row['time'],
                "open": float(row['开盘']),
                "high": float(row['最高']),
                "low": float(row['最低']),
                "close": float(row['收盘']),
                "value": float(row['成交量']),
                "EMA5": float(row.get('EMA5', 0)),
                "EMA20": float(row.get('EMA20', 0)),
                "EMA60": float(row.get('EMA60', 0)),
                "RSI": float(row.get('RSI', 0)),
                "MACD": float(row.get('MACD_HIST', 0))
            })

        # 2. 买卖信号
        signals = {}
        try:
            signals = get_signal_details(df, strategy_type="squeeze")
            if "trailing_stops" in signals:
                ts_dict = {}
                for ts in signals["trailing_stops"]:
                    t = ts["time"]
                    val = float(ts["value"])
                    if t not in ts_dict or val < ts_dict[t]:
                        ts_dict[t] = val
                signals["trailing_stops"] = [{"time": t, "value": v} for t, v in sorted(ts_dict.items())]
        except Exception as e:
            logger.warning(f"Signal generation for {code}: {e}")

        # 3. 基础信息 + 拟合实盘数据
        engine = get_db_engine()
        from core.strategy import check_strategy, calculate_historical_win_rate

        is_paper_trade = False
        buy_price = 0.0
        stop_price = 0.0
        take_profit_price = 0.0
        paper_remark = ""
        hold_days = 0
        pl_pct = 0.0

        with engine.connect() as conn:
            basic_res = conn.execute(text("SELECT name, industry FROM stock_basic WHERE code = :code"), {"code": code}).fetchone()
            name = basic_res[0] if basic_res else "未知"
            industry = basic_res[1] if basic_res else "未知"

            paper_res = conn.execute(text("""
                SELECT entry_price, high_since_entry, status, remark, entry_date, current_price
                FROM paper_trading
                WHERE code = :code AND status = 'OPEN'
                LIMIT 1
            """), {"code": code}).fetchone()

            if paper_res:
                entry_price = float(paper_res[0])
                high_since_entry = float(paper_res[1]) if paper_res[1] else entry_price
                # NaN 防御
                import math
                if math.isnan(high_since_entry):
                    high_since_entry = entry_price
                is_paper_trade = True
                buy_price = round(entry_price, 2)
                stop_price = round(max(entry_price * FIXED_STOP_LOSS_RATIO, high_since_entry * TRAILING_STOP_RATIO), 2)
                take_profit_price = round(entry_price * TAKE_PROFIT_RATIO, 2)
                paper_remark = str(paper_res[3] or "")
                if paper_res[4]:
                    entry_date = paper_res[4]
                    if isinstance(entry_date, str):
                        entry_date = datetime.strptime(entry_date, "%Y-%m-%d").date()
                    hold_days = (datetime.now().date() - entry_date).days
                curr_price = float(paper_res[5]) if paper_res[5] else float(df.iloc[-1]['收盘'])
                pl_pct = round(((curr_price - entry_price) / entry_price) * 100, 2)

        close = float(df.iloc[-1]['收盘'])
        _, stats = check_strategy(df)
        bt = calculate_historical_win_rate(df)

        stock_info = {
            "代码": code,
            "名称": name,
            "行业": industry,
            "现价": round(close, 2),
            "涨幅%": round(float((df.iloc[-1]['收盘'] - df.iloc[-2]['收盘']) / df.iloc[-2]['收盘'] * 100), 2) if len(df) > 1 else 0.0,
            "Score": round(float(stats.get('Score', 50.0)), 1),
            "RSI": round(float(df.iloc[-1].get('RSI', 50.0)), 1),
            "历史胜率": f"{bt['win_rate']}%",
            "is_paper_trade": is_paper_trade,
            "buy_price": buy_price,
            "stop_price": stop_price,
            "take_profit_price": take_profit_price,
            "hold_days": hold_days,
            "pl_pct": pl_pct,
            "paper_remark": paper_remark,
        }

        # 4. 概念板块 (异步友好 + 24小时缓存)
        concepts = _fetch_stock_concepts(code)

        # 5. 财务画像
        financials = _fetch_financials(code)

        # 6. 风险评估
        risk_assessment = _compute_risk_assessment(df, code, financials)

        # 7. AI 操作建议
        ai_suggestion = _generate_ai_suggestion(df, stock_info, risk_assessment)

        return {
            "code": code,
            "kline": kline_records,
            "signals": signals,
            "stock_info": stock_info,
            "concepts": concepts,
            "financials": financials,
            "risk_assessment": risk_assessment,
            "ai_suggestion": ai_suggestion,
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Full analysis error for {code}: {e}")
        raise HTTPException(status_code=500, detail=f"分析失败: {str(e)}")


@router.get("/{code}/signals")
def get_stock_signals(
    code: str,
    strategy: str = "squeeze",
    stop_loss_pct: float = -8.0,
    take_profit_pct: float = 5.0,
    max_hold_days: int = 5,
):
    """
    获取个股历史买卖信号明细，回测可视化用

    Args:
        code: Stock code
        strategy: Strategy type (squeeze/pine/consensus)
        stop_loss_pct: Stop loss percentage (negative)
        take_profit_pct: Take profit percentage
        max_hold_days: Max holding days
    """
    if not validate_stock_code(code):
        raise HTTPException(status_code=400, detail="Invalid stock code format")

    df = fetch_stock_data_with_indicators(code)
    if df.empty:
        return {"buy_signals": [], "sell_signals": []}

    # Calculate additional Pine indicators if needed
    if strategy in ["pine", "both"]:
        df = calculate_pine_indicators(df)

    return get_signal_details(
        df,
        strategy_type=strategy,
        stop_loss_pct=stop_loss_pct,
        take_profit_pct=take_profit_pct,
        max_hold_days=max_hold_days,
    )


# In-memory cache for stock search (lazy initialized)
_stocks_cache = None

def _get_stocks_cache():
    global _stocks_cache
    if _stocks_cache is not None:
        return _stocks_cache
        
    engine = get_db_engine()
    if not engine:
        return []
        
    try:
        import pypinyin
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT code, name, industry FROM stock_basic")).fetchall()
            cache = []
            for r in rows:
                code, name, industry = r
                ind = industry or "未知"
                # Extract initials
                initials = ''.join([x[0] for x in pypinyin.lazy_pinyin(name) if x]).lower()
                cache.append({
                    "code": code,
                    "name": name,
                    "industry": ind,
                    "initials": initials
                })
            _stocks_cache = cache
            return _stocks_cache
    except Exception as e:
        logger.error(f"Error building stock search cache: {e}")
        return []


@router.get("/search")
def search_stocks(query: str):
    """
    Fuzzy search stock by code, name, or pinyin initials
    """
    if not query:
        return []
        
    q = query.strip().lower()
    if not q:
        return []
        
    cache = _get_stocks_cache()
    if not cache:
        # Fallback to direct DB query if cache is empty or fails
        try:
            engine = get_db_engine()
            with engine.connect() as conn:
                stmt = text("""
                    SELECT code, name, industry 
                    FROM stock_basic 
                    WHERE code LIKE :q_code OR name LIKE :q_name OR name LIKE :q_name_fuzzy
                    LIMIT 15
                """)
                res = conn.execute(stmt, {
                    "q_code": f"{query}%",
                    "q_name": f"%{query}%",
                    "q_name_fuzzy": f"{query}%"
                }).fetchall()
                return [{
                    "code": r[0],
                    "name": r[1],
                    "industry": r[2] or "未知"
                } for r in res]
        except Exception as e:
            logger.error(f"Error fallback searching stocks: {e}")
            return []

    # In-memory fast search with initials, name, and code matching
    matches = []
    for item in cache:
        code = item["code"]
        name = item["name"]
        initials = item["initials"]
        
        # Matches:
        # 1. code starts with / contains query
        # 2. name contains query (case-insensitive)
        # 3. initials contains query (pinyin abbreviation)
        if q in code or q in name.lower() or q in initials:
            matches.append({
                "code": code,
                "name": name,
                "industry": item["industry"]
            })
            if len(matches) >= 15:
                break
                
    return matches


