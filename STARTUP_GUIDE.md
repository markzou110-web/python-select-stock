# Alpha Vision 启动指南

## 概述

Alpha Vision 是一个 A股全市场选股与监控交易系统，采用前后端分离架构：
- **后端**: FastAPI (Python) - 端口 8000
- **前端**: Next.js (React) - 端口 3000
- **数据库**: PostgreSQL

## 前置要求

### 1. 系统要求
- macOS / Linux
- Python 3.12（对应 `backend/venv_new`；仓库内 `backend/venv` 为遗留的 3.14 环境，可删除）
- Node.js 18+
- PostgreSQL 12+
- Redis（可选：未启动时 Celery 自动降级为同步执行模式）

### 2. 环境配置

#### 后端配置
```bash
cd backend

# 创建配置文件
cp .env.example .env

# 编辑 .env 填写数据库配置
# DATABASE_URL=postgresql://user:password@localhost:5432/alpha_vision
```

#### 前端配置
```bash
cd frontend

# 无需额外配置，默认连接 http://localhost:8000
```

## 启动方式

### 方式一：一键启动（推荐）

```bash
# 在项目根目录执行
./start.sh
```

启动后访问：
- 前端界面: http://localhost:3000
- 后端API: http://localhost:8000
- API文档: http://localhost:8000/docs

### 方式二：手动启动

#### 启动后端
```bash
cd backend

# 激活虚拟环境（必须使用 venv_new / Python 3.12）
source venv_new/bin/activate

# 安装依赖（首次）
pip install -r requirements.txt

# 启动服务
python3 api.py
```

#### 启动前端（新终端窗口）
```bash
cd frontend

# 安装依赖（首次）
npm install

# 启动开发服务器
npm run dev
```

## 停止服务

### 方式一：使用停止脚本
```bash
./stop.sh
```

### 方式二：手动停止
```bash
# 查找并停止后端
ps aux | grep "python3 api.py"
kill <PID>

# 查找并停止前端
ps aux | grep "next dev"
kill <PID>
```

## 常见问题

### 后端启动失败

**问题**: `ModuleNotFoundError: No module named 'fastapi'`
```bash
# 解决：安装依赖
cd backend
source venv_new/bin/activate
pip install -r requirements.txt
```

**问题**: `connection to server at "localhost", port 5432 failed`
```bash
# 解决：检查PostgreSQL是否运行
brew services list | grep postgresql
brew services start postgresql
```

### 前端启动失败

**问题**: `command not found: npm`
```bash
# 解决：安装Node.js
brew install node
```

**问题**: `Cannot connect to backend`
```bash
# 解决：确保后端已启动在 http://localhost:8000
curl http://localhost:8000/api/health
```

## 开发模式

### 后端热重载
```bash
cd backend
source venv_new/bin/activate
uvicorn api:app --reload --host 0.0.0.0 --port 8000
```

### 前端热重载
```bash
cd frontend
npm run dev
# Next.js 默认开启热重载
```

## 质量校验

```bash
# 后端测试（115 个文件 / 1040 用例）
cd backend
source venv_new/bin/activate
pip install -r requirements-dev.txt   # 仅首次（pytest/httpx 不在生产依赖中）
pytest tests/

# 前端：类型检查 + Lint + 单测
cd frontend
npx tsc --noEmit
npm run lint
npm run test:risk-lines && npm run test:signal-display
```

## 数据同步

启动后端后，可执行数据同步：

```bash
cd backend

# 查看数据源状态
venv_new/bin/python sync_cli.py status

# 同步所有股票
venv_new/bin/python sync_cli.py sync

# 同步前100只股票
venv_new/bin/python sync_cli.py sync --limit 100

# 检查单个股票
venv_new/bin/python sync_cli.py check --code 000001
```

## 生产部署

本仓库**未提供 Dockerfile，也没有 systemd unit 文件**；当前实际部署方式就是根目录的 `./start.sh`（前台常驻）。
`docker-compose.yml` 仅用于启动 Redis 容器。

### 后端

```bash
cd backend && source venv_new/bin/activate

# 前台运行
python3 api.py

# 或后台驻留（日志写入 backend/logs/）
nohup python3 api.py > logs/api.log 2>&1 &
```

如需 systemd / launchd 常驻，请自行编写 unit 文件，关键三项：
`ExecStart=<repo>/backend/venv_new/bin/python api.py`、`WorkingDirectory=<repo>/backend`、`EnvironmentFile=<repo>/backend/.env`。

### 前端

```bash
cd frontend
npm run build
npm run start        # 生产模式，端口 3000
```

## 端口占用检查

```bash
# 检查端口 8000（后端）
lsof -i :8000

# 检查端口 3000（前端）
lsof -i :3000

# 释放端口
kill -9 <PID>
```
