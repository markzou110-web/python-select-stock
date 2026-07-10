"""
市场状态判断模块
根据大盘指数判断当前处于牛市/熊市/震荡市，并推荐自适应策略参数
"""
import pandas as pd
import numpy as np
from enum import Enum
from typing import Dict, Any, Optional

from core.logging_config import logger


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

    修复 BUG：原用 load_from_db("000001") 从 daily_k 取数据，但 000001 在库里是
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
