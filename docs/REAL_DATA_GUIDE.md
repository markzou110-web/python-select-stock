# 真实新闻数据获取指南

## 📖 概述

本指南介绍如何获取和使用真实的新闻舆情数据，替代演示数据。

---

## 🚀 快速开始

### 方式一：手动运行脚本

```bash
cd backend
python3 scripts/fetch_real_news.py
```

### 方式二：配置定时任务（推荐）

**编辑 crontab：**
```bash
crontab -e
```

**添加以下任务（每天 16:00 运行）：**
```bash
0 16 * * * cd /Users/liangzou/Desktop/AI_Tools/python-select-stock/backend && python3 scripts/fetch_real_news.py >> logs/news_fetch.log 2>&1
```

**更多定时任务示例：**
```bash
# 每小时运行一次
0 * * * * cd /path/to/backend && python3 scripts/fetch_real_news.py

# 工作日每天 9:00, 12:00, 15:00 运行
0 9,12,15 * * 1-5 cd /path/to/backend && python3 scripts/fetch_real_news.py
```

---

## 📊 数据来源

### 当前支持的数据源

1. **东方财富网** (akshare)
   - 市场新闻快讯
   - 个股新闻
   - 公告信息

2. **扩展数据源**（可添加）
   - 新浪财经
   - 同花顺
   - 雪球
   - 财联社

---

## 🔧 脚本功能说明

### `fetch_real_news.py` 脚本功能

#### 1. 抓取市场新闻
```python
# 使用 akshare 获取东方财富网新闻
df_news = ak.stock_news_em()
```

**获取的数据包括：**
- 新闻标题
- 发布时间
- 新闻链接
- 相关股票

#### 2. 提取题材关键词
```python
# 内置题材关键词库
theme_keywords = {
    '人工智能': ['AI', '人工智能', '芯片', '算力'],
    '新能源汽车': ['新能源', '电动车', '电池'],
    '半导体': ['半导体', '集成电路'],
    # ... 更多题材
}
```

**热度计算：**
```
题材热度 = 相关新闻数量 × 10
```

**生命周期判断：**
- 新闻数 > 5: 爆发期 (emerging)
- 新闻数 > 3: 成长期 (growing)
- 其他: 成熟期 (mature)

#### 3. 检测风险事件

**风险关键词库：**
```python
RISK_KEYWORDS = {
    "financial": ["亏损", "业绩下滑", "债务", "财务造假"],
    "operational": ["立案调查", "处罚", "诉讼", "违规"],
    "market": ["减持", "质押", "平仓", "解禁"],
    "major": ["事故", "停产", "问责", "罢免"]
}
```

**风险等级判断：**
- 财务/经营 + 立案/造假 → 高风险
- 市场类 → 中风险
- 其他 → 低风险

---

## 📝 数据库表结构

### themes 表（题材）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | integer | 主键 |
| name | varchar | 题材名称 |
| hotness | float | 热度分数 |
| life_cycle_stage | varchar | 生命周期阶段 |
| leader_stock | varchar | 龙头股票代码 |
| updated_at | timestamp | 更新时间 |

### risk_events 表（风险事件）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | integer | 主键 |
| stock_code | varchar | 股票代码 |
| risk_type | varchar | 风险类型 |
| risk_level | varchar | 风险等级 |
| title | varchar | 风险标题 |
| description | text | 风险描述 |
| news_url | varchar | 新闻链接 |
| event_date | date | 事件日期 |

### news_raw 表（原始新闻）
| 字段 | 类型 | 说明 |
|------|------|------|
| id | integer | 主键 |
| title | varchar | 新闻标题 |
| content | text | 新闻内容 |
| source | varchar | 新闻来源 |
| url | varchar | 新闻链接 |
| publish_time | timestamp | 发布时间 |

---

## 🎨 前端展示

### 1. 热点题材展示
位置：Dashboard 中间位置

**显示内容：**
- 题材名称
- 热度分数
- 生命周期阶段（颜色标识）
- 龙头股票代码

**颜色含义：**
- 🔴 红色：爆发期
- 🟠 橙色：成长期
- 🟡 黄色：成熟期
- ⚪ 灰色：衰退期

### 2. 风险预警通知
位置：Dashboard 热点题材下方

**显示内容：**
- 风险数量统计
- 风险等级徽章
- 股票代码
- 风险标题和描述
- 发布时间

**交互功能：**
- 点击"查看详情"展开所有风险
- 点击"忽略"隐藏单条通知
- 点击外链图标查看原文

### 3. 个股新闻详情
位置：扫描结果表格操作列

**使用方法：**
1. 点击 📰 图标按钮
2. 弹窗显示该股票相关新闻
3. 点击"刷新新闻"获取最新数据

---

## 🔍 手动测试 API

### 测试题材 API
```bash
curl "http://127.0.0.1:8000/api/news/themes?limit=10"
```

### 测试风险事件 API
```bash
curl "http://127.0.0.1:8000/api/news/risks?days=7"
```

### 测试个股新闻 API
```bash
curl "http://127.0.0.1:8000/api/news/stock/600519"
```

---

## ⚙️ 高级配置

### 1. 自定义题材关键词

编辑 `scripts/fetch_real_news.py`：

```python
theme_keywords = {
    '您的题材': ['关键词1', '关键词2', '关键词3'],
    # 添加更多...
}
```

### 2. 自定义风险关键词

编辑 `core/risk_detector.py`：

```python
RISK_KEYWORDS = {
    "financial": ["您的", "自定义", "关键词"],
    # 添加更多...
}
```

### 3. 调整热度计算

```python
# 当前：热度 = 新闻数 × 10
hotness = float(news_count * 10)

# 可改为：
hotness = float(news_count * 20)  # 提高权重
hotness = float(news_count * 5)   # 降低权重
```

### 4. 调整生命周期阈值

```python
if news_count > 10:  # 提高阈值
    stage = 'emerging'
elif news_count > 5:
    stage = 'growing'
```

---

## 🛠️ 故障排查

### 问题1：脚本无法运行

**检查 Python 环境：**
```bash
python3 --version  # 应该是 3.8+
```

**检查依赖：**
```bash
pip3 install akshare sqlalchemy psycopg2-binary
```

### 问题2：无法获取新闻

**检查网络连接：**
```bash
ping finance.eastmoney.com
```

**检查 akshare 版本：**
```bash
pip3 show akshare
```

**更新 akshare：**
```bash
pip3 install --upgrade akshare
```

### 问题3：题材提取为空

**原因：** 新闻标题中没有匹配到关键词

**解决方案：**
1. 检查新闻标题内容
2. 扩展关键词库
3. 降低匹配阈值

### 问题4：前端数据未更新

**检查数据库：**
```bash
python3 << EOF
from core.db import get_db_engine
from sqlalchemy import text

engine = get_db_engine()
with engine.connect() as conn:
    themes = conn.execute(text("SELECT COUNT(*) FROM themes")).fetchone()[0]
    risks = conn.execute(text("SELECT COUNT(*) FROM risk_events")).fetchone()[0]
    print(f"题材: {themes}, 风险事件: {risks}")
EOF
```

**重启后端服务：**
```bash
# 停止当前服务
# 重新启动
cd backend
uvicorn api:app --reload
```

**强制刷新前端：**
- 浏览器: `Cmd + Shift + R` (Mac) 或 `Ctrl + Shift + R` (Windows)

---

## 📈 性能优化建议

### 1. 数据库优化

**添加索引：**
```sql
CREATE INDEX idx_themes_hotness ON themes(hotness DESC);
CREATE INDEX idx_risk_events_date ON risk_events(event_date DESC);
CREATE INDEX idx_news_publish_time ON news_raw(publish_time DESC);
```

### 2. 缓存策略

**启用 Redis 缓存（可选）：**
```python
import redis

r = redis.Redis(host='localhost', port=6379, db=0)

# 缓存题材数据（1小时）
r.setex('themes', 3600, json.dumps(themes_data))
```

### 3. 定时任务优化

**使用系统级定时任务：**
- Linux/Mac: cron
- Windows: Task Scheduler
- Docker: Kubernetes CronJob

---

## 🚀 扩展功能

### 1. 添加更多数据源

**示例：添加新浪财经**
```python
import akshare as ak

# 获取新浪财经新闻
df_sina = ak.stock_news_sina()
```

### 2. 实现 AI 情绪分析

**配置 DeepSeek API：**
```bash
export DEEPSEEK_API_KEY="your_api_key"
```

**系统会自动调用 AI 进行情绪分析**

### 3. 新闻推送通知

**集成邮件/短信/钉钉通知**
- 检测到高风险事件时推送
- 重要题材突破时推送
- 自定义推送规则

---

## 📞 技术支持

如有问题，请查看：
- 功能文档：`docs/NEWS_ENHANCEMENT_2025-01-13.md`
- API 文档：`http://127.0.0.1:8000/docs`
- 系统日志：`backend/logs/news_fetch.log`

---

**版本**: v1.0
**最后更新**: 2025-01-13
**作者**: Alpha Vision Team
