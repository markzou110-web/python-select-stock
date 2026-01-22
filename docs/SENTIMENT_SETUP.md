# 智谱AI情绪分析配置指南

## 📖 功能说明

Alpha Vision Pro 集成了AI情绪分析功能，可以自动分析新闻标题的情绪（正面/负面/中性），为投资决策提供参考。

**支持两种AI服务**：
1. **智谱AI**（推荐）- 国产AI，速度快，价格优惠
2. **DeepSeek**（备选）- 开源AI，效果优秀

---

## 🚀 快速开始

### 方式一：使用智谱AI（推荐）

#### 1. 注册智谱AI账号

访问：https://open.bigmodel.cn/

- 注册账号并登录
- 进入"API Key"页面
- 点击"新建API Key"
- 复制生成的API Key

#### 2. 配置环境变量

```bash
cd backend

# 方式A：直接设置环境变量
export ZHIPUAI_API_KEY="your_api_key_here"

# 方式B：创建 .env 文件
cp .env.example .env
# 编辑 .env 文件，填入你的API Key
vim .env  # 或使用其他编辑器
```

`.env` 文件内容：
```bash
ZHIPUAI_API_KEY=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

#### 3. 启动服务

```bash
PYTHONPATH=. python3 -m uvicorn api:app --host 127.0.0.1 --port 8000 --reload
```

#### 4. 验证配置

查看启动日志，如果看到以下信息说明配置成功：

```
🎭 Initializing sentiment analyzer...
```

测试API：
```bash
curl http://localhost:8000/api/news/sentiment/600519?days=30
```

---

### 方式二：使用DeepSeek

如果你更喜欢使用DeepSeek，可以配置：

```bash
export DEEPSEEK_API_KEY="your_deepseek_api_key_here"
```

**注意**：如果同时设置了 `ZHIPUAI_API_KEY` 和 `DEEPSEEK_API_KEY`，系统会**优先使用智谱AI**。

---

## 📊 使用说明

### 1. 前端界面

情绪分析会自动在以下位置显示：

- **AI深度分析页面** - 显示完整情绪分析仪表盘
- **个股详情弹窗** - 快速查看情绪评分
- **Dashboard** - 股票卡片中的情绪标签

### 2. API接口

```bash
# 获取个股情绪分析
GET /api/news/sentiment/{code}?days=30

# 参数说明：
# - code: 股票代码（如 600519）
# - days: 分析最近多少天的新闻（默认30天）

# 返回示例：
{
  "positive": 8,        # 正面新闻数
  "negative": 2,        # 负面新闻数
  "neutral": 5,         # 中性新闻数
  "average_score": 1.8, # 平均情绪评分 (-5到+5)
  "trend": "up",        # 趋势 (up/down/stable)
  "news_analyzed": 15   # 分析的新闻总数
}
```

### 3. 情绪评分说明

| 评分范围 | 情绪 | 说明 |
|---------|------|------|
| +3 到 +5 | 强烈正面 | 重大利好，如涨停、重大收购 |
| +1 到 +3 | 正面 | 一般利好，如上涨、业绩增长 |
| -1 到 +1 | 中性 | 消息面平淡 |
| -3 到 -1 | 负面 | 一般利空，如下跌、业绩下滑 |
| -5 到 -3 | 强烈负面 | 重大利空，如暴跌、违规处罚 |

### 4. 趋势说明

- **up (看涨)**: 近期新闻情绪变好
- **down (看跌)**: 近期新闻情绪变差
- **stable (中性)**: 新闻情绪稳定

---

## 💰 费用说明

### 智谱AI价格（2024年）

| 模型 | 价格 | 速度 |
|------|------|------|
| glm-4-flash | ¥0.1/百万tokens | 最快 |
| glm-4 | ¥0.5/百万tokens | 较快 |
| glm-4-plus | ¥1.0/百万tokens | 慢 |

**本项目使用 `glm-4-flash` 模型**

**估算成本**：
- 平均每条新闻约50 tokens
- 分析100条新闻 ≈ 5000 tokens ≈ ¥0.0005
- **每月成本 < ¥1**（正常使用）

### DeepSeek价格

| 模型 | 价格 |
|------|------|
| deepseek-chat | ¥1/百万tokens |

---

## 🔧 高级配置

### 1. 更换模型

如果你想使用智谱AI的其他模型，修改 `core/sentiment_analyzer.py`：

```python
self.model = "glm-4"      # 标准模型
# self.model = "glm-4-plus"  # 更强模型
# self.model = "glm-4-flash"  # 快速模型（默认）
```

### 2. 调整超时时间

```python
response = requests.post(self.api_url, headers=headers, json=payload, timeout=15)
# 改为
response = requests.post(self.api_url, headers=headers, json=payload, timeout=30)
```

### 3. 添加缓存（可选）

在 `api.py` 的情绪分析接口添加缓存：

```python
from functools import lru_cache

@lru_cache(maxsize=1000)
def get_sentiment_with_cache(code: str, days: int):
    # ... 现有逻辑
```

---

## ⚠️ 常见问题

### 1. API Key无效

**错误信息**：`⚠️ ZHIPUAI_API_KEY 调用失败: 401`

**解决方法**：
- 检查API Key是否正确复制
- 确认API Key已激活（新Key需要等待几分钟）
- 检查账户余额是否充足

### 2. 网络超时

**错误信息**：`⚠️ ZHIPUAI_API 调用失败: timeout`

**解决方法**：
- 检查网络连接
- 增加timeout参数（见"高级配置"）
- 系统会自动降级到关键词匹配

### 3. 没有新闻数据

**错误信息**：`最近30天暂无相关新闻`

**解决方法**：
- 确保后台新闻同步任务正在运行
- 等待30分钟后再次尝试（自动同步周期）
- 手动触发新闻同步：`curl -X POST http://localhost:8000/api/sync/daily`

### 4. 中文乱码

**解决方法**：
- 确保终端使用UTF-8编码：`export LANG=en_US.UTF-8`
- 数据库使用UTF-8编码

---

## 🎯 最佳实践

### 1. 开发环境

使用关键词匹配即可，无需配置AI：

```bash
# 不设置 ZHIPUAI_API_KEY，系统自动使用关键词匹配
python3 -m uvicorn api:app --reload
```

### 2. 生产环境

推荐配置智谱AI，效果更好：

```bash
export ZHIPUAI_API_KEY="your_key"
python3 -m uvicorn api:app --host 0.0.0.0 --port 8000
```

### 3. 成本控制

- 调整新闻同步频率（默认30分钟）
- 减少分析天数（默认30天，可改为7天）
- 使用缓存避免重复分析

---

## 📚 参考链接

- [智谱AI官方文档](https://open.bigmodel.cn/dev/api)
- [智谱AI控制台](https://open.bigmodel.cn/usercenter/apikeys)
- [DeepSeek API文档](https://platform.deepseek.com/api-docs/)

---

## 🤝 贡献

如果你有更好的情绪分析方案，欢迎提交PR！

---

**最后更新**：2024-01-18
**版本**：v1.0
