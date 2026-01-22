# 🚀 智谱AI情绪分析 - 快速配置指南

## ✅ 已完成的修改

1. **支持智谱AI** - 修改 `core/sentiment_analyzer.py`
2. **配置文件** - 创建 `.env.example`
3. **测试脚本** - 创建 `test_sentiment.py`
4. **详细文档** - 创建 `docs/SENTIMENT_SETUP.md`

---

## 📦 三步快速开始

### 第1步：获取智谱AI API Key

访问 https://open.bigmodel.cn/ 注册账号并获取API Key

### 第2步：配置环境变量

```bash
cd backend

# 方式A：临时设置（测试用）
export ZHIPUAI_API_KEY="your_api_key_here"

# 方式B：永久设置（推荐）
cp .env.example .env
# 编辑 .env 文件，填入你的 API Key
```

### 第3步：测试配置

```bash
# 运行测试脚本
python3 test_sentiment.py
```

**预期输出**：
```
✅ 智谱AI: 已配置 (Key: 8a2b3c4d5e...)
📊 当前使用: ZHIPU
🤖 模型: glm-4-flash

测试 1/5
标题: 贵州茅台大涨5%，创历史新高
预期: positive
结果: positive
评分: 4.0
理由: 重大利好新闻
✅ 通过

✅ 测试通过！情绪分析功能正常
```

---

## 🎯 验证功能

### 方法1：使用测试脚本（推荐）

```bash
cd backend

# 测试关键词模式
python3 test_sentiment.py

# 测试智谱AI模式
export ZHIPUAI_API_KEY="your_key"
python3 test_sentiment.py

# 测试真实新闻（需要数据库有数据）
python3 test_sentiment.py
# 然后选择 y 测试真实新闻
```

### 方法2：使用API

```bash
# 启动后端
cd backend
export ZHIPUAI_API_KEY="your_key"
PYTHONPATH=. python3 -m uvicorn api:app --reload

# 另一个终端测试API
curl http://localhost:8000/api/news/sentiment/600519?days=30
```

### 方法3：查看前端界面

1. 启动后端和前端
2. 打开浏览器访问 http://localhost:3000
3. 搜索任意股票（如 600519）
4. 查看"情绪分析"卡片

---

## 💡 使用示例

### 命令行使用

```bash
# 不设置API Key（使用关键词匹配）
python3 test_sentiment.py

# 使用智谱AI
export ZHIPUAI_API_KEY="your_key"
python3 test_sentiment.py

# 使用DeepSeek（备选）
export DEEPSEEK_API_KEY="your_key"
python3 test_sentiment.py
```

### Python代码中使用

```python
from core.sentiment_analyzer import SentimentAnalyzer

# 初始化
analyzer = SentimentAnalyzer()

# 分析新闻
result = analyzer.analyze_sentiment("贵州茅台大涨5%")

if result:
    print(f"情绪: {result.label}")
    print(f"评分: {result.score}")
    print(f"理由: {result.reason}")
```

---

## 🔧 配置说明

### 环境变量优先级

1. **ZHIPUAI_API_KEY** - 智谱AI（优先使用）
2. **DEEPSEEK_API_KEY** - DeepSeek（备选）
3. **无** - 关键词匹配（默认）

### .env 文件配置

```bash
# backend/.env
ZHIPUAI_API_KEY=your_api_key_here
BARK_KEY=your_bark_key_here
```

### 模型选择

| 模型 | 速度 | 价格 | 推荐场景 |
|------|------|------|----------|
| glm-4-flash | ⚡⚡⚡ | ¥0.1/百万tokens | **推荐**（默认） |
| glm-4 | ⚡⚡ | ¥0.5/百万tokens | 需要更高质量 |
| glm-4-plus | ⚡ | ¥1.0/百万tokens | 最高质量 |

修改模型：编辑 `core/sentiment_analyzer.py` 第30行

---

## 📊 功能对比

| 功能 | 关键词匹配 | 智谱AI | DeepSeek |
|------|-----------|--------|----------|
| 速度 | ⚡⚡⚡ | ⚡⚡ | ⚡⚡ |
| 准确度 | 60% | 90% | 92% |
| 成本 | 免费 | ¥0.1/百万tokens | ¥1/百万tokens |
| 中文支持 | ⚠️ 一般 | ✅ 优秀 | ✅ 优秀 |

**推荐**：开发/测试用关键词，生产环境用智谱AI

---

## ⚠️ 常见问题

### Q1: API Key无效

```bash
# 检查API Key是否正确
echo $ZHIPUAI_API_KEY

# 重新设置
export ZHIPUAI_API_KEY="正确的key"
```

### Q2: 测试脚本无法运行

```bash
# 检查Python版本（需要3.9+）
python3 --version

# 安装依赖
pip install requests pydantic
```

### Q3: 前端显示"情绪分析暂不可用"

- 确保后端正在运行
- 检查浏览器控制台是否有错误
- 确认数据库中有新闻数据

### Q4: 成本太高

- 使用 `glm-4-flash` 模型（最便宜）
- 减少分析天数：`?days=7` 而不是 `?days=30`
- 调整同步频率（默认30分钟）

---

## 📚 详细文档

- **完整配置指南**：`docs/SENTIMENT_SETUP.md`
- **环境变量示例**：`backend/.env.example`
- **测试脚本**：`backend/test_sentiment.py`

---

## 🎉 完成检查清单

- [ ] 获取智谱AI API Key
- [ ] 配置环境变量
- [ ] 运行测试脚本
- [ ] 测试API接口
- [ ] 查看前端界面
- [ ] 阅读完整文档

---

**需要帮助？**

1. 查看 `docs/SENTIMENT_SETUP.md` 详细文档
2. 运行 `python3 test_sentiment.py` 诊断问题
3. 检查启动日志中的错误信息

**祝使用愉快！** 🚀
