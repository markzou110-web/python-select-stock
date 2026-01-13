# 新闻舆情系统设计文档

**日期**: 2025-01-13
**项目**: Alpha Vision v6.1
**功能**: 新闻舆情系统（风险预警、热点追踪、新闻聚合、情绪分析）

---

## 1. 系统架构概览

新闻舆情系统采用模块化设计，与现有 Alpha Vision 架构无缝集成。系统分为四个核心模块：**数据采集层**、**数据处理层**、**AI 分析层**和**展示层**。

### 数据采集层
负责从多个免费源（东方财富、新浪财经、雪球）抓取新闻数据，采用定时任务（每日盘后）和按需抓取（用户查看时）两种模式。使用 `requests` + `BeautifulSoup` 技术栈，配合 `APScheduler` 实现定时调度。采集器设计为插件式架构，每个数据源一个独立模块，便于扩展和维护。所有采集的数据先存入 PostgreSQL 的原始新闻表，等待进一步处理。

### 数据处理层
负责新闻去重、分类和关联。使用标题相似度算法（TF-IDF + 余弦相似度）去除重复新闻。通过关键词匹配将新闻分类到不同题材（如"人工智能"、"新能源"、"芯片半导体"等）。系统自动维护题材库，支持动态添加新题材。同时，将新闻与股票代码关联，建立股票-新闻多对多关系表，支持快速查询某只股票的所有相关新闻。

### AI 分析层
使用 DeepSeek API 进行情绪分析，返回情绪评分（-5 到 +5）和情绪标签（正面/负面/中性）。分析结果存入数据库，避免重复调用 API。支持批量分析，降低调用成本。

### 展示层
前端使用 React + Next.js 构建，集成到现有 Dashboard。新增市场情绪区块、扫描结果新增新闻和情绪列、新闻详情抽屉组件、风险预警弹窗等。

---

## 2. 数据模型设计

系统在 PostgreSQL 中新增 5 个核心表。

### news_raw（原始新闻表）
```sql
CREATE TABLE news_raw (
    id SERIAL PRIMARY KEY,
    title TEXT NOT NULL,
    content TEXT,
    source VARCHAR(50),  -- eastmoney, sina, xueqiu
    url VARCHAR(500) UNIQUE,
    publish_time TIMESTAMP,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_news_raw_url ON news_raw(url);
CREATE INDEX idx_news_raw_publish_time ON news_raw(publish_time);
```

### news_sentiment（情绪分析表）
```sql
CREATE TABLE news_sentiment (
    news_id INTEGER REFERENCES news_raw(id),
    sentiment_score FLOAT,  -- -5 to +5
    sentiment_label VARCHAR(20),  -- positive/negative/neutral
    ai_model VARCHAR(50),
    analyzed_at TIMESTAMP DEFAULT NOW()
);
```

### themes（题材表）
```sql
CREATE TABLE themes (
    id SERIAL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    keywords TEXT,  -- JSON array
    hotness FLOAT,
    life_cycle_stage VARCHAR(50),  -- emerging/growing/mature/declining
    updated_at TIMESTAMP DEFAULT NOW()
);
```

### news_stocks（新闻-股票关联表）
```sql
CREATE TABLE news_stocks (
    news_id INTEGER REFERENCES news_raw(id),
    stock_code VARCHAR(20),
    relevance FLOAT,  -- 0-1
    PRIMARY KEY (news_id, stock_code)
);
```

### risk_events（风险事件表）
```sql
CREATE TABLE risk_events (
    id SERIAL PRIMARY KEY,
    stock_code VARCHAR(20),
    risk_type VARCHAR(50),  -- financial/operational/market/major_negative
    risk_level VARCHAR(20),  -- high/medium/low
    title VARCHAR(200),
    description TEXT,
    news_url VARCHAR(500),
    event_date DATE,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_risk_events_stock_code ON risk_events(stock_code);
```

---

## 3. 功能模块设计

### 3.1 热点题材追踪模块

**功能**：每日盘后自动分析题材热度，识别龙头股

**算法流程**：
1. 从 `news_raw` 表提取当日所有新闻
2. 使用 TF-IDF 提取关键词，匹配题材库中的 `keywords`
3. 计算热度分数 = (新闻数 × 0.4 + 涨幅中位数 × 0.4 + 成交额 × 0.2)
4. 更新 `themes` 表的 `hotness` 和 `life_cycle_stage`
5. 识别龙头股：该题材中涨幅最大、且最早涨停的股票
6. 在前端展示题材排行榜（前 20 名）

**生命周期识别**：
- 爆发期（emerging）：新闻数和热度分数快速增长
- 成长期（growing）：热度持续上升，龙头股涨停
- 成熟期（mature）：热度维持高位，题材内股票普涨
- 衰退期（declining）：热度下降，龙头股开板

### 3.2 风险预警模块

**功能**：自动扫描风险事件，在扫描结果中标记

**风险分类**：
- **财务类**：业绩预告亏损、审计非标、财务造假嫌疑
- **经营类**：立案调查、行政处罚、重大诉讼
- **市场类**：大股东减持、股权质押平仓、解禁
- **重大负面**：安全事故、质量问题、管理层动荡

**风险等级**：
- 高风险（🔴）：立案调查、财务造假、重大安全事故
- 中风险（🟡）：大股东减持、股权质押、业绩预告亏损
- 低风险（🟢）：解禁、一般诉讼

**展示方式**：在扫描结果表格中添加风险图标，鼠标悬停显示详情

### 3.3 个股新闻聚合模块

**功能**：展示个股相关新闻，支持筛选和搜索

**展示内容**：
- 新闻标题、来源、发布时间
- 情绪标签（正面/负面/中性）
- 点击跳转原文

**交互**：
- 在扫描结果表格中新增"新闻"列
- 显示新闻数量徽章（如"12条"）
- 点击弹出新闻详情抽屉
- 支持按时间、情绪、来源筛选

### 3.4 AI 情绪分析模块

**功能**：使用 DeepSeek API 分析新闻情绪

**输入**：新闻标题 + 摘要（前 200 字）

**输出**：
- 情绪评分（-5 到 +5）
- 情绪标签（positive/negative/neutral）
- 关键理由

**优化策略**：
- 缓存分析结果，避免重复调用
- 批量处理，降低 API 成本
- API 失败时回退到关键词匹配

**情绪趋势图**：展示个股情绪随时间的变化曲线（最近 30 天）

---

## 4. 技术实现细节

### 4.1 后端实现

**新增模块**: `backend/core/news.py`

**核心类**：

1. **NewsCrawler（爬虫基类）**
   - `fetch_eastmoney()` - 东方财富新闻爬虫
   - `fetch_sina()` - 新浪财经新闻爬虫
   - `fetch_xueqiu()` - 雪球热门讨论爬虫
   - 使用随机延迟（1-3 秒）避免被封 IP

2. **NewsAnalyzer（AI 情绪分析器）**
   - `analyze_sentiment(text: str) -> SentimentResult`
   - 调用 DeepSeek API
   - 错误处理：API 失败时回退到关键词匹配

3. **ThemeTracker（题材追踪器）**
   - `update_themes()` - 每日更新题材热度
   - `identify_leaders()` - 识别龙头股
   - `calculate_life_cycle()` - 计算生命周期阶段

4. **RiskDetector（风险检测器）**
   - `detect_risks()` - 从新闻中提取风险事件
   - 基于规则引擎 + AI 辅助

### 4.2 定时任务配置

使用 `APScheduler` 在 `backend/api.py` 中配置：

```python
from apscheduler.schedulers.background import BackgroundScheduler

scheduler = BackgroundScheduler()
scheduler.add_job(update_themes, 'cron', hour=16, minute=0)  # 16:00 题材追踪
scheduler.add_job(scan_risks, 'cron', hour=16, minute=30)  # 16:30 风险扫描
scheduler.add_job(batch_analyze_sentiment, 'cron', hour=17, minute=0)  # 17:00 情绪分析
scheduler.start()
```

### 4.3 新增 API 端点

- `GET /api/news/themes` - 获取题材排行榜（Top 20）
- `GET /api/news/stock/{code}` - 获取个股新闻（分页）
- `GET /api/news/sentiment/{code}` - 获取个股情绪趋势
- `GET /api/news/risks` - 获取所有风险事件
- `POST /api/news/refresh/{code}` - 手动刷新个股新闻

---

## 5. 用户界面设计

### 5.1 首页新增"市场情绪"区块

在 MarketCard 和 SectorGrid 之间新增 `MarketSentiment` 组件：

**热点题材横向滚动卡片**：
- 左侧：题材名称 + 热度分数
- 右侧：龙头股代码和名称
- 颜色编码：🔥 爆发期（红）→ 成长期（橙）→ 成熟期（黄）→ 衰退期（灰）
- 支持横向滚动，显示 Top 10

**风险预警徽章**：
- 显示今日新增风险事件数量
- 点击展开风险事件列表

### 5.2 扫描结果表格新增列

**"新闻"列**：
- 显示新闻数量徽章（如"5"）
- 颜色：绿色（正面多）/ 红色（负面多）/ 灰色（中性）
- 点击弹出新闻详情抽屉

**"情绪"列**：
- 显示情绪趋势图（MiniSparkline，最近 7 天）
- 当前情绪值：+3.2（绿）/ -1.5（红）
- 鼠标悬停显示 30 天趋势图

### 5.3 新闻详情抽屉组件

**NewsDrawer（右侧滑出面板）**：
- **头部**：股票代码、名称、当前情绪值
- **情绪趋势图**：Recharts 折线图（30 天）
- **新闻列表**：按时间倒序，每条显示标题、时间、标签、来源
- **筛选器**：按情绪、来源、时间筛选
- **分页加载**：每页 10 条

### 5.4 风险预警弹窗

**RiskAlertModal（模态对话框）**：
- 按风险等级分组（高/中/低）
- 每条风险显示：股票代码、类型、描述、新闻链接
- 支持"导出风险报告"（CSV）

---

## 6. 数据流、错误处理与性能优化

### 6.1 数据流

**采集流程**（每日 15:30-16:00）：
```
定时任务 → 并发爬虫（3 个源）→ 去重 → 存储 → 触发情绪分析
```

**情绪分析流程**（每日 17:00-18:00）：
```
批量读取未分析新闻 → DeepSeek API（并发=5）→ 存储结果 → 更新缓存
```

**按需抓取流程**（用户点击时）：
```
用户点击 → 检查缓存（5 分钟）→ 无缓存则实时爬取 → 显示
```

### 6.2 错误处理

**网络错误**：
- 爬虫失败 → 跳过该源，尝试备用源
- API 超时 → 重试 3 次，指数退避
- API 限流 → 降低并发数，延迟重试

**数据质量**：
- HTML 解析失败 → 回退到正则表达式
- 情绪分析失败 → 回退到关键词匹配
- 去重异常 → 降级为 URL 去重

**存储错误**：
- 数据库连接失败 → 使用内存缓存，1 小时后重试
- 重复键冲突 → 忽略

### 6.3 性能优化

1. **数据库索引**：url、publish_time、hotness、stock_code
2. **缓存策略**：
   - 题材排行榜缓存 1 小时
   - 个股新闻缓存 5 分钟
   - 情绪分析永久缓存
3. **批量操作**：每 100 条提交一次
4. **并发控制**：爬虫并发=3，API 并发=5

---

## 7. 实施计划

### 阶段 1：基础数据采集（1 周）
- [ ] 实现 3 个爬虫（东方财富、新浪、雪球）
- [ ] 创建数据库表和基础 API
- [ ] 简单去重逻辑（URL 去重）
- [ ] 测试数据采集稳定性

### 阶段 2：核心功能（2 周）
- [ ] 实现题材追踪算法
- [ ] 实现风险预警规则引擎
- [ ] 集成 DeepSeek 情绪分析
- [ ] 前端界面开发
- [ ] 测试核心功能

### 阶段 3：优化与完善（1 周）
- [ ] 性能优化（缓存、索引、并发）
- [ ] 错误处理完善
- [ ] 用户体验优化
- [ ] 文档编写
- [ ] 全面测试

---

## 8. 依赖项

**Python 后端新增依赖**：
```
beautifulsoup4==4.12.3
scikit-learn==1.4.0  # TF-IDF
apscheduler==3.10.4  # 定时任务
```

**API Key 需求**：
- DeepSeek API Key（用于情绪分析）

**数据源**：
- 东方财富（免费，无需 key）
- 新浪财经（免费，无需 key）
- 雪球（免费，无需 key）

---

## 9. 风险与限制

1. **反爬风险**：免费数据源可能随时调整页面结构或增加反爬机制
2. **API 成本**：DeepSeek API 虽然便宜，但大量调用仍有成本
3. **数据准确性**：AI 情绪分析可能存在误差，需要持续调优
4. **性能瓶颈**：大量新闻采集和分析可能耗时较长

**缓解措施**：
- 多数据源互为备份
- 批量处理 + 缓存减少 API 调用
- 定期校验情绪分析准确性
- 并发优化 + 定时任务错峰执行

---

**设计版本**: v1.0
**设计者**: Claude (Superpowers Brainstorming)
**状态**: 待实施
