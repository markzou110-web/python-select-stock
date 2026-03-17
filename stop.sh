#!/bin/bash
# Alpha Vision 停止脚本

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

echo "============================================================"
echo " Alpha Vision 停止"
echo "============================================================"
echo ""

# 1. 停止后端
if [ -f ".backend_pid" ]; then
    BACKEND_PID=$(cat .backend_pid)
    echo "停止记录的后端 (PID: $BACKEND_PID)..."
    kill $BACKEND_PID 2>/dev/null
    rm -f .backend_pid
fi

# 强制清理所有 api.py 相关的 Python 进程
echo "清理所有残留的后端进程 (api.py)..."
pkill -9 -f "api.py" && echo "  已清理" || echo "  未发现残留进程"

# 2. 停止前端
if [ -f ".frontend_pid" ]; then
    FRONTEND_PID=$(cat .frontend_pid)
    echo "停止记录的前端 (PID: $FRONTEND_PID)..."
    kill $FRONTEND_PID 2>/dev/null
    rm -f .frontend_pid
fi

# 强制清理所有 Next.js/Node 相关的开发进程
echo "清理所有残留的前端进程 (next/node)..."
pkill -9 -f "next-dev" 2>/dev/null
pkill -9 -f "next dev" 2>/dev/null
echo "  前端进程已清理"

echo ""
echo "============================================================"
echo " 已停止所有服务"
echo "============================================================"
