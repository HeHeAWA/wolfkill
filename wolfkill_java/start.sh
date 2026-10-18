#!/bin/bash
# 狼人杀（Java 版）启动脚本
#
# ⚠️ 必须用“前台”方式运行本脚本：
#    云平台的进程管理器会监控“启动命令对应的那个进程”是否常驻。
#    脚本里用 `exec java ...` 把自身替换为 java 进程，使 java 成为平台
#    长期监控的对象。切勿用 nohup / & 把 java 丢到后台，否则脚本会立刻
#    退出，平台会误报“实例启动后在极短的时间内退出”。
#
# 用法（前台运行，不要加 &）：
#   bash start.sh --port 3000
#   WEREWOLF_API_KEY=sk-or-xxx bash start.sh --port 3000   # 真实对局
#   bash start.sh --mock --port 3000                        # 离线自测
#
# 配置大模型（无需改源码/重编译），用环境变量注入：
#   WEREWOLF_API_KEY    OpenRouter Key（真实对局必填）
#   WEREWOLF_BASE_URL   可选，默认 OpenRouter
#   WEREWOLF_MODEL      可选，默认 nvidia/nemotron-3-super-120b-a12b:free
#   WEREWOLF_MOCK       设为 true 强制离线
# 端口优先级：--port 命令行 > 环境变量 PORT（云平台常注入）> 默认 3000
cd "$(dirname "$0")"

# 若尚未编译，尝试编译（需要 JDK；若服务器只有 JRE，请带上我们已编译好的 .class 文件）
if [ ! -f WerewolfServer.class ]; then
  javac --release 11 *.java 2>/dev/null || javac *.java 2>/dev/null || echo "[提示] 未找到 javac，将直接尝试运行（请确保目录内含 .class 文件）"
fi

echo "🐺 启动狼人杀服务……（前台运行，Ctrl+C 或平台停止即可关闭）"
# exec：把当前 shell 进程替换为 java，使其成为平台监控的长期进程
exec java WerewolfServer "$@"
