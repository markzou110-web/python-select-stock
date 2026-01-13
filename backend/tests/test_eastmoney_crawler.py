import pytest
from core.news import EastMoneyCrawler

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
