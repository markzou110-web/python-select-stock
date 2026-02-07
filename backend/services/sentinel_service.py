import os
import threading
import time
import requests
from datetime import datetime
from core.db import get_setting

# BARK_KEY should be passed or imported from config
BARK_KEY = os.getenv("BARK_KEY", "")

def send_intraday_notification(stock_list):
    """Sends a push notification via Bark for the 14:30 Sentinel."""
    if not stock_list: return
    
    names = [s.get('名称', s.get('name')) for s in stock_list]
    codes = [s.get('代码', s.get('code')) for s in stock_list]
    
    title = "Alpha Vision 哨兵提醒"
    body = f"【14:30 尾盘确认】\n发现 {len(names)} 只标的走势稳健：\n" + "、".join([f"{n}({c})" for n, c in zip(names, codes)])
    
    print(f"\n🔔 NOTIFICATION: {body}\n")

    if BARK_KEY and "YOUR_BARK_KEY" not in BARK_KEY:
        try:
            # Bark API: https://api.day.app/{key}/{title}/{body}
            url = f"https://api.day.app/{BARK_KEY}/{title}/{body}?icon=https://i.imgur.com/8p4jA4w.png"
            requests.get(url, timeout=5)
            print("✅ Bark push sent successfully.")
        except Exception as e:
            print(f"❌ Bark push failed: {e}")
    else:
        print("⚠️ Bark Key not configured. Skipping push.")
        
    return body

class IntradaySentinel:
    def __init__(self, run_market_scan_func):
        self.last_top_5 = []
        self.thread = None
        self._stop = False
        self.trigger_time = "14:30"
        self.run_market_scan = run_market_scan_func

    def start(self):
        # Load time from DB
        self.trigger_time = get_setting("sentinel_time", "14:20")
        self._stop = False
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self):
        while not self._stop:
            now = datetime.now()
            current_time = now.strftime("%H:%M")
            
            # Dynamic trigger time check
            if current_time == self.trigger_time:
                print(f"Sentinel Triggered at {self.trigger_time}: Automated check...")
                try:
                    # Run a full scan
                    results = self.run_market_scan(local_only=False)
                    if results:
                        self.last_top_5 = results[:5]
                        send_intraday_notification(self.last_top_5)
                except Exception as e:
                    error_msg = f"[{datetime.now()}] Sentinel Scan Error: {e}\n"
                    print(error_msg)
                    with open("sync_error.log", "a") as f:
                        f.write(error_msg)
                    
                    # Robustness: If limit reached, try with higher turnover and smaller range
                    if "待扫描股票过多" in str(e):
                        print("🔄 Sentinel: Attempting recovery with stricter filters...")
                        try:
                            # Try with higher turnover AND limiting to major indices if needed
                            results = self.run_market_scan(local_only=False, turnover_min=8.0, market_range="沪深300")
                            if not results:
                                results = self.run_market_scan(local_only=True) # Last resort
                            
                            if results:
                                self.last_top_5 = results[:5]
                                send_intraday_notification(self.last_top_5)
                        except Exception as e2:
                            print(f"❌ Sentinel: Recovery failed: {e2}")
                
                time.sleep(60) # Skip this minute
            
            # Periodically refresh settings (every 10 mins)
            if now.minute % 10 == 0 and now.second < 30:
                self.trigger_time = get_setting("sentinel_time", "14:30")
                
            time.sleep(30)
