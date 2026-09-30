#!/bin/bash
# Alpha Vision 启动脚本

# 设置 PATH（确保 Homebrew 和用户路径可用）
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"

# macOS Fork Safety Fix (防止 Celery Worker 在并行扫描时崩溃)
export OBJC_DISABLE_INITIALIZE_FORK_SAFETY=YES

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

check_pid_file_not_running() {
    local pid_file="$1"
    local label="$2"
    local expected="$3"
    [ -f "$pid_file" ] || return 0

    local pid command
    pid=$(tr -cd '0-9' < "$pid_file")
    command=$(ps -p "$pid" -o command= 2>/dev/null || true)
    if [ -n "$pid" ] && [[ "$command" == *"$expected"* ]]; then
        echo "❌ $label 已在运行 (PID: $pid)"
        return 1
    fi
    return 0
}

SERVICES_ALREADY_RUNNING=0
check_pid_file_not_running ".backend_pid" "后端" "api.py" || SERVICES_ALREADY_RUNNING=1
check_pid_file_not_running ".celery_realtime_pid" "Celery 实时队列" "celery" || SERVICES_ALREADY_RUNNING=1
check_pid_file_not_running ".celery_collector_pid" "Celery 采集队列" "celery" || SERVICES_ALREADY_RUNNING=1
check_pid_file_not_running ".celery_scan_pid" "Celery 扫描队列" "celery" || SERVICES_ALREADY_RUNNING=1
check_pid_file_not_running ".celery_maintenance_pid" "Celery 维护队列" "celery" || SERVICES_ALREADY_RUNNING=1
check_pid_file_not_running ".celery_beat_pid" "Celery 调度器" "celery" || SERVICES_ALREADY_RUNNING=1
check_pid_file_not_running ".frontend_pid" "前端" "next" || SERVICES_ALREADY_RUNNING=1
if [ "$SERVICES_ALREADY_RUNNING" -ne 0 ]; then
    echo "请先运行 ./stop.sh，再重新执行 ./start.sh。"
    exit 1
fi

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

# 日志清理：删除 30 天前的历史日志，避免磁盘被日志占满（应用日志已启用每日轮转）
if [ -d "backend/logs" ]; then
    find backend/logs -type f -name 'alphavision*' -mtime +30 -delete 2>/dev/null || true
fi

# 启动后端
echo ""
echo "🚀 启动后端 (FastAPI)..."
cd backend

# 检查并激活虚拟环境（损坏自愈 + 版本锁定）：
# 1) activate 内的 VIRTUAL_ENV 是创建时的绝对路径——仓库移动/拷贝后会失效，bin/ 下的
#    python 符号链接也可能在拷贝中丢失；激活后校验 python 是否真的解析到本目录 venv 内。
# 2) requirements.txt 的依赖（sqlalchemy==2.0.30 等）与 Python 3.13+ 不兼容，
#    而 brew 的 python3 已升到 3.14——创建 venv 必须显式用 3.12（老 venv 即 3.12.12）。
# 两种情况不满足都删除重建，避免静默回退或装出不兼容环境。
if command -v python3.12 >/dev/null 2>&1; then
    VENV_PYTHON="$(command -v python3.12)"
elif [ -x "/opt/homebrew/opt/python@3.12/bin/python3.12" ]; then
    VENV_PYTHON="/opt/homebrew/opt/python@3.12/bin/python3.12"
else
    VENV_PYTHON="python3"
fi

venv_python_ok() {
    [ -x "venv_new/bin/python" ] || return 1
    command -v python >/dev/null 2>&1 || return 1
    case "$(command -v python)" in
        "$PWD/venv_new/"*) ;;
        *) return 1 ;;
    esac
    python -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 12) else 1)' 2>/dev/null
}

if [ -d "venv_new" ]; then
    source venv_new/bin/activate 2>/dev/null || true
fi
if ! venv_python_ok; then
    echo "venv_new 缺失、损坏、指向旧路径或 Python 版本不符，重建虚拟环境（锁定 3.12）..."
    command -v deactivate >/dev/null 2>&1 && deactivate
    rm -rf venv_new
    rm -f .activate_lock
    "$VENV_PYTHON" -m venv venv_new && source venv_new/bin/activate
fi
if ! venv_python_ok; then
    echo "错误: venv_new 重建失败。需要 Python 3.12 创建虚拟环境（找不到 python3.12？请 brew install python@3.12）"
    exit 1
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
if command -v redis-cli &> /dev/null && redis-cli ping 2>/dev/null | grep -q PONG; then
    echo "启动 Celery 实时、采集、扫描、维护队列及独立调度器..."
    # 用 python 而非 python3：activate 后 venv/bin 在 PATH 首位，venv 必有 python；
    # 即使 venv 缺 python3 软链也不会静默回退到系统解释器。
    python -m celery -A core.celery_app.celery_app worker -Q realtime -n realtime@%h --loglevel=info > celery-realtime.log 2>&1 &
    CELERY_REALTIME_PID=$!
    python -m celery -A core.celery_app.celery_app worker -Q collector -n collector@%h --loglevel=info > celery-collector.log 2>&1 &
    CELERY_COLLECTOR_PID=$!
    python -m celery -A core.celery_app.celery_app worker -Q scan -n scan@%h --loglevel=info > celery-scan.log 2>&1 &
    CELERY_SCAN_PID=$!
    python -m celery -A core.celery_app.celery_app worker -Q maintenance,celery -n maintenance@%h --loglevel=info > celery-maintenance.log 2>&1 &
    CELERY_MAINTENANCE_PID=$!
    python -m celery -A core.celery_app.celery_app beat --loglevel=info > celery-beat.log 2>&1 &
    CELERY_BEAT_PID=$!
    echo "Celery PIDs: realtime=$CELERY_REALTIME_PID collector=$CELERY_COLLECTOR_PID scan=$CELERY_SCAN_PID maintenance=$CELERY_MAINTENANCE_PID beat=$CELERY_BEAT_PID"
else
    echo "⚠️ 警告: Redis 未通过 PING 检查，不启动 Celery，避免任务进入不可用状态。"
fi

# 启动后端
echo "后端启动中... (http://localhost:8000)"
python api.py &
BACKEND_PID=$!
echo "后端 PID: $BACKEND_PID"

# 后端完成数据库、交易日历等初始化后再启动前端，避免前端首屏请求产生批量 Network Error。
BACKEND_HEALTH_URL="http://127.0.0.1:8000/api/health"
BACKEND_HEALTH_TIMEOUT_SECONDS=60
BACKEND_READY=0
echo "等待后端健康检查..."
for ((attempt=1; attempt<=BACKEND_HEALTH_TIMEOUT_SECONDS; attempt++)); do
    if ! kill -0 "$BACKEND_PID" 2>/dev/null; then
        echo "❌ 后端进程已退出，停止启动前端"
        break
    fi
    if curl -fsS --max-time 2 "$BACKEND_HEALTH_URL" >/dev/null 2>&1; then
        BACKEND_READY=1
        echo "后端健康检查: OK ($BACKEND_HEALTH_URL)"
        break
    fi
    sleep 1
done

if [ "$BACKEND_READY" -ne 1 ]; then
    echo "❌ 后端在 ${BACKEND_HEALTH_TIMEOUT_SECONDS} 秒内未就绪，停止本次启动"
    kill -TERM "$BACKEND_PID" 2>/dev/null || true
    [ -n "$CELERY_REALTIME_PID" ] && kill -TERM "$CELERY_REALTIME_PID" 2>/dev/null || true
    [ -n "$CELERY_SCAN_PID" ] && kill -TERM "$CELERY_SCAN_PID" 2>/dev/null || true
    [ -n "$CELERY_MAINTENANCE_PID" ] && kill -TERM "$CELERY_MAINTENANCE_PID" 2>/dev/null || true
    [ -n "$CELERY_BEAT_PID" ] && kill -TERM "$CELERY_BEAT_PID" 2>/dev/null || true
    exit 1
fi

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
if [ ! -z "$CELERY_REALTIME_PID" ]; then
    echo "$CELERY_REALTIME_PID" > .celery_realtime_pid
    echo "$CELERY_COLLECTOR_PID" > .celery_collector_pid
    echo "$CELERY_SCAN_PID" > .celery_scan_pid
    echo "$CELERY_MAINTENANCE_PID" > .celery_maintenance_pid
    echo "$CELERY_BEAT_PID" > .celery_beat_pid
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
