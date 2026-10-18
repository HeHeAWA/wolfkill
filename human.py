# -*- coding: utf-8 -*-
"""真人玩家交互：输入号码、发言、确认等。"""
import config
import ai as ai_mod
from game import WOLF, SEER, WITCH


def input_number(prompt, valid, allow_zero=False):
    """循环读取一个落在 valid 集合内的整数。allow_zero 时 0 也可（表示跳过）。"""
    while True:
        try:
            raw = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            raise
        if raw == "" and allow_zero:
            return 0
        if not raw.isdigit():
            print("  请输入数字。")
            continue
        n = int(raw)
        if n == 0 and allow_zero:
            return 0
        if n in valid:
            return n
        print(f"  请输入有效号码：{sorted(valid)}")


def human_speech(player):
    """真人发言（可留空跳过）。"""
    print(f"\n💬 轮到你（{player.idx}号）发言。直接回车可跳过。")
    text = input("你的发言：").strip()
    return text or "（沉默）"


def human_wolf_choice(game, wolf, discussion=""):
    """真人狼人选择击杀目标（可与狼队友讨论后决定）。允许自刀（含自己）。"""
    valid = [p.idx for p in game.alive_players()]  # 可杀队友，也可刀自己
    print(f"\n🐺 你是狼人。你的队友：", end="")
    print("、".join(f"{m}号{game.players[m].name}" for m in wolf.wolf_teammates) or "无")
    if discussion:
        print("【狼队友的讨论】")
        print(discussion)
    return input_number(f"选择今晚击杀的目标号码（可刀队友或自己）{valid}：", set(valid))


def human_wolf_discuss(game, wolf, init_discussion):
    """狼人真人玩家与 AI 狼队友的自由讨论环节。返回完整讨论文本（多行）。
    - 先展示 AI 狼队友的初始意见
    - 玩家可多轮输入自由文本，每轮 AI 狼队友逐一回应
    - 输入空行 或 '结束'/'决定' 结束讨论，进入最终决策
    """
    print(f"\n🐺  [狼队密谋] 你是狼人，可以和你的 AI 狼队友自由讨论今晚击杀谁。")
    print("   输入你的想法参与讨论；输入空行 或 '结束' 即可结束讨论并做决定。")
    if init_discussion:
        print("【AI 狼队友的初始意见】")
        print(init_discussion)
    disc = init_discussion.split("\n") if init_discussion else []

    ai_mates = [m for m in wolf.wolf_teammates
                if game.get(m).alive and not game.get(m).is_human]
    if not ai_mates:
        print("（你当前没有存活的 AI 狼队友，可直接做决定。）")
        return "\n".join(disc)

    for _ in range(config.WOLF_DISCUSS_ROUNDS):
        try:
            msg = input("\n💬  你的发言（狼队内部，仅队友可见）：").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if msg == "" or msg in ("结束", "end", "决定", "done"):
            break
        disc.append(f"你：{msg}")
        print("  —— 狼队友回应 ——")
        for m in ai_mates:
            mate = game.get(m)
            reply = ai_mod.ai_wolf_chat(game, mate, "\n".join(disc))
            disc.append(f"{m}号{mate.name}：{reply}")
            print(f"  {m}号{mate.name}：{reply}")
    return "\n".join(disc)


def human_seer_check(game, seer):
    valid = [p.idx for p in game.alive_players() if p.idx != seer.idx and p.idx not in seer.checked]
    print(f"\n🔮 你是预言家。可选查验：{valid}")
    return input_number("选择要查验的玩家号码：", set(valid))


def human_witch_action(game, witch, killed_idx):
    save = False
    poison = None
    killed_name = game.get(killed_idx).name if killed_idx else "无人"
    print(f"\n🧪 你是女巫。今晚被狼人袭击的是：{killed_name}（{killed_idx}号）")
    if witch.has_antidote:
        a = input("是否使用解药救人？(y/n)：").strip().lower()
        save = a in ("y", "yes", "是")
    if witch.has_poison:
        valid = [p.idx for p in game.alive_players() if p.idx != killed_idx]
        n = input_number(f"是否使用毒药？(输入号码毒杀，0跳过) {valid}：", set(valid), allow_zero=True)
        if n != 0:
            poison = n
    return save, poison


def human_vote(game, player, restrict=None):
    valid = [p.idx for p in game.alive_players() if p.idx != player.idx]
    if restrict:
        valid = [c for c in valid if c in restrict]
    extra = "（平票二次投票，只能在以下玩家中选择）" if restrict else ""
    return input_number(
        f"\n🗳️  轮到你（{player.idx}号）投票{extra}。放逐谁？(输入号码，0=弃票) {valid}：",
        set(valid), allow_zero=True,
    )


def human_lastwill(player):
    """玩家出局时留下遗言（可留空）。"""
    print(f"\n💬  你（{player.idx}号）已出局，请留下遗言（直接回车可跳过）：")
    text = input("你的遗言：").strip()
    return text or "（沉默）"


def human_run_sheriff(game, player):
    """第 1 天上警竞选：是否上警 + 竞选发言。返回 (run: bool, speech: str)。"""
    print(f"\n🔔  第 1 天『上警竞选』环节：你是否要竞选警长（上警）？")
    print("   警长拥有放逐投票 1.5 票加权（可打破平票、主导风向）。")
    while True:
        a = input("   是否上警？(y/n)：").strip().lower()
        if a in ("y", "yes", "是", "上警"):
            run = True
            break
        if a in ("n", "no", "否", "不上警"):
            run = False
            break
        print("   请输入 y 或 n。")
    speech = ""
    if run:
        speech = input("   竞选发言（一句话，可留空）：").strip()
    return run, speech


def human_vote_sheriff(game, player, candidates):
    """选举警长：在候选人中选一人或弃票。"""
    cands = [c for c in candidates if game.get(c) and game.get(c).alive]
    print(f"\n🗳️  选举警长。候选人：{cands}（0=弃票）")
    return input_number("   投给哪位候选人？", set(cands), allow_zero=True)


def human_guard_choice(game, guard, valid):
    last = guard.last_protected
    last_txt = f"{last}号{game.players[last].name}" if last else "无"
    print(f"\n🛡️  你是守卫。上一晚守护：{last_txt}（不可连守同一人，不可守自己）")
    return input_number(f"选择今晚守护的玩家号码 {valid}：", set(valid))


def human_hunter_shoot(game, hunter, valid):
    print(f"\n💥  你（{hunter.idx}号猎人）出局了！可以开枪带走一名玩家。")
    return input_number(f"选择你要带走的人 {valid}：", set(valid))
