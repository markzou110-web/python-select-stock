"""
Alpha Vision - Intraday Sentinel Module.
Handles automated intraday schedule checks, multi-strategy scanning, and push notifications.
"""
from typing import List, Optional, Dict, Any
from datetime import datetime
import time
import threading
import asyncio

from core.config import config
from core.logging_config import logger
from core.db import get_setting
from core.trading_calendar import is_a_share_intraday_session


def send_intraday_notification(stock_list: List[Dict[str, Any]]) -> Optional[str]:
    """
    Sends a push notification via the Notifier for the Sentinel.
    Only sends A/B grade stocks with actionable information.
    """
    if not stock_list:
        return None
    if not is_a_share_intraday_session():
        logger.info("Sentinel: Market is closed, skip Bark intraday notification.")
        return None

    # 仅推送 A 和 B 级标的
    ab_stocks = [s for s in stock_list if s.get('sop_grade') in ('A', 'B')]
    if not ab_stocks:
        logger.info("Sentinel: No A/B grade stocks to push.")
        return None

    # 获取大盘状态
    regime_emoji = {"OFFENSIVE": "🚀 进攻模式", "DEFENSIVE": "⚠️ 防守模式", "CRITICAL": "🛡️ 严格防守"}
    from core.data import get_market_regime
    regime = get_market_regime()
    regime_str = regime_emoji.get(regime.get('status', ''), '❓ 未知')

    now_str = datetime.now().strftime("%H:%M")
    title = f"Alpha Vision 哨兵 {now_str}"

    lines = [f"大盘：{regime_str}", ""]
    sector_emoji = {'LEAD': '🚀领涨', 'FOLLOW': '📈跟涨', 'FLAT': '➖横盘', 'DOWN': '📉下跌'}

    for s in ab_stocks[:5]:
        grade = s.get('sop_grade', '?')
        grade_icon = "🟢" if grade == "A" else "🔵"
        name = s.get('名称', s.get('name', ''))
        code = s.get('代码', s.get('code', ''))
        sector = s.get('行业', '')
        s_trend = sector_emoji.get(s.get('sector_trend', ''), '')
        s_pct = s.get('sector_pct', 0)
        entry = s.get('entry_price', 0)
        stop = s.get('stop_price', 0)
        win_rate = s.get('历史胜率', 'N/A')
        pf = s.get('回测统计', {}).get('profit_factor', 'N/A')

        lines.append(f"{grade_icon} {grade}级 {name} ({code})")
        if sector:
            lines.append(f"  板块: {sector} {s_trend}{'+' if s_pct >= 0 else ''}{s_pct}%")
        lines.append(f"  入场: {entry} | 止损: {stop}")
        lines.append(f"  胜率: {win_rate} | 盈亏比: {pf}")
        # 加分项
        bonuses = s.get('sop_bonuses', [])
        if bonuses:
            lines.append(f"  ⭐ {'、'.join(bonuses)}")
        lines.append("")

    total_a = sum(1 for s in stock_list if s.get('sop_grade') == 'A')
    total_b = sum(1 for s in stock_list if s.get('sop_grade') == 'B')
    lines.append(f"A级{total_a}只 | B级{total_b}只")
    lines.append("")

    # --- 附加上实盘股状态 ---
    from sqlalchemy import text
    from core.db import get_db_engine
    engine = get_db_engine()
    if engine:
        import pandas as pd
        from core.data import get_market_snapshot, get_index_hist
        from core.indicators import calculate_indicators
        from core.strategy import evaluate_exit_signals
        from core.risk_constants import ATR_STOP_MULTIPLIER, ATR_STOP_MIN_PCT, ATR_STOP_MAX_PCT
        from core.risk_engine import safe_float
        
        try:
            df_real = pd.read_sql("SELECT code, name, entry_price, high_since_entry FROM paper_trading WHERE status = 'OPEN' AND trade_mode = 'REAL'", engine)
            if not df_real.empty:
                lines.append("────────────────")
                lines.append("【🔴 实盘持仓状态】")
                
                snapshot = get_market_snapshot()
                snap_map = snapshot.set_index('code')['price'].to_dict() if not snapshot.empty else {}
                snap_high_map = snapshot.set_index('code')['high'].to_dict() if not snapshot.empty and 'high' in snapshot.columns else {}
                bench_df = get_index_hist('000001')
                
                for _, row in df_real.iterrows():
                    code = row['code']
                    name = row['name']
                    entry = safe_float(row['entry_price'])
                    high = safe_float(row.get('high_since_entry'), entry)
                        
                    curr = snap_map.get(code)
                    if not curr:
                        lines.append(f"• {name}: 暂无行情")
                        continue
                    high = max(high, safe_float(snap_high_map.get(code), curr), curr)
                        
                    pl_pct = (curr - entry) / entry * 100
                    status_line = f"• {name}: 现价 {curr} ({pl_pct:+.2f}%)"
                    
                    # 获取指标
                    query = text("SELECT date as \"日期\", close as \"收盘\", open as \"开盘\", high as \"最高\", low as \"最低\", vol as \"成交量\" FROM daily_k WHERE code = :code ORDER BY date DESC LIMIT 40")
                    with engine.connect() as conn:
                        df_hist = pd.read_sql(query, conn, params={'code': code})
                        df_hist = df_hist.sort_values('日期')
                    
                    suggestion = "持股观望"
                    if len(df_hist) >= 20:
                        df_labeled = calculate_indicators(df_hist, current_price=curr, bench_df=bench_df)
                        signals = evaluate_exit_signals(df_labeled, entry, high)
                        if signals:
                            # 取最高级别的报警
                            sig = signals[0]
                            icon = "🚨" if sig['level'] == 'critical' else "⚠️"
                            suggestion = f"{icon} {sig['reason']} -> {sig['suggestion']}"
                        else:
                            # 没报警，显示自适应止损位
                            atr = df_labeled.iloc[-1].get('ATR', 0)
                            if atr > 0:
                                atr_stop_pct = ((entry - atr * ATR_STOP_MULTIPLIER) - entry) / entry * 100
                                act_stop_pct = max(ATR_STOP_MAX_PCT, min(ATR_STOP_MIN_PCT, atr_stop_pct))
                                suggestion = f"✅ 安全区间 (防线 {act_stop_pct:.1f}%)"
                    
                    lines.append(status_line)
                    lines.append(f"   └ {suggestion}")
                lines.append("")
        except Exception as e:
            logger.error(f"Error appending real stock status: {e}")

    body = "\n".join(lines)
    logger.info(f"Notification: {body}")

    from core.notifier import notifier
    try:
        asyncio.run(notifier.send(title, body, channels=["bark"]))
    except Exception as e:
        logger.error(f"Push notification failed: {e}")

    return body


class IntradaySentinel:
    def __init__(self):
        self.last_top_5 = []
        self.thread = None
        self._stop = False
        self.schedule_times = ["14:20"]
        self.triggered_today = set()

    def update_schedule(self, times_str: Optional[str] = None):
        """实时更新调度时间点"""
        if times_str is None:
            times_str = get_setting("sentinel_schedule_times", "14:20")
        self.schedule_times = [t.strip() for t in times_str.split(",") if t.strip()]
        logger.info(f"Sentinel schedule updated to: {self.schedule_times}")

    def _load_schedule(self):
        # 保持兼容性调用 update_schedule
        self.update_schedule()

    def start(self):
        self._load_schedule()
        self.triggered_today.clear()
        self._stop = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while not self._stop:
            try:
                now = datetime.now()
                current_time = now.strftime("%H:%M")

                # Reset tracking every day
                if current_time == "00:00":
                    self.triggered_today.clear()

                if current_time in self.schedule_times and current_time not in self.triggered_today:
                    if not is_a_share_intraday_session(now):
                        logger.info(f"Sentinel skipped at {current_time}: non-trading session.")
                        self.triggered_today.add(current_time)
                        time.sleep(30)
                        continue

                    logger.info(f"Sentinel Triggered at {current_time}: Automated check...")
                    self.triggered_today.add(current_time)
                    try:
                        from routers.scan import run_market_scan_task
                        
                        # 1. 执行均线收敛策略 (Squeeze) 扫描
                        logger.info("Sentinel: Running Squeeze strategy scan...")
                        squeeze_results = run_market_scan_task(local_only=False, strategy_type="squeeze") or []
                        
                        # 2. 执行多指标共振策略 (Pine) 扫描
                        logger.info("Sentinel: Running Pine strategy scan...")
                        pine_results = run_market_scan_task(local_only=False, strategy_type="pine") or []
                        
                        # 3. 双策略合并去重逻辑
                        combined_dict = {}
                        
                        # 先放入 Squeeze 结果
                        for s in squeeze_results:
                            code = s.get('代码', s.get('code', ''))
                            if code:
                                s['strategy_type'] = 'squeeze'
                                combined_dict[code] = s
                                
                        # 再放入 Pine 结果，如存在，进行策略升级合并
                        for s in pine_results:
                            code = s.get('代码', s.get('code', ''))
                            if code:
                                if code in combined_dict:
                                    existing = combined_dict[code]
                                    existing['strategy_type'] = 'both'
                                    existing['reason'] = "双策略共振(Squeeze+Pine)"
                                    if 'sop_bonuses' in existing and 'sop_bonuses' in s:
                                        existing['sop_bonuses'] = list(set(existing['sop_bonuses'] + s['sop_bonuses']))
                                    if s.get('Score', 0) > existing.get('Score', 0):
                                        existing['Score'] = s['Score']
                                else:
                                    s['strategy_type'] = 'pine'
                                    combined_dict[code] = s
                        
                        results = list(combined_dict.values())
                        # 按评级排序（A级优先，B级次之，C级再次），其次按 Score 降序
                        grade_order = {'A': 0, 'B': 1, 'C': 2, 'D': 3, '?': 4}
                        results = sorted(results, key=lambda x: (grade_order.get(x.get('sop_grade', '?'), 4), -x.get('Score', 0)))
                        
                        if results:
                            self.last_top_5 = results[:5]
                            send_intraday_notification(self.last_top_5)

                        # --- 新增: 拟合实盘风控检查 ---
                        logger.info("Sentinel: Running Paper Trading Wind Control...")
                        from routers.paper_trade import run_wind_control
                        wc_res = run_wind_control()
                        if wc_res.get("closed_count", 0) > 0:
                            logger.info(f"Wind Control: Closed {wc_res['closed_count']} positions.")
                    except Exception as e:
                        logger.error(f"Sentinel Scan Error: {e}")

                if now.minute % 10 == 0 and now.second < 30:
                    self._load_schedule()

            except Exception as outer_e:
                logger.error(f"Sentinel Loop Error (will auto-recover): {outer_e}")

            time.sleep(30)


sentinel = IntradaySentinel()
