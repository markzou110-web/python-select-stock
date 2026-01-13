import pytest
from core.theme_tracker import ThemeTracker
from core.db import get_db_engine

def test_update_themes():
    """测试题材更新"""
    engine = get_db_engine()
    tracker = ThemeTracker(engine)

    themes = tracker.update_themes()

    assert isinstance(themes, dict)
    # 如果数据库中有新闻数据，应该返回一些题材

def test_get_top_themes():
    """测试获取热门题材"""
    engine = get_db_engine()
    tracker = ThemeTracker(engine)

    themes = tracker.get_top_themes(limit=10)

    assert isinstance(themes, list)
    # 确保返回的是列表
    if len(themes) > 0:
        assert 'name' in themes[0]
        assert 'hotness' in themes[0]
