# Alpha Vision 启动指南

## 概述

Alpha Vision 是一个 A股全市场选股与监控交易系统，采用前后端分离架构：
- **后端**: FastAPI (Python) - 端口 8000
- **前端**: Next.js (React) - 端口 3000
- **数据库**: PostgreSQL

## 前置要求

### 1. 系统要求
- macOS / Linux
- Python 3.8+
- Node.js 16+
- PostgreSQL 12+

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

# 激活虚拟环境
source venv_new/bin/activate
# 或
source venv/bin/activate

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
curl http://localhost:8000/health
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

### 后端部署
```bash
# 使用 systemd 服务
sudo cp alpha-vision-backend.service /etc/systemd/system/
sudo systemctl enable alpha-vision-backend
sudo systemctl start alpha-vision-backend
```

### 前端部署
```bash
# 构建生产版本
cd frontend
npm run build
npm run start
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
