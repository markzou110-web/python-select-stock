#!/bin/bash
# Alpha Vision 停止脚本

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

echo "============================================================"
echo " Alpha Vision 停止"
echo "============================================================"
echo ""

# 读取保存的PID
if [ -f ".backend_pid" ]; then
    BACKEND_PID=$(cat .backend_pid)
    echo "停止后端 (PID: $BACKEND_PID)..."
    kill $BACKEND_PID 2>/dev/null && echo "  后端已停止" || echo "  后端进程不存在"
    rm -f .backend_pid
else
    echo "未找到后端PID文件，尝试查找进程..."
    pkill -f "python3 api.py" && echo "  后端已停止" || echo "  未找到运行中的后端"
fi

if [ -f ".frontend_pid" ]; then
    FRONTEND_PID=$(cat .frontend_pid)
    echo "停止前端 (PID: $FRONTEND_PID)..."
    kill $FRONTEND_PID 2>/dev/null && echo "  前端已停止" || echo "  前端进程不存在"
    rm -f .frontend_pid
else
    echo "未找到前端PID文件，尝试查找进程..."
    pkill -f "next dev" && echo "  前端已停止" || echo "  未找到运行中的前端"
fi

echo ""
echo "============================================================"
echo " 已停止所有服务"
echo "============================================================"
