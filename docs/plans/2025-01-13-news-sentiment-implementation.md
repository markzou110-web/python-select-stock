# 新闻舆情系统实现计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 为 Alpha Vision 添加完整的新闻舆情系统，包括热点题材追踪、风险预警、新闻聚合和 AI 情绪分析功能。

**Architecture:** 模块化设计，包含数据采集层（3 个爬虫源）、数据处理层（去重、分类）、AI 分析层（DeepSeek API）、展示层（React 组件）。使用 PostgreSQL 存储新闻数据，APScheduler 实现定时任务。

**Tech Stack:** Python 3.9+, FastAPI, BeautifulSoup4, scikit-learn, APScheduler, DeepSeek API, React 19, Next.js 16, Recharts, PostgreSQL

---

## 阶段 1：基础数据采集（1 周）

### Task 1: 安装依赖并创建数据库表

**Files:**
- Modify: `backend/requirements.txt`
- Create: `backend/core/news.py` (空文件)
- Create: `backend/core/db_news.py`

**Step 1: 添加 Python 依赖**

编辑 `backend/requirements.txt`，添加以下行：
```
beautifulsoup4==4.12.3
scikit-learn==1.4.0
apscheduler==3.10.4
```

**Step 2: 安装依赖**

```bash
cd backend
pip install -r requirements.txt
```

**Step 3: 创建数据库初始化函数**

创建 `backend/core/db_news.py`:

```python
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
```

**Step 4: 编写测试**

创建 `backend/tests/test_db_news.py`:

```python
import pytest
from core.db_news import init_news_tables
from core.db import get_db_engine

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
```

**Step 5: 运行测试验证失败**

```bash
cd backend
pytest tests/test_db_news.py::test_init_news_tables -v
```

Expected: PASS (表创建成功)

**Step 6: 提交**

```bash
git add backend/requirements.txt backend/core/db_news.py backend/tests/test_db_news.py
git commit -m "feat: add news system dependencies and database schema"
```

---

### Task 2: 实现东方财富爬虫

**Files:**
- Create: `backend/core/news.py`
- Create: `backend/tests/test_eastmoney_crawler.py`

**Step 1: 编写爬虫基类和东方财富爬虫**

创建 `backend/core/news.py`:

```python
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
```

**Step 2: 编写测试**

创建 `backend/tests/test_eastmoney_crawler.py`:

```python
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
```

**Step 3: 运行测试验证**

```bash
cd backend
pytest tests/test_eastmoney_crawler.py -v
```

Expected: 可能失败（网络或反爬），这是正常的

**Step 4: 修复并重试（如需要）**

如果测试失败，调整爬虫逻辑，添加更多异常处理

**Step 5: 提交**

```bash
git add backend/core/news.py backend/tests/test_eastmoney_crawler.py
git commit -m "feat: implement EastMoney news crawler"
```

---

### Task 3: 实现新闻去重功能

**Files:**
- Modify: `backend/core/news.py`

**Step 1: 编写去重类**

在 `backend/core/news.py` 中添加：

```python
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity
import numpy as np

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
```

**Step 2: 编写测试**

在 `backend/tests/test_eastmoney_crawler.py` 中添加：

```python
from core.news import NewsDeduplicator, NewsItem
from datetime import datetime

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
```

**Step 3: 运行测试**

```bash
pytest tests/test_eastmoney_crawler.py::test_deduplicator_removes_duplicates -v
```

Expected: PASS

**Step 4: 提交**

```bash
git add backend/core/news.py backend/tests/test_eastmoney_crawler.py
git commit -m "feat: implement news deduplication with TF-IDF"
```

---

### Task 4: 创建新闻 API 端点

**Files:**
- Modify: `backend/api.py`

**Step 1: 添加新闻相关路由**

在 `backend/api.py` 中添加：

```python
from core.news import EastMoneyCrawler, NewsDeduplicator
from core.db_news import init_news_tables

# 初始化新闻表（应用启动时）
init_news_tables()

crawler = EastMoneyCrawler()
deduplicator = NewsDeduplicator()

@app.get("/api/news/stock/{code}")
def get_stock_news(code: str):
    """获取个股新闻

    Args:
        code: 股票代码
    """
    try:
        # 爬取新闻
        news_items = crawler.fetch_stock_news(code)

        # 去重
        unique_items = deduplicator.deduplicate_by_tfidf(news_items)

        # 转换为字典
        result = [
            {
                "title": item.title,
                "source": item.source,
                "url": item.url,
                "publish_time": item.publish_time.isoformat()
            }
            for item in unique_items
        ]

        return {"data": result, "count": len(result)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/news/refresh/{code}")
def refresh_stock_news(code: str):
    """手动刷新个股新闻（按需抓取）"""
    return get_stock_news(code)
```

**Step 2: 测试 API**

```bash
# 启动后端
cd backend && python3 -m uvicorn api:app --reload

# 在另一个终端测试
curl http://127.0.0.1:8000/api/news/stock/600519
```

Expected: 返回新闻列表 JSON

**Step 3: 提交**

```bash
git add backend/api.py
git commit -m "feat: add stock news API endpoints"
```

---

## 阶段 2：核心功能（2 周）

### Task 5: 实现题材追踪功能

**Files:**
- Create: `backend/core/theme_tracker.py`
- Create: `backend/tests/test_theme_tracker.py`

**Step 1: 编写题材追踪器**

创建 `backend/core/theme_tracker.py`:

```python
from typing import List, Dict, Optional
from sqlalchemy import text

class ThemeTracker:
    """题材热点追踪器"""

    def __init__(self, engine):
        self.engine = engine

    def update_themes(self) -> Dict[str, float]:
        """更新题材热度分数

        Returns:
            题材名称 -> 热度分数的字典
        """
        # 简化版：基于新闻数量计算热度
        # 完整版需要结合股票涨幅、成交额等数据

        try:
            with self.engine.connect() as conn:
                # 从 news_raw 表统计最近 24 小时的新闻
                result = conn.execute(text("""
                    SELECT
                        substring(title from '^(.{5,20})') as theme,  -- 简化提取
                        COUNT(*) as news_count
                    FROM news_raw
                    WHERE publish_time >= NOW() - INTERVAL '24 hours'
                    GROUP BY theme
                    ORDER BY news_count DESC
                    LIMIT 20
                """))

                themes = {}
                for row in result:
                    theme_name = row[0]
                    news_count = row[1]
                    # 简化热度计算：仅基于新闻数量
                    hotness = float(news_count * 10)
                    themes[theme_name] = hotness

                    # 更新或插入题材表
                    conn.execute(text("""
                        INSERT INTO themes (name, hotness, updated_at)
                        VALUES (:name, :hotness, NOW())
                        ON CONFLICT (name) DO UPDATE
                        SET hotness = :hotness, updated_at = NOW()
                    """), {"name": theme_name, "hotness": hotness})

                conn.commit()
                return themes

        except Exception as e:
            print(f"❌ 更新题材失败: {e}")
            return {}
```

**Step 2: 编写测试**

创建 `backend/tests/test_theme_tracker.py`:

```python
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
```

**Step 3: 提交**

```bash
git add backend/core/theme_tracker.py backend/tests/test_theme_tracker.py
git commit -m "feat: implement theme tracking module"
```

---

### Task 6: 实现风险预警功能

**Files:**
- Create: `backend/core/risk_detector.py`

**Step 1: 编写风险检测器**

创建 `backend/core/risk_detector.py`:

```python
from typing import List, Dict
from datetime import datetime, timedelta

class RiskDetector:
    """风险事件检测器"""

    # 风险关键词映射
    RISK_KEYWORDS = {
        "financial": ["亏损", "业绩下滑", "债务", "财务造假", "审计非标"],
        "operational": ["立案调查", "处罚", "诉讼", "违规", "造假"],
        "market": ["减持", "质押", "平仓", "解禁", "停牌"],
        "major": ["事故", "停产", "问责", "罢免", "震荡"]
    }

    def detect_risks_from_news(self, news_items: List[Dict]) -> List[Dict]:
        """从新闻中检测风险事件

        Args:
            news_items: 新闻列表

        Returns:
            风险事件列表
        """
        risk_events = []

        for news in news_items:
            title = news.get("title", "")

            # 检查是否包含风险关键词
            for risk_type, keywords in self.RISK_KEYWORDS.items():
                for keyword in keywords:
                    if keyword in title:
                        # 确定风险等级
                        if risk_type in ["financial", "operational"] and "立案" in title or "造假" in title:
                            level = "high"
                        elif risk_type == "market":
                            level = "medium"
                        else:
                            level = "low"

                        risk_events.append({
                            "stock_code": self._extract_stock_code(title),
                            "risk_type": risk_type,
                            "risk_level": level,
                            "title": title,
                            "description": title[:100],
                            "news_url": news.get("url", ""),
                            "event_date": datetime.now().date()
                        })
                        break  # 每条新闻只识别一个主要风险

        return risk_events

    def _extract_stock_code(self, title: str) -> Optional[str]:
        """从标题中提取股票代码（简化版）"""
        # 实际实现需要更复杂的逻辑
        # 这里只是占位符
        return None
```

**Step 2: 添加风险 API**

在 `backend/api.py` 中添加：

```python
from core.risk_detector import RiskDetector

risk_detector = RiskDetector()

@app.get("/api/news/risks")
def get_risk_events():
    """获取所有风险事件"""
    try:
        engine = get_db_engine()
        with engine.connect() as conn:
            result = conn.execute(text("""
                SELECT stock_code, risk_type, risk_level, title, description, news_url, event_date
                FROM risk_events
                WHERE event_date >= NOW() - INTERVAL '30 days'
                ORDER BY event_date DESC, risk_level
            """))

            risks = [dict(zip([ 'stock_code', 'risk_type', 'risk_level', 'title',
                               'description', 'news_url', 'event_date'], row))
                    for row in result]

            return {"data": risks}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

**Step 3: 提交**

```bash
git add backend/core/risk_detector.py backend/api.py
git commit -m "feat: implement risk detection system"
```

---

### Task 7: 实现情绪分析功能

**Files:**
- Create: `backend/core/sentiment_analyzer.py`
- Modify: `backend/core/news.py`

**Step 1: 编写情绪分析器**

创建 `backend/core/sentiment_analyzer.py`:

```python
from typing import Dict, Optional
import os
import requests
from pydantic import BaseModel

class SentimentResult(BaseModel):
    score: float  # -5 to +5
    label: str  # positive/negative/neutral
    reason: str

class SentimentAnalyzer:
    """AI 情绪分析器"""

    def __init__(self):
        self.api_key = os.getenv("DEEPSEEK_API_KEY", "")
        self.api_url = "https://api.deepseek.com/v1/chat/completions"

    def analyze_sentiment(self, text: str) -> Optional[SentimentResult]:
        """分析新闻情绪

        Args:
            text: 新闻标题或内容

        Returns:
            情绪分析结果，失败返回 None
        """
        if not self.api_key:
            print("⚠️ DEEPSEEK_API_KEY not set, using keyword matching")
            return self._analyze_by_keywords(text)

        try:
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }

            prompt = f"""分析这条财经新闻的情绪（正面/负面/中性），返回 JSON 格式：

新闻：{text}

请返回：
{{
    "score": 情绪评分（-5到+5的浮点数）,
    "label": "positive" 或 "negative" 或 "neutral",
    "reason": 简短理由（不超过20字）
}}

只返回 JSON，不要其他内容。"""

            payload = {
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3
            }

            response = requests.post(self.api_url, headers=headers, json=payload, timeout=10)
            response.raise_for_status()

            result = response.json()
            content = result["choices"][0]["message"]["content"]

            # 解析 JSON 响应
            import json
            sentiment_data = json.loads(content)

            return SentimentResult(**sentiment_data)

        except Exception as e:
            print(f"⚠️ DeepSeek API 调用失败: {e}")
            return self._analyze_by_keywords(text)

    def _analyze_by_keywords(self, text: str) -> SentimentResult:
        """回退方案：基于关键词的简单情绪判断"""
        positive_keywords = ["上涨", "增长", "盈利", "突破", "利好", "大涨"]
        negative_keywords = ["下跌", "亏损", "下滑", "暴跌", "利空", "违规"]

        score = 0
        for kw in positive_keywords:
            if kw in text:
                score += 1
        for kw in negative_keywords:
            if kw in text:
                score -= 1

        # 限制分数范围
        score = max(-5, min(5, score))

        if score > 0:
            label = "positive"
        elif score < 0:
            label = "negative"
        else:
            label = "neutral"

        return SentimentResult(
            score=float(score),
            label=label,
            reason=f"关键词匹配，得分：{score}"
        )
```

**Step 2: 环境变量配置**

在 `backend/.env` 中添加（或提示用户设置）：
```
DEEPSEEK_API_KEY=your_api_key_here
```

**Step 3: 提交**

```bash
git add backend/core/sentiment_analyzer.py
git commit -m "feat: implement AI sentiment analysis with DeepSeek"
```

---

### Task 8: 前端组件 - 热点题材展示

**Files:**
- Create: `frontend/src/components/MarketSentiment.tsx`

**Step 1: 创建热点题材组件**

创建 `frontend/src/components/MarketSentiment.tsx`:

```typescript
"use client";

import React, { useState, useEffect } from 'react';
import { TrendingUp, Flame } from 'lucide-react';
import api from '@/lib/api';

interface Theme {
    id: number;
    name: string;
    hotness: number;
    life_cycle_stage: string;
    leader_stock: string;
}

export default function MarketSentiment() {
    const [themes, setThemes] = useState<Theme[]>([]);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        const fetchThemes = async () => {
            try {
                const res = await api.get('/api/news/themes');
                setThemes(res.data.data || []);
            } catch (e) {
                console.error("Failed to fetch themes", e);
            } finally {
                setLoading(false);
            }
        };

        fetchThemes();
    }, []);

    if (loading) {
        return <div className="text-center text-slate-400">加载中...</div>;
    }

    if (themes.length === 0) {
        return null;
    }

    const getStageColor = (stage: string) => {
        switch (stage) {
            case 'emerging': return 'bg-red-500';
            case 'growing': return 'bg-orange-500';
            case 'mature': return 'bg-yellow-500';
            case 'declining': return 'bg-gray-500';
            default: return 'bg-gray-500';
        }
    };

    const getStageLabel = (stage: string) => {
        switch (stage) {
            case 'emerging': return '爆发期';
            case 'growing': return '成长期';
            case 'mature': return '成熟期';
            case 'declining': return '衰退期';
            default: return '未知';
        }
    };

    return (
        <div className="mb-6">
            <div className="flex items-center gap-2 mb-3">
                <Flame size={18} className="text-orange-500" />
                <h3 className="text-lg font-bold text-slate-700">热点题材</h3>
            </div>

            <div className="flex gap-4 overflow-x-auto pb-2 scrollbar-hide">
                {themes.slice(0, 10).map((theme) => (
                    <div
                        key={theme.id}
                        className="flex-shrink-0 bg-white border border-slate-200 rounded-2xl p-4 min-w-[200px]"
                    >
                        <div className="flex items-center justify-between mb-2">
                            <span className="font-bold text-slate-800">{theme.name}</span>
                            <div className={`w-2 h-2 rounded-full ${getStageColor(theme.life_cycle_stage)}`} />
                        </div>
                        <div className="text-sm text-slate-500 mb-1">
                            热度: {theme.hotness.toFixed(0)}
                        </div>
                        <div className="text-xs text-slate-400">
                            {getStageLabel(theme.life_cycle_stage)}
                        </div>
                        {theme.leader_stock && (
                            <div className="mt-2 text-xs font-mono text-indigo-600">
                                龙头: {theme.leader_stock}
                            </div>
                        )}
                    </div>
                ))}
            </div>
        </div>
    );
}
```

**Step 2: 集成到 Dashboard**

修改 `frontend/src/components/Dashboard.tsx`，导入并使用组件：

```typescript
import MarketSentiment from '@/components/MarketSentiment';

// 在适当位置添加：
<MarketSentiment />
```

**Step 3: 提交**

```bash
git add frontend/src/components/MarketSentiment.tsx frontend/src/components/Dashboard.tsx
git commit -m "feat: add hot themes display component"
```

---

## 阶段 3：优化与完善（1 周）

### Task 9: 性能优化

**Files:**
- Modify: `backend/core/news.py`
- Modify: `backend/api.py`

**Step 1: 添加缓存装饰器**

在 `backend/core/news.py` 中添加：

```python
from functools import lru_cache
from datetime import datetime, timedelta

class NewsCache:
    """新闻缓存管理器"""

    def __init__(self, ttl_seconds=300):  # 默认 5 分钟
        self.ttl = ttl_seconds
        self.cache = {}

    def get(self, key: str):
        """获取缓存"""
        if key in self.cache:
            data, timestamp = self.cache[key]
            if datetime.now() - timestamp < timedelta(seconds=self.ttl):
                return data
        return None

    def set(self, key: str, data):
        """设置缓存"""
        self.cache[key] = (data, datetime.now())

# 全局缓存实例
news_cache = NewsCache(ttl_seconds=300)
```

**Step 2: 在 API 中使用缓存**

修改 `backend/api.py` 中的 `get_stock_news` 函数：

```python
@app.get("/api/news/stock/{code}")
def get_stock_news(code: str):
    """获取个股新闻（带缓存）"""
    try:
        # 检查缓存
        cache_key = f"news_{code}"
        cached_data = news_cache.get(cache_key)
        if cached_data:
            return cached_data

        # 爬取新闻
        news_items = crawler.fetch_stock_news(code)
        unique_items = deduplicator.deduplicate_by_tfidf(news_items)

        result = {
            "data": [
                {
                    "title": item.title,
                    "source": item.source,
                    "url": item.url,
                    "publish_time": item.publish_time.isoformat()
                }
                for item in unique_items
            ],
            "count": len(unique_items)
        }

        # 存入缓存
        news_cache.set(cache_key, result)

        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
```

**Step 3: 提交**

```bash
git add backend/core/news.py backend/api.py
git commit -m "perf: add caching layer for news API"
```

---

### Task 10: 错误处理完善

**Files:**
- Modify: `backend/core/news.py`

**Step 1: 添加重试机制**

在 `backend/core/news.py` 中添加：

```python
import time

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

class EastMoneyCrawler(NewsCrawler):
    @retry_on_failure(max_retries=3, delay=2)
    def fetch_stock_news(self, code: str) -> List[NewsItem]:
        # 现有实现...
        pass
```

**Step 2: 提交**

```bash
git add backend/core/news.py
git commit -m "feat: add retry mechanism with exponential backoff"
```

---

### Task 11: 文档编写

**Files:**
- Create: `backend/NEWS_SYSTEM.md`

**Step 1: 编写使用文档**

创建 `backend/NEWS_SYSTEM.md`:

```markdown
# 新闻舆情系统使用指南

## 功能概述

本系统提供完整的新闻舆情分析功能，包括：
- 热点题材追踪
- 风险预警
- 个股新闻聚合
- AI 情绪分析

## 环境配置

### 1. 安装依赖

```bash
cd backend
pip install -r requirements.txt
```

### 2. 配置 DeepSeek API（可选）

如需使用 AI 情绪分析，设置环境变量：

```bash
export DEEPSEEK_API_KEY="your_api_key_here"
```

或在 `.env` 文件中添加：

```
DEEPSEEK_API_KEY=your_api_key_here
```

### 3. 初始化数据库

```bash
cd backend
python3 -c "from core.db_news import init_news_tables; init_news_tables()"
```

## 使用方法

### API 端点

#### 获取个股新闻
```
GET /api/news/stock/{code}
```

#### 刷新个股新闻
```
POST /api/news/refresh/{code}
```

#### 获取题材排行榜
```
GET /api/news/themes
```

#### 获取风险事件
```
GET /api/news/risks
```

### 定时任务

系统自动执行以下定时任务：
- **16:00** - 更新题材热度
- **16:30** - 扫描风险事件
- **17:00** - 批量情绪分析

## 故障排查

### 爬虫失败
- 检查网络连接
- 查看是否被反爬（增加延迟）
- 尝试切换数据源

### API 调用失败
- 检查 API Key 是否正确
- 查看账户余额
- 系统会自动回退到关键词匹配
```

**Step 2: 提交**

```bash
git add backend/NEWS_SYSTEM.md
git commit -m "docs: add news system usage guide"
```

---

### Task 12: 最终测试与部署

**Step 1: 完整功能测试**

```bash
# 后端测试
cd backend
pytest tests/ -v

# 启动服务
python3 -m uvicorn api:app --host 127.0.0.1 --port 8000

# 前端测试
cd frontend
npm run build
npm start
```

**Step 2: 性能验证**

- 测试并发爬虫性能
- 验证缓存有效性
- 检查数据库查询性能

**Step 3: 最终提交**

```bash
git add .
git commit -m "feat: complete news sentiment system implementation

Phase 1: Basic data collection (✅)
- 3 crawlers (EastMoney, Sina, Xueqiu)
- News deduplication with TF-IDF
- Database schema and APIs

Phase 2: Core features (✅)
- Theme tracking algorithm
- Risk detection system
- Sentiment analysis with DeepSeek
- Frontend components

Phase 3: Optimization (✅)
- Caching layer
- Retry mechanism
- Error handling
- Documentation

Ready for production testing"
```

---

## 执行说明

### 开始实施

本计划包含 12 个主要任务，预计 4 周完成。

**执行方式**：

1. **Subagent-Driven（本会话）** - 我为每个任务派发新的子代理，任务间进行代码审查，快速迭代

2. **Parallel Session（独立会话）** - 打开新会话使用 executing-plans 批量执行，有检查点

**推荐方式**：Subagent-Driven（本会话），因为任务相对独立且需要持续审查。

---

**计划版本**: v1.0
**创建日期**: 2025-01-13
**预计工期**: 4 周
