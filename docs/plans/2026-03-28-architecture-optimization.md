# AlphaVision 架构优化实施计划

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 将单体 api.py (1463行) 拆分为模块化路由架构，统一错误处理，优化前端状态管理和包体积，添加核心测试。

**Architecture:** 后端按领域拆分为 FastAPI Router 模块（scan/paper/market/sync/stock/settings），统一错误响应格式；前端引入 Zustand 状态管理，移除冗余依赖，拆分大组件。

**Tech Stack:** FastAPI 0.111, SQLAlchemy 2.0, Pydantic, Next.js 16, React 19, Zustand, Tailwind CSS 4

---

## Phase 1: 后端模块化拆分 (P0)

### Task 1: 创建 Pydantic Schemas 模块

**Files:**
- Create: `backend/schemas/__init__.py`
- Create: `backend/schemas/paper_trade.py`
- Create: `backend/schemas/scan.py`
- Create: `backend/schemas/settings.py`

**Step 1: 创建 schemas 目录和基础结构**

```python
# backend/schemas/__init__.py
from .paper_trade import PaperTradeCreate
from .scan import ScanParams
from .settings import SettingsUpdate

__all__ = ["PaperTradeCreate", "ScanParams", "SettingsUpdate"]
```

```python
# backend/schemas/paper_trade.py
from pydantic import BaseModel
from typing import Optional

class PaperTradeCreate(BaseModel):
    code: str
    name: str
    price: float
    strategy_type: Optional[str] = None
```

```python
# backend/schemas/scan.py
from pydantic import BaseModel
from typing import Optional

class ScanParams(BaseModel):
    threshold: float = 0.12
    vol_multiplier: float = 1.5
    rsi_min: int = 55
    mkt_cap_min: float = 50.0
    turnover_min: float = 3.0
    strategy: str = "squeeze"
    week_ma_period: int = 60
    score_threshold: float = 60.0
    max_results: int = 50
    use_snapshot: bool = True
```

```python
# backend/schemas/settings.py
from pydantic import BaseModel
from typing import Optional, Dict, Any

class SettingsUpdate(BaseModel):
    settings: Dict[str, Any]
```

**Step 2: 验证语法**

Run: `cd backend && python -c "from schemas import PaperTradeCreate, ScanParams, SettingsUpdate; print('OK')"`
Expected: `OK`

**Step 3: Commit**

```bash
git add backend/schemas/
git commit -m "refactor: extract Pydantic schemas into dedicated module"
```

---

### Task 2: 创建统一错误处理

**Files:**
- Create: `backend/core/errors.py`

**Step 1: 编写统一错误处理模块**

```python
# backend/core/errors.py
from fastapi import HTTPException
from typing import Any, Dict, Optional

class AppError(Exception):
    """Application-level error with structured response."""
    def __init__(self, code: str, message: str, status: int = 400, detail: Any = None):
        self.code = code
        self.message = message
        self.status = status
        self.detail = detail
        super().__init__(message)

def error_response(code: str, message: str, detail: Any = None) -> Dict[str, Any]:
    """Standard error response format."""
    resp = {"status": "error", "code": code, "message": message}
    if detail is not None:
        resp["detail"] = detail
    return resp

def success_response(data: Any = None, message: str = "success") -> Dict[str, Any]:
    """Standard success response format."""
    resp = {"status": "success", "message": message}
    if data is not None:
        resp["data"] = data
    return resp
```

**Step 2: 验证语法**

Run: `cd backend && python -c "from core.errors import AppError, error_response, success_response; print(success_response({'test': 1}))"`
Expected: `{'status': 'success', 'message': 'success', 'data': {'test': 1}}`

**Step 3: Commit**

```bash
git add backend/core/errors.py
git commit -m "refactor: add unified error/success response helpers"
```

---

### Task 3: 提取市场行情路由 (market router)

**Files:**
- Create: `backend/routers/__init__.py`
- Create: `backend/routers/market.py`
- Modify: `backend/api.py` (移除对应端点，改为注册路由)

**Step 1: 创建 routers 目录**

```python
# backend/routers/__init__.py
# empty
```

**Step 2: 提取 market 路由**

从 api.py 提取以下端点到 `backend/routers/market.py`：
- `GET /api/health` (line 244)
- `GET /api/market/indices` (line 248)
- `GET /api/market/sectors` (line 255)
- `fetch_mine_sweeper_data()` 函数 (line 100)
- `_mine_sweeper_cache` 全局变量 (line 97)

```python
# backend/routers/market.py
from fastapi import APIRouter
from typing import Dict, Any
from datetime import datetime

from core.data import get_market_snapshot, get_index_data, get_hot_sectors
from core.config import config
from core.logging_config import logger

router = APIRouter(prefix="/api", tags=["market"])

_mine_sweeper_cache = {"data": None, "timestamp": 0}

def fetch_mine_sweeper_data():
    # ... 从 api.py 原样搬过来 (lines 100-168)
    pass

@router.get("/health")
def health_check() -> Dict[str, Any]:
    return {"status": "ok", "timestamp": datetime.now().isoformat()}

@router.get("/market/indices")
def get_indices() -> Dict[str, Any]:
    # ... 从 api.py 原样搬过来 (lines 248-254)
    pass

@router.get("/market/sectors")
def get_sectors() -> Dict[str, Any]:
    # ... 从 api.py 原样搬过来 (lines 255-376)
    pass
```

**Step 3: 在 api.py 中注册路由，删除原端点**

在 api.py 中：
```python
from routers.market import router as market_router
app.include_router(market_router)
```

删除 api.py 中的对应函数和装饰器。

**Step 4: 启动验证**

Run: `cd backend && python -c "from api import app; routes = [r.path for r in app.routes]; print(routes)"`
Expected: 包含 `/api/health`, `/api/market/indices`, `/api/market/sectors`

**Step 5: Commit**

```bash
git add backend/routers/
git commit -m "refactor: extract market router (health/indices/sectors)"
```

---

### Task 4: 提取同步路由 (sync router)

**Files:**
- Create: `backend/routers/sync.py`
- Modify: `backend/api.py`

**Step 1: 提取 sync 路由**

从 api.py 提取以下内容到 `backend/routers/sync.py`：
- `sync_progress` 全局状态和 `sync_progress_lock` (lines 41-51)
- `update_sync_progress()` (line 378)
- `background_sync_task()` (line 262)
- `GET /api/sync/status` (line 412)
- `POST /api/sync/stop` (line 391)
- `POST /api/sync/daily` (line 401)

注意: `sync_progress` 是共享全局状态，需要在 `api.py` 中创建并通过 import 共享，或放在独立模块中。

```python
# backend/core/sync_state.py (新文件，管理同步状态)
import threading

sync_progress_lock = threading.Lock()
sync_progress = {
    "is_running": False,
    "total": 0,
    "current": 0,
    "success": 0,
    "fail": 0,
    "start_time": None,
    "status_text": "等待中...",
    "stop_requested": False
}
```

```python
# backend/routers/sync.py
from fastapi import APIRouter, BackgroundTasks
from core.sync_state import sync_progress, sync_progress_lock
# ... 其他逻辑从 api.py 搬过来

router = APIRouter(prefix="/api/sync", tags=["sync"])
```

**Step 2: 在 api.py 中注册路由**

```python
from routers.sync import router as sync_router
app.include_router(sync_router)
```

**Step 3: 启动验证**

Run: `cd backend && python -c "from api import app; print([r.path for r in app.routes if 'sync' in str(r.path)])"`
Expected: 包含 sync 相关路由

**Step 4: Commit**

```bash
git add backend/core/sync_state.py backend/routers/sync.py
git commit -m "refactor: extract sync router and sync state module"
```

---

### Task 5: 提取扫描路由 (scan router) — 最复杂的部分

**Files:**
- Create: `backend/routers/scan.py`
- Modify: `backend/api.py`

**Step 1: 提取 scan 路由**

从 api.py 提取以下内容到 `backend/routers/scan.py`：
- `run_market_scan()` (line 418, 核心扫描函数，约430行)
- `single_stock_task()` (line 851, 单股分析，约130行)
- `GET /api/scan` (line 1164)
- `GET /api/scan/history` (line 1146)
- `GET /api/scan/dates` (line 1151)
- `GET /api/scan/available-dates` (line 1157)

这是最大的一块，约 560 行代码。`run_market_scan` 和 `single_stock_task` 作为内部函数放在 scan.py 中。

```python
# backend/routers/scan.py
from fastapi import APIRouter, Query
from typing import Optional

router = APIRouter(prefix="/api", tags=["scan"])

def run_market_scan(...):
    # 从 api.py 搬过来

def single_stock_task(...):
    # 从 api.py 搬过来

@router.get("/scan")
def scan_market(...):
    # 从 api.py 搬过来

@router.get("/scan/history")
def get_history_results(...):
    # 从 api.py 搬过来

# ... 其他 scan 端点
```

**Step 2: 注册路由**

```python
from routers.scan import router as scan_router
app.include_router(scan_router)
```

**Step 3: 启动验证 + 功能测试**

Run: `cd backend && python -c "from api import app; print('OK')"`

手动测试: 启动服务器，从前端发起扫描请求验证正常工作。

**Step 4: Commit**

```bash
git add backend/routers/scan.py
git commit -m "refactor: extract scan router (core scanning logic)"
```

---

### Task 6: 提取纸上交易路由 (paper trade router)

**Files:**
- Create: `backend/routers/paper_trade.py`
- Modify: `backend/api.py`

**Step 1: 提取 paper trade 路由**

从 api.py 提取以下内容到 `backend/routers/paper_trade.py`：
- `GET /api/paper/list` (line 1243, 含实时盈亏计算，约90行)
- `POST /api/paper/add` (line 1214)
- `POST /api/paper/close/{id}` (line 1411)
- `DELETE /api/paper/remove/{id}` (line 1395)

```python
# backend/routers/paper_trade.py
from fastapi import APIRouter, HTTPException
from schemas.paper_trade import PaperTradeCreate

router = APIRouter(prefix="/api/paper", tags=["paper-trading"])

@router.post("/add")
def add_paper_trade(trade: PaperTradeCreate):
    # ...

@router.get("/list")
def list_paper_trades():
    # ...

@router.post("/close/{id}")
def close_paper_trade(id: int, data: dict):
    # ...

@router.delete("/remove/{id}")
def remove_paper_trade(id: int):
    # ...
```

**Step 2: 注册路由**

**Step 3: Commit**

```bash
git add backend/routers/paper_trade.py
git commit -m "refactor: extract paper trading router"
```

---

### Task 7: 提取个股和设置路由

**Files:**
- Create: `backend/routers/stock.py`
- Create: `backend/routers/settings.py`
- Modify: `backend/api.py`

**Step 1: 提取 stock 路由**

```python
# backend/routers/stock.py
# 包含:
# GET /api/stock/{code}/kline (line 988)
# GET /api/stock/detail (line 1088)
```

**Step 2: 提取 settings 路由**

```python
# backend/routers/settings.py
# 包含:
# GET /api/settings (line 1202)
# POST /api/settings (line 1207)
# GET /api/test/push (line 1447)
```

**Step 3: Commit**

```bash
git add backend/routers/stock.py backend/routers/settings.py
git commit -m "refactor: extract stock and settings routers"
```

---

### Task 8: 清理 api.py 为精简入口

**Files:**
- Modify: `backend/api.py` (最终精简为 ~100行)

**Step 1: 确认 api.py 仅包含**

```python
# api.py 最终形态 (~100行)
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager

from core.config import config
from core.logging_config import logger
from core.db import init_db
from core.sync_state import sync_progress  # 共享状态

# 路由导入
from routers.market import router as market_router
from routers.sync import router as sync_router
from routers.scan import router as scan_router
from routers.paper_trade import router as paper_router
from routers.stock import router as stock_router
from routers.settings import router as settings_router

# IntradaySentinel 留在 api.py 或提取到 core/sentinel.py
# ...

@asynccontextmanager
async def lifespan(app: FastAPI):
    config.setup_no_proxy()
    init_db()
    # sentinel.start()
    # pre-warm caches
    yield

app = FastAPI(title="Alpha Vision API", version="5.2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=config.ALLOWED_ORIGINS, ...)

# 注册路由
app.include_router(market_router)
app.include_router(sync_router)
app.include_router(scan_router)
app.include_router(paper_router)
app.include_router(stock_router)
app.include_router(settings_router)

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

**Step 2: 全功能冒烟测试**

Run: `cd backend && python -c "from api import app; routes = sorted([r.path for r in app.routes if hasattr(r, 'path')]); print(f'{len(routes)} routes loaded'); print('\n'.join(routes))"`
Expected: 所有 19 个端点全部正确注册

**Step 3: Commit**

```bash
git add backend/api.py
git commit -m "refactor: slim api.py to router registry (~100 lines)"
```

---

## Phase 2: 数据库优化 (P1)

### Task 9: 添加数据库索引

**Files:**
- Modify: `backend/core/db.py` (在 init_db 中添加索引)

**Step 1: 在 init_db 中添加索引创建**

```python
# 在 init_db() 的 conn.commit() 前添加:
# 性能索引
conn.execute(text("CREATE INDEX IF NOT EXISTS idx_daily_k_date ON daily_k(date);"))
conn.execute(text("CREATE INDEX IF NOT EXISTS idx_daily_k_code ON daily_k(code);"))
conn.execute(text("CREATE INDEX IF NOT EXISTS idx_scan_history_date ON scan_history(date);"))
conn.execute(text("CREATE INDEX IF NOT EXISTS idx_paper_trading_status ON paper_trading(status);"))
conn.execute(text("CREATE INDEX IF NOT EXISTS idx_stock_basic_industry ON stock_basic(industry);"))
```

**Step 2: 启动验证**

Run: `cd backend && python -c "from core.db import init_db; init_db(); print('Indexes created')"`
Expected: 无报错

**Step 3: Commit**

```bash
git add backend/core/db.py
git commit -m "perf: add database indexes for daily_k, scan_history, paper_trading"
```

---

### Task 10: 批量更新纸上交易价格

**Files:**
- Modify: `backend/routers/paper_trade.py` (原 api.py 中的 list_paper_trades)

**Step 1: 将逐条 UPDATE 改为批量**

```python
# 在 list_paper_trades 中，收集所有需要更新的 (id, price) 对
updates = []
for _, row in df.iterrows():
    # ... 现有逻辑 ...
    if status == 'OPEN' and current_price != row.get('current_price'):
        updates.append({"id": int(row['id']), "price": current_price})

# 批量更新
if updates:
    with engine.connect() as conn:
        for u in updates:
            conn.execute(text("UPDATE paper_trading SET current_price = :price WHERE id = :id"), u)
        conn.commit()
```

**Step 2: Commit**

```bash
git add backend/routers/paper_trade.py
git commit -m "perf: batch paper trading price updates"
```

---

## Phase 3: 前端优化 (P2)

### Task 11: 引入 Zustand 状态管理

**Files:**
- Modify: `frontend/package.json` (添加 zustand)
- Create: `frontend/src/stores/scanStore.ts`
- Create: `frontend/src/stores/tradeStore.ts`
- Create: `frontend/src/stores/marketStore.ts`

**Step 1: 安装 Zustand**

Run: `cd frontend && npm install zustand`

**Step 2: 创建 scanStore**

```typescript
// frontend/src/stores/scanStore.ts
import { create } from 'zustand';

interface ScanResult {
    代码: string;
    名称: string;
    行业: string;
    现价: number;
    '涨幅%': number;
    Score: number;
    RSI: number;
    DIF: number;
    BB: number;
    粘合度: number;
    历史胜率: number;
    信号次数: number;
    北向: string;
    共振: string;
    影线比: number;
    strategy_type?: string;
    warnings?: string[];
    结构?: string;
    体质?: string;
}

interface ScanStore {
    results: ScanResult[];
    isScanning: boolean;
    selectedStock: ScanResult | null;
    params: Record<string, any>;
    setResults: (results: ScanResult[]) => void;
    setIsScanning: (v: boolean) => void;
    setSelectedStock: (stock: ScanResult | null) => void;
    setParams: (params: Record<string, any>) => void;
}

export const useScanStore = create<ScanStore>((set) => ({
    results: [],
    isScanning: false,
    selectedStock: null,
    params: {},
    setResults: (results) => set({ results }),
    setIsScanning: (isScanning) => set({ isScanning }),
    setSelectedStock: (selectedStock) => set({ selectedStock }),
    setParams: (params) => set({ params }),
}));
```

**Step 3: 创建 tradeStore**

```typescript
// frontend/src/stores/tradeStore.ts
import { create } from 'zustand';
import api from '@/lib/api';

interface Trade {
    id: number;
    code: string;
    name: string;
    entry_price: number;
    current_price: number;
    entry_date: string;
    pl: number;
    pl_pct: number;
    hold_days: number;
    industry: string;
    status: string;
    close_price?: number;
    close_date?: string;
}

interface TradeStore {
    trades: Trade[];
    loading: boolean;
    stats: Record<string, any>;
    tab: 'open' | 'closed';
    fetchTrades: (showRefresh?: boolean) => Promise<void>;
    setTab: (tab: 'open' | 'closed') => void;
    removeTrade: (id: number) => Promise<void>;
    closeTrade: (id: number, closePrice: number) => Promise<void>;
}

export const useTradeStore = create<TradeStore>((set, get) => ({
    trades: [],
    loading: true,
    stats: {
        total_trades: 0, wins: 0, losses: 0, flat: 0,
        win_rate: 0, avg_pl_pct: 0, total_pl_pct: 0, avg_hold_days: 0,
        best_trade: null, worst_trade: null, sector_distribution: [],
    },
    tab: 'open',
    fetchTrades: async (showRefresh = false) => {
        if (showRefresh) set({ loading: true });
        try {
            const res = await api.get('/api/paper/list');
            set({ trades: res.data.trades || [], stats: res.data.stats || get().stats, loading: false });
        } catch { set({ loading: false }); }
    },
    setTab: (tab) => set({ tab }),
    removeTrade: async (id) => {
        await api.delete(`/api/paper/remove/${id}`);
        get().fetchTrades();
    },
    closeTrade: async (id, closePrice) => {
        await api.post(`/api/paper/close/${id}`, { close_price: closePrice });
        get().fetchTrades();
    },
}));
```

**Step 4: Commit**

```bash
git add frontend/src/stores/ frontend/package.json frontend/package-lock.json
git commit -m "feat: add Zustand stores for scan, trade, and market state"
```

---

### Task 12: 重构 Dashboard 使用 Zustand

**Files:**
- Modify: `frontend/src/components/Dashboard.tsx`

**Step 1: 替换 useState 为 useScanStore**

将 Dashboard.tsx 中的 `results`, `isScanning`, `selectedStock`, `params` 替换为 store 调用。保留仅属于 Dashboard 本地的状态（如 `activeView`, `indices`, `syncProgress`）。

```typescript
// 替换前:
const [results, setResults] = useState<Result[]>([]);
const [isScanning, setIsScanning] = useState(false);
const [selectedStock, setSelectedStock] = useState<Result | null>(null);

// 替换后:
const { results, isScanning, selectedStock, setResults, setIsScanning, setSelectedStock } = useScanStore();
```

**Step 2: 验证页面正常渲染**

Run: `cd frontend && npm run build`
Expected: 编译成功

**Step 3: Commit**

```bash
git add frontend/src/components/Dashboard.tsx
git commit -m "refactor: Dashboard uses Zustand store instead of local state"
```

---

### Task 13: 重构 PaperTradingView 使用 Zustand

**Files:**
- Modify: `frontend/src/components/PaperTradingView.tsx`

**Step 1: 替换本地状态为 useTradeStore**

```typescript
const { trades, loading, stats, tab, fetchTrades, setTab, removeTrade, closeTrade } = useTradeStore();
// 移除本地 useState: trades, loading, stats, tab
// 保留: closingId, closePrice (仅UI交互状态)
```

**Step 2: 简化组件，移除内联 API 调用**

**Step 3: Commit**

```bash
git add frontend/src/components/PaperTradingView.tsx
git commit -m "refactor: PaperTradingView uses Zustand trade store"
```

---

### Task 14: 移除 recharts 冗余依赖

**Files:**
- Modify: `frontend/package.json`

**Step 1: 检查 recharts 实际使用**

Run: `cd frontend && grep -r "recharts" src/ || echo "Not used"`
如果确认未使用，执行移除。

**Step 2: 移除 recharts**

Run: `cd frontend && npm uninstall recharts`

**Step 3: Commit**

```bash
git add frontend/package.json frontend/package-lock.json
git commit -m "chore: remove unused recharts dependency"
```

---

### Task 15: KLineChart 懒加载

**Files:**
- Modify: `frontend/src/components/ResultsTable.tsx`

**Step 1: Dynamic import KLineChart**

```typescript
// 替换:
import KLineChart from '@/components/KLineChart';

// 改为:
import dynamic from 'next/dynamic';
const KLineChart = dynamic(() => import('@/components/KLineChart'), { ssr: false });
```

**Step 2: Commit**

```bash
git add frontend/src/components/ResultsTable.tsx
git commit -m "perf: lazy load KLineChart component"
```

---

## Phase 4: 清理与配置统一 (P3)

### Task 16: 配置管理统一 — Pydantic Settings

**Files:**
- Create: `backend/core/settings.py`
- Modify: `backend/core/config.py`

**Step 1: 用 Pydantic BaseSettings 替换手动 env 读取**

```python
# backend/core/settings.py
from pydantic_settings import BaseSettings
from typing import List

class AppSettings(BaseSettings):
    # Database
    DB_HOST: str = "localhost"
    DB_PORT: str = "5432"
    DB_USER: str = "liangzou"
    DB_PASSWORD: str = ""
    DB_NAME: str = "stock_db"
    DATABASE_URL: str | None = None

    # API
    API_TOKEN: str | None = None
    ENABLE_AUTH: bool = False
    ALLOWED_ORIGINS: List[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    # Rate Limit
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_SCAN: str = "10/minute"
    RATE_LIMIT_SYNC: str = "1/hour"

    # Notifications
    BARK_KEY: str = ""

    # Scanning Defaults
    DEFAULT_THRESHOLD: float = 0.12
    DEFAULT_VOL_MULTIPLIER: float = 1.5
    DEFAULT_RSI_MIN: int = 55

    # Performance
    AKSHARE_TIMEOUT: int = 30
    MAX_WORKERS: int = 15
    TUSHARE_TOKEN: str = ""

    # Misc
    DISABLE_PROXY: bool = True
    ENVIRONMENT: str = "development"
    DEBUG: bool = True
    SENTINEL_DEFAULT_TIME: str = "14:20"

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

settings = AppSettings()
```

**Step 2: 保留 config.py 兼容层（逐步迁移）**

```python
# backend/core/config.py
from core.settings import settings

class config:
    """Compatibility wrapper - delegates to Pydantic settings."""
    DB_HOST = settings.DB_HOST
    DB_PORT = settings.DB_PORT
    # ... 所有字段代理到 settings
```

**Step 3: Commit**

```bash
pip install pydantic-settings  # 添加到 requirements.txt
git add backend/core/settings.py backend/core/config.py backend/requirements.txt
git commit -m "refactor: introduce Pydantic Settings for config validation"
```

---

### Task 17: 清理调试文件 + gitignore

**Files:**
- Modify: `.gitignore`
- Delete: `backend/debug_*.py`, `backend/test_*.py`, `backend/query_vol.py`, `backend/reset_daily_k.py`, `backend/check_db_sectors.py`, `backend/repair_sectors.py`

**Step 1: 更新 .gitignore**

```
# Python
__pycache__/
*.pyc
*.pyo
.env

# Debug scripts
backend/debug_*
backend/test_*
backend/query_*.py
backend/reset_*.py
backend/check_*.py
backend/repair_*.py
backend/verify_schema.py
```

**Step 2: 从 git 追踪中移除**

```bash
git rm --cached backend/core/__pycache__/
git rm backend/debug_*.py backend/test_*.py
```

**Step 3: Commit**

```bash
git add .gitignore
git commit -m "chore: clean up debug scripts, update .gitignore"
```

---

### Task 18: 添加 ruff lint + format

**Files:**
- Create: `backend/pyproject.toml` (ruff config)
- Modify: `backend/requirements.txt`

**Step 1: 配置 ruff**

```toml
# backend/pyproject.toml
[tool.ruff]
line-length = 120
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "W", "I"]
ignore = ["E501"]
```

**Step 2: 添加到 requirements.txt**

```
ruff>=0.3.0
```

**Step 3: Commit**

```bash
git add backend/pyproject.toml backend/requirements.txt
git commit -m "chore: add ruff linter configuration"
```

---

## 验证清单

所有 Task 完成后，运行以下验证：

- [ ] `cd backend && python -c "from api import app; print('OK')"` — 后端启动无报错
- [ ] `cd frontend && npm run build` — 前端编译无报错
- [ ] 后端所有 19 个 API 端点可访问
- [ ] 前端扫描、纸上交易、设置页面功能正常
- [ ] `ruff check backend/` 无严重告警
- [ ] 数据库索引已创建，查询性能可验证

---

## 实施顺序总结

| # | Task | 影响范围 | 风险 |
|---|------|---------|------|
| 1 | Pydantic Schemas | 新文件 | 低 |
| 2 | 统一错误处理 | 新文件 | 低 |
| 3 | Market Router | api.py → routers/market.py | 中 |
| 4 | Sync Router | api.py → routers/sync.py | 中 |
| 5 | Scan Router | api.py → routers/scan.py | 中(最大) |
| 6 | Paper Trade Router | api.py → routers/paper_trade.py | 中 |
| 7 | Stock + Settings Router | api.py → routers/ | 中 |
| 8 | 精简 api.py | api.py | 低(验证用) |
| 9 | 数据库索引 | db.py | 低 |
| 10 | 批量价格更新 | paper_trade router | 低 |
| 11 | Zustand Stores | 新文件 | 低 |
| 12 | Dashboard 重构 | Dashboard.tsx | 中 |
| 13 | PaperTrading 重构 | PaperTradingView.tsx | 中 |
| 14 | 移除 recharts | package.json | 低 |
| 15 | KLine 懒加载 | ResultsTable.tsx | 低 |
| 16 | Pydantic Settings | config 重构 | 中 |
| 17 | 清理调试文件 | .gitignore + 删除文件 | 低 |
| 18 | Ruff lint | pyproject.toml | 低 |
