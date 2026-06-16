from copy import deepcopy
from typing import Any, Dict, List


_STRATEGIES: Dict[str, Dict[str, Any]] = {
    "squeeze": {
        "name": "均线粘合突破",
        "description": "EMA 粘合后结合量能、RSI、MACD 和布林收缩寻找突破。",
        "supports_scan": True,
        "supports_backtest": True,
        "required_indicators": ["EMA5", "EMA10", "EMA20", "EMA60", "RSI", "MACD_DIF", "BB_Width", "Vol_MA20"],
        "explain_fields": ["signal_reason", "threshold", "vol_multiplier", "rsi_min"],
        "default_params": {"threshold": 0.12, "vol_multiplier": 1.5, "rsi_min": 55, "use_macd_filter": True},
    },
    "pine": {
        "name": "Pine 多指标共振",
        "description": "Range Filter、QQE、SuperTrend、核回归和 HalfTrend 多信号共振。",
        "supports_scan": True,
        "supports_backtest": True,
        "required_indicators": ["RF_Upward", "QQE_Long", "ST_Signal", "RQK_Up", "HalfTrend_Up"],
        "explain_fields": ["signal_reason", "pine_min_signals"],
        "default_params": {"pine_min_signals": 3},
    },
    "consensus": {
        "name": "Azul 共识",
        "description": "趋势、量能与技术结构共同确认的共识型信号。",
        "supports_scan": True,
        "supports_backtest": True,
        "required_indicators": ["MA20", "MA60", "Trend_Quality", "RSI", "MACD_DIF"],
        "explain_fields": ["signal_reason"],
        "default_params": {},
    },
    "tv_zp": {
        "name": "TV ZP 趋势",
        "description": "基于 TradingView ZP 趋势信号的方向策略。",
        "supports_scan": True,
        "supports_backtest": True,
        "required_indicators": ["RF_Upward", "RF_Downward", "ST_Signal"],
        "explain_fields": ["signal_reason"],
        "default_params": {},
    },
    "tv_dual": {
        "name": "TV 双信号",
        "description": "组合 TV 趋势信号，任一有效即可进入候选。",
        "supports_scan": True,
        "supports_backtest": False,
        "required_indicators": ["RF_Upward", "ST_Signal"],
        "explain_fields": ["signal", "tv_match"],
        "default_params": {"pine_min_signals": 3},
    },
    "tv_dual_strict": {
        "name": "TV 双信号严格共振",
        "description": "仅保留双信号共同确认的严格候选。",
        "supports_scan": True,
        "supports_backtest": False,
        "required_indicators": ["RF_Upward", "ST_Signal"],
        "explain_fields": ["signal", "tv_match", "trade_blockers"],
        "default_params": {"pine_min_signals": 3},
    },
    "sector_watch": {
        "name": "板块观察",
        "description": "只进入观察池的板块强度候选，不作为可执行交易信号。",
        "supports_scan": True,
        "supports_backtest": False,
        "required_indicators": ["sector_phase", "sector_rank", "leadership_score"],
        "explain_fields": ["sector_watch_reason", "sector_phase"],
        "default_params": {},
    },
}


def list_strategies() -> List[Dict[str, Any]]:
    return [{"key": key, **deepcopy(value)} for key, value in _STRATEGIES.items()]


def get_strategy(strategy_type: str) -> Dict[str, Any] | None:
    item = _STRATEGIES.get(str(strategy_type or ""))
    return {"key": strategy_type, **deepcopy(item)} if item else None


def supported_backtest_strategies() -> set[str]:
    return {key for key, value in _STRATEGIES.items() if value["supports_backtest"]}
