import requests
from bs4 import BeautifulSoup
from datetime import datetime
from typing import List, Dict, Optional
from pydantic import BaseModel
import time
import random

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
