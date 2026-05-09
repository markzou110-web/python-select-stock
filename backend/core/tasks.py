from .celery_app import celery_app
from .logging_config import logger
from .notifier import notifier
from .db import get_db_engine
from .data import get_market_snapshot
from .indicators import calculate_indicators
from core.strategy import evaluate_exit_signals
import pandas as pd
from datetime import datetime
from sqlalchemy import text

@celery_app.task(name="tasks.check_realtime_alerts")
def check_realtime_alerts():
    """
    实时监控任务：
    1. 获取持仓列表
    2. 获取实时行情
    3. 评估卖出信号
    4. 触发手机推送
    """
    now = datetime.now()
    # 仅在 A 股交易时间段运行 (简化判断)
    is_market_open = (9 <= now.hour <= 15) and (now.weekday() < 5)
    if not is_market_open:
        logger.debug("Market is closed. Skipping real-time check.")
        return "Market closed"

    engine = get_db_engine()
    try:
        # 1. 获取所有 OPEN 持仓
        df_paper = pd.read_sql("SELECT * FROM paper_trading WHERE status = 'OPEN'", engine)
        if df_paper.empty:
            return "No open positions"

        # 2. 获取全市场快照（为了拿到最新价）
        snapshot = get_market_snapshot()
        if snapshot.empty:
            return "Failed to fetch snapshot"
            
        snapshot_map = snapshot.set_index('code')['price'].to_dict()

        codes = df_paper['code'].unique().tolist()
        alerts_triggered = []

        for _, row in df_paper.iterrows():
            code = row['code']
            name = row['name']
            entry_price = float(row['entry_price'])
            high_since_entry = float(row.get('high_since_entry') or entry_price)
            
            curr_price = snapshot_map.get(code)
            if not curr_price: continue

            # 3. 拉取最近 K 线计算技术指标 (EMA, VolMA 等)
            # 这里复用 alert.py 的逻辑，但为了性能只取少量数据
            query = text("""
                SELECT date as "日期", close as "收盘", open as "开盘", 
                       high as "最高", low as "最低", vol as "成交量"
                FROM daily_k
                WHERE code = :code
                ORDER BY date DESC LIMIT 40
            """)
            with engine.connect() as conn:
                df_hist = pd.read_sql(query, conn, params={"code": code})
                df_hist = df_hist.sort_values("日期")
            
            if len(df_hist) < 20: continue

            # 注入实时价
            df_labeled = calculate_indicators(df_hist, current_price=curr_price)
            
            # 4. 评估信号
            signals = evaluate_exit_signals(df_labeled, entry_price, high_since_entry)
            
            if signals:
                # 过滤出需要推送的信号 (warning 和 critical)
                important_signals = [s for s in signals if s['level'] in ['warning', 'critical']]
                if important_signals:
                    sig = important_signals[0]
                    alerts_triggered.append(f"{name}({code}): {sig['reason']} -> {sig['suggestion']}")

        # 5. 发送推送
        if alerts_triggered:
            title = "Alpha Vision 实时风险预警"
            body = "\n".join(alerts_triggered)
            # 这是一个异步操作，但不等待结果
            import asyncio
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
            
            loop.run_until_complete(notifier.send(title, body))
            logger.info(f"Sent {len(alerts_triggered)} alerts via push channels.")

        return f"Processed {len(df_paper)} positions, triggered {len(alerts_triggered)} alerts"

    except Exception as e:
        logger.error(f"Error in check_realtime_alerts task: {e}")
        return str(e)
