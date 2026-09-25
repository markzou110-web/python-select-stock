# Alpha Vision

A 股全市场量化选股、盯盘与风控分析系统（前后端分离架构）。

- **后端**：FastAPI + Celery + PostgreSQL + Redis（Python 3.12）
- **前端**：Next.js 16 + React 19 + Tailwind CSS 4 + Zustand 5（TypeScript）
- **策略核心**：均线粘合 + 趋势突破（TV / Trend Pullback）多因子共振
- **服务端口**：后端 8000，前端 3000

## 架构速览

```
┌──────────────┐      HTTP / WebSocket       ┌──────────────────────────┐
│   Frontend    │ ◄──────────────────────────► │   Backend (FastAPI)       │
│  Next.js 16   │   /api/*  /api/ws/scan-      │  api.py                   │
│  React 19     │   progress                   │  ├─ routers/ (16 路由)    │
│  Zustand      │                              │  ├─ core/  (策略/数据)    │
└──────────────┘                              │  └─ Sentinel (盘中盯盘)   │
                                              └──────┬─────────┬─────────┘
                                                     │         │
                                          ┌──────────▼──┐   ┌──▼──────────┐
                                          │ PostgreSQL  │   │ Redis+Celery │
                                          │ 行情/交易/   │   │ 3队列 + Beat  │
                                          │ 扫描/复盘    │   │ 定时任务调度  │
                                          └─────────────┘   └──────┬───────┘
                                                                   │
                                          ┌────────────────────────▼───────┐
                                          │ 数据源: AkShare / Sina /       │
                                          │ Tencent / Tushare / BaoStock   │
                                          │ + Bark 推送 + OpenAI 兼容 AI   │
                                          └────────────────────────────────┘
```

## 核心功能

| 模块 | 说明 |
|------|------|
| 全市场扫描 | 5000+ A 股标的动态筛选、爆发力评分与 Top 榜 |
| 多因子策略 | Squeeze / Pine / TV(Trend Pullback) / Consensus 多策略评级 |
| 盘中盯盘 | `sentinel.py` 定时价格与风控告警，多周期共振确认 |
| 决策与执行 | 候选证据质量门、买入意图审批（execution_intents）、执行回放审计 |
| 模拟盘 | `paper_trades` 全生命周期：建仓、加仓、止损、复盘 |
| 消息推送 | Bark（iOS）：扫描结果、告警、盘后观察名单、日报/周报 |
| AI 复盘 | OpenAI 兼容接口对候选股做质量评估（可选） |
| 回测与研究 | 回测实验室、K 线回放、ETF 趋势轮动、胜率校准 |
| 运维可观测 | 任务审计、幂等控制、系统健康检查、运营指标 |

## 快速启动

### 前置要求

- macOS / Linux
- Python 3.12（对应 `backend/venv_new`）
- Node.js 18+
- PostgreSQL 12+（必需，所有数据落库于此）
- Redis（可选；未启动时 Celery 自动降级为同步执行模式）

### 1. 配置环境变量

```bash
cd backend
cp .env.example .env
# 编辑 .env：DB_HOST/DB_PORT/DB_USER/DB_PASSWORD/DB_NAME、BARK_KEY、
# TUSHARE_TOKEN、AI_API_KEY 等（.env 已被 gitignore，切勿提交）
```

### 2. 一键启动（推荐）

```bash
./start.sh     # 启动 Redis -> Celery 三队列 + Beat -> 后端 -> 前端
./stop.sh      # 停止所有服务
```

启动后访问：

- 前端界面：http://localhost:3000
- 后端 API：http://localhost:8000
- API 文档：http://localhost:8000/docs

### 3. 手动启动（开发调试）

```bash
# 后端 API（入口为 api.py，不是 main.py）
cd backend && source venv_new/bin/activate
python3 api.py                          # 或 uvicorn api:app --reload

# Celery 队列（需 Redis，按 start.sh 的队列划分启动）
celery -A core.celery_app.celery_app worker -Q realtime -n realtime@%h --loglevel=info
celery -A core.celery_app.celery_app worker -Q scan -n scan@%h --loglevel=info
celery -A core.celery_app.celery_app worker -Q maintenance,celery -n maintenance@%h --loglevel=info
celery -A core.celery_app.celery_app beat --loglevel=info

# 前端
cd frontend && npm run dev
```

## 常用命令

| 场景 | 命令 |
|------|------|
| 后端测试（115 个测试文件 / 1040 用例） | `cd backend && source venv_new/bin/activate && pytest tests/` |
| 前端类型检查 | `cd frontend && npx tsc --noEmit` |
| 前端 Lint | `cd frontend && npm run lint` |
| 前端单测 | `cd frontend && npm run test:risk-lines && npm run test:signal-display` |
| 数据同步 | `cd backend && venv_new/bin/python sync_cli.py sync [--limit 100]` |
| 数据源状态 | `cd backend && venv_new/bin/python sync_cli.py status` |
| 单股检查 | `cd backend && venv_new/bin/python sync_cli.py check --code 000001` |
| 数据库备份 | `cd backend && venv_new/bin/python scripts/backup_database.py` |

## 项目结构

```
python-select-stock/
├── start.sh / stop.sh           # 一键启停（Redis、Celery 三队列、后端、前端）
├── docker-compose.yml           # 仅 Redis 容器；PostgreSQL 需本地安装
├── app.py                       # 旧版 Streamlit 界面（已废弃，保留仅作参考）
├── backend/
│   ├── api.py                   # FastAPI 入口：中间件、限流、鉴权、路由注册、Sentinel 启动
│   ├── sync_cli.py              # 数据同步命令行工具
│   ├── core/
│   │   ├── celery_app.py        # Celery 配置 + Beat 定时任务（约 20 个）
│   │   ├── scanner.py           # 全市场扫描引擎
│   │   ├── strategy.py          # 策略主逻辑（TV/Squeeze/Pine/Consensus）
│   │   ├── price_action.py      # 价格行为（PA）信号与分级
│   │   ├── sentinel.py          # 盘中实时盯盘与推送
│   │   ├── decision_layer.py    # 信号决策与评级
│   │   ├── execution_*.py       # 意图、回放、审计、标签
│   │   ├── risk_engine.py / risk_constants.py  # 风控引擎与阈值常量
│   │   ├── data.py / direct_sources.py / multi_source_sync.py  # 多数据源
│   │   ├── db.py                # PostgreSQL 访问层与建表/索引
│   │   └── tasks.py             # Celery 任务定义
│   ├── routers/                 # FastAPI 路由（market/scan/stock/review/paper_trade…）
│   ├── tests/                   # pytest 测试集（115 文件 / 1040 用例）
│   ├── scripts/                 # 回测与运维脚本
│   └── requirements.txt
├── frontend/
│   ├── src/app/                 # Next.js App Router
│   ├── src/components/          # React 组件（Dashboard、ResultsTable、ReviewCenter…）
│   ├── src/stores/              # Zustand 状态（scanStore、marketStore 等）
│   ├── src/lib/api.ts           # axios 客户端（默认 http://127.0.0.1:8000）
│   └── tests/                   # node:test 前端单测
├── docs/                        # 策略研究、PRD、路线图等文档
└── AGENTS.md                    # 开发规范（代码风格、安全边界、Ponytail 原则）
```

## 关键环境变量

全部配置项见 `backend/.env.example`：

| 变量 | 说明 | 必填 |
|------|------|------|
| `DB_HOST` `DB_PORT` `DB_USER` `DB_PASSWORD` `DB_NAME` | PostgreSQL 连接信息 | ✅ |
| `DATABASE_URL` | 完整连接串，设置后覆盖上面的分项 | 可选 |
| `BARK_KEY` | Bark 推送密钥 | 推送功能需要 |
| `TUSHARE_TOKEN` | Tushare 数据源令牌 | 多源降级需要 |
| `AI_API_KEY` `AI_BASE_URL` `AI_MODEL` | OpenAI 兼容 AI 复盘 | AI 功能需要 |
| `API_TOKEN` `ENABLE_AUTH` | 写操作令牌校验（默认关闭，仅本机使用） | 生产建议开启 |
| `SENTINEL_DEFAULT_TIME` `SENTINEL_SCHEDULE_TIMES` | 盯盘时间点 | 可选 |
| `RATE_LIMIT_*` `ALLOWED_ORIGINS` | 限流与 CORS | 可选 |

## 相关文档

- `STARTUP_GUIDE.md` — 启动指南与常见问题排查
- `AGENTS.md` — 开发规范（勿改策略核心、风控阈值常量化、凭证安全）
- `docs/PRD.md` — 产品需求文档
- `docs/TECHNICAL_SPECS.md` — 技术规格说明
- `docs/ROADMAP.md` — 路线图
- `docs/ACCEPTANCE_CRITERIA.md` — 验收标准
