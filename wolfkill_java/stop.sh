#!/bin/bash
# 停止狼人杀（Java 版）服务（本地开发用）
# 注意：start.sh 现已改为前台（exec）运行，不再写 server.pid，
#       因此本脚本按进程名停止。
cd "$(dirname "$0")"
pkill -f "WerewolfServer" 2>/dev/null && echo "已停止狼人杀服务进程" || echo "未找到运行中的进程"
