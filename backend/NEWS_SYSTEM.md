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

新增依赖：
- beautifulsoup4==4.12.3
- scikit-learn==1.4.0
- apscheduler==3.10.4

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
返回指定股票的新闻列表，自动去重。

参数：
- `code`: 股票代码（如 600519）

响应示例：
```json
{
  "data": [
    {
      "title": "贵州茅台发布业绩预告",
      "source": "eastmoney",
      "url": "http://...",
      "publish_time": "2025-01-13T10:30:00"
    }
  ],
  "count": 10
}
```

#### 刷新个股新闻
```
POST /api/news/refresh/{code}
```
强制刷新指定股票的新闻缓存。

#### 获取题材排行榜
```
GET /api/news/themes?limit=10
```
返回热门题材列表。

参数：
- `limit`: 返回数量（默认 10）

响应示例：
```json
{
  "data": [
    {
      "id": 1,
      "name": "人工智能",
      "hotness": 150.0,
      "life_cycle_stage": "growing",
      "leader_stock": "300750"
    }
  ]
}
```

#### 获取风险事件
```
GET /api/news/risks?days=30
```
返回最近 N 天的风险事件。

参数：
- `days`: 天数（默认 30）

响应示例：
```json
{
  "data": [
    {
      "stock_code": "600519",
      "risk_type": "financial",
      "risk_level": "high",
      "title": "贵州茅台立案调查",
      "description": "...",
      "news_url": "http://...",
      "event_date": "2025-01-13"
    }
  ]
}
```

## 模块说明

### 核心模块

#### `core/news.py`
- `NewsItem`: 新闻数据模型
- `NewsCrawler`: 爬虫基类
- `EastMoneyCrawler`: 东方财富爬虫
- `NewsDeduplicator`: TF-IDF 去重器
- `NewsCache`: 缓存管理器
- `retry_on_failure`: 重试装饰器

#### `core/db_news.py`
- `init_news_tables()`: 初始化新闻数据库表

#### `core/theme_tracker.py`
- `ThemeTracker`: 题材热点追踪器

#### `core/risk_detector.py`
- `RiskDetector`: 风险事件检测器

#### `core/sentiment_analyzer.py`
- `SentimentAnalyzer`: AI 情绪分析器

### 数据库表

#### `news_raw`
原始新闻数据表
- id: 主键
- title: 新闻标题
- content: 新闻内容
- source: 新闻来源
- url: 新闻链接
- publish_time: 发布时间

#### `news_sentiment`
新闻情绪分析结果
- news_id: 关联 news_raw
- sentiment_score: 情绪评分 (-5 到 +5)
- sentiment_label: 情绪标签 (positive/negative/neutral)
- ai_model: 使用的 AI 模型

#### `themes`
题材热点表
- id: 主键
- name: 题材名称
- keywords: 关键词
- hotness: 热度分数
- life_cycle_stage: 生命周期阶段
- leader_stock: 龙头股票

#### `news_stocks`
新闻股票关联表
- news_id: 关联 news_raw
- stock_code: 股票代码
- relevance: 相关度

#### `risk_events`
风险事件表
- id: 主键
- stock_code: 股票代码
- risk_type: 风险类型
- risk_level: 风险等级
- title: 标题
- description: 描述
- news_url: 新闻链接
- event_date: 事件日期

## 缓存策略

新闻 API 使用内存缓存，TTL 为 5 分钟。
- 缓存键格式：`news_{code}`
- 自动过期机制
- 刷新端点可绕过缓存

## 故障排查

### 爬虫失败
- 检查网络连接
- 查看是否被反爬（增加延迟）
- 尝试切换数据源

### API 调用失败
- 检查 API Key 是否正确
- 查看账户余额
- 系统会自动回退到关键词匹配

### 数据库连接失败
- 检查 PostgreSQL 是否运行
- 验证数据库配置

## 性能优化

1. **并发控制**: 爬虫使用随机延迟（1-3秒）避免被封
2. **缓存机制**: 5分钟 TTL 减少重复请求
3. **重试机制**: 指数退避策略（2s, 4s, 8s）
4. **去重优化**: TF-IDF 相似度计算，回退到 URL 去重

## 定时任务

系统支持定时任务（需手动配置）：
- **16:00** - 更新题材热度
- **16:30** - 扫描风险事件
- **17:00** - 批量情绪分析

使用 APScheduler 实现定时任务调度。
