# Alpha Vision v5.0 Pro - 极光量化终端

## 架构说明 (Next.js + FastAPI)
本项目已从单体 Streamlit 架构升级为现代化的 **前后端分离架构**，以提供极其丝滑的 UI 交互和高性能的多因子扫描体验。

- **Backend (FastAPI)**: 处理所有数据抓取、技术指标计算和共振选股逻辑。
- **Frontend (Next.js)**: 采用 Tailwind CSS + Framer Motion 构建的极简极客风仪表盘。

---

## 启动指南

### 1. 启动后端服务器
打开终端，进入 `backend` 目录并运行：
```bash
cd backend
# 建议安装依赖
python3 -m pip install fastapi uvicorn akshare pandas sqlalchemy psycopg2-binary
# 启动 API (显式指定 host 避免 Mac IPv6 冲突)
PYTHONPATH=. python3 -m uvicorn api:app --host 127.0.0.1 --port 8000 --reload
```
后端默认运行在 `http://localhost:8000`

### 2. 启动前端页面
打开另一个终端，进入 `frontend` 目录并运行：
```bash
cd frontend
# 安装依赖 (首次运行)
npm install
# 启动页面
npm run dev
```
前端默认运行在 `http://localhost:3000`

---

## 核心功能点
1. **多因子共振引擎**: 完美同步 TradingView Pro 逻辑 (RSI > 55, MACD 零轴之上, 极致 BB 粘合)。
2. **实时指数全览**: 极速刷新上证、创业板等大盘核心数据。
3. **行业热点追踪**: 自动识别领涨板块及领涨个股。
4. **历史胜率回测**: 在扫描结果中直接展示每个信号在过去 1 年的胜率表现。
5. **极简极客设计**: 采用磨砂玻璃质感、微动效及高对比度数据排版。

---

## 后续建议
- **K线集成**: 目前已预留位置，后续建议接入 `Lightweight Charts` 库以实现秒级缩放的 K 线预览。
- **自动对齐**: 建议配置独立的同步脚本，定期向 PostgreSQL 写入历史 K 线数据以加速扫描。
