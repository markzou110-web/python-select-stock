# AGENTS.md

## 项目概览

- **项目类型**：A股量化选股与风控分析系统（前后端分离架构，包含全市场扫描、实盘/模拟盘盯盘、Bark消息推送、以及多因子共振策略评级）。
- **主要语言**：
  - 后端：Python 3 (FastAPI, SQLAlchemy, Pandas, Celery)
  - 前端：TypeScript (React, Vite, Tailwind CSS, Zustand)
- **关键目录**：
  - `backend/`：后端核心逻辑。
    - `backend/core/`：量化策略（Squeeze/Pine/Consensus）、数据抓取（Sina/Tencent）、技术指标计算及风控引擎（Wind Control Sentinel）。
    - `backend/routers/`：FastAPI 路由控制器。
    - `backend/data/`：本地 SQLite 数据库存储（包括 `paper_trades` 实盘记录等核心数据）。
  - `frontend/`：前端界面展示。
    - `frontend/src/components/`：核心 React 组件（如 Dashboard, ResultsTable, 股票详情页等）。
    - `frontend/src/stores/`：前端状态管理。
  - `docs/`：产品说明文档（PRD、ROADMAP 等）。

## 常用命令

- **安装依赖**：
  - 后端：`cd backend && source venv_new/bin/activate && pip install -r requirements.txt`
  - 前端：`cd frontend && npm install`
- **本地开发**：
  - 后端 API：`cd backend && source venv_new/bin/activate && uvicorn main:app --reload`
  - 后端 Celery 队列（用于后台扫描/盯盘）：`cd backend && source venv_new/bin/activate && celery -A core.celery_app worker --loglevel=info`
  - 前端 UI：`cd frontend && npm run dev`
- **运行测试**：
  - 后端测试：`cd backend && source venv_new/bin/activate && pytest tests/`
- **类型检查**：
  - 前端：`cd frontend && npm run type-check` (或 `tsc --noEmit`)
- **格式化**：
  - 前端：`cd frontend && npm run lint` / `npm run format` (取决于具体 script 配置)

## 代码规范

- 遵循现有代码风格（后端遵循 PEP8/Black，前端遵循 ESLint/Prettier）。
- 不做无关重构（尤其是 `backend/core/strategy.py` 中的复杂量化逻辑，非必要不重构，修改前需完全理解指标算法）。
- 新增功能必须补充或更新测试（尤其是在修改买卖信号逻辑、风控阈值及数据库 Schema 时）。

## 安全边界

- **凭证安全**：绝对不读取或提交 `.env` 文件、Bark 隐私推送 Token 及个人的私有 API 密钥。
- **数据安全**：绝对不执行删除生产数据或本地 `backend/data/market_data.db` 的命令。实盘交易记录 (`paper_trades`) 和扫描历史极其重要。
- **数据库操作**：修改数据库结构（Schema）前，必须先与用户说明影响，并提供无损的迁移方案（如 `ALTER TABLE ADD COLUMN` 附带默认值），确保向下兼容。

## 交付要求

- **说明改动文件**：每次交付时清晰列出被修改的后端/前端文件及具体原因。
- **说明验证命令和结果**：交付前必须通过本地手动或自动化脚本验证，输出 API 请求状态或前端编译结果，证明功能畅通。
- **说明未验证项和剩余风险**：如果在盘后时间修改了实时盯盘（Sentinel）逻辑或第三方数据接口（如新浪/腾讯行情 API），需明确指出必须等下一个交易日开盘方能最终验证，并说明可能的风险点。

## Ponytail 原则（懒资开发 / Lazy Senior Dev）

写代码前，从上往下找到第一个成立的就用（来自 [ponytail](https://github.com/DietrichGebert/ponytail)）：

1. **这东西需要存在吗？**（YAGNI — 不需要就跳过，不做"未来可能用到"的预判）
2. **标准库能做吗？**（优先 Python/TS 内置能力，而非引入第三方包）
3. **平台原生功能能做吗？**（如 FastAPI/SQLAlchemy/React 已有能力就不重造）
4. **已安装的依赖能解决吗？**（先查 requirements.txt/package.json 再 pip/npm install）
5. **能写成一行吗？**（在不牺牲可读性的前提下，短优先于长）
6. 以上都不行，才写"最少能跑的代码"（MVP，非完美方案）

### 必须遵守的约束
- **不主动加抽象**：除非有 ≥2 个具体复用场景，否则不抽函数/类/接口。YAGNI。
- **不增新依赖**：新增 pip/npm 依赖前必须说明"现有依赖为何不够"，并与用户确认。
- **删除优先于新增**：能用删除/简化解决的，不靠新增代码解决。
- **复杂请求先反问**："你真的需要 X 吗？Y 够不够？"——在用户确认前不写大段代码。
- **复用优先**：动手前先 Grep/Glob 搜现有实现，避免重复造轮子。

### 不允许偷懒的地方（懒 ≠ 粗糙）
- 信任边界的输入校验（API 参数、跨模块调用、外部数据）
- 防数据丢失的错误处理（尤其涉及 `paper_trades`、`daily_k` 的写操作）
- 安全（凭证、注入、权限）
- 可访问性（前端语义化）

### 应用到本项目的具体要求
- 量化策略（`backend/core/strategy.py`）非必要不重构，修改前必须完全理解指标算法。
- 风控阈值改动必须常量化（`risk_constants.py`），禁止魔法数字散落代码中。
- 推送改动只增字段不改字段（避免破坏前端契约）。


<!-- headroom:rtk-instructions -->
# RTK (Rust Token Killer) - Token-Optimized Commands

When running shell commands, **always prefix with `rtk`**. This reduces context
usage by 60-90% with zero behavior change. If rtk has no filter for a command,
it passes through unchanged — so it is always safe to use.

## Key Commands
```bash
# Git (59-80% savings)
rtk git status          rtk git diff            rtk git log

# Files & Search (60-75% savings)
rtk ls <path>           rtk read <file>         rtk grep <pattern>
rtk find <pattern>      rtk diff <file>

# Test (90-99% savings) — shows failures only
rtk pytest tests/       rtk cargo test          rtk test <cmd>

# Build & Lint (80-90% savings) — shows errors only
rtk tsc                 rtk lint                rtk cargo build
rtk prettier --check    rtk mypy                rtk ruff check

# Analysis (70-90% savings)
rtk err <cmd>           rtk log <file>          rtk json <file>
rtk summary <cmd>       rtk deps                rtk env

# GitHub (26-87% savings)
rtk gh pr view <n>      rtk gh run list         rtk gh issue list

# Infrastructure (85% savings)
rtk docker ps           rtk kubectl get         rtk docker logs <c>

# Package managers (70-90% savings)
rtk pip list            rtk pnpm install        rtk npm run <script>
```

## Rules
- In command chains, prefix each segment: `rtk git add . && rtk git commit -m "msg"`
- For debugging, use raw command without rtk prefix
- `rtk proxy <cmd>` runs command without filtering but tracks usage
<!-- /headroom:rtk-instructions -->
