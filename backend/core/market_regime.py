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
        regime_str: "bull" / "bear" / "volatile"
        strategy_type: "squeeze" / "pine" / "both" / "consensus"
    
    Returns:
        推荐参数字典
    """
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


def _fetch_index_data() -> Optional[pd.DataFrame]:
    """从数据库获取上证指数(000001)的K线数据"""
    try:
        from core.db import get_db_engine, load_from_db
        from datetime import datetime, timedelta
        
        engine = get_db_engine()
        if not engine:
            return None
        
        start_date = (datetime.now() - timedelta(days=200)).strftime("%Y-%m-%d")
        df = load_from_db("000001", start_date, engine)
        
        if df.empty:
            # 尝试在线获取
            try:
                import akshare as ak
                start_fetch = (datetime.now() - timedelta(days=200)).strftime("%Y%m%d")
                df = ak.stock_zh_a_hist(symbol="000001", period="daily", start_date=start_fetch, adjust="qfq")
            except Exception:
                pass
        
        return df if not df.empty else None
    except Exception as e:
        logger.error(f"Failed to fetch index data: {e}")
        return None
