# -*- coding: utf-8 -*-
"""
狼人杀 · 多人联机服务端（与原命令行版完全分开，互不影响）
================================================================
特点：
- 零第三方依赖，只用 Python 标准库（http.server / threading / json）。
- 复用原项目的纯逻辑模块：game.py（引擎）、ai.py（AI 决策）、llm.py（大模型）、config.py。
  —— 不修改、不导入原来的命令行主循环 main.py。
- 支持「多名真人 + AI 补位」：真人不足时由 AI 顶上，凑齐一桌。
- 浏览器进入：电脑开一个端口，其他人用浏览器打开即可加入。
  局域网直接用本机 IP；外网可用 cloudflared / ngrok 等内网穿透（见 net/README.md）。

用法：
    python3 net/server.py                 # 真实对局（默认免费模型 tencent/hy3:free，端口 3000）
    python3 net/server.py --port 3000     # 指定端口
    python3 net/server.py --mock          # 离线：AI 用确定性逻辑，不联网、不消耗额度
    python3 net/server.py --total 8        # 设定一桌总人数（真人+AI），默认 8

安全提示：这是一个用于朋友间娱乐的轻量服务端，没有做鉴权加固，不要暴露到不可信的公网。
"""
import os
import sys
import json
import time
import random
import threading
import secrets as _secrets
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# 让本文件能 import 到同目录里的 game/ai/llm/config
# （部署时 server.py 与这些文件放在同一目录，因此同时把自身目录加入路径）
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
for _p in (_HERE, _ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import config  # noqa: E402
import game as G  # noqa: E402
import ai as A  # noqa: E402


# ============================ 角色配比（按总人数自动生成） ============================
def make_roles(total):
    """根据总人数生成角色列表（狼人/预言家/女巫/猎人/守卫/平民）。"""
    total = max(4, int(total))
    wolves = max(1, (total + 2) // 4)          # 4→1, 6→2, 8→2, 12→3
    roles = ["狼人"] * wolves + ["预言家", "女巫"]
    if total >= 6:
        roles.append("猎人")
    if total >= 7:
        roles.append("守卫")
    while len(roles) < total:
        roles.append("平民")
    return roles[:total]


# ============================ 房间 / 游戏状态 ============================
class Room:
    def __init__(self, total=8):
        self.lock = threading.RLock()
        self.state = "lobby"           # lobby | playing | ended
        self.version = 1               # 每次变更 +1，客户端据此轮询刷新
        self.total = total             # 一桌总人数（真人 + AI）

        # 大厅：token -> {"name":..}
        self.humans = {}
        self.join_order = []           # token 加入顺序
        self.host_token = None

        # 开局后：
        self.game = None
        self.human_pids = set()        # 真人对应的玩家 idx 集合
        self.pid_to_token = {}         # 玩家 idx -> token
        self.token_to_pid = {}         # token -> 玩家 idx

        # 日志
        self.public_log = []           # [str]，所有人可见
        self.private_log = {}          # pid -> [str]，仅该玩家可见（查验结果/被救等）
        self.secret_events = []        # [(set_of_pids_or_None, text)] 机密（狼队讨论等）

        # 待办行动：pid -> {"spec":..,"answer":..,"event":Event}
        self.pending = {}
        self.speaking = None           # 当前正在发言/行动的 pid（UI 提示）
        self.phase_text = "等待开始"
        self.winner = None
        self._thread = None

        # 狼队密谋室（同阵营真人玩家讨论频道）
        self.wolf_chat = []                       # [{name, text, ai}]
        self.wolf_chat_cond = threading.Condition(self.lock)
        self.wolf_discussion_open = False         # 当前是否处于狼队讨论（客户端据此显示密谋室）
        self.wolf_discussion_done = False         # 真人是否已标记结束讨论

        # 已退出游戏的真人玩家（其后续行动由 AI 接管，避免有人离开后游戏卡死）
        self.left = set()

    # ---------- 基础工具 ----------
    def bump(self):
        self.version += 1

    def pub(self, text):
        """公共信息：所有人可见。

        关键：联机模式下 AI 读取的是 game.public_log（ai.py 的
        private_state_text -> public_state_text 只认 game.public_log），
        而网页端读的是 room.public_log。两者必须同步，否则 AI 收不到
        发言、死亡公告、主持人播报等任何公开信息，等于瞎子对局。
        这里双写，确保网页与 AI 看到完全一致的内容。
        """
        with self.lock:
            self.public_log.append(text)
            if self.game is not None:
                self.game.public_log.append(text)
            self.bump()

    def notify(self, pid, text):
        """私密提示：仅该玩家可见（如预言家查验结果）。"""
        with self.lock:
            self.private_log.setdefault(pid, []).append(text)
            self.bump()

    def sec(self, text, pids=None):
        """机密事件：仅 pids 集合中的真人可见（AI 决策/狼队讨论等）。pids=None 表示无人可见。"""
        with self.lock:
            self.secret_events.append((pids, text))
            self.bump()

    def set_phase(self, text, speaking=None):
        with self.lock:
            self.phase_text = text
            self.speaking = speaking
            self.bump()

    def append_wolf_chat(self, name, text, is_ai=False):
        """往狼队密谋室追加一条消息（仅狼可见）。"""
        with self.lock:
            self.wolf_chat.append({"name": name, "text": text, "ai": is_ai})
            self.bump()

    # ---------- 大厅 ----------
    def add_human(self, name):
        with self.lock:
            if self.state != "lobby":
                return None, "游戏已经开始，无法加入。"
            if len(self.humans) >= self.total:
                return None, "房间已满。"
            token = _secrets.token_hex(8)
            name = (name or "").strip()[:12] or f"玩家{len(self.humans) + 1}"
            self.humans[token] = {"name": name}
            self.join_order.append(token)
            if self.host_token is None:
                self.host_token = token
            self.bump()
            return token, None

    def set_total(self, token, total):
        with self.lock:
            if token != self.host_token or self.state != "lobby":
                return False
            self.total = max(4, min(int(total), 12))
            if self.total < len(self.humans):
                self.total = len(self.humans)
            self.bump()
            return True

    # ---------- 人类行动：阻塞等待，超时兜底 ----------
    def ask(self, pid, spec, timeout=180):
        # 已退出游戏的玩家：立即用默认值返回，不阻塞，避免卡住整局
        with self.lock:
            if pid in self.left:
                return spec.get("default")
        ev = threading.Event()
        with self.lock:
            self.pending[pid] = {"spec": spec, "answer": None, "event": ev}
            self.bump()
        got = ev.wait(timeout)
        with self.lock:
            entry = self.pending.pop(pid, None)
            self.bump()
        if got and entry and entry.get("answer") is not None:
            return entry["answer"]
        # 超时：返回默认值（避免有人掉线拖死整局）
        return spec.get("default")

    def submit(self, pid, value):
        with self.lock:
            entry = self.pending.get(pid)
            if not entry:
                return False
            entry["answer"] = value
            entry["event"].set()
            self.bump()
            return True

    def exit_player(self, token):
        """真人主动退出游戏：释放其待办、标记离开，后续行动由 AI 接管，避免卡死。"""
        with self.lock:
            if self.state == "lobby":
                if token in self.humans:
                    name = self.humans[token]["name"]
                    del self.humans[token]
                    if token in self.join_order:
                        self.join_order.remove(token)
                    if self.host_token == token:
                        self.host_token = self.join_order[0] if self.join_order else None
                    self.bump()
                    return True, None
                return False, "你还没有加入房间。"
            pid = self.token_to_pid.get(token)
            if pid is None:
                return False, "无效身份"
            if pid in self.left:
                return True, None
            self.left.add(pid)
            name = self.game.get(pid).name
            # 若有正在等待该玩家的行动，立即用默认值了结，解开阻塞
            entry = self.pending.pop(pid, None)
            if entry:
                entry["answer"] = entry["spec"].get("default")
                entry["event"].set()
            self.pub(f"🚪 {name} 退出了游戏，剩余行动由 AI 接管。")
            self.bump()
        # 唤醒狼队讨论的等待（退出者若是真人狼）
        with self.wolf_chat_cond:
            self.wolf_chat_cond.notify_all()
        return True, None

    # ---------- 快照（按 token 过滤可见信息） ----------
    def snapshot(self, token):
        with self.lock:
            base = {"v": self.version, "state": self.state, "phase": self.phase_text}
            if self.state == "lobby":
                base["lobby"] = {
                    "players": [self.humans[t]["name"] for t in self.join_order],
                    "you_name": self.humans.get(token, {}).get("name", ""),
                    "is_host": token == self.host_token,
                    "total": self.total,
                    "n_humans": len(self.humans),
                    "n_ai": max(0, self.total - len(self.humans)),
                }
                base["token_valid"] = token in self.humans
                return base

            pid = self.token_to_pid.get(token)
            g = self.game
            # 公共玩家列表
            players = []
            for idx in g.order:
                p = g.get(idx)
                info = {
                    "pid": idx,
                    "name": p.name,
                    "alive": p.alive,
                    "is_human": idx in self.human_pids,
                    "is_you": idx == pid,
                }
                if self.state == "ended":
                    info["role"] = p.role
                players.append(info)
            base["players"] = players
            base["public_log"] = self.public_log
            base["speaking"] = self.speaking
            base["winner"] = self.winner
            base["sheriff"] = g.sheriff
            base["token_valid"] = pid is not None

            # 你的身份与私密信息
            if pid is not None:
                p = g.get(pid)
                you = {
                    "pid": pid, "name": p.name, "role": p.role,
                    "is_wolf": p.is_wolf,
                    "alive": p.alive,
                }
                if p.is_wolf:
                    you["teammates"] = [
                        {"pid": m, "name": g.get(m).name} for m in p.wolf_teammates
                    ]
                base["you"] = you
                base["private_log"] = self.private_log.get(pid, [])
                # 你可见的机密（狼队讨论：仅狼可见）
                vis = []
                for pids, text in self.secret_events:
                    if pids and pid in pids:
                        vis.append(text)
                base["secret_log"] = vis
                # 狼队密谋室（仅狼人可见）
                if p.is_wolf:
                    base["wolf_chat"] = self.wolf_chat
                    base["wolf_discussion_open"] = self.wolf_discussion_open
                else:
                    base["wolf_chat"] = []
                    base["wolf_discussion_open"] = False
                # 待办
                entry = self.pending.get(pid)
                base["pending"] = entry["spec"] if entry else None
            else:
                base["you"] = None
                base["pending"] = None
            return base

    # ---------- 开局 ----------
    def start(self, token):
        with self.lock:
            if token != self.host_token:
                return False, "只有房主可以开始游戏。"
            if self.state != "lobby":
                return False, "游戏已经开始。"
            if len(self.humans) < 1:
                return False, "至少需要 1 名玩家。"
            total = max(self.total, len(self.humans))
            # 用配置临时驱动 game.Game 构建（本进程独立，不影响 CLI）
            config.HUMAN_PLAYER = False
            roles = make_roles(total)
            from collections import OrderedDict, Counter as _C
            cnt = _C(roles)
            config.ROLES_CONFIG = OrderedDict()
            for r in ["狼人", "预言家", "女巫", "猎人", "守卫", "平民"]:
                if cnt.get(r):
                    config.ROLES_CONFIG[r] = cnt[r]
            g = G.Game()  # 全部按 AI 建好并随机分配角色
            # 将前 N 个位置指定为真人（其余保持 AI）
            human_tokens = list(self.join_order)
            random.shuffle(g.order)  # 打乱座位，避免真人固定拿某位置
            g.order = sorted(g.order)  # 座位按号码显示，但角色已随机
            # 选择哪些 idx 作为真人：随机挑 len(humans) 个位置
            all_idx = list(g.players.keys())
            random.shuffle(all_idx)
            human_idx = sorted(all_idx[:len(human_tokens)])
            self.human_pids = set(human_idx)
            for t, idx in zip(human_tokens, human_idx):
                self.pid_to_token[idx] = t
                self.token_to_pid[t] = idx
                g.players[idx].name = self.humans[t]["name"]
                g.players[idx].personality = ""  # 真人无需 AI 性格
            self.game = g
            self.state = "playing"
            self.winner = None
            self.wolf_chat = []
            self.wolf_discussion_open = False
            self.wolf_discussion_done = False
            self.left = set()
            self.bump()
        # 启动后台游戏线程
        self._thread = threading.Thread(target=run_game, args=(self,), daemon=True)
        self._thread.start()
        return True, None

    def end(self, winner):
        with self.lock:
            self.winner = winner
            self.state = "ended"
            self.speaking = None
            self.phase_text = "游戏结束"
            self.bump()


ROOM = Room()


# ============================ 行动封装（真人走 ask，AI 走 ai.py） ============================
def _alive_options(game, exclude=(), only=None):
    out = []
    for p in game.alive_players():
        if p.idx in exclude:
            continue
        if only is not None and p.idx not in only:
            continue
        out.append({"value": p.idx, "label": f"{p.idx}号 {p.name}"})
    return out


def do_guard(room, game, guard):
    valid = [p.idx for p in game.alive_players()
             if p.idx != guard.idx and p.idx != guard.last_protected]
    if not valid:
        valid = [p.idx for p in game.alive_players() if p.idx != guard.idx]
    if guard.idx in room.human_pids:
        spec = {
            "kind": "guard", "input": "choice",
            "prompt": "🛡️ 你是守卫，选择今晚守护谁（不能守自己/连守同一人）",
            "options": _alive_options(game, only=set(valid)),
            "default": random.choice(valid) if valid else None,
        }
        return room.ask(guard.idx, spec)
    return A.ai_guard_protect(game, guard, valid)


def do_seer(room, game, seer):
    valid = [p.idx for p in game.alive_players()
             if p.idx != seer.idx and p.idx not in seer.checked]
    if not valid:
        return None
    if seer.idx in room.human_pids:
        spec = {
            "kind": "seer", "input": "choice",
            "prompt": "🔮 你是预言家，选择今晚查验谁",
            "options": _alive_options(game, only=set(valid)),
            "default": random.choice(valid),
        }
        return room.ask(seer.idx, spec)
    return A.ai_seer_check(game, seer)


def do_witch(room, game, witch, killed_idx):
    if witch.idx in room.human_pids:
        killed_name = game.get(killed_idx).name if killed_idx else "无人"
        poison_opts = _alive_options(game, exclude={killed_idx} if killed_idx else set())
        spec = {
            "kind": "witch", "input": "witch",
            "prompt": "🧪 你是女巫，请决定用药",
            "killed_idx": killed_idx,
            "killed_name": killed_name,
            "has_antidote": witch.has_antidote,
            "has_poison": witch.has_poison,
            "poison_options": poison_opts,
            "default": {"save": False, "poison": 0},
        }
        ans = room.ask(witch.idx, spec)
        ans = ans or {"save": False, "poison": 0}
        save = bool(ans.get("save")) and witch.has_antidote
        poison = ans.get("poison") or 0
        poison = poison if (poison and witch.has_poison) else None
        return save, poison
    return A.ai_witch_action(game, witch, killed_idx)


def do_wolf(room, game, wolf, discussion):
    # 允许自刀（含自己），也可杀队友；不可选已出局者
    cands = [p.idx for p in game.alive_players()]
    if wolf.idx in room.human_pids:
        mates = "、".join(f"{m}号{game.get(m).name}" for m in wolf.wolf_teammates) or "无"
        spec = {
            "kind": "kill", "input": "choice",
            "prompt": f"🐺 狼队投票：你是狼人（队友：{mates}）。请投出你要击杀的目标（可选队友或自己；狼队多数决定，平票随机）",
            "options": _alive_options(game, only=set(cands)),
            "discussion": discussion,
            "default": random.choice(cands) if cands else None,
        }
        t = room.ask(wolf.idx, spec)
        return t, "（真人狼人的选择）"
    return A.ai_wolf_propose(game, wolf, discussion)


def do_vote(room, game, player, restrict=None):
    cands = [p.idx for p in game.alive_players() if p.idx != player.idx]
    if restrict:
        cands = [c for c in cands if c in restrict]
    if player.idx in room.human_pids:
        spec = {
            "kind": "vote", "input": "choice",
            "prompt": "🗳️ 投票放逐你怀疑的狼人（也可以弃票）",
            "options": _alive_options(game, only=set(cands)),
            "allow_abstain": True,
            "default": 0,  # 超时=弃票
        }
        return room.ask(player.idx, spec)
    return A.ai_vote(game, player, restrict)


def do_lastwill(room, game, player):
    """玩家出局时留下遗言（可留空）。"""
    if player.idx in room.human_pids:
        spec = {
            "kind": "lastwill", "input": "text",
            "prompt": f"💬 {player.idx}号 {player.name}，请留下你的遗言（可留空）",
            "default": "",
        }
        t = room.ask(player.idx, spec)
        return (t or "").strip()
    return A.ai_lastwill(game, player)


def do_run_sheriff(room, game, player):
    """第 1 天『上警』：是否竞选警长 + 竞选发言。返回 (run: bool, speech: str)。"""
    if player.idx in room.human_pids:
        spec = {
            "kind": "sheriff_run", "input": "sheriff_run",
            "prompt": f"🔔 {player.idx}号 {player.name}，是否上警竞选警长？",
            "default": {"run": False, "speech": ""},
        }
        ans = room.ask(player.idx, spec)
        ans = ans or {"run": False, "speech": ""}
        return bool(ans.get("run")), (ans.get("speech") or "").strip()
    return A.ai_run_for_sheriff(game, player)


def do_vote_sheriff(room, game, player, candidates):
    """选举警长：在候选人中选一人或弃票（0）。"""
    cands = [c for c in candidates if game.get(c) and game.get(c).alive]
    if player.idx in room.human_pids:
        spec = {
            "kind": "vote", "input": "choice",
            "prompt": "🗳️ 选举警长：投给你支持的候选人（也可弃票）",
            "options": _alive_options(game, only=set(cands)),
            "allow_abstain": True,
            "default": 0,
        }
        return room.ask(player.idx, spec)
    return A.ai_vote_sheriff(game, player, candidates)


def do_speech(room, game, player):
    if player.idx in room.human_pids:
        spec = {
            "kind": "speech", "input": "text",
            "prompt": f"💬 轮到你（{player.idx}号 {player.name}）发言",
            "default": "（沉默）",
        }
        t = room.ask(player.idx, spec)
        return (t or "").strip() or "（沉默）"
    return A.ai_speak(game, player)


def do_hunter(room, game, hunter):
    valid = [p.idx for p in game.alive_players() if p.idx != hunter.idx]
    if not valid:
        return None
    if hunter.idx in room.human_pids:
        spec = {
            "kind": "hunter", "input": "choice",
            "prompt": "💥 你是猎人，出局了！开枪带走一名玩家",
            "options": _alive_options(game, only=set(valid)),
            "default": random.choice(valid),
        }
        return room.ask(hunter.idx, spec)
    return A.ai_hunter_shoot(game, hunter, valid)


# ============================ 游戏主流程（后台线程） ============================
def apply_night_deaths(room, game, wolf_target, guard_target, saved, poison_target):
    deaths = []
    if wolf_target is not None and guard_target != wolf_target and not saved:
        deaths.append((wolf_target, "wolf"))
    if poison_target:
        deaths.append((poison_target, "poison"))

    pending = []
    dead_names = []
    newly_dead = []
    for idx, cause in deaths:
        p = game.get(idx)
        if not p.alive:
            continue
        p.alive = False
        # 公开信息：只说"昨晚谁死了"，不写死因（狼刀/毒杀）、不写死者身份（角色）。
        dead_names.append(f"{idx}号{p.name}")
        newly_dead.append((p, cause))
        if p.role == G.HUNTER and cause != "poison":
            pending.append(p)

    if dead_names:
        room.pub(f"🌅 第 {game.day} 天清晨：昨晚 [{'、'.join(dead_names)}] 死了。")
    else:
        room.pub(f"🌅 第 {game.day} 天清晨：昨夜风平浪静，无人死亡。")

    # 遗言：仅在「首晚被刀」时留（被放逐的遗言在放逐环节处理；猎人由开枪代替）。
    for p, cause in newly_dead:
        if p.role == G.HUNTER:
            continue
        if not (game.day == 1 and cause == "wolf"):
            continue
        text = do_lastwill(room, game, p)
        room.pub(f"💬 {p.idx}号{p.name} 的遗言：{text}")

    while pending:
        h = pending.pop(0)
        room.set_phase("猎人开枪", speaking=h.idx)
        t = do_hunter(room, game, h)
        if t and game.get(t).alive:
            tp = game.get(t)
            tp.alive = False
            room.pub(f"💥 猎人 {h.idx}号{h.name} 开枪带走了 {t}号{tp.name}！")
            if tp.role == G.HUNTER:
                pending.append(tp)


def run_wolf_discussion(room, game, human_wolves, ai_wolves, max_rounds=12, timeout=180):
    """狼队密谋室（同阵营真人玩家讨论频道）。

    - 若有真人狼：AI 狼先抛出初始意见，真人狼可在客户端密谋室里自由聊天，
      AI 狼（及群内其他真人狼）实时可见并逐一回应；任一直人点「结束讨论」后进入最终刀人。
    - 若全是 AI 狼：不开启交互（由 caller 直接走确定性提议）。
    - 已退出的真人狼不参与讨论；全部真人狼退出时自动收尾，避免有人离开后卡死。
    - 用条件变量等待真人发言，最长 timeout 秒自动收尾。
    """
    # 跳过已退出游戏的真人狼
    human_wolves = [w for w in human_wolves if w.idx not in room.left]
    if not human_wolves:
        return  # 没有真人狼参与，直接走确定性提议
    with room.lock:
        room.wolf_chat = []
        room.wolf_discussion_open = True
        room.wolf_discussion_done = False
    # AI 狼的初始意见（作为群内消息）
    for w in ai_wolves:
        t, r = A.ai_wolf_propose(game, w, "")
        if t is not None:
            tgt = game.get(t)
            room.append_wolf_chat(f"{w.idx}号{w.name}", f"提议杀 {t}号{tgt.name}：{r}", is_ai=True)
    # 等待真人狼聊天 / 标记结束
    rounds = 0
    while rounds < max_rounds:
        with room.lock:
            if room.wolf_discussion_done:
                break
            if all(w.idx in room.left for w in human_wolves):
                room.wolf_discussion_done = True
                break
        with room.lock:
            notified = room.wolf_chat_cond.wait(timeout=timeout)
        rounds += 1
        with room.lock:
            if room.wolf_discussion_done:
                break
            if all(w.idx in room.left for w in human_wolves):
                room.wolf_discussion_done = True
                break
        if not notified:
            break  # 超时无人发言，结束讨论
        # 真人发了消息：AI 狼逐一回应（纯真人狼阵营则跳过）
        disc_text = "\n".join(f"{m['name']}：{m['text']}" for m in room.wolf_chat)
        for w in ai_wolves:
            resp = A.ai_wolf_chat(game, w, disc_text)
            if resp:
                room.append_wolf_chat(f"{w.idx}号{w.name}", resp, is_ai=True)
    with room.lock:
        room.wolf_discussion_open = False


def night_phase(room, game):
    room.pub(f"🌙 ===== 第 {game.day} 天 · 黑夜降临 =====")
    room.set_phase(f"第 {game.day} 天 · 黑夜")
    room.pub("🌙 天黑请闭眼。")  # 主持人播报：进入夜晚
    wolves = game.alive_wolves()
    # 在全部玩家中查找神职（即使已死亡也要保留引用），以便夜晚仍播报「XX请睁眼」，
    # 防止玩家由播报缺失推断出死者身份。
    seer = next((p for p in game.players.values() if p.role == G.SEER), None)
    witch = next((p for p in game.players.values() if p.role == G.WITCH), None)
    guard = next((p for p in game.players.values() if p.role == G.GUARD), None)

    # 0) 守卫；即使守卫已死亡也照常播报，避免泄露身份
    guard_target = None
    if guard:
        room.pub("🛡️ 守卫请睁眼……")
        if guard.alive:
            room.set_phase("守卫行动中", speaking=guard.idx if guard.idx in room.human_pids else None)
            guard_target = do_guard(room, game, guard)
            if guard_target:
                guard.last_protected = guard_target
                room.sec(f"[守卫] 守护了 {guard_target}号{game.get(guard_target).name}",
                         pids={guard.idx} if guard.idx in room.human_pids else None)
        room.pub("🛡️ 守卫请闭眼。")

    # 1) 狼人（先开启狼队密谋室让同阵营真人玩家讨论，再各自决定 + 多数决）
    killed_idx = None
    if wolves:
        room.pub("🐺 狼人请睁眼……")
        from collections import Counter
        human_wolves = [w for w in wolves if w.idx in room.human_pids]
        ai_wolves = [w for w in wolves if w.idx not in room.human_pids]
        # 有真人狼时进入可交互的狼队密谋室；全 AI 时直接走确定性提议
        if human_wolves:
            run_wolf_discussion(room, game, human_wolves, ai_wolves)
        # 各自给出最终击杀决定（带入完整讨论记录）
        discussion_text = "\n".join(f"{m['name']}：{m['text']}" for m in room.wolf_chat)
        proposals = {}
        for w in wolves:
            room.set_phase("狼人行动中", speaking=w.idx if w.idx in room.human_pids else None)
            t, r = do_wolf(room, game, w, discussion_text)
            proposals[w.idx] = t
            tgt = game.get(t)
            line = f"{w.idx}号{w.name} → 提议杀 {t}号{tgt.name}：{r}"
        killed_idx, vote_cnt = game.resolve_wolf_kill(proposals)
        wolf_human_pids = {w.idx for w in wolves if w.idx in room.human_pids}
        # 狼队讨论记录（仅狼可见）
        for m in room.wolf_chat:
            tag = "🤖" if m["ai"] else "👤"
            room.sec(f"[狼队讨论] {tag}{m['name']}：{m['text']}", pids=wolf_human_pids or None)
        # 狼队投票：逐狼计票（仅狼可见），多数决定、平票随机打破
        for w in wolves:
            t = proposals.get(w.idx)
            tgt = game.get(t) if t is not None else None
            room.sec(f"[狼队投票] {w.idx}号{w.name} → 杀 {t}号{tgt.name if tgt else '（无效）'}",
                     pids=wolf_human_pids or None)
        if vote_cnt:
            maxc = max(vote_cnt.values())
            tied = [t for t, c in vote_cnt.items() if c == maxc]
            if len(tied) > 1:
                room.sec(f"[狼队投票] 平票（各 {maxc} 票），随机选中击杀目标 → "
                         f"{killed_idx}号{game.get(killed_idx).name}",
                         pids=wolf_human_pids or None)
            else:
                room.sec(f"[狼队] 今夜击杀 {killed_idx}号{game.get(killed_idx).name}",
                         pids=wolf_human_pids or None)
        room.wolf_chat = []
        room.pub("🐺 狼人请闭眼。")

    # 2) 预言家；即使预言家已死亡也照常播报，避免泄露身份
    if seer:
        room.pub("🔮 预言家请睁眼……")
        if seer.alive:
            room.set_phase("预言家行动中", speaking=seer.idx if seer.idx in room.human_pids else None)
            tid = do_seer(room, game, seer)
            if tid:
                result = "狼人" if game.get(tid).is_wolf else "好人"
                seer.checked[tid] = result
                if seer.idx in room.human_pids:
                    room.notify(seer.idx, f"🔮 你查验了 {tid}号{game.get(tid).name}，结果：【{result}】")
        room.pub("🔮 预言家请闭眼。")

    # 3) 女巫（被守卫挡刀则视为无人被袭）；即使女巫已死亡/无药也照常播报，避免泄露身份
    saved, poison_idx = False, None
    witch_sees = killed_idx if (killed_idx is not None and guard_target != killed_idx) else None
    if witch:
        room.pub("🧪 女巫请睁眼……")
        if witch.alive and (witch.has_antidote or witch.has_poison):
            room.set_phase("女巫行动中", speaking=witch.idx if witch.idx in room.human_pids else None)
            saved, poison_idx = do_witch(room, game, witch, witch_sees)
            if saved and witch.has_antidote:
                witch.has_antidote = False
                room.sec("[女巫] 使用了解药",
                         pids={witch.idx} if witch.idx in room.human_pids else None)
                if witch.idx in room.human_pids:
                    room.notify(witch.idx, "🧪 你使用了解药。")
            if poison_idx and witch.has_poison:
                witch.has_poison = False
                room.sec(f"[女巫] 使用了毒药 → {poison_idx}号{game.get(poison_idx).name}",
                         pids={witch.idx} if witch.idx in room.human_pids else None)
                if witch.idx in room.human_pids:
                    room.notify(witch.idx, f"🧪 你使用了毒药，目标 {poison_idx}号{game.get(poison_idx).name}。")
        room.pub("🧪 女巫请闭眼。")

    # 4) 结算
    room.pub("☀️ 天亮了。")
    if killed_idx is None and not poison_idx:
        room.pub(f"🌅 第 {game.day} 天清晨：昨夜风平浪静，无人死亡。")
    else:
        apply_night_deaths(room, game, killed_idx, guard_target, saved, poison_idx)


def _fmt_w(w):
    """计票权重格式化：整数去小数，否则保留一位。"""
    return str(int(w)) if w == int(w) else str(w)


def server_sheriff_phase(room, game):
    """第 1 天『上警竞选』：是否上警 + 竞选发言，再选举警长（警长放逐投票计 1.5 票）。"""
    room.pub("🔔 第 1 天 · 上警竞选警长环节")
    room.set_phase("上警竞选", speaking=None)
    candidates = []
    for idx in game.order:
        p = game.get(idx)
        if not p.alive:
            continue
        room.set_phase("上警竞选", speaking=idx)
        run, speech = do_run_sheriff(room, game, p)
        if run:
            candidates.append(p.idx)
            speech = speech or "（无发言）"
            room.pub(f"  {idx}号{p.name} 上警：{speech}")
        else:
            room.pub(f"  {idx}号{p.name} 不上警")
    if not candidates:
        room.pub("  无人上警，本轮无警长。")
        return
    room.pub("—— 选举警长 ——")
    votes = {}
    for idx in game.order:
        p = game.get(idx)
        if not p.alive:
            continue
        room.set_phase("选举警长", speaking=idx)
        t = do_vote_sheriff(room, game, p, candidates)
        votes[idx] = t
        disp = "弃票" if t == 0 else f"投给 {t}号"
        room.pub(f"  {idx}号{p.name} → {disp}")
    from collections import Counter
    cnt = Counter(v for v in votes.values() if v and v != 0)
    if not cnt:
        room.pub("  警长选举：全员弃票，本轮无警长。")
        return
    top, n = cnt.most_common(1)[0]
    tied = [t for t, c in cnt.items() if c == n]
    if len(tied) > 1:
        room.pub(f"  警长选举平票（各 {n} 票），本轮无警长。")
        return
    game.sheriff = top
    room.pub(f"🚩 {top}号{game.get(top).name} 当选警长！（放逐投票计 1.5 票）")


def server_exile_victim(room, game, victim, n_text=""):
    """放逐一名玩家出局：记录结果、留下遗言（猎人由开枪代替）。"""
    victim.alive = False
    if n_text:
        room.pub(f"🗳️ 投票结果：{victim.idx}号{victim.name} 被放逐出局（{n_text} 票）。")
    else:
        room.pub(f"🗳️ 投票结果：{victim.idx}号{victim.name} 被放逐出局。")
    if victim.role != G.HUNTER:
        text = do_lastwill(room, game, victim)
        room.pub(f"💬 {victim.idx}号{victim.name} 的遗言：{text}")
    if victim.role == G.HUNTER:
        room.set_phase("猎人开枪", speaking=victim.idx)
        t = do_hunter(room, game, victim)
        if t and game.get(t).alive:
            tp = game.get(t)
            tp.alive = False
            room.pub(f"💥 猎人 {victim.idx}号{victim.name} 开枪带走了 {t}号{tp.name}！")


def server_resolve_exile(room, game, votes):
    """按带权计票放逐；平票则进行第二次投票（只在平票者间选）。"""
    winner, tied, maxw = game.exile_tally(votes)
    abstains = sum(1 for v in votes.values() if not v or v == 0)
    if winner is not None:
        server_exile_victim(room, game, game.get(winner), _fmt_w(maxw))
        return
    if not tied:
        room.pub(f"🗳️ 投票结果：全员弃票（{abstains}票弃票），流局，无人被放逐。")
        return
    names = "、".join(f"{t}号{game.get(t).name}" for t in tied)
    room.pub(f"🗳️ 投票结果：{names} 平票（各 {_fmt_w(maxw)} 票），进行第二次投票。")
    votes2 = {}
    for idx in game.order:
        p = game.get(idx)
        if not p.alive:
            continue
        room.set_phase("二次投票", speaking=idx)
        t = do_vote(room, game, p, restrict=set(tied))
        votes2[idx] = t
        disp = "弃票" if t == 0 else f"投给 {t}号"
        room.pub(f"  {idx}号{p.name} → {disp}")
    winner2, tied2, maxw2 = game.exile_tally(votes2)
    if winner2 is not None:
        server_exile_victim(room, game, game.get(winner2), _fmt_w(maxw2))
    else:
        room.pub("🗳️ 第二次投票仍平票，流局，无人被放逐。")


def day_phase(room, game):
    room.pub(f"☀️ ===== 第 {game.day} 天 · 白天 =====")
    # 第 1 天：上警竞选警长
    if game.day == 1 and game.sheriff is None:
        server_sheriff_phase(room, game)
    room.set_phase(f"第 {game.day} 天 · 白天发言")
    room.pub("—— 发言环节 ——")
    for idx in game.order:
        p = game.get(idx)
        if not p.alive:
            continue
        room.set_phase("发言中", speaking=idx)
        text = do_speech(room, game, p)
        room.pub(f"  {idx}号{p.name}：{text}")

    room.set_phase(f"第 {game.day} 天 · 投票", speaking=None)
    room.pub("—— 投票放逐环节 ——")
    votes = {}
    for idx in game.order:
        p = game.get(idx)
        if not p.alive:
            continue
        room.set_phase("投票中", speaking=idx)
        t = do_vote(room, game, p)
        votes[idx] = t
        disp = "弃票" if t == 0 else f"投给 {t}号"
        room.pub(f"  {idx}号{p.name} → {disp}")

    server_resolve_exile(room, game, votes)


def run_game(room):
    game = room.game
    try:
        room.pub("🎮 游戏开始！祝各位好运。")
        while True:
            game.day += 1
            night_phase(room, game)
            w = game.check_winner()
            if w:
                break
            day_phase(room, game)
            w = game.check_winner()
            if w:
                break
    except Exception as e:  # noqa: BLE001
        room.pub(f"[系统] 运行异常：{e}")
        w = game.check_winner() or "好人"
    # 揭示身份
    room.pub("=" * 30)
    room.pub("🐺 狼人阵营胜利！" if w == "狼人" else "🏆 好人阵营胜利！")
    room.pub("【最终身份揭示】")
    for idx in game.order:
        p = game.get(idx)
        tag = "存活" if p.alive else "出局"
        who = "（真人）" if idx in room.human_pids else ""
        room.pub(f"  {idx}号{p.name}{who}：{p.role}（{tag}）")
    room.end(w)


# ============================ HTTP 服务 ============================
INDEX_PATH = os.path.join(_HERE, "index.html")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静默
        pass

    def _send(self, code, body, ctype="application/json; charset=utf-8"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _json(self, code, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False))

    def _read_json(self):
        try:
            n = int(self.headers.get("Content-Length", 0))
            raw = self.rfile.read(n) if n else b"{}"
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:  # noqa: BLE001
            return {}

    def do_GET(self):
        u = urlparse(self.path)
        if u.path in ("/", "/index.html"):
            try:
                with open(INDEX_PATH, "rb") as f:
                    self._send(200, f.read(), "text/html; charset=utf-8")
            except FileNotFoundError:
                self._send(404, "index.html not found")
            return
        if u.path == "/api/state":
            q = parse_qs(u.query)
            token = (q.get("token") or [""])[0]
            self._json(200, ROOM.snapshot(token))
            return
        self._send(404, "not found")

    def do_POST(self):
        u = urlparse(self.path)
        body = self._read_json()
        if u.path == "/api/join":
            token, err = ROOM.add_human(body.get("name", ""))
            if err:
                self._json(200, {"ok": False, "error": err})
            else:
                self._json(200, {"ok": True, "token": token})
            return
        if u.path == "/api/config":
            ok = ROOM.set_total(body.get("token", ""), body.get("total", 8))
            self._json(200, {"ok": ok})
            return
        if u.path == "/api/start":
            ok, err = ROOM.start(body.get("token", ""))
            self._json(200, {"ok": ok, "error": err})
            return
        if u.path == "/api/action":
            token = body.get("token", "")
            pid = ROOM.token_to_pid.get(token)
            if pid is None:
                self._json(200, {"ok": False, "error": "无效身份"})
                return
            ok = ROOM.submit(pid, body.get("value"))
            self._json(200, {"ok": ok})
            return
        if u.path == "/api/chat":
            token = body.get("token", "")
            pid = ROOM.token_to_pid.get(token)
            g = ROOM.game
            p = g.get(pid) if (g and pid is not None) else None
            if not p or not p.is_wolf:
                self._json(200, {"ok": False, "error": "你不是狼人，无法进入狼队密谋室"})
                return
            if not ROOM.wolf_discussion_open:
                self._json(200, {"ok": False, "error": "当前不是狼队讨论时间"})
                return
            text = (body.get("text", "") or "").strip()
            done = bool(body.get("done"))
            if text:
                ROOM.append_wolf_chat(p.name, text, is_ai=False)
            if done:
                with ROOM.lock:
                    ROOM.wolf_discussion_done = True
            with ROOM.lock:
                ROOM.wolf_chat_cond.notify_all()
            self._json(200, {"ok": True})
            return
        if u.path == "/api/exit":
            ok, err = ROOM.exit_player(body.get("token", ""))
            self._json(200, {"ok": ok, "error": err})
            return
        self._send(404, "not found")


def _local_ip():
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:  # noqa: BLE001
        return "127.0.0.1"


def main():
    port = 3000
    argv = sys.argv[1:]
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--mock":
            config.USE_MOCK = True
        elif a == "--port" and i + 1 < len(argv):
            port = int(argv[i + 1]); i += 1
        elif a.startswith("--port="):
            port = int(a.split("=", 1)[1])
        elif a == "--total" and i + 1 < len(argv):
            ROOM.total = max(4, min(int(argv[i + 1]), 12)); i += 1
        elif a.startswith("--total="):
            ROOM.total = max(4, min(int(a.split("=", 1)[1]), 12))
        elif a == "--model" and i + 1 < len(argv):
            config.MODEL = argv[i + 1]; i += 1
        elif a.startswith("--model="):
            config.MODEL = a.split("=", 1)[1]
        i += 1

    ip = _local_ip()
    srv = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    print("=" * 56)
    print("🐺 狼人杀 · 多人联机服务已启动")
    print("=" * 56)
    print(f"  本机访问：   http://127.0.0.1:{port}")
    print(f"  局域网访问： http://{ip}:{port}   （同一 WiFi/网络的朋友用这个）")
    print(f"  一桌人数：   {ROOM.total} 人（真人 + AI 补位）")
    print(f"  模型：       {'离线 mock' if config.USE_MOCK else config.MODEL}")
    print("  外网联机：   见 net/README.md（cloudflared / ngrok 内网穿透）")
    print("  停止服务：   Ctrl + C")
    print("=" * 56)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\n[服务已停止]")
        srv.shutdown()


if __name__ == "__main__":
    main()
