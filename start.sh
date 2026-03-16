#!/bin/bash
# Alpha Vision 启动脚本

# 设置 PATH（确保 Homebrew 和用户路径可用）
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

echo "============================================================"
echo " Alpha Vision 启动"
echo "============================================================"

# 检查环境
if ! command -v python3 &> /dev/null; then
    echo "错误: 未找到 python3"
    exit 1
fi

if ! command -v npm &> /dev/null; then
    echo "警告: 未找到 npm，前端将无法启动"
    echo "请安装 Node.js: brew install node"
fi

# 启动后端
echo ""
echo "🚀 启动后端 (FastAPI)..."
cd backend

# 检查虚拟环境
if [ ! -d "venv_new" ] && [ ! -d "venv" ]; then
    echo "创建虚拟环境..."
    python3 -m venv venv_new
fi

# 激活虚拟环境
if [ -d "venv_new" ]; then
    source venv_new/bin/activate
else
    source venv/bin/activate
fi

# 安装依赖
if [ ! -f ".activate_lock" ]; then
    echo "安装Python依赖..."
    pip install -q -r requirements.txt
    touch .activate_lock
fi

# 检查数据库配置
if [ ! -f ".env" ] && [ -f "db_config.json" ]; then
    echo "使用 db_config.json 配置"
elif [ ! -f ".env" ]; then
    echo "⚠️  警告: 请先配置 .env 文件"
    echo "   cp .env.example .env"
    echo "   然后编辑 .env 填写数据库配置"
fi

# 启动后端
echo "后端启动中... (http://localhost:8000)"
python3 api.py &
BACKEND_PID=$!
echo "后端 PID: $BACKEND_PID"

# 等待后端启动
sleep 3

# 启动前端
echo ""
echo "🎨 启动前端 (Next.js)..."
cd ../frontend

# 检查node_modules
if [ ! -d "node_modules" ]; then
    echo "安装前端依赖..."
    npm install
fi

# 启动前端开发服务器
echo "前端启动中... (http://localhost:3000)"
npm run dev &
FRONTEND_PID=$!
echo "前端 PID: $FRONTEND_PID"

cd "$PROJECT_DIR"

# 保存PID
echo "$BACKEND_PID" > .backend_pid
echo "$FRONTEND_PID" > .frontend_pid

echo ""
echo "============================================================"
echo " ✅ Alpha Vision 已启动!"
echo "============================================================"
echo ""
echo "  前端: http://localhost:3000"
echo "  后端: http://localhost:8000"
echo "  API文档: http://localhost:8000/docs"
echo ""
echo "  按 Ctrl+C 停止所有服务"
echo "  或运行: ./stop.sh"
echo "============================================================"

# 等待用户中断
wait
