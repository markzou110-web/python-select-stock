import requests
from bs4 import BeautifulSoup
from datetime import datetime
from typing import List, Dict, Optional
from pydantic import BaseModel
import time
import random
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np

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
            'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36'
        }

    def _random_delay(self):
        """随机延迟，避免被封"""
        time.sleep(random.uniform(1.0, 3.0))

class EastMoneyCrawler(NewsCrawler):
    """东方财富新闻爬虫"""

    def fetch_stock_news(self, code: str) -> List[NewsItem]:
        """抓取个股新闻

        Args:
            code: 股票代码（如 600519）
        """
        self._random_delay()

        url = f"http://finance.eastmoney.com/a/{code}.html"

        try:
            response = requests.get(url, headers=self.headers, timeout=10)
            response.encoding = 'utf-8'
            soup = BeautifulSoup(response.text, 'html.parser')

            news_items = []

            # 东方财富新闻列表通常在特定的 div 或 ul 中
            news_list = soup.find_all('div', class_='news_content')

            for item in news_list[:20]:  # 限制最多抓取 20 条
                try:
                    title_elem = item.find('a')
                    if not title_elem:
                        continue

                    title = title_elem.get_text(strip=True)
                    url = title_elem.get('href', '')

                    if not title or not url:
                        continue

                    # 提取时间
                    time_elem = item.find('span', class_='time')
                    publish_time = datetime.now()
                    if time_elem:
                        time_str = time_elem.get_text(strip=True)
                        # 解析时间字符串（如 "2025-01-13 10:30"）
                        # 简化版：直接用当前时间

                    news_item = NewsItem(
                        title=title,
                        source='eastmoney',
                        url=url,
                        publish_time=publish_time
                    )
                    news_items.append(news_item)

                except Exception as e:
                    print(f"⚠️ 解析新闻失败: {e}")
                    continue

            return news_items

        except Exception as e:
            print(f"❌ 东方财富爬虫错误: {e}")
            return []

class NewsDeduplicator:
    """新闻去重器"""

    def __init__(self, similarity_threshold=0.85):
        self.similarity_threshold = similarity_threshold
        self.vectorizer = TfidfVectorizer(
            max_features=1000,
            stop_words=None,  # 中文暂无停用词
            ngram_range=(1, 2)
        )

    def deduplicate_by_tfidf(self, news_items: List[NewsItem]) -> List[NewsItem]:
        """使用 TF-IDF 相似度去重"""
        if len(news_items) <= 1:
            return news_items

        titles = [item.title for item in news_items]

        try:
            # 计算标题的 TF-IDF 矩阵
            tfidf_matrix = self.vectorizer.fit_transform(titles)

            # 计算相似度矩阵
            similarity_matrix = cosine_similarity(tfidf_matrix)

            # 找出重复的新闻
            to_remove = set()
            for i in range(len(news_items)):
                if i in to_remove:
                    continue
                for j in range(i + 1, len(news_items)):
                    if j in to_remove:
                        continue
                    if similarity_matrix[i][j] > self.similarity_threshold:
                        # 保留发布时间较晚的（通常更详细）
                        if news_items[i].publish_time < news_items[j].publish_time:
                            to_remove.add(i)
                        else:
                            to_remove.add(j)

            # 返回未重复的新闻
            unique_items = [item for i, item in enumerate(news_items) if i not in to_remove]
            return unique_items

        except Exception as e:
            print(f"⚠️ TF-IDF 去重失败，回退到简单去重: {e}")
            return self._deduplicate_by_url(news_items)

    def _deduplicate_by_url(self, news_items: List[NewsItem]) -> List[NewsItem]:
        """回退方案：基于 URL 去重"""
        seen_urls = set()
        unique_items = []
        for item in news_items:
            if item.url not in seen_urls:
                seen_urls.add(item.url)
                unique_items.append(item)
        return unique_items
