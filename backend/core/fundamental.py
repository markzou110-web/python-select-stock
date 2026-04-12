import akshare as ak
import pandas as pd
from datetime import datetime
import time
from sqlalchemy import text
from .logging_config import logger
from .models import StockFundamental

def fetch_latest_fundamentals() -> pd.DataFrame:
    """
    抓取东方财富最新的个股业绩报表，提取 ROE 和 净利润同比增长。
    自动按当前日期倒推最近 4 个可能的财报季（3月31日, 6月30日, 9月30日, 12月31日），
    直到获取到足够多数目的财报数据。
    """
    # 生成过去 5 个可能的财报日期
    now = datetime.now()
    year = now.year
    quarters = [f"{year}0331", f"{year}0630", f"{year}0930", f"{year}1231", 
                f"{year-1}0331", f"{year-1}0630", f"{year-1}0930", f"{year-1}1231"]
    
    # 因为报告有滞后期，把当前日期之后的或者太近的去掉
    # 例如 2026年4月，20260331 的报告还没完全出完，我们尽量往回看
    valid_dates = []
    for q in quarters:
        q_date = datetime.strptime(q, "%Y%m%d")
        if (now - q_date).days > 30: # 留出一个月的发布期
            valid_dates.append(q)
            
    # 按时间倒序排列（最近的排前面）
    valid_dates.sort(reverse=True)
    
    best_df = pd.DataFrame()
    
    for date_str in valid_dates:
        logger.info(f"正在尝试拉取 {date_str} 财报数据...")
        try:
            df = ak.stock_yjbb_em(date=date_str)
            if df is not None and not df.empty and len(df) > 1000:
                logger.info(f"成功获取 {date_str} 的全市场业绩报表，共 {len(df)} 条记录。")
                best_df = df
                break
            else:
                logger.info(f"{date_str} 的数据条目数不足 ({len(df) if df is not None else 0})，尝试更早的季度...")
        except Exception as e:
            logger.warning(f"获取 {date_str} 业绩报表失败: {e}")
        
        time.sleep(1) # 请求间隔
        
    if best_df.empty:
        logger.error("无法获取任何历史季度的基本面数据。")
        return pd.DataFrame()
        
    # 重命名和筛选核心字段
    # 原始字段例：'股票代码', '净利润-同比增长', '净资产收益率', '营业总收入-同比增长'
    rename_map = {
        '股票代码': 'code',
        '净利润-同比增长': 'net_profit_yoy',
        '净资产收益率': 'roe',
        '营业总收入-同比增长': 'revenue_yoy'
    }
    
    # 过滤掉不存在的列，防止接口变化
    valid_cols = [c for c in rename_map.keys() if c in best_df.columns]
    if '股票代码' not in valid_cols:
        logger.error(f"基础数据缺少 '股票代码' 列！当前列: {best_df.columns.tolist()}")
        return pd.DataFrame()
        
    # 保留所需的列
    result_df = best_df[valid_cols].copy()
    result_df.rename(columns=rename_map, inplace=True)
    
    # 清理数据：把空值转换为 NaN 后来转为 0，或者保留 NaN
    for col in ['net_profit_yoy', 'roe', 'revenue_yoy']:
        if col in result_df.columns:
            # akshare 有些时候返回 '' 或者 '-'，强制转换为 float
            result_df[col] = pd.to_numeric(result_df[col], errors='coerce')
    
    # 填充缺失值为 0
    result_df.fillna(0, inplace=True)
    
    # 构建 label（例如：是否是高成长标的）
    def generate_label(row):
        labels = []
        roe = row.get('roe', 0)
        np_yoy = row.get('net_profit_yoy', 0)
        
        if pd.notna(roe) and roe > 15:
            labels.append("高ROE")
        if pd.notna(np_yoy) and np_yoy > 30:
            labels.append("极速成长")
        elif pd.notna(np_yoy) and np_yoy > 15:
            labels.append("稳定增长")
            
        return " | ".join(labels) if labels else ""
        
    result_df['label'] = result_df.apply(generate_label, axis=1)
    
    # 补齐未获得数据的字段（如PE分位暂时搁置）
    result_df['pe_ttm'] = 0.0
    result_df['pe_percentile'] = 0.0
    result_df['updated_at'] = datetime.now().date()
    
    return result_df

def sync_all_fundamentals(engine) -> dict:
    """
    抓取全市场基本面并入库。供 routers/sync.py 调用。
    """
    logger.info("开始拉取全市场基本面数据...")
    df = fetch_latest_fundamentals()
    
    if df.empty:
        return {"status": "error", "message": "获取基本面数据失败，请检查网络或代理配置"}
        
    logger.info(f"准备写入 {len(df)} 条基本面记录到 stock_fundamentals 表...")
    
    try:
        from sqlalchemy.orm import Session
        with Session(engine) as session:
            # 清空旧数据
            session.execute(text("DELETE FROM stock_fundamentals"))
            
            # 使用 pandas 的 to_sql 快速批量写入
            df.to_sql('stock_fundamentals', engine, if_exists='append', index=False)
            session.commit()
            
        logger.info("全市场基本面数据入库完成！")
        return {"status": "success", "count": len(df)}
    except Exception as e:
        logger.error(f"写入 stock_fundamentals 失败: {e}")
        return {"status": "error", "message": str(e)}

