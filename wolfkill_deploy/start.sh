#!/usr/bin/env bash
# 狼人杀联机服务 · 后台启动脚本
# 用法：
#   ./start.sh              # 默认端口 3000，调用真实大模型
#   ./start.sh 8080         # 指定端口
#   ./start.sh 3000 --mock  # 离线模式（不调用大模型，纯规则 AI，适合无网络/测试）
set -e
cd "$(dirname "$0")"

PORT="${1:-3000}"
EXTRA="${2:-}"   # 第二个参数可传 --mock

if ! command -v python3 >/dev/null 2>&1; then
  echo "❌ 未检测到 python3，请先安装 Python 3.7+"
  exit 1
fi

# 若端口被占用，先提示
if command -v lsof >/dev/null 2>&1; then
  if lsof -ti tcp:"$PORT" >/dev/null 2>&1; then
    echo "⚠️ 端口 $PORT 已被占用，请换端口或先停止旧进程"
    exit 1
  fi
fi

echo "🐺 启动狼人杀联机服务（端口 $PORT）"
echo "   真实 AI 模式请保证服务器能访问外网（OpenRouter）"
echo "   访问地址： http://<服务器公网IP>:$PORT"
echo "   停止服务： ./stop.sh"
echo "----------------------------------------"

nohup python3 server.py --port "$PORT" $EXTRA > server.log 2>&1 &
echo $! > server.pid
echo "✅ 已后台启动，进程 PID=$(cat server.pid)，日志见 server.log"
