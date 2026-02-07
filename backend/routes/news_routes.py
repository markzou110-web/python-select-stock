from fastapi import APIRouter, HTTPException
import pandas as pd
from typing import List, Optional
from core.data import get_market_snapshot

# Global instances (will be set by main app)
theme_tracker = None
risk_detector = None

router = APIRouter(prefix="/api/news", tags=["news"])

@router.get("/stock")
def get_stock_news(code: str):
    """获取个股新闻（带缓存）"""
    from core.news import EastMoneyCrawler, news_cache
    
    # 尝试从缓存获取
    cached = news_cache.get(f"news_{code}")
    if cached:
        return {"data": cached}
        
    try:
        crawler = EastMoneyCrawler()
        news = crawler.fetch_stock_news(code)
        # 将 NewsItem 对象转换为字典
        results = [item.dict() for item in news]
        news_cache.set(f"news_{code}", results)
        return {"data": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/refresh")
def refresh_stock_news(code: str):
    """手动刷新个股新闻"""
    from core.news import EastMoneyCrawler, news_cache
    try:
        crawler = EastMoneyCrawler()
        news = crawler.fetch_stock_news(code)
        results = [item.dict() for item in news]
        news_cache.set(f"news_{code}", results)
        return {"status": "success", "data": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/themes")
def get_themes(limit: int = 10):
    """获取热门题材列表"""
    try:
        if theme_tracker is None:
            return {"data": []}

        themes = theme_tracker.get_top_themes(limit=limit)
        return {"data": themes}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/themes/{theme_id}/stocks")
def get_theme_stocks_api(theme_id: int):
    """获取题材成分股及最新市场价"""
    try:
        if theme_tracker is None:
            return {"data": []}

        stocks = theme_tracker.get_theme_stocks(theme_id)
        if not stocks:
            return {"data": []}

        snapshot_df = pd.DataFrame()
        try:
            snapshot_df = get_market_snapshot()
        except:
            print("⚠️ Endpoint snapshot failed, falling back to basic info.")

        results = []
        for s in stocks:
            code = s['code']
            price = 0.0
            change_pct = 0.0
            volume = 0.0
            
            if not snapshot_df.empty:
                match = snapshot_df[snapshot_df['code'].str.contains(code)]
                if not match.empty:
                    row = match.iloc[0]
                    price = float(row.get('price', 0))
                    change_pct = float(row.get('pct_chg', 0))
                    volume = float(row.get('amount', 0))
            
            results.append({
                "code": code,
                "name": s['name'],
                "price": price,
                "change_pct": change_pct,
                "volume": volume,
                "relevance": s['relevance']
            })

        return {"data": results}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/risks")
def get_risk_events(days: int = 30):
    """获取所有风险事件"""
    try:
        if risk_detector is None:
            return {"data": []}

        risks = risk_detector.get_risk_events(days=days)
        return {"data": risks}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
