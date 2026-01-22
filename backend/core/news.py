import requests
from bs4 import BeautifulSoup
from datetime import datetime, timedelta
from typing import List, Dict, Optional
from pydantic import BaseModel
import time
import random
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np

def retry_on_failure(max_retries=3, delay=1):
    """重试装饰器"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt < max_retries - 1:
                        wait_time = delay * (2 ** attempt)  # 指数退避
                        print(f"⚠️ 第 {attempt + 1} 次尝试失败，{wait_time}秒后重试...")
                        time.sleep(wait_time)
                    else:
                        raise e
        return wrapper
    return decorator

class NewsItem(BaseModel):
    """新闻数据模型"""
    title: str
    content: Optional[str] = None
    source: str
    url: str
    publish_time: datetime

class NewsCrawler:
    """新闻爬虫基类"""

    def __init__(self):
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
        }

    def _random_delay(self):
        """随机延迟，避免被封"""
        time.sleep(random.uniform(1.0, 3.0))

class EastMoneyCrawler(NewsCrawler):
    """东方财富新闻爬虫"""

    @retry_on_failure(max_retries=3, delay=2)
    def fetch_stock_news(self, code: str) -> List[NewsItem]:
        """抓取个股新闻"""
        self._random_delay()
        # 修复：使用正确的东方财富个股新闻URL格式
        # 格式：http://finance.eastmoney.com/a/cnazdd.html - 全市场
        # 个股：http://emweb.eastmoney.com/news_f10_stk.aspx?code=SH600519
        # 使用更简单的方案：从全局新闻中筛选
        try:
            # 方案：从全局新闻中通过代码筛选
            global_news = self.fetch_global_news()
            stock_news = []

            # 获取股票名称（如果可用）
            from .db import get_db_engine
            from sqlalchemy import text
            stock_name = None
            try:
                engine = get_db_engine()
                if engine:
                    with engine.connect() as conn:
                        result = conn.execute(text("SELECT name FROM stock_basic WHERE code = :code"), {"code": code})
                        row = result.fetchone()
                        if row:
                            stock_name = row[0]
            except:
                pass

            # 筛选包含该股票代码或名称的新闻
            for item in global_news:
                title = item.title
                # 检查是否包含6位代码
                import re
                if code in title or (stock_name and stock_name in title):
                    stock_news.append(item)

            return stock_news[:20]

        except Exception as e:
            print(f"❌ 东方财富爬虫错误: {e}")
            return []

    @retry_on_failure(max_retries=3, delay=2)
    def fetch_global_news(self) -> List[NewsItem]:
        """抓取市场滚动新闻列表"""
        self._random_delay()
        # 使用更稳健的财经导读汇总页
        url = "http://finance.eastmoney.com/a/ccjdd.html"
        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            response.encoding = 'utf-8'
            if response.status_code != 200:
                print(f"⚠️ EastMoney returns status {response.status_code}")
                return []
            
            soup = BeautifulSoup(response.text, 'html.parser')
            news_items = []
            
            # 使用更通用的链接提取方式，寻找包含 /a/ 和数字 ID 的正文链接
            links = soup.find_all('a')
            seen_urls = set()
            
            for a in links:
                href = a.get('href', '')
                title = a.get_text(strip=True)
                
                # 过滤条件：有标题，链接包含 /a/，链接以 .html 结尾
                if title and len(title) > 8 and '/a/' in href and href.endswith('.html'):
                    if href in seen_urls: continue
                    seen_urls.add(href)
                    
                    full_url = href if href.startswith('http') else f"https:{href}"
                    
                    news_items.append(NewsItem(
                        title=title,
                        source='eastmoney',
                        url=full_url,
                        publish_time=datetime.now()
                    ))
                
                if len(news_items) >= 60: break
                
            return news_items
        except Exception as e:
            print(f"❌ Global news fetch failed: {e}")
            return []

def save_news_items(engine, news_items: List[NewsItem]):
    """将新闻存入数据库 news_raw 表，并自动关联股票"""
    from sqlalchemy import text
    if not engine or not news_items: return
    
    associator = NewsStockAssociator(engine)
    
    try:
        with engine.connect() as conn:
            for item in news_items:
                # 1. 保存新闻
                res = conn.execute(text("""
                    INSERT INTO news_raw (title, source, url, publish_time)
                    VALUES (:title, :source, :url, :publish_time)
                    ON CONFLICT (url) DO UPDATE SET title = EXCLUDED.title
                    RETURNING id
                """), {
                    "title": item.title,
                    "source": item.source,
                    "url": item.url,
                    "publish_time": item.publish_time
                })
                news_id = res.fetchone()[0]
                
                # 2. 关联股票
                stock_codes = associator.associate(item.title)
                for code in stock_codes:
                    conn.execute(text("""
                        INSERT INTO news_stocks (news_id, stock_code, relevance)
                        VALUES (:news_id, :stock_code, 1.0)
                        ON CONFLICT (news_id, stock_code) DO NOTHING
                    """), {"news_id": news_id, "stock_code": code})
            
            conn.commit()
    except Exception as e:
        print(f"❌ Save news and associate stocks failed: {e}")

class NewsStockAssociator:
    """新闻-股票自动关联器"""
    
    _stock_list_cache = None
    _last_cache_time = 0

    def __init__(self, engine):
        self.engine = engine
        self._refresh_stock_list()

    def _refresh_stock_list(self):
        """从数据库加载股票列表（名称和代码）"""
        now = time.time()
        if NewsStockAssociator._stock_list_cache and (now - NewsStockAssociator._last_cache_time < 3600):
            return
            
        try:
            from sqlalchemy import text
            with self.engine.connect() as conn:
                result = conn.execute(text("SELECT code, name FROM stock_basic"))
                # 缓存为 {name: code} 字典，方便匹配
                NewsStockAssociator._stock_list_cache = {row[1]: row[0] for row in result}
                NewsStockAssociator._last_cache_time = now
                print(f"✅ Loaded {len(NewsStockAssociator._stock_list_cache)} stocks for association.")
        except Exception as e:
            print(f"⚠️ Load stock list failed: {e}")
            NewsStockAssociator._stock_list_cache = {}

    def associate(self, title: str) -> List[str]:
        """根据标题识别股票代码"""
        if not title: return []
        
        found_codes = set()
        
        # 1. 提取 6 位数字代码
        import re
        codes = re.findall(r'\b\d{6}\b', title)
        for c in codes:
            found_codes.add(c)
            
        # 2. 匹配股票名称
        if NewsStockAssociator._stock_list_cache:
            for name, code in NewsStockAssociator._stock_list_cache.items():
                if len(name) >= 2 and name in title:
                    found_codes.add(code)
                    
        return list(found_codes)

class NewsDeduplicator:
    """新闻去重器"""

    def __init__(self, similarity_threshold=0.85):
        self.similarity_threshold = similarity_threshold
        self.vectorizer = TfidfVectorizer(
            max_features=1000,
            stop_words=None,
            ngram_range=(1, 2)
        )

    def deduplicate_by_tfidf(self, news_items: List[NewsItem]) -> List[NewsItem]:
        """使用 TF-IDF 相似度去重"""
        if len(news_items) <= 1:
            return news_items
        titles = [item.title for item in news_items]
        try:
            tfidf_matrix = self.vectorizer.fit_transform(titles)
            similarity_matrix = cosine_similarity(tfidf_matrix)
            to_remove = set()
            for i in range(len(news_items)):
                if i in to_remove: continue
                for j in range(i + 1, len(news_items)):
                    if j in to_remove: continue
                    if similarity_matrix[i][j] > self.similarity_threshold:
                        if news_items[i].publish_time < news_items[j].publish_time:
                            to_remove.add(i)
                        else:
                            to_remove.add(j)
            unique_items = [item for i, item in enumerate(news_items) if i not in to_remove]
            return unique_items
        except Exception as e:
            print(f"⚠️ TF-IDF 去重失败: {e}")
            return self._deduplicate_by_url(news_items)

    def _deduplicate_by_url(self, news_items: List[NewsItem]) -> List[NewsItem]:
        seen_urls = set()
        unique_items = []
        for item in news_items:
            if item.url not in seen_urls:
                seen_urls.add(item.url)
                unique_items.append(item)
        return unique_items

class NewsCache:
    """新闻缓存管理器"""

    def __init__(self, ttl_seconds=300):
        self.ttl = ttl_seconds
        self.cache = {}

    def get(self, key: str):
        if key in self.cache:
            data, timestamp = self.cache[key]
            if datetime.now() - timestamp < timedelta(seconds=self.ttl):
                return data
        return None

    def set(self, key: str, data):
        self.cache[key] = (data, datetime.now())

news_cache = NewsCache(ttl_seconds=300)
