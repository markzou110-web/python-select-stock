"""
市场状态判断模块
根据大盘指数判断当前处于牛市/熊市/震荡市，并推荐自适应策略参数
"""
import pandas as pd
import numpy as np
from datetime import datetime
from enum import Enum
from typing import Dict, Any, Optional

from core.logging_config import logger
from sqlalchemy import text


class MarketRegime(Enum):
    BULL = "bull"
    BEAR = "bear"
    VOLATILE = "volatile"


# 每种市场状态下的推荐参数
REGIME_PARAMS = {
    MarketRegime.BULL: {
        "squeeze": {
            "threshold": 0.15, "vol_multiplier": 1.2, "rsi_min": 50,
            "use_bb_sqz": True, "sqz_lookback": 12, "stop_loss_pct": -6,
            "description": "牛市放宽条件，捕捉更多上涨机会"
        },
        "pine": {
            "pine_min_signals": 2, "stop_loss_pct": -6,
            "description": "牛市放宽共振要求，2/3 信号即可入场"
        },
        "consensus": {
            "vol_multiplier": 1.5, "stop_loss_pct": -6,
            "description": "牛市降低量能门槛，趋势跟随为主"
        }
    },
    MarketRegime.VOLATILE: {
        "squeeze": {
            "threshold": 0.12, "vol_multiplier": 1.5, "rsi_min": 55,
            "use_bb_sqz": True, "sqz_lookback": 10, "stop_loss_pct": -8,
            "description": "震荡市使用 SOP 标准参数，稳健为主"
        },
        "pine": {
            "pine_min_signals": 3, "stop_loss_pct": -8,
            "description": "震荡市要求 3/3 完全共振确认"
        },
        "consensus": {
            "vol_multiplier": 1.8, "stop_loss_pct": -8,
            "description": "震荡市维持严格量能确认"
        }
    },
    MarketRegime.BEAR: {
        "squeeze": {
            "threshold": 0.08, "vol_multiplier": 2.0, "rsi_min": 60,
            "use_bb_sqz": True, "sqz_lookback": 8, "stop_loss_pct": -5,
            "description": "熊市收紧条件，仅选极强标的，快进快出"
        },
        "pine": {
            "pine_min_signals": 3, "stop_loss_pct": -5,
            "description": "熊市严格要求全部共振，减小止损幅度"
        },
        "consensus": {
            "vol_multiplier": 2.2, "stop_loss_pct": -5,
            "description": "熊市提高放量要求，谨慎操作"
        }
    }
}

REGIME_LABELS = {
    MarketRegime.BULL: {"label": "🐂 牛市", "color": "rose", "desc": "指数位于MA60上方，均线多头排列，市场整体向好"},
    MarketRegime.BEAR: {"label": "🐻 熊市", "color": "emerald", "desc": "指数位于MA60下方，均线空头排列，市场偏弱"},
    MarketRegime.VOLATILE: {"label": "📊 震荡", "color": "amber", "desc": "指数在均线附近震荡，趋势不明朗"},
}


def detect_market_regime(index_df: Optional[pd.DataFrame] = None) -> Dict[str, Any]:
    """
    检测当前市场状态
    
    Args:
        index_df: 上证指数的日K数据 (需包含 收盘 列, 至少60行)
                  如果为 None，则尝试从数据库获取
    
    Returns:
        {
            "regime": "bull" | "bear" | "volatile",
            "label": "🐂 牛市",
            "color": "rose",
            "description": "...",
            "details": { "price": ..., "ma20": ..., "ma60": ..., "pct_above_ma60": ... }
        }
    """
    if index_df is None:
        index_df = _fetch_index_data()
    
    if index_df is None or len(index_df) < 60:
        return {
            "regime": MarketRegime.VOLATILE.value,
            "label": "📊 震荡",
            "color": "amber",
            "description": "数据不足，默认判定为震荡市",
            "details": {}
        }
    
    try:
        close = index_df['收盘'].astype(float)
        
        ma20 = close.rolling(20).mean()
        ma60 = close.rolling(60).mean()
        
        curr_price = float(close.iloc[-1])
        curr_ma20 = float(ma20.iloc[-1])
        curr_ma60 = float(ma60.iloc[-1])
        
        pct_above_ma60 = (curr_price - curr_ma60) / curr_ma60 * 100
        
        # 判断规则:
        # 牛市: 价格 > MA60, MA20 > MA60, 且价格高于MA60至少2%
        # 熊市: 价格 < MA60, MA20 < MA60
        # 震荡: 其他情况
        if curr_price > curr_ma60 and curr_ma20 > curr_ma60 and pct_above_ma60 > 2:
            regime = MarketRegime.BULL
        elif curr_price < curr_ma60 and curr_ma20 < curr_ma60:
            regime = MarketRegime.BEAR
        else:
            regime = MarketRegime.VOLATILE
        
        info = REGIME_LABELS[regime]
        
        return {
            "regime": regime.value,
            "label": info["label"],
            "color": info["color"],
            "description": info["desc"],
            "details": {
                "price": round(curr_price, 2),
                "ma20": round(curr_ma20, 2),
                "ma60": round(curr_ma60, 2),
                "pct_above_ma60": round(pct_above_ma60, 2),
            }
        }
    except Exception as e:
        logger.error(f"Market regime detection error: {e}")
        return {
            "regime": MarketRegime.VOLATILE.value,
            "label": "📊 震荡",
            "color": "amber",
            "description": f"判断异常: {str(e)[:50]}",
            "details": {}
        }


def get_adaptive_params(regime_str: str, strategy_type: str) -> Dict[str, Any]:
    """
    根据市场状态返回推荐参数

    Args:
        regime_str: "bull" / "bear" / "volatile"（MarketRegime 值），或
                    core.data 的 OFFENSIVE/CRITICAL/DEFENSIVE/UNKNOWN（自动映射）
        strategy_type: "squeeze" / "pine" / "both" / "consensus" / 其它(降级为 squeeze)

    Returns:
        推荐参数字典
    """
    regime_str = map_status_to_regime(regime_str)
    try:
        regime = MarketRegime(regime_str)
    except ValueError:
        regime = MarketRegime.VOLATILE

    # both 策略使用 squeeze 的参数
    st = "squeeze" if strategy_type == "both" else strategy_type
    if st not in ("squeeze", "pine", "consensus"):
        st = "squeeze"

    params = REGIME_PARAMS.get(regime, REGIME_PARAMS[MarketRegime.VOLATILE])
    return params.get(st, params["squeeze"])


def map_status_to_regime(status: str) -> str:
    """把 core.data.get_market_regime() 的 status 词表映射到 MarketRegime 值。

    core.data 用 OFFENSIVE/CRITICAL/DEFENSIVE/UNKNOWN（基于指数 vs EMA20），
    market_regime 用 bull/bear/volatile。映射规则：
      OFFENSIVE  → bull     （两大指数均在 EMA20 之上，强势）
      CRITICAL   → bear     （两大指数均在 EMA20 之下，弱势）
      DEFENSIVE  → volatile （一强一弱，震荡）
      其它/未知   → volatile （默认中性偏防守）
    传入已是 bull/bear/volatile 时原样返回（幂等）。
    """
    s = str(status or "").strip().upper()
    return {
        "OFFENSIVE": "bull",
        "CRITICAL": "bear",
        "DEFENSIVE": "volatile",
        "BULL": "bull",
        "BEAR": "bear",
        "VOLATILE": "volatile",
    }.get(s, "volatile")


def _fetch_index_data() -> Optional[pd.DataFrame]:
    """从数据库获取上证指数的K线数据。
    **平安银行**（个股，收盘 ~10），不是上证指数（收盘 ~3000+）。这导致市场状态
    判定（牛/熊/震荡）以及弱市止损收紧完全基于一只银行股，系统性失真。
    改为复用 data.get_index_hist("000001")：优先新浪在线接口（返回真正指数），
    在线失败时返回 None（detect_market_regime 会诚实降级为"震荡/数据不足"），
    绝不用个股冒充指数。
    """
    try:
        from core.data import get_index_hist
        df = get_index_hist("000001")
        if df is not None and not df.empty:
            return df
        # 在线失败时不再用个股 000001 冒充指数，直接返回 None
        return None
    except Exception as e:
        logger.error(f"Failed to fetch index data: {e}")
        return None


def to_daily_close_series(df: Optional[pd.DataFrame]) -> Optional[pd.Series]:
    """从指数日线 DataFrame 提取带日期索引的收盘序列（兼容新浪/腾讯/东财格式）。"""
    if df is None or df.empty:
        return None
    close_col = "close" if "close" in df.columns else ("收盘" if "收盘" in df.columns else None)
    if close_col is None:
        return None
    series = pd.Series(pd.to_numeric(df[close_col], errors="coerce").values).dropna()
    if "date" in df.columns:
        series.index = pd.to_datetime(df["date"].values[: len(series)])
    elif isinstance(df.index, pd.DatetimeIndex):
        series.index = df.index[: len(series)]
    else:
        return None
    series = series[~series.index.isna()].sort_index()
    return series if not series.empty else None


def detect_weekly_macd_top_divergence(daily_closes: Optional[pd.Series]) -> bool:
    """周线 MACD 顶背离：价格创近端新高而 MACD 柱峰值走低。

    《交易之路》长周期情绪度量："周线MACD顶背离，撒丫子就走"。
    实现：日线收盘重采样为周线（W-FRI），计算 MACD(12,26,9) 柱，
    取"最近的内部大顶"（±4周局部高点）与"最近8周的当前顶"比较：
    当前顶价创新高（>0.5%）而柱峰值走低（<90%）判定为顶背离。
    数据不足/索引非日期/任何异常 → False（fail-open，不误降级）。
    """
    try:
        if daily_closes is None or len(daily_closes) < 120:
            return False
        closes = pd.Series(daily_closes, dtype="float64").dropna()
        if not isinstance(closes.index, pd.DatetimeIndex):
            return False
        weekly = closes.resample("W-FRI").last().dropna().tail(60)
        if len(weekly) < 30:
            return False
        ema12 = weekly.ewm(span=12, adjust=False).mean()
        ema26 = weekly.ewm(span=26, adjust=False).mean()
        dif = ema12 - ema26
        dea = dif.ewm(span=9, adjust=False).mean()
        hist = (dif - dea) * 2
        price = weekly.values
        hist_values = hist.values
        n = len(price)
        # 内部大顶：±4周窗口内的局部最高点（需要右侧确认，排除序列末端）
        major_peaks = [
            i for i in range(4, n - 4)
            if price[i] >= max(price[i - 4:i + 5]) - 1e-9 and hist_values[i] > 0
        ]
        # 当前顶：最近8周内的最高收盘（允许出现在序列末端——“今天创新高”正是检测时机）
        p2 = int(np.argmax(price[max(0, n - 8):n])) + max(0, n - 8)
        if hist_values[p2] < 0:
            return False
        prior = [i for i in major_peaks if i <= p2 - 4]
        if not prior:
            return False
        p1 = prior[-1]
        return bool(price[p2] > price[p1] * 1.005 and hist_values[p2] < hist_values[p1] * 0.9)
    except Exception as e:
        logger.warning(f"weekly MACD divergence detection failed (fail-open): {e}")
        return False


# 月度季节性备注（本库 2022 以来中证500日度均值统计，样本仅约4年）。
# 只做信息提示，不进入任何闸门/评分——样本太短，可能由单一年份主导。
SEASONALITY_NOTES = {
    10: "历史强月（10-12月偏强，样本仅约4年，仅供参考）",
    11: "历史强月（10-12月偏强，样本仅约4年，仅供参考）",
    12: "历史强月（10-12月偏强，样本仅约4年，仅供参考）",
    7: "历史偏强月（样本仅约4年，仅供参考）",
    6: "历史最弱月（样本仅约4年，仅供参考），注意控制仓位节奏",
    1: "历史偏弱月（样本仅约4年，仅供参考）",
    2: "历史偏弱月（样本仅约4年，仅供参考）",
    4: "历史偏弱月（样本仅约4年，仅供参考）",
}


def get_seasonality_note(month: Optional[int] = None) -> Optional[str]:
    month = int(month) if month else datetime.now().month
    return SEASONALITY_NOTES.get(month)


# Elder NH-NL 默认回看窗口（52 周新高/新低）
NHNL_WINDOW_DAYS = 250
NHNL_MIN_BARS = 200  # 不足此数的新股不计入（避免次新股把每根K线都算成新低）


def compute_market_breadth_extremes(
    engine,
    window: int = NHNL_WINDOW_DAYS,
) -> Dict[str, Any]:
    """Elder 市场宽度指标：全市场创 N 日新高/新低家数 + 站上 MA50 占比。

    《以交易为生》：NH-NL 指数是股票市场最好的领先指标，与指数背离预示转折；
    站上 50 日均线的股票占比的极端值（>75% / <25%）后回落是中期顶部/底部信号。

    直接对 daily_k 全市场逐票滚动统计，输出以数据中最后一个交易日为基准
    （daily_k 未同步到今日时不写今日标签）。只做只读统计，不写库——
    落库由 db.record_breadth_extremes 负责。

    Args:
        engine: SQLAlchemy engine（PG 生产 / SQLite 测试均可）
        window: 新高新低回看交易日数（默认 250 ≈ 52 周）
    Returns:
        {bar_date, nh_count, nl_count, pct_above_ma50, total_count, window}；
        无数据时 total_count=0。
    """
    result: Dict[str, Any] = {
        "bar_date": None, "nh_count": 0, "nl_count": 0,
        "pct_above_ma50": None, "total_count": 0, "window": int(window),
    }
    if engine is None:
        return result
    try:
        df = pd.read_sql(
            text("SELECT code, date, high, low, close FROM daily_k WHERE date >= :start ORDER BY code, date"),
            engine,
            params={"start": (datetime.now() - pd.Timedelta(days=int(window * 2.2))).strftime("%Y-%m-%d")},
        )
    except Exception as exc:
        logger.warning(f"compute_market_breadth_extremes: daily_k query failed: {exc}")
        return result
    if df.empty:
        return result

    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date", "close"]).sort_values(["code", "date"])
    bar_date = df["date"].max()
    result["bar_date"] = bar_date.strftime("%Y-%m-%d")

    # 逐票滚动窗口（含当日）：当日 high 即窗口最大值 → 创新高；low 同理。
    # rolling(max) 含当日，故 high >= rolling_max 当且仅当当日创窗口新高。
    grouped = df.groupby("code", sort=False)
    high_max = grouped["high"].transform(lambda s: s.rolling(int(window), min_periods=NHNL_MIN_BARS).max())
    low_min = grouped["low"].transform(lambda s: s.rolling(int(window), min_periods=NHNL_MIN_BARS).min())
    ma50 = grouped["close"].transform(lambda s: s.rolling(50, min_periods=50).mean())
    df = df.assign(_nh=df["high"] >= high_max, _nl=df["low"] <= low_min, _ma50=ma50)

    tail = df[df["date"] == bar_date]
    result["nh_count"] = int(tail["_nh"].sum())
    result["nl_count"] = int(tail["_nl"].sum())
    valid_ma = tail["_ma50"].notna()  # 上市不足50日的新股不计入 MA50 占比
    result["pct_above_ma50"] = (
        round(float((tail.loc[valid_ma, "close"] > tail.loc[valid_ma, "_ma50"]).mean() * 100), 2)
        if bool(valid_ma.any()) else None
    )
    result["total_count"] = int(len(tail))
    logger.debug(
        f"NH-NL @{result['bar_date']}: nh={result['nh_count']} nl={result['nl_count']} "
        f"ma50={result['pct_above_ma50']}% over {result['total_count']} stocks"
    )
    return result


# ── 涨停情绪周期（借鉴 easy-stock 超短连板分析；数据源为 limit_up_events）──


def compute_limit_up_sentiment(engine, event_date: Optional[str] = None) -> Dict[str, Any]:
    """从 limit_up_events 聚合当日涨停情绪：涨停/炸板家数、最高连板、晋级率、炸板率。

    原始事件由 collect_limit_up_leadership 盘中逐分钟落库（东财涨停池 SEALED +
    炸板池 BROKEN），本函数只做只读 SQL 聚合，不写库——落库由
    db.record_limit_up_sentiment 负责（与 NH-NL 宽度同一条安全契约）。

    口径：
      - 炸板率 = 炸板家数 / (涨停家数 + 炸板家数)，>40% 为退潮特征（ZT_EBB_BROKEN_RATE_PCT）
      - 晋级率 = 今日 2 板及以上家数 / 上一交易日涨停家数；取表内最近的前一交易日，
        表内无前日数据（冷启动/停采）时为 None，不猜。
    """
    result: Dict[str, Any] = {
        "bar_date": None, "prev_bar_date": None,
        "sealed_count": 0, "broken_count": 0, "max_streak": 0,
        "streak_ge2_count": 0, "promotion_rate": None, "broken_rate": None,
    }
    if engine is None:
        return result
    try:
        df = pd.read_sql(text("""
            SELECT event_date, status, COUNT(*) AS cnt,
                   MAX(CASE WHEN status = 'SEALED' THEN limit_up_streak ELSE 0 END) AS max_streak,
                   SUM(CASE WHEN status = 'SEALED' AND limit_up_streak >= 2 THEN 1 ELSE 0 END) AS streak_ge2
            FROM limit_up_events
            GROUP BY event_date, status
        """), engine)
    except Exception as exc:
        logger.warning(f"compute_limit_up_sentiment: limit_up_events query failed: {exc}")
        return result
    if df.empty:
        return result

    df["event_date"] = pd.to_datetime(df["event_date"], errors="coerce")
    df = df.dropna(subset=["event_date"])
    if df.empty:
        return result

    dates = sorted(df["event_date"].unique())
    if event_date:
        target = pd.to_datetime(event_date, errors="coerce")
        candidates = [d for d in dates if d <= target]
        if not candidates:
            return result
        bar_date = max(candidates)
    else:
        bar_date = max(dates)
    prev_date = max((d for d in dates if d < bar_date), default=None)
    result["bar_date"] = pd.Timestamp(bar_date).strftime("%Y-%m-%d")
    if prev_date is not None:
        result["prev_bar_date"] = pd.Timestamp(prev_date).strftime("%Y-%m-%d")

    day = df[df["event_date"] == bar_date]
    sealed = int(day.loc[day["status"] == "SEALED", "cnt"].sum())
    broken = int(day.loc[day["status"] == "BROKEN", "cnt"].sum())
    result["sealed_count"] = sealed
    result["broken_count"] = broken
    result["max_streak"] = int(day["max_streak"].fillna(0).max())
    result["streak_ge2_count"] = int(day["streak_ge2"].fillna(0).sum())
    if sealed + broken > 0:
        result["broken_rate"] = round(broken / (sealed + broken) * 100, 1)
    if prev_date is not None:
        prev = df[df["event_date"] == prev_date]
        prev_sealed = int(prev.loc[prev["status"] == "SEALED", "cnt"].sum())
        if prev_sealed > 0 and sealed + broken > 0:
            result["promotion_rate"] = round(result["streak_ge2_count"] / prev_sealed * 100, 1)
    logger.debug(
        f"limit-up sentiment @{result['bar_date']}: sealed={sealed} broken={broken} "
        f"max_streak={result['max_streak']} promotion={result['promotion_rate']} broken_rate={result['broken_rate']}%"
    )
    return result


def limit_up_sentiment_ebb(stats: Dict[str, Any]) -> bool:
    """炸板率达到退潮阈值时返回 True（数据缺失时 fail-open 返回 False）。"""
    from core.risk_constants import ZT_EBB_BROKEN_RATE_PCT

    broken_rate = stats.get("broken_rate")
    return broken_rate is not None and float(broken_rate) >= ZT_EBB_BROKEN_RATE_PCT
