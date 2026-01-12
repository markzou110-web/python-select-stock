# 技术规范文档

**项目**: Alpha Vision v6.0
**最后更新**: 2025-01-12

---

## 1. 非功能性需求 (Non-Functional Requirements)

### 1.1 性能要求 (Performance)

#### 1.1.1 响应时间

| 操作 | 目标 | 最大可接受 | 测量方法 |
|------|------|------------|----------|
| 市场扫描 | < 10秒 | < 30秒 | 从点击扫描到结果渲染 |
| 数据同步 (全量) | < 30分钟 | < 60分钟 | 5000只股票 |
| 数据同步 (增量) | < 5分钟 | < 15分钟 | 仅更新最新数据 |
| API 响应 (P50) | < 500ms | < 1秒 | 50分位响应时间 |
| API 响应 (P95) | < 2秒 | < 5秒 | 95分位响应时间 |
| 历史回测计算 | < 2秒 | < 5秒 | 单只股票1年数据 |
| 页面首次加载 | < 3秒 | < 5秒 | FCP (First Contentful Paint) |
| 路由切换 | < 500ms | < 1秒 | SPA 内部导航 |

#### 1.1.2 吞吐量

| 指标 | 目标值 | 说明 |
|------|--------|------|
| 并发用户 | 5人 | 单机部署场景 |
| API QPS | 10 req/s | 每秒处理请求数 |
| 数据库连接池 | 20 连接 | SQLAlchemy pool_size |
| 扫描并发 | 10 线程 | ThreadPoolExecutor |

#### 1.1.3 资源使用

| 资源 | 正常 | 最大 | 限制 |
|------|------|------|------|
| 内存 (后端) | < 500MB | < 2GB | Python 进程 |
| 内存 (前端) | < 100MB | < 200MB | 浏览器标签页 |
| CPU (后端) | < 20% | < 80% | 扫描时峰值 |
| 磁盘 I/O | < 10MB/s | < 50MB/s | 数据同步时 |
| 数据库存储 | 约 200MB/年 | - | 5000只股票 |

---

### 1.2 可靠性要求 (Reliability)

#### 1.2.1 可用性

| 指标 | 目标值 | 测量方式 |
|------|--------|----------|
| 系统可用性 | 99% | (交易日 9:00-15:00) |
| 数据同步成功率 | > 99% | 失败自动重试 |
| 服务崩溃率 | < 1% | 每月 |
| 数据准确性 | 99.9% | 与 akshare 源数据对比 |

#### 1.2.2 容错机制

**数据同步容错**:
- 网络超时: 自动重试 3次，指数退避
- API 限流: 请求限流，延迟后重试
- 数据损坏: 跳过异常数据，记录日志

**扫描容错**:
- 数据缺失: 跳过该股票，继续扫描
- 计算错误: 返回默认值，记录错误
- 数据库连接失败: 返回友好错误提示

**前端容错**:
- API 失败: 显示错误提示，自动重试
- 数据异常: 显示占位符或空状态
- 网络中断: 显示离线提示

#### 1.2.3 数据备份

| 备份类型 | 频率 | 保留期 | 位置 |
|----------|------|--------|------|
| 数据库全量备份 | 每周 | 1个月 | 本地文件 |
| 配置文件备份 | 每次修改 | 永久 | Git 版本控制 |
| 扫描结果备份 | 每日 | 1年 | 数据库表 |

---

### 1.3 可扩展性要求 (Scalability)

#### 1.3.1 横向扩展
- **当前设计**: 单机部署
- **未来扩展**:
  - 数据库: 支持读写分离
  - 缓存: 引入 Redis
  - 后端: 支持多实例负载均衡

#### 1.3.2 纵向扩展
- **数据库**: 支持更多指标列 (ALTER TABLE)
- **策略**: 支持动态加载自定义策略
- **前端**: 支持插件式组件扩展

---

### 1.4 可维护性要求 (Maintainability)

#### 1.4.1 代码质量

| 指标 | 目标值 | 工具 |
|------|--------|------|
| 代码覆盖率 | > 60% | pytest, Jest |
| 代码复杂度 | < 10 | cyclomatic complexity |
| 代码重复率 | < 5% | sonar-scanner |
| 函数行数 | < 50行 | - |
| 文件行数 | < 500行 | - |

#### 1.4.2 文档要求

| 文档类型 | 必需性 | 更新频率 |
|----------|--------|----------|
| API 文档 | 必需 | 自动生成 (Swagger) |
| 代码注释 | 推荐 | 随代码更新 |
| README | 必需 | 每个版本 |
| 变更日志 | 必需 | 每次发布 |

#### 1.4.3 日志规范

**日志级别**:
```python
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# 使用示例
logger.info("开始同步数据: total=%d", total)
logger.warning("股票 %s 数据异常", code)
logger.error("同步失败: %s", str(e))
```

**日志内容**:
- 时间戳
- 日志级别
- 模块名称
- 关键参数
- 错误堆栈 (ERROR 级别)

---

### 1.5 安全性要求 (Security)

#### 1.5.1 数据安全

| 数据类型 | 保护措施 |
|----------|----------|
| 数据库密码 | 本地存储，不提交到 Git (.gitignore) |
| API 密钥 | 环境变量或配置文件 |
| 用户输入 | 参数化查询，防止 SQL 注入 |
| 输出数据 | 清理敏感信息 (NaN/Inf 处理) |

#### 1.5.2 输入验证

**API 输入**:
```python
from pydantic import BaseModel, Field, validator

class ScanRequest(BaseModel):
    threshold: float = Field(gt=0, le=1, default=0.12)
    vol_multiplier: float = Field(gt=0, le=10, default=1.5)
    rsi_min: int = Field(ge=0, le=100, default=55)

    @validator('threshold')
    def validate_threshold(cls, v):
        if v <= 0 or v > 1:
            raise ValueError('threshold must be between 0 and 1')
        return v
```

**前端输入**:
- 用户输入转义 (防止 XSS)
- 文件上传限制 (类型、大小)
- URL 参数验证

#### 1.5.3 网络安全

| 措施 | 状态 |
|------|------|
| HTTPS (生产环境) | 推荐 |
| CORS 配置 | ✅ 已配置 |
| 请求限流 | ⚠️ 待实现 |
| CSRF 保护 | N/A (无用户认证) |

---

### 1.6 兼容性要求 (Compatibility)

#### 1.6.1 浏览器兼容

| 浏览器 | 最低版本 | 支持状态 |
|--------|----------|----------|
| Chrome | 90+ | ✅ 完全支持 |
| Firefox | 88+ | ✅ 完全支持 |
| Safari | 14+ | ✅ 完全支持 |
| Edge | 90+ | ✅ 完全支持 |
| IE 11 | - | ❌ 不支持 |

#### 1.6.2 操作系统兼容

| OS | 最低版本 | 支持状态 |
|----|----------|----------|
| macOS | 10.15+ | ✅ 完全支持 |
| Ubuntu | 20.04+ | ✅ 完全支持 |
| Windows | 10+ | ✅ 完全支持 |

#### 1.6.3 Python 版本

| Python 版本 | 支持状态 |
|-------------|----------|
| 3.9 | ✅ 推荐 |
| 3.10 | ✅ 支持 |
| 3.11 | ✅ 支持 |
| 3.12 | ⚠️ 待测试 |
| 3.8 及以下 | ❌ 不支持 |

#### 1.6.4 Node.js 版本

| Node 版本 | 支持状态 |
|-----------|----------|
| 18.x | ✅ 推荐 |
| 20.x | ✅ 支持 |
| 16.x | ⚠️ 可能兼容 |
| 15.x 及以下 | ❌ 不支持 |

---

### 1.7 可用性要求 (Usability)

#### 1.7.1 用户界面

| 原则 | 说明 |
|------|------|
| 响应式设计 | 适配桌面 (1920×1080) 和移动 (375×667) |
| 加载反馈 | 所有操作 > 500ms 显示加载状态 |
| 错误提示 | 友好的中文错误信息 |
| 快捷键 | 支持常用快捷键 (空格扫描等) |
| 无障碍 | 支持键盘导航，语义化 HTML |

#### 1.7.2 学习曲线

| 任务 | 预计时间 | 说明 |
|------|----------|------|
| 首次安装 | 30分钟 | 包括环境配置和数据同步 |
| 第一次扫描 | 5分钟 | 了解基本操作 |
| 参数调优 | 1小时 | 理解各参数含义 |
| 自定义策略 | 2小时 | (未来功能) |

---

## 2. 技术架构

### 2.1 系统架构图

```
┌─────────────────────────────────────────────────────────────┐
│                         用户层                               │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐      │
│  │  Browser     │  │  Browser     │  │  Browser     │      │
│  │  (Desktop)   │  │  (Mobile)    │  │  (Tablet)    │      │
│  └──────┬───────┘  └──────┬───────┘  └──────┬───────┘      │
└─────────┼──────────────────┼──────────────────┼─────────────┘
          │                  │                  │
          └──────────────────┼──────────────────┘
                             │ HTTP/REST
                    ┌────────▼────────┐
                    │   Next.js App   │
                    │  (Frontend)     │
                    │  - React 19     │
                    │  - TypeScript   │
                    │  - Tailwind CSS │
                    └────────┬────────┘
                             │ API Call
                    ┌────────▼────────┐
                    │   FastAPI App   │
                    │  (Backend)      │
                    │  - Python 3.9+  │
                    │  - SQLAlchemy   │
                    └────────┬────────┘
                             │
        ┌────────────────────┼────────────────────┐
        │                    │                    │
┌───────▼───────┐   ┌────────▼────────┐   ┌──────▼──────┐
│  PostgreSQL   │   │  akshare API    │   │  File System│
│  (Database)   │   │  (Data Source)  │   │  (Logs)     │
└───────────────┘   └─────────────────┘   └─────────────┘
```

### 2.2 数据流图

#### 2.2.1 数据同步流程

```
┌─────────────┐
│   User      │ 点击 "同步"
└──────┬──────┘
       │
       ▼
┌─────────────────┐
│  Frontend       │ POST /api/sync/daily
└──────┬──────────┘
       │
       ▼
┌─────────────────┐
│  FastAPI        │ 创建后台任务
│  BackgroundTasks│
└──────┬──────────┘
       │
       ▼
┌─────────────────┐
│  sync_data.py   │
│  - 查询本地最新日期│
│  - 调用 akshare  │
│  - 并发下载      │
└──────┬──────────┘
       │
       ▼
┌─────────────────┐
│  PostgreSQL     │
│  daily_k 表     │
└─────────────────┘
```

#### 2.2.2 扫描流程

```
┌─────────────┐
│   User      │ 设置参数 → 点击 "扫描"
└──────┬──────┘
       │
       ▼
┌─────────────────┐
│  Frontend       │ GET /api/scan?params=...
└──────┬──────────┘
       │
       ▼
┌─────────────────┐
│  FastAPI        │
│  /api/scan      │
└──────┬──────────┘
       │
       ├─→ ┌─────────────────┐
       │   │  Strategy Engine │
       │   │  - check_strategy()│
       │   │  - 并发扫描       │
       │   └────────┬─────────┘
       │            │
       └────────────┤
                    ▼
           ┌─────────────────┐
           │  PostgreSQL     │
           │  daily_k 表     │
           └─────────────────┘
                    │
                    ▼
           ┌─────────────────┐
           │  计算结果        │
           │  - 评分          │
           │  - 历史胜率      │
           └────────┬─────────┘
                    │
                    ▼
           ┌─────────────────┐
           │  JSON Response  │
           └─────────────────┘
```

---

### 2.3 数据库设计

#### 2.3.1 表结构

**stock_basic (股票基础信息)**
```sql
CREATE TABLE stock_basic (
    code VARCHAR(20) PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    industry VARCHAR(50),
    market_cap BIGINT,
    list_date DATE,
    created_at TIMESTAMP DEFAULT NOW(),
    updated_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_stock_basic_industry ON stock_basic(industry);
```

**daily_k (日线数据)**
```sql
CREATE TABLE daily_k (
    code VARCHAR(20) NOT NULL,
    date DATE NOT NULL,
    open FLOAT,
    high FLOAT,
    low FLOAT,
    close FLOAT,
    volume BIGINT,
    amount FLOAT,
    -- 技术指标
    ema5 FLOAT,
    ema10 FLOAT,
    ema20 FLOAT,
    ema60 FLOAT,
    rsi FLOAT,
    macd_dif FLOAT,
    macd_dea FLOAT,
    macd_bar FLOAT,
    bb_upper FLOAT,
    bb_lower FLOAT,
    bb_width FLOAT,
    vol_ma20 FLOAT,
    PRIMARY KEY (code, date)
);

CREATE INDEX idx_daily_k_date ON daily_k(date);
CREATE INDEX idx_daily_k_code ON daily_k(code);
```

**scan_history (扫描历史)**
```sql
CREATE TABLE scan_history (
    id SERIAL PRIMARY KEY,
    code VARCHAR(20) NOT NULL,
    date DATE NOT NULL,
    score FLOAT,
    close_price FLOAT,
    change_pct FLOAT,
    volume_ratio FLOAT,
    turnover_rate FLOAT,
    rsi FLOAT,
    macd_dif FLOAT,
    bb_width FLOAT,
    squeeze_ratio FLOAT,
    pe_ratio FLOAT,
    sector VARCHAR(50),
    historical_win_rate FLOAT,
    signal_count INTEGER,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_scan_history_date ON scan_history(date);
CREATE INDEX idx_scan_history_code ON scan_history(code);
```

**paper_trading (模拟交易)**
```sql
CREATE TABLE paper_trading (
    id SERIAL PRIMARY KEY,
    code VARCHAR(20) NOT NULL,
    name VARCHAR(100),
    buy_price FLOAT NOT NULL,
    buy_date DATE NOT NULL,
    quantity INTEGER DEFAULT 100,
    notes TEXT,
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE INDEX idx_paper_trading_code ON paper_trading(code);
```

#### 2.3.2 数据字典

| 表名 | 字段名 | 类型 | 说明 | 示例 |
|------|--------|------|------|------|
| stock_basic | code | VARCHAR(20) | 股票代码 | 600519 |
| stock_basic | name | VARCHAR(100) | 股票名称 | 贵州茅台 |
| stock_basic | industry | VARCHAR(50) | 所属行业 | 白酒 |
| daily_k | close | FLOAT | 收盘价 | 1680.50 |
| daily_k | volume_ratio | FLOAT | 量比 | 2.5 |
| daily_k | rsi | FLOAT | RSI指标 | 65.3 |
| scan_history | score | FLOAT | 策略评分 | 85.5 |
| scan_history | win_rate | FLOAT | 历史胜率 | 0.75 |

---

### 2.4 API 设计

#### 2.4.1 RESTful 规范

**基础 URL**: `http://127.0.0.1:8000/api`

**通用响应格式**:
```json
{
  "data": { ... },
  "status": "success",
  "message": "操作成功"
}
```

**错误响应格式**:
```json
{
  "detail": "错误描述",
  "status": "error",
  "error_code": "ERROR_CODE"
}
```

#### 2.4.2 API 端点列表

| 方法 | 路径 | 说明 | 参数 |
|------|------|------|------|
| GET | /health | 健康检查 | - |
| GET | /market/indices | 市场指数 | - |
| GET | /market/sectors | 板块排行 | - |
| GET | /market/snapshot | 市场概览 | - |
| GET | /scan | 执行扫描 | threshold, vol_multiplier, rsi_min, ... |
| GET | /scan/history | 历史结果 | date |
| GET | /scan/dates | 可选日期列表 | - |
| POST | /sync/daily | 触发同步 | - |
| GET | /sync/status | 同步状态 | - |
| GET | /paper-trade | 持仓列表 | - |
| POST | /paper-trade | 创建持仓 | code, name, price |
| DELETE | /paper-trade/{id} | 删除持仓 | - |
| GET | /settings | 获取设置 | - |
| POST | /settings | 保存设置 | key, value |

#### 2.4.3 请求/响应示例

**扫描请求**:
```http
GET /api/scan?threshold=0.12&vol_multiplier=1.5&rsi_min=55
```

**扫描响应**:
```json
{
  "data": [
    {
      "代码": "600519",
      "名称": "贵州茅台",
      "现价": 1680.5,
      "涨幅%": 3.2,
      "量比": 2.1,
      "换手率": 0.5,
      "PE": 35.6,
      "RSI": 62.5,
      "MACD_DIF": 5.2,
      "BB_Width": 0.03,
      "粘合度": 0.08,
      "行业": "白酒",
      "历史胜率": 0.75,
      "信号次数": 8
    }
  ],
  "count": 1
}
```

---

### 2.5 前端架构

#### 2.5.1 组件树

```
App
└── Dashboard
    ├── Header
    │   ├── Logo
    │   ├── Navigation
    │   └── SyncStatus
    ├── Sidebar
    │   ├── FilterPanel
    │   ├── ParamControls
    │   └── QuickActions
    ├── MainContent
    │   ├── MarketCard (×3)
    │   ├── SectorGrid
    │   ├── FilterModal
    │   ├── ResultsTable
    │   │   ├── TableHeader
    │   │   ├── TableBody
    │   │   └── Pagination
    │   └── PaperTradingView
    │       ├── PositionList
    │       ├── AddPositionForm
    │       └── ProfitLossSummary
    └── SettingsView
```

#### 2.5.2 状态管理

**本地状态 (useState)**:
```typescript
// 组件内部状态
const [results, setResults] = useState([]);
const [loading, setLoading] = useState(false);
const [params, setParams] = useState({...});
```

**持久化状态 (localStorage)**:
```typescript
// 参数持久化
localStorage.setItem('scan_params', JSON.stringify(params));
```

**远程状态 (API)**:
```typescript
// 从服务器获取
const response = await api.get('/api/scan', { params });
setResults(response.data);
```

#### 2.5.3 样式规范

**Tailwind CSS 类命名**:
```typescript
// 布局
className="flex items-center justify-between p-4"

// 颜色 (金融风格)
className="text-red-500"  // 涨
className="text-green-500"  // 跌

// 响应式
className="grid grid-cols-1 md:grid-cols-3 gap-4"

// 状态
className="bg-blue-500 hover:bg-blue-600 disabled:opacity-50"
```

---

### 2.6 后端架构

#### 2.6.1 模块结构

```
backend/
├── api.py                 # FastAPI 应用入口
├── core/
│   ├── __init__.py
│   ├── db.py             # 数据库操作
│   ├── data.py           # 数据获取与缓存
│   ├── indicators.py     # 技术指标计算
│   └── strategy.py       # 交易策略逻辑
├── sync_data.py          # 数据同步脚本
├── test_db.py            # 数据库测试
├── requirements.txt      # Python 依赖
└── db_config.json        # 数据库配置
```

#### 2.6.2 依赖注入

```python
from fastapi import Depends
from core.db import get_db_engine

def get_db():
    engine = get_db_engine()
    try:
        yield engine
    finally:
        pass

@app.get("/api/scan")
def scan(engine = Depends(get_db)):
    # 使用 engine
    ...
```

#### 2.6.3 异常处理

```python
from fastapi import HTTPException

try:
    result = check_strategy(df, params)
except Exception as e:
    logger.error(f"Strategy error: {e}")
    raise HTTPException(
        status_code=500,
        detail=f"策略执行失败: {str(e)}"
    )
```

---

### 2.7 部署架构

#### 2.7.1 开发环境

```yaml
# docker-compose.dev.yml
version: '3.8'
services:
  postgres:
    image: postgres:14
    environment:
      POSTGRES_DB: stock_db
      POSTGRES_USER: liangzou
      POSTGRES_PASSWORD: ""
    ports:
      - "5432:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data

  backend:
    build: ./backend
    command: uvicorn api:app --host 0.0.0.0 --port 8000 --reload
    volumes:
      - ./backend:/app
    ports:
      - "8000:8000"
    depends_on:
      - postgres

  frontend:
    build: ./frontend
    command: npm run dev
    volumes:
      - ./frontend:/app
      - /app/node_modules
    ports:
      - "3000:3000"
    environment:
      - NEXT_PUBLIC_API_URL=http://localhost:8000

volumes:
  postgres_data:
```

#### 2.7.2 生产环境 (推荐)

```yaml
# docker-compose.prod.yml
version: '3.8'
services:
  postgres:
    image: postgres:14-alpine
    restart: always
    environment:
      POSTGRES_DB: stock_db
      POSTGRES_USER: ${DB_USER}
      POSTGRES_PASSWORD: ${DB_PASSWORD}
    volumes:
      - postgres_data:/var/lib/postgresql/data
      - ./backups:/backups

  backend:
    build: ./backend
    command: uvicorn api:app --host 0.0.0.0 --port 8000 --workers 4
    restart: always
    environment:
      - DB_HOST=postgres
      - DB_USER=${DB_USER}
      - DB_PASSWORD=${DB_PASSWORD}
    depends_on:
      - postgres

  frontend:
    build: ./frontend
    command: npm start
    restart: always
    environment:
      - NEXT_PUBLIC_API_URL=https://api.yourdomain.com
    ports:
      - "80:3000"

volumes:
  postgres_data:
```

---

## 3. 开发规范

### 3.1 代码风格

#### Python (后端)
```python
# 遵循 PEP 8
# 使用 black 格式化
# 使用 flake8 检查

# 函数命名: snake_case
def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """计算技术指标.

    Args:
        df: 包含OHLCV数据的DataFrame

    Returns:
        添加了技术指标的DataFrame
    """
    pass

# 常量: UPPER_CASE
DEFAULT_THRESHOLD = 0.12
MAX_WORKERS = 10
```

#### TypeScript (前端)
```typescript
// 遵循 ESLint + Prettier
// 组件命名: PascalCase
interface Result {
    code: string;
    name: string;
    price: number;
}

// 函数命名: camelCase
const formatNumber = (num: number): string => {
    return num.toFixed(2);
};

// 常量: UPPER_SNAKE_CASE
const DEFAULT_PAGE_SIZE = 20;
```

### 3.2 Git 规范

#### 分支命名
```
main          # 主分支，生产环境
dev           # 开发分支
feature/xxx   # 功能分支
fix/xxx       # 修复分支
hotfix/xxx    # 紧急修复分支
```

#### Commit 消息
```
feat: 添加K线图表功能
fix: 修复历史胜率计算错误
docs: 更新API文档
refactor: 优化数据库查询
test: 添加单元测试
chore: 更新依赖包版本
```

### 3.3 测试规范

#### 单元测试
```python
# backend/tests/test_strategy.py
import pytest
from core.strategy import check_strategy

def test_check_strategy_with_squeeze():
    # Given
    df = create_test_dataframe(squeeze=True)

    # When
    is_signal, debug = check_strategy(df)

    # Then
    assert is_signal is True
    assert debug['squeeze'] < 0.12
```

#### 集成测试
```typescript
// frontend/tests/components/ResultsTable.test.tsx
import { render, screen } from '@testing-library/react';
import ResultsTable from '@/components/ResultsTable';

test('renders stock results', () => {
    const mockResults = [{ code: '600519', name: '贵州茅台' }];
    render(<ResultsTable results={mockResults} />);
    expect(screen.getByText('贵州茅台')).toBeInTheDocument();
});
```

---

## 4. 性能优化

### 4.1 数据库优化

```sql
-- 添加索引
CREATE INDEX idx_daily_k_code_date ON daily_k(code, date);
CREATE INDEX idx_scan_history_date ON scan_history(date DESC);

-- 查询优化
EXPLAIN ANALYZE
SELECT * FROM daily_k
WHERE code = '600519'
ORDER BY date DESC
LIMIT 120;
```

### 4.2 缓存策略

```python
from functools import lru_cache

@lru_cache(maxsize=128)
def get_stock_basic_info(code: str):
    # 缓存股票基础信息
    return query_database(code)

# 或使用 Redis
import redis
r = redis.Redis()

def get_cached_data(key):
    data = r.get(key)
    if data:
        return json.loads(data)
    # 从数据库查询
    data = query_database()
    r.setex(key, 3600, json.dumps(data))  # 1小时过期
    return data
```

### 4.3 前端优化

```typescript
// 虚拟滚动
import { FixedSizeList } from 'react-window';

<FixedSizeList
    height={600}
    itemCount={results.length}
    itemSize={50}
>
    {({ index, style }) => (
        <div style={style}>
            {results[index].name}
        </div>
    )}
</FixedSizeList>

// 懒加载
import dynamic from 'next/dynamic';

const HeavyChart = dynamic(() => import('./HeavyChart'), {
    loading: () => <p>Loading...</p>,
    ssr: false
});
```

---

**文档版本**: v1.0
**最后更新**: 2025-01-12
**维护者**: Technical Team
