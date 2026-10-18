# -*- coding: utf-8 -*-
"""
狼人杀游戏引擎：角色、玩家状态、公共/私密信息、胜负判定、状态文本格式化。
本文件只包含「纯逻辑与状态」，不发起任何 LLM 调用。
"""
import random

import config

# 角色常量
WOLF = "狼人"
SEER = "预言家"
WITCH = "女巫"
HUNTER = "猎人"
GUARD = "守卫"
VILLAGER = "平民"

GOOD_ROLES = {SEER, WITCH, HUNTER, GUARD, VILLAGER}


class Player:
    def __init__(self, idx, name, is_human=False, personality=""):
        self.idx = idx              # 1-based 玩家号
        self.name = name
        self.is_human = is_human
        self.personality = personality
        self.role = None
        self.is_wolf = False
        self.alive = True
        # 预言家：已查验记录 {被查玩家idx: "狼人"/"好人"}
        self.checked = {}
        # 女巫药剂
        self.has_antidote = True
        self.has_poison = True
        # 守卫：上一晚守护的玩家 idx（用于"不能连守同一人"规则）
        self.last_protected = None
        # 狼人队友（idx 列表，不含自己）
        self.wolf_teammates = []

    def __repr__(self):
        tag = "你" if self.is_human else self.name
        return f"{self.idx}号[{tag}]"


class Game:
    def __init__(self):
        self.players = {}            # idx -> Player
        self.order = []              # 玩家 idx 顺序
        self.day = 0                 # 第几天（从 1 开始）
        self.public_log = []         # 所有人可见的公共事件
        self.human_idx = None        # 真人玩家 idx
        self.sheriff = None          # 警长玩家 idx（上警竞选产生，放逐投票计 1.5 票）
        self._build_players()
        self._assign_roles()

    # ---------------- 初始化 ----------------
    def _build_players(self):
        role_list = []
        for role, cnt in config.ROLES_CONFIG.items():
            role_list.extend([role] * cnt)
        total = len(role_list)

        # 真人放在 1 号
        if config.HUMAN_PLAYER:
            p = Player(1, "你", is_human=True)
            self.players[1] = p
            self.human_idx = 1
            self.order.append(1)

        # AI 玩家
        ai_idx = 2 if config.HUMAN_PLAYER else 1
        for i in range(total - (1 if config.HUMAN_PLAYER else 0)):
            if i < len(config.AI_PROFILES):
                name, personality = config.AI_PROFILES[i]
            else:
                name, personality = f"玩家{ai_idx}", "沉默寡言"
            self.players[ai_idx] = Player(ai_idx, name, personality=personality)
            self.order.append(ai_idx)
            ai_idx += 1

    def _assign_roles(self):
        role_list = []
        for role, cnt in config.ROLES_CONFIG.items():
            role_list.extend([role] * cnt)
        random.shuffle(role_list)
        for idx, role in zip(self.order, role_list):
            p = self.players[idx]
            p.role = role
            p.is_wolf = (role == WOLF)
        # 设置狼队友
        wolves = [p.idx for p in self.players.values() if p.is_wolf]
        for p in self.players.values():
            if p.is_wolf:
                p.wolf_teammates = [w for w in wolves if w != p.idx]

    # ---------------- 查询 ----------------
    def get(self, idx):
        return self.players.get(idx)

    def alive_players(self):
        return [p for p in self.players.values() if p.alive]

    def alive_wolves(self):
        return [p for p in self.players.values() if p.alive and p.is_wolf]

    def alive_good(self):
        return [p for p in self.players.values() if p.alive and not p.is_wolf]

    def human(self):
        return self.players.get(self.human_idx) if self.human_idx else None

    def record(self, text):
        """记录一条公共事件（同时打印）。"""
        self.public_log.append(text)
        print(text)

    # ---------------- 胜负 ----------------
    def check_winner(self):
        aw = len(self.alive_wolves())
        ag = len(self.alive_good())
        if aw == 0:
            return "好人"
        if aw >= ag:
            return "狼人"
        return None

    # ---------------- 状态文本（用于喂给 AI / 展示） ----------------
    def _alive_line(self):
        parts = []
        for idx in self.order:
            p = self.players[idx]
            mark = f"{idx}号{p.name}" if not p.is_human else f"{idx}号你(玩家)"
            if not p.alive:
                mark += "【已出局】"
            parts.append(mark)
        return "、".join(parts)

    def public_state_text(self):
        """所有人可见的公共信息文本。"""
        lines = [
            f"【当前天数】第 {self.day} 天",
            f"【存活玩家】{self._alive_line()}",
        ]
        if self.sheriff is not None:
            sp = self.players.get(self.sheriff)
            sname = sp.name if sp else "?"
            lines.append(f"【警长】{self.sheriff}号{sname}（放逐投票计 1.5 票）")
        lines.append("【公共事件记录】")
        if self.public_log:
            lines.extend(f"  - {e}" for e in self.public_log)
        else:
            lines.append("  （暂无）")
        return "\n".join(lines)

    def vote_weight(self, voter_pid):
        """放逐投票的票权：警长 1.5 票，其余 1 票。"""
        if self.sheriff is not None and voter_pid == self.sheriff:
            return 1.5
        return 1.0

    def exile_tally(self, votes):
        """放逐计票（带警长 1.5 票权）。

        votes: {投票人pid: 目标pid(0/None=弃票)}
        返回 (winner_or_None, tied_list, max_weight)
        - winner: 唯一最高票者；平票时为 None（交由调用方处理二次投票）
        - tied_list: 与最高票同票的目标列表
        """
        weight = {}
        for voter, tgt in votes.items():
            if tgt and tgt != 0:
                weight[tgt] = weight.get(tgt, 0) + self.vote_weight(voter)
        if not weight:
            return None, [], 0.0
        max_w = max(weight.values())
        tied = [t for t, w in weight.items() if w == max_w]
        winner = tied[0] if len(tied) == 1 else None
        return winner, tied, max_w

    def resolve_wolf_kill(self, votes):
        """狼队刀人投票：votes = {狼人pid: 目标pid}（目标可选任意存活玩家，含队友/自己）。

        - 得票最高的目标被击杀；
        - 若最高票出现平票，则在平票目标中**随机**选一个（避免固定取首个导致的不公平/可预测）；
        - 返回 (final_target_or_None, count_dict)。
        """
        from collections import Counter
        cnt = Counter(v for v in votes.values() if v is not None)
        if not cnt:
            return None, {}
        maxn = max(cnt.values())
        tied = [t for t, c in cnt.items() if c == maxn]
        final = random.choice(tied) if len(tied) > 1 else tied[0]
        return final, dict(cnt)

    def private_state_text(self, player):
        """在公共信息之上，附加该玩家的私密信息。"""
        lines = [self.public_state_text(), "【你的私密信息】"]
        if player.is_wolf:
            mates = "、".join(f"{m}号{self.players[m].name}" for m in player.wolf_teammates) or "无"
            lines.append(f"  - 你的身份：狼人")
            lines.append(f"  - 你的狼队友：{mates}")
        else:
            lines.append(f"  - 你的身份：{player.role}（好人阵营）")
        if player.role == SEER and player.checked:
            checked = "；".join(
                f"{k}号{self.players[k].name}={v}" for k, v in player.checked.items()
            )
            lines.append(f"  - 你查验过：{checked}")
        if player.role == WITCH:
            lines.append(
                f"  - 你的药剂：解药{'有' if player.has_antidote else '已用'} / "
                f"毒药{'有' if player.has_poison else '已用'}"
            )
        if player.role == GUARD:
            last = player.last_protected
            last_txt = f"{last}号{self.players[last].name}" if last else "无"
            lines.append(f"  - 你上一晚守护的是：{last_txt}（不能连续两晚守护同一人）")
        return "\n".join(lines)

    def role_reveal_text(self):
        lines = ["【最终身份揭示】"]
        for idx in self.order:
            p = self.players[idx]
            status = "存活" if p.alive else "出局"
            name = "你" if p.is_human else p.name
            lines.append(f"  {idx}号{name}：{p.role}（{status}）")
        return "\n".join(lines)
