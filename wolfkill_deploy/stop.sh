#!/usr/bin/env bash
# 狼人杀联机服务 · 停止脚本
cd "$(dirname "$0")"
if [ -f server.pid ]; then
  PID="$(cat server.pid)"
  if kill "$PID" 2>/dev/null; then
    echo "✅ 已停止服务（PID=$PID）"
  else
    echo "⚠️ PID=$PID 的进程不存在，可能已退出"
  fi
  rm -f server.pid
else
  echo "未找到 server.pid，尝试按名称结束进程…"
  pkill -f "server.py" && echo "✅ 已停止" || echo "无正在运行的 server.py"
fi
