# 🐺 WolfKill · 狼人杀（纯文字版）

一个支持 **真人 + AI** 对局的纯文字狼人杀。提供三种运行模式，共用同一套规则与版型逻辑：

| 模式 | 入口 | 特点 |
|------|------|------|
| 本地命令行 | `main.py` | 1 名真人在终端与 AI 对局，无需联网（mock 模式） |
| 联机（Python） | `net/server.py` + `net/index.html` | 浏览器多人/人机混玩，Python 后端 |
| 服务器（Java） | `wolfkill_java/` | 浏览器多人/人机混玩，Java 后端（纯 JDK，无第三方依赖） |

---

## 功能特性

- **标准版型身份分配**：4–12 人按标准板分配（狼人 / 预言家 / 女巫 / 猎人 / 守卫 / 平民）。
- **完整昼夜流程**：夜晚狼人杀人、女巫（先于预言家）救人/毒人、预言家查验、守卫守人；白天发言、上警竞选、投票放逐。
- **上警竞选**：全员先统一决定是否上警，再公布；参选者不参与警长投票；警长放逐票计 1.5 票。
- **房主踢人**：房主可在大厅或游戏中将玩家移出房间（游戏中由 AI 接管其座位）。
- **身份构成展示**：游戏结束后显示本局身份构成（不暴露归属）。
- **AI 驱动**：AI 经 OpenRouter 调用大模型进行推理与发言（可离线 mock）。
- **防卡死**：心跳 + 有界超时，真人挂机时自动跳过，不会卡住整局。

---

## 环境准备

### 1. 配置 API Key（真实对局需要）

所有模式从环境变量 `WEREWOLF_API_KEY` 读取 OpenRouter 密钥（**仓库内不硬编码密钥**）：

```bash
export WEREWOLF_API_KEY="sk-or-你的真实key"
```

> 仅运行 `--mock` 离线模式时无需配置。

默认模型：
- **Java 服务器模式**：`nvidia/nemotron-3-super-120b-a12b:free`（免费、无需额度）
- **Python 模式**：默认 `tencent/hy3:free` 已被 OpenRouter 下架，请改用环境变量指定可用模型：
  ```bash
  export WEREWOLF_MODEL="nvidia/nemotron-3-super-120b-a12b:free"
  ```

### 2. 运行环境

- **Python 模式**：Python 3.9+（建议 3.13）。依赖仅标准库，无需 `pip install`。
- **Java 模式**：JDK 11+（使用 `javac --release 11` 编译）。

---

## 运行方式

### 模式一：本地命令行（1 真人 + AI）

```bash
cd wolfKill

# 离线模拟（不消耗额度，推荐先试）
python3 main.py --mock

# 真实对局（需先 export WEREWOLF_API_KEY）
python3 main.py

# 其它选项
python3 main.py --auto    # 纯 AI 观战（显示机密）
python3 main.py --debug   # 调试模式（显示狼队讨论/守护/用药）
python3 main.py --model openai/gpt-4o-mini
```

### 模式二：联机（Python 后端 + 网页）

```bash
cd wolfKill

# 启动服务器（默认端口 3000）
python3 net/server.py --mock          # 离线
python3 net/server.py --port 8080     # 指定端口
```

浏览器打开 `http://<本机IP>:3000` 即可加入。多个玩家（含真人/AI 混编）共用一个房间。

> 注意：`net/server.py` 默认端口 3000，重启前请先关闭占用该端口的旧进程。

### 模式三：服务器（Java 后端 + 网页）

```bash
cd wolfKill/wolfkill_java

# 编译（仅需一次，JDK 11+）
javac --release 11 Room.java WerewolfServer.java AI.java Game.java LLM.java Config.java Json.java

# 启动服务器（默认端口 3000）
java WerewolfServer --mock                 # 离线
java WerewolfServer --port 8080            # 指定端口（也支持环境变量 PORT）
```

浏览器打开 `http://<本机IP>:3000` 即可加入。

---

## 游戏规则要点

- **胜负**：狼人全部出局 → 好人胜；神职/平民全部出局 → 狼人胜。
- **夜晚顺序**：天黑 → 狼人睁眼杀人 → **女巫睁眼**（先于预言家，可救/毒）→ 预言家查验 → 守卫守人 → 天亮。
- **上警**：第 1 天全员先决定是否参选警长，统一公布后由未参选者投票；当选警长放逐票计 1.5 票。
- **猎人**：被狼刀/放逐时可开枪带走一人；被女巫毒杀不能开枪。
- **遗言**：所有死亡者（夜刀/毒杀/放逐）留公开遗言，猎人改为开枪。

---

## 目录结构

```
wolfKill/
├── main.py                 # 本地命令行模式主程序
├── game.py                 # 纯游戏逻辑/状态机
├── ai.py                   # AI 决策（通过 LLM 推理）
├── config.py               # Python 配置（API Key/模型，读环境变量）
├── llm.py                  # OpenRouter 请求
├── human.py                # 真人交互
├── net/                    # 联机模式（Python 后端 + 网页）
│   ├── server.py
│   └── index.html
├── wolfkill_java/          # 服务器模式（Java 后端 + 网页）
│   ├── WerewolfServer.java # HTTP 服务 + 前端托管
│   ├── Room.java           # 房间状态机/游戏编排
│   ├── AI.java / Game.java / LLM.java / Config.java / Json.java
│   └── index.html
├── wolfkill_deploy/        # 部署副本（前端 + Python 后端）
└── server_mode_test_report.html  # 服务器模式自动化测试报告
```

---

## 常见问题

**Q：加入时卡住 / 刷新出现两个玩家？**
A：已修复为幂等加入（基于浏览器持久化 `clientId`），刷新重连会找回原座位，不会重复创建。

**Q：真实对局报 OpenRouter 请求失败？**
A：检查 `WEREWOLF_API_KEY` 是否正确、模型名是否仍可用（注意 `tencent/hy3:free` 已下架，请用环境变量 `WEREWOLF_MODEL` 切换）。

**Q：AI 思考时页面无响应？**
A：属正常等待；若整局卡死，多为真人玩家挂机，系统会在数秒后自动跳过该玩家。

---

## 安全说明

- 源码中**不含任何硬编码密钥**；OpenRouter Key 仅通过环境变量 `WEREWOLF_API_KEY` 注入。
- 密钥日志 `wolfkill_secret.log`、编译产物 `*.class`、本地私有目录 `.workbuddy/` 等已在 `.gitignore` 中排除，不会进入版本库。
- 公开仓库请勿把真实 Key 写入代码或提交。
