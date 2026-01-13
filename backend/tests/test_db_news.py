import pytest
from core.db_news import init_news_tables
from core.db import get_db_engine
from sqlalchemy import text

def test_init_news_tables():
    """测试新闻表初始化"""
    engine = get_db_engine()
    assert engine is not None

    init_news_tables(engine)

    # 验证表已创建
    with engine.connect() as conn:
        result = conn.execute(text("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema='public'
            AND table_name IN ('news_raw', 'news_sentiment', 'themes', 'news_stocks', 'risk_events')
        """))
        tables = [row[0] for row in result]
        assert len(tables) == 5
        assert 'news_raw' in tables
