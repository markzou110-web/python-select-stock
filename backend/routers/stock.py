"""
Stock router - individual stock data endpoints.

Extracted from api.py.
"""
from fastapi import APIRouter, HTTPException
from sqlalchemy import text
from datetime import datetime, timedelta
import akshare as ak

from core.logging_config import logger
from core.db import get_db_engine, validate_stock_code, load_from_db, save_to_db
from core.indicators import calculate_indicators, calculate_pine_indicators
from core.strategy import get_signal_details, run_optimization_grid

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

        return {
            "code": code,
            "data": records,
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
