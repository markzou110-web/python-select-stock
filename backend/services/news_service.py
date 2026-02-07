import time
from core.news import EastMoneyCrawler, save_news_items
from core.db import get_db_engine

# Variables that will be set by the main app during startup
theme_tracker = None

def background_news_sync():
    """定期抓取全球新闻并更新题材"""
    global theme_tracker
    crawler = EastMoneyCrawler()
    engine = get_db_engine()
    
    print("📰 Starting background news sync...")
    while True:
        try:
            # 1. 抓取全局新闻
            news_items = crawler.fetch_global_news()
            if news_items:
                print(f"📦 Fetched {len(news_items)} global news items. Saving to DB...")
                save_news_items(engine, news_items)
            
            # 2. 更新题材热度
            if theme_tracker:
                print("🔥 Updating themes based on new data...")
                theme_tracker.update_themes()
                
            # 3. 每 30 分钟同步一次
            time.sleep(1800)
        except Exception as e:
            print(f"❌ Background news sync error: {e}")
            time.sleep(300) # Error backoff
