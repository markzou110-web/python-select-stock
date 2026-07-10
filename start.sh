#!/bin/bash
# Alpha Vision 启动脚本

# 设置 PATH（确保 Homebrew 和用户路径可用）
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

# macOS Fork Safety Fix (防止 Celery Worker 在并行扫描时崩溃)
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES

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

# 尝试启动基础设施 (Redis)
echo ""
echo "🗄️ 初始化基础设施 (Redis)..."
if command -v redis-cli &> /dev/null && redis-cli ping &> /dev/null; then
    echo "✅ Redis 已在运行"
elif command -v brew &> /dev/null && brew services list | grep -q redis; then
    echo "启动本地 Redis 服务 (Homebrew)..."
    brew services start redis
elif command -v redis-server &> /dev/null; then
    echo "启动本地 Redis 服务 (后台运行)..."
    redis-server --daemonize yes
else
    echo "⚠️ 提示: 未找到本地 Redis 服务，系统将自动切入单机免依赖模式！"
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

# 检查 Redis 状态
echo "检查 Redis 连接状态..."
if nc -z localhost 6379 2>/dev/null || ping -c 1 localhost &> /dev/null; then
    echo "启动 Celery Worker (任务队列)..."
    python3 -m celery -A core.celery_app.celery_app worker -B --loglevel=info > celery.log 2>&1 &
    CELERY_PID=$!
    echo "Celery PID: $CELERY_PID"
else
    echo "⚠️ 警告: Redis 可能未运行或无法连接。请运行 docker-compose up -d redis 否则扫描功能可能卡住！"
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
FRONTEND_NPM_PID=$!
sleep 2
FRONTEND_PID=$(pgrep -P "$FRONTEND_NPM_PID" -f "next" | head -n 1)
if [ -z "$FRONTEND_PID" ]; then
    FRONTEND_PID=$FRONTEND_NPM_PID
fi
if curl -fsS http://localhost:3000 >/dev/null 2>&1; then
    echo "前端健康检查: OK (http://localhost:3000)"
else
    echo "⚠️ 前端健康检查未通过，请查看 npm/Next.js 输出"
fi
echo "前端 PID: $FRONTEND_PID"

cd "$PROJECT_DIR"

# 保存PID
echo "$BACKEND_PID" > .backend_pid
echo "$FRONTEND_PID" > .frontend_pid
echo "http://localhost:3000" > .frontend_url
if [ ! -z "$CELERY_PID" ]; then
    echo "$CELERY_PID" > .celery_pid
fi

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
