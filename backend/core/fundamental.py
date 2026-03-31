"""
Fundamental data module - fetches and processes financial fundamentals for stock scoring.

Uses AkShare to obtain ROE, PE, net profit growth, and revenue data.
"""
import pandas as pd
import numpy as np
from typing import Dict, Any, Optional, List
from datetime import datetime

from .logging_config import logger

try:
    import akshare as ak
except ImportError:
    ak = None


def fetch_stock_fundamentals(code: str) -> Optional[Dict[str, Any]]:
    """
    获取单只股票基本面数据

    Args:
        code: Stock code (6 digits)

    Returns:
        Dict with roe, pe_ttm, pe_percentile, net_profit_yoy, revenue_yoy
    """
    if ak is None:
        return None

    try:
        result: Dict[str, Any] = {
            "code": code,
            "roe": None,
            "pe_ttm": None,
            "pe_percentile": None,
            "net_profit_yoy": None,
            "revenue_yoy": None,
            "label": None,
            "updated_at": datetime.now().strftime("%Y-%m-%d"),
        }

        # 1. ROE - 从财务分析指标获取
        try:
            df_fin = ak.stock_financial_analysis_indicator(symbol=code)
            if df_fin is not None and not df_fin.empty:
                # 取最近一期数据
                latest = df_fin.iloc[0]
                roe_col = None
                for col in df_fin.columns:
                    if 'roe' in col.lower() or '净资产收益率' in col:
                        roe_col = col
                        break
                if roe_col:
                    val = latest[roe_col]
                    result["roe"] = _safe_float(val)
        except Exception as e:
            logger.debug(f"ROE fetch failed for {code}: {e}")

        # 2. PE + 净利润增速 - 从估值指标获取
        try:
            df_val = ak.stock_a_indicator_lg(symbol=code)
            if df_val is not None and not df_val.empty:
                latest = df_val.iloc[-1]

                # PE TTM
                for col in ['pe_ttm', 'pe', '市盈率']:
                    if col in df_val.columns:
                        result["pe_ttm"] = _safe_float(latest[col])
                        break

                # PE 5年分位数
                if result["pe_ttm"] and len(df_val) > 20:
                    pe_series = pd.to_numeric(df_val.get('pe_ttm', df_val.get('pe', pd.Series())), errors='coerce').dropna()
                    if len(pe_series) > 20:
                        result["pe_percentile"] = round(float((pe_series < result["pe_ttm"]).sum() / len(pe_series) * 100), 1)

                # 净利润同比
                for col in ['net_profit_yoy', '净利润同比增长率']:
                    if col in df_val.columns:
                        result["net_profit_yoy"] = _safe_float(latest[col])
                        break

                # 营收同比
                for col in ['revenue_yoy', '营业收入同比增长率']:
                    if col in df_val.columns:
                        result["revenue_yoy"] = _safe_float(latest[col])
                        break
        except Exception as e:
            logger.debug(f"Valuation fetch failed for {code}: {e}")

        # 3. 标签判断
        result["label"] = _classify_fundamental(result)

        return result

    except Exception as e:
        logger.warning(f"Fundamental fetch error for {code}: {e}")
        return None


def _safe_float(val) -> Optional[float]:
    """安全转换为浮点数"""
    try:
        v = float(val)
        return round(v, 2) if not np.isnan(v) else None
    except (ValueError, TypeError):
        return None


def _classify_fundamental(data: Dict[str, Any]) -> Optional[str]:
    """
    基本面分类标签

    戴维斯双击: ROE>15 + 净利润增速>20 + PE分位数<30
    成长型: 净利润增速>20
    价值型: PE分位数<30 + ROE>10
    """
    roe = data.get("roe")
    pe_pct = data.get("pe_percentile")
    profit_yoy = data.get("net_profit_yoy")

    if roe is None and profit_yoy is None:
        return None

    is_high_roe = roe is not None and roe > 15
    is_profit_surge = profit_yoy is not None and profit_yoy > 20
    is_low_pe = pe_pct is not None and pe_pct < 30

    if is_high_roe and is_profit_surge and is_low_pe:
        return "戴维斯双击"
    if is_profit_surge:
        return "成长型"
    if is_low_pe and (roe is None or roe > 10):
        return "价值型"
    return None


def batch_fetch_fundamentals(codes: List[str]) -> List[Dict[str, Any]]:
    """
    批量获取基本面数据

    Args:
        codes: List of stock codes

    Returns:
        List of fundamental data dicts
    """
    import time
    import random
    results = []
    for code in codes:
        time.sleep(random.uniform(0.3, 0.8))
        data = fetch_stock_fundamentals(code)
        if data:
            results.append(data)
    return results


def calculate_fundamental_score(fundamental: Optional[Dict[str, Any]]) -> float:
    """
    计算基本面加分 (0-40 分)

    Rules:
    - ROE > 15: +15
    - 净利润增速 > 20: +15 (净利润断层)
    - PE 分位数 < 30: +10 (低估)
    """
    if fundamental is None:
        return 0

    score = 0
    roe = fundamental.get("roe")
    profit_yoy = fundamental.get("net_profit_yoy")
    pe_pct = fundamental.get("pe_percentile")

    if roe is not None and roe > 15:
        score += 15
    if profit_yoy is not None and profit_yoy > 20:
        score += 15
    if pe_pct is not None and pe_pct < 30:
        score += 10

    return score
