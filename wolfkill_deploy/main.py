# -*- coding: utf-8 -*-
"""
狼人杀（纯文字）主程序
- 1 名真人玩家 + 若干 AI 玩家（默认 5 个 AI，共 6 人）
- AI 通过 OpenRouter 接口（大模型）进行推理与发言
- 用法：
    python3 main.py            # 真实对局（消耗 API 额度）
    python3 main.py --mock     # 离线模拟（不消耗额度，用于测试）
    python3 main.py --auto     # 纯 AI 观战（机密信息也会显示）
    python3 main.py --debug    # 调试模式（显示狼队讨论/守护/用药等机密）
    python3 main.py --model openai/gpt-4o-mini
- 机密信息（狼队讨论、守护、用药）默认只写入后台 wolfkill_secret.log，
  不显示在玩家屏幕，避免泄露身份；仅 --auto / --debug 时显示。
"""
import os
import sys

import config
from game import Game, WOLF, SEER, WITCH, HUNTER, GUARD, VILLAGER
import ai as ai_mod
import human as human_mod


# 后台机密日志：狼队讨论、守护、用药等只写入此文件，默认不在玩家屏幕显示。
_SECRET_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "wolfkill_secret.log")
_secret_fh = open(_SECRET_PATH, "w", encoding="utf-8")


def secret(text, show=False):
    """机密信息：写入后台日志文件（wolfkill_secret.log）。
    默认不显示在玩家屏幕；show=True 或 config.REVEAL_SECRET 时才打印。"""
    _secret_fh.write(text + "\n")
    _secret_fh.flush()
    if config.REVEAL_SECRET or show:
        print(text)


def hunter_shoot(game, hunter):
    """猎人开枪带走一名玩家，返回目标 idx 或 None。"""
    valid = [p.idx for p in game.alive_players() if p.idx != hunter.idx]
    if not valid:
        return None
    if hunter.is_human and not config.AUTO_HUMAN:
        return human_mod.human_hunter_shoot(game, hunter, valid)
    return ai_mod.ai_hunter_shoot(game, hunter, valid)


def apply_night_deaths(game, wolf_target, guard_target, saved, poison_target):
    """结算夜晚死亡，并处理猎人开枪（非毒杀导致的猎人出局可开枪）。"""
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
        if p.role == HUNTER and cause != "poison":
            pending.append(p)

    if dead_names:
        game.record(f"🌅 第 {game.day} 天清晨：昨晚 [{'、'.join(dead_names)}] 死了。")
    else:
        game.record(f"🌅 第 {game.day} 天清晨：昨夜风平浪静，无人死亡。")

    # 遗言：仅在「首晚被刀」时留（被放逐的遗言在放逐环节处理；猎人由开枪代替）。
    for p, cause in newly_dead:
        if p.role == HUNTER:
            continue
        if not (game.day == 1 and cause == "wolf"):
            continue
        text = (human_mod.human_lastwill(p)
                if (p.is_human and not config.AUTO_HUMAN)
                else ai_mod.ai_lastwill(game, p))
        game.record(f"💬 {p.idx}号{p.name} 的遗言：{text}")

    while pending:
        h = pending.pop(0)
        t = hunter_shoot(game, h)
        if t and game.get(t).alive:
            tp = game.get(t)
            tp.alive = False
            game.record(f"💥 猎人 {h.idx}号{h.name} 开枪带走了 {t}号{tp.name}！")


def parse_args(argv):
    args = {"mock": False, "model": None, "auto": False, "debug": False}
    for a in argv[1:]:
        if a == "--mock":
            args["mock"] = True
        elif a == "--auto":
            args["auto"] = True
        elif a == "--debug":
            args["debug"] = True
        elif a.startswith("--model="):
            args["model"] = a.split("=", 1)[1]
    return args


def print_banner(game):
    print("=" * 56)
    print("🐺  狼 人 杀  ·  纯 文 字 版")
    print("=" * 56)
    total = len(game.players)
    print(f"玩家总数：{total} 人（1 真人 + {total - 1} AI）")
    print(f"角色配比：", end="")
    print("、".join(f"{r}×{c}" for r, c in config.ROLES_CONFIG.items()))
    print("出场玩家：", end="")
    print("、".join(f"{p.idx}号{'你' if p.is_human else p.name}" for p in game.players.values()))
    print("=" * 56)


def night_phase(game):
    game.record(f"\n🌙  ===== 第 {game.day} 天 · 黑夜降临 =====")
    game.record("🌙 天黑请闭眼。")  # 主持人播报：进入夜晚
    wolves = game.alive_wolves()
    # 在全部玩家中查找神职（即使已死亡也要保留引用），以便夜晚仍播报「XX请睁眼」，
    # 防止玩家由播报缺失推断出死者身份。
    seer = next((p for p in game.players.values() if p.role == SEER), None)
    witch = next((p for p in game.players.values() if p.role == WITCH), None)
    guard = next((p for p in game.players.values() if p.role == GUARD), None)

    # 0) 守卫守护（必须先于狼人）；即使守卫已死亡也照常播报，避免泄露身份
    guard_target = None
    if guard:
        game.record("🛡️ 守卫请睁眼……")
        if guard.alive:
            valid = [p.idx for p in game.alive_players()
                     if p.idx != guard.idx and p.idx != guard.last_protected]
            if not valid:  # 兜底：允许守自己
                valid = [p.idx for p in game.alive_players() if p.idx != guard.idx]
            if guard.is_human and not config.AUTO_HUMAN:
                guard_target = human_mod.human_guard_choice(game, guard, valid)
            else:
                guard_target = ai_mod.ai_guard_protect(game, guard, valid)
            if guard_target:
                guard.last_protected = guard_target
                if guard.is_human and not config.AUTO_HUMAN:
                    print(f"  [守卫] 你守护了 {guard_target}号{game.get(guard_target).name}")
                else:
                    secret(f"  [守卫·机密] 守护了 {guard_target}号{game.get(guard_target).name}")
        game.record("🛡️ 守卫请闭眼。")

    # 1) 狼人行动（含狼队讨论：AI 狼先出初始意见，真人狼可与之自由讨论后拍板）
    killed_idx = None
    if wolves:
        game.record("🐺 狼人请睁眼……")
        from collections import Counter
        human_wolf = next((w for w in wolves if w.is_human and not config.AUTO_HUMAN), None)
        ai_wolves = [w for w in wolves if w is not human_wolf]

        # AI 狼先各自给出初始意见（作为讨论起点）
        discussion = []
        for w in ai_wolves:
            t, r = ai_mod.ai_wolf_propose(game, w, "")
            discussion.append(f"{w.idx}号{w.name} 提议杀 {t}号{game.get(t).name}：{r}")

        if human_wolf:
            # 玩家是狼人：先与 AI 狼队友自由讨论，再看讨论结果拍板
            disc_text = human_mod.human_wolf_discuss(game, human_wolf, "\n".join(discussion))
            final = {human_wolf.idx: human_mod.human_wolf_choice(game, human_wolf, disc_text)}
            for w in ai_wolves:
                t, _ = ai_mod.ai_wolf_propose(game, w, disc_text)
                final[w.idx] = t
            discussion = disc_text.split("\n") if disc_text else discussion
        else:
            # 全是 AI 狼：看初始意见后做最终决定
            final = {}
            disc_text = "\n".join(discussion)
            for w in ai_wolves:
                t, _ = ai_mod.ai_wolf_propose(game, w, disc_text)
                final[w.idx] = t

        killed_idx, vote_cnt = game.resolve_wolf_kill(final)
        # 狼队讨论属机密：仅当玩家本人是狼人（REVEAL_SECRET 或 human 为狼）时才显示
        show_wolf = config.REVEAL_SECRET or (game.human() and game.human().is_wolf)
        secret("  [狼人] 内部讨论：", show=show_wolf)
        for line in discussion:
            if line:
                secret("    - " + line, show=show_wolf)
        # 逐狼计票（狼队投票刀人），平票随机打破
        for w in final:
            secret(f"  [狼队投票] {w}号 → 杀 {final[w]}号{game.get(final[w]).name}",
                   show=show_wolf)
        if vote_cnt:
            maxc = max(vote_cnt.values())
            tied = [t for t, c in vote_cnt.items() if c == maxc]
            if len(tied) > 1:
                secret(f"  [狼人] 平票（各 {maxc} 票），随机选中击杀目标 → 今夜击杀 "
                       f"{killed_idx}号{game.get(killed_idx).name}", show=show_wolf)
            else:
                secret(f"  [狼人] 最终投票 → 今夜击杀 {killed_idx}号{game.get(killed_idx).name}",
                       show=show_wolf)
        game.record("🐺 狼人请闭眼。")
    else:
        print("  [狼人] 已全部出局。")

    # 2) 预言家查验；即使预言家已死亡也照常播报，避免泄露身份
    if seer:
        game.record("🔮 预言家请睁眼……")
        if seer.alive:
            if seer.is_human and not config.AUTO_HUMAN:
                tid = human_mod.human_seer_check(game, seer)
            else:
                tid = ai_mod.ai_seer_check(game, seer)
            if tid:
                result = "狼人" if game.get(tid).is_wolf else "好人"
                seer.checked[tid] = result
                if seer.is_human:
                    print(f"  [私密] 你查验了 {tid}号{game.get(tid).name}，结果：{result}")
        game.record("🔮 预言家请闭眼。")

    # 3) 女巫用药（若被守卫挡刀，则视为无人被袭击）；即使女巫已死亡/无药也照常播报，避免泄露身份
    saved = False
    poison_idx = None
    witch_sees = killed_idx if (killed_idx is not None and guard_target != killed_idx) else None
    if witch:
        game.record("🧪 女巫请睁眼……")
        if witch.alive and (witch.has_antidote or witch.has_poison):
            if witch.is_human and not config.AUTO_HUMAN:
                saved, poison_idx = human_mod.human_witch_action(game, witch, witch_sees)
            else:
                saved, poison_idx = ai_mod.ai_witch_action(game, witch, witch_sees)
            if saved and witch.has_antidote:
                witch.has_antidote = False
                if witch.is_human and not config.AUTO_HUMAN:
                    print("  [女巫] 你使用了解药。")
                else:
                    secret("  [女巫·机密] 使用了解药。")
            if poison_idx and witch.has_poison:
                witch.has_poison = False
                if witch.is_human and not config.AUTO_HUMAN:
                    print(f"  [女巫] 你使用了毒药，目标 {poison_idx}号{game.get(poison_idx).name}。")
                else:
                    secret(f"  [女巫·机密] 使用了毒药，目标 {poison_idx}号{game.get(poison_idx).name}。")
        game.record("🧪 女巫请闭眼。")

    # 4) 结算夜晚死亡（含猎人开枪）
    game.record("☀️ 天亮了。")
    if killed_idx is None and not poison_idx:
        game.record(f"🌅 第 {game.day} 天清晨：昨夜风平浪静，无人死亡。")
    else:
        apply_night_deaths(game, killed_idx, guard_target, saved, poison_idx)


def _fmt_w(w):
    """把计票权重格式化：整数去小数，否则保留一位。"""
    return str(int(w)) if w == int(w) else str(w)


def sheriff_phase(game):
    """第 1 天『上警竞选』：是否上警 + 竞选发言，然后选举警长（警长放逐投票计 1.5 票）。"""
    print(f"\n🔔  ===== 第 {game.day} 天 · 上警竞选 =====")
    game.record("🔔 第 1 天 · 上警竞选警长环节")
    candidates = []
    for idx in game.order:
        p = game.get(idx)
        if not p.alive:
            continue
        if p.is_human and not config.AUTO_HUMAN:
            run, speech = human_mod.human_run_sheriff(game, p)
        else:
            run, speech = ai_mod.ai_run_for_sheriff(game, p)
        name = "你" if p.is_human else p.name
        if run:
            candidates.append(p.idx)
            speech = speech or "（无发言）"
            print(f"  {idx}号{name} 上警：{speech}")
            game.record(f"  {idx}号{name} 上警：{speech}")
        else:
            print(f"  {idx}号{name} 不上警")
            game.record(f"  {idx}号{name} 不上警")
    if not candidates:
        game.record("  无人上警，本轮无警长。")
        return
    game.record("—— 选举警长 ——")
    votes = {}
    for idx in game.order:
        p = game.get(idx)
        if not p.alive:
            continue
        if p.is_human and not config.AUTO_HUMAN:
            t = human_mod.human_vote_sheriff(game, p, candidates)
        else:
            t = ai_mod.ai_vote_sheriff(game, p, candidates)
        votes[p.idx] = t
        disp = "弃票" if t == 0 else f"投给 {t}号"
        print(f"  {idx}号{p.name if not p.is_human else '你'} → {disp}")
    from collections import Counter
    cnt = Counter(v for v in votes.values() if v and v != 0)
    if not cnt:
        game.record("  警长选举：全员弃票，本轮无警长。")
        return
    top, n = cnt.most_common(1)[0]
    tied = [t for t, c in cnt.items() if c == n]
    if len(tied) > 1:
        game.record(f"  警长选举平票（各 {n} 票），本轮无警长。")
        return
    game.sheriff = top
    game.record(f"🚩 {top}号{game.get(top).name} 当选警长！（放逐投票计 1.5 票）")


def exile_victim(game, victim, n_text=""):
    """放逐一名玩家出局：记录结果、留下遗言（猎人由开枪代替）。"""
    victim.alive = False
    if n_text:
        game.record(f"🗳️  投票结果：{victim.idx}号{victim.name} 被放逐出局（{n_text} 票）。")
    else:
        game.record(f"🗳️  投票结果：{victim.idx}号{victim.name} 被放逐出局。")
    if victim.role != HUNTER:
        text = (human_mod.human_lastwill(victim)
                if (victim.is_human and not config.AUTO_HUMAN)
                else ai_mod.ai_lastwill(game, victim))
        game.record(f"💬 {victim.idx}号{victim.name} 的遗言：{text}")
    if victim.role == HUNTER:
        t = hunter_shoot(game, victim)
        if t and game.get(t).alive:
            tp = game.get(t)
            tp.alive = False
            game.record(f"💥 猎人 {victim.idx}号{victim.name} 开枪带走了 {t}号{tp.name}！")


def _resolve_exile(game, votes):
    """按带权计票放逐；若平票则进行第二次投票（只在平票者间选）。"""
    winner, tied, maxw = game.exile_tally(votes)
    abstains = sum(1 for v in votes.values() if not v or v == 0)
    if winner is not None:
        exile_victim(game, game.get(winner), _fmt_w(maxw))
        return
    if not tied:
        game.record(f"🗳️  投票结果：全员弃票（{abstains}票弃票），流局，无人被放逐。")
        return
    # 平票 → 第二次投票（仅在平票玩家之间选）
    names = "、".join(f"{t}号{game.get(t).name}" for t in tied)
    game.record(f"🗳️  投票结果：{names} 平票（各 {_fmt_w(maxw)} 票），进行第二次投票。")
    votes2 = {}
    for idx in game.order:
        p = game.get(idx)
        if not p.alive:
            continue
        if p.is_human and not config.AUTO_HUMAN:
            t = human_mod.human_vote(game, p, restrict=set(tied))
        else:
            t = ai_mod.ai_vote(game, p, restrict=set(tied))
        votes2[p.idx] = t
        disp = "弃票" if t == 0 else f"投给 {t}号"
        print(f"  {idx}号{p.name if not p.is_human else '你'} → {disp}")
    winner2, tied2, maxw2 = game.exile_tally(votes2)
    if winner2 is not None:
        exile_victim(game, game.get(winner2), _fmt_w(maxw2))
    else:
        game.record(f"🗳️  第二次投票仍平票，流局，无人被放逐。")


def day_phase(game):
    print(f"\n☀️  ===== 第 {game.day} 天 · 白天 =====")
    # 第 1 天：上警竞选警长
    if game.day == 1 and game.sheriff is None:
        sheriff_phase(game)
    # 发言
    print("—— 发言环节 ——")
    for idx in game.order:
        p = game.get(idx)
        if not p.alive:
            continue
        if p.is_human and not config.AUTO_HUMAN:
            text = human_mod.human_speech(p)
        else:
            text = ai_mod.ai_speak(game, p)
        name = "你" if p.is_human else p.name
        print(f"  {idx}号{name}：{text}")
        game.public_log.append(f"{idx}号{name}：{text}")

    # 投票放逐（含平票二次投票）
    print("\n—— 投票放逐环节 ——")
    votes = {}
    for idx in game.order:
        p = game.get(idx)
        if not p.alive:
            continue
        if p.is_human and not config.AUTO_HUMAN:
            t = human_mod.human_vote(game, p)
        else:
            t = ai_mod.ai_vote(game, p)
        votes[p.idx] = t
        disp = "弃票" if t == 0 else f"投给 {t}号"
        print(f"  {idx}号{p.name if not p.is_human else '你'} → {disp}")

    _resolve_exile(game, votes)


def main():
    args = parse_args(sys.argv)
    if args["mock"]:
        config.USE_MOCK = True
    if args["auto"]:
        config.AUTO_HUMAN = True
        config.REVEAL_SECRET = True   # 观战模式：展示 AI 内部决策
    if args["debug"]:
        config.REVEAL_SECRET = True   # 调试模式：展示机密信息
    if args["model"]:
        config.MODEL = args["model"]

    game = Game()
    print_banner(game)

    h = game.human()
    if h:
        print(f"\n🔒  你的身份是：{h.role}（{'狼人阵营' if h.is_wolf else '好人阵营'}）")
        if h.is_wolf:
            print("   你的狼队友：",
                  "、".join(f"{m}号{game.players[m].name}" for m in h.wolf_teammates) or "无")
        print("   请不要让别人看到你的屏幕。祝你好运！")

    if not config.USE_MOCK and not game.human():
        pass

    try:
        while True:
            game.day += 1
            night_phase(game)
            winner = game.check_winner()
            if winner:
                break
            day_phase(game)
            winner = game.check_winner()
            if winner:
                break
    except (KeyboardInterrupt, EOFError):
        print("\n\n[游戏被中断]")
        return

    print("\n" + "=" * 56)
    if winner == "狼人":
        print("🐺  狼人阵营胜利！")
    else:
        print("🏆  好人阵营胜利！")
    print("=" * 56)
    print(game.role_reveal_text())


if __name__ == "__main__":
    main()
