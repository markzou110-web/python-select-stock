#!/bin/bash
# Alpha Vision 停止脚本

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

echo "============================================================"
echo " Alpha Vision 停止"
echo "============================================================"
echo ""

stop_pid_file() {
    local pid_file="$1"
    local label="$2"
    local expected="$3"
    [ -f "$pid_file" ] || return 0
    local pid command
    pid=$(tr -cd '0-9' < "$pid_file")
    command=$(ps -p "$pid" -o command= 2>/dev/null || true)
    if [ -n "$pid" ] && [[ "$command" == *"$expected"* ]]; then
        echo "停止 $label (PID: $pid)..."
        kill -TERM "$pid" 2>/dev/null || true
        for _ in {1..10}; do
            kill -0 "$pid" 2>/dev/null || break
            sleep 0.5
        done
        if kill -0 "$pid" 2>/dev/null; then
            echo "  $label 未及时退出，仅终止记录的 PID"
            kill -KILL "$pid" 2>/dev/null || true
        fi
    elif [ -n "$command" ]; then
        echo "跳过 $label：PID $pid 已属于其他进程"
    fi
    rm -f "$pid_file"
}

stop_pid_file ".backend_pid" "后端" "api.py"
stop_pid_file ".celery_realtime_pid" "Celery 实时队列" "celery"
stop_pid_file ".celery_scan_pid" "Celery 扫描队列" "celery"
stop_pid_file ".celery_maintenance_pid" "Celery 维护队列" "celery"
stop_pid_file ".celery_beat_pid" "Celery 调度器" "celery"
stop_pid_file ".celery_pid" "旧版 Celery" "celery"
stop_pid_file ".frontend_pid" "前端" "next"

echo ""
echo "============================================================"
echo " 已停止所有服务"
echo "============================================================"
