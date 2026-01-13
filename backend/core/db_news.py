from sqlalchemy import text
from .db import get_db_engine

def init_news_tables(engine=None):
    """初始化新闻舆情相关数据库表"""
    if engine is None:
        engine = get_db_engine()
    if not engine: return

    try:
        with engine.connect() as conn:
            # news_raw 表
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS news_raw (
                    id SERIAL PRIMARY KEY,
                    title TEXT NOT NULL,
                    content TEXT,
                    source VARCHAR(50),
                    url VARCHAR(500) UNIQUE,
                    publish_time TIMESTAMP,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """))

            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_news_raw_url ON news_raw(url);
                CREATE INDEX IF NOT EXISTS idx_news_raw_publish_time ON news_raw(publish_time);
            """))

            # news_sentiment 表
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS news_sentiment (
                    news_id INTEGER REFERENCES news_raw(id) ON DELETE CASCADE,
                    sentiment_score FLOAT,
                    sentiment_label VARCHAR(20),
                    ai_model VARCHAR(50),
                    analyzed_at TIMESTAMP DEFAULT NOW()
                );
            """))

            # themes 表
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS themes (
                    id SERIAL PRIMARY KEY,
                    name VARCHAR(100) NOT NULL UNIQUE,
                    keywords TEXT,
                    hotness FLOAT DEFAULT 0.0,
                    life_cycle_stage VARCHAR(50),
                    leader_stock VARCHAR(20),
                    updated_at TIMESTAMP DEFAULT NOW()
                );
            """))

            # news_stocks 表
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS news_stocks (
                    news_id INTEGER REFERENCES news_raw(id) ON DELETE CASCADE,
                    stock_code VARCHAR(20),
                    relevance FLOAT DEFAULT 1.0,
                    PRIMARY KEY (news_id, stock_code)
                );
            """))

            # risk_events 表
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS risk_events (
                    id SERIAL PRIMARY KEY,
                    stock_code VARCHAR(20),
                    risk_type VARCHAR(50),
                    risk_level VARCHAR(20),
                    title VARCHAR(200),
                    description TEXT,
                    news_url VARCHAR(500),
                    event_date DATE,
                    created_at TIMESTAMP DEFAULT NOW()
                );
            """))

            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_risk_events_stock_code ON risk_events(stock_code);
                CREATE INDEX IF NOT EXISTS idx_risk_events_date ON risk_events(event_date);
            """))

            conn.commit()
            print("✅ News tables initialized successfully")
    except Exception as e:
        print(f"❌ Error initializing news tables: {e}")
        raise
