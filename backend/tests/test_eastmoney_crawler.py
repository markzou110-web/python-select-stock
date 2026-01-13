import pytest
from core.news import EastMoneyCrawler, NewsDeduplicator, NewsItem
from datetime import datetime

def test_eastmoney_crawler_returns_list():
    """测试东方财富爬虫返回列表"""
    crawler = EastMoneyCrawler()
    news = crawler.fetch_stock_news("600519")

    assert isinstance(news, list)
    # 注意：如果没有网络或爬虫被反爬，可能返回空列表

def test_eastmoney_crawler_structure():
    """测试新闻数据结构"""
    crawler = EastMoneyCrawler()
    news = crawler.fetch_stock_news("600519")

    if len(news) > 0:
        item = news[0]
        assert hasattr(item, 'title')
        assert hasattr(item, 'source')
        assert hasattr(item, 'url')
        assert item.source == 'eastmoney'

def test_deduplicator_removes_duplicates():
    """测试去重功能"""
    dedup = NewsDeduplicator(similarity_threshold=0.85)

    news_items = [
        NewsItem(
            title="贵州茅台发布业绩预告",
            source="test",
            url="http://test1.com",
            publish_time=datetime.now()
        ),
        NewsItem(
            title="贵州茅台发布业绩预告",  # 完全相同的标题
            source="test",
            url="http://test2.com",
            publish_time=datetime.now()
        )
    ]

    unique_items = dedup.deduplicate_by_tfidf(news_items)

    assert len(unique_items) == 1
