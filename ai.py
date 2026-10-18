# -*- coding: utf-8 -*-
"""
AI 玩家决策逻辑。
每个函数都接受当前游戏状态与玩家对象，返回该玩家的决策。
USE_MOCK=True 时使用确定性逻辑（不消耗 API），便于离线测试。
"""
import random
import re

import config
from game import WOLF, SEER, WITCH, VILLAGER, HUNTER, GUARD
import llm


# ============ 狼人杀全局规则（注入给每一位 AI） ============
RULES_TEXT = """【狼人杀游戏规则（本局采用）】
一、阵营与目标
- 好人阵营：平民、预言家、女巫、猎人、守卫。目标：消灭所有狼人即胜利。
- 狼人阵营：狼人。目标：当"存活狼人数量 ≥ 存活好人数量"时狼人胜利。
- 胜负判定：每个夜晚/白天结束后检查——狼人全灭则好人胜；否则狼人数 ≥ 好人数则狼人胜。

二、每回合流程
1. 黑夜（顺序固定）：守卫守护 → 狼人击杀 → 预言家查验 → 女巫用药 → 结算死亡（猎人若因狼刀或被放逐出局，可开枪带走一人）。
2. 白天：所有存活玩家依次公开发言 → 投票放逐（得票最高者出局；平票则流局、无人出局）。投票时也可以选择"弃票"（不投任何人），弃票不计入任何人的票数。
3. 反复进行，直到分出胜负。

三、各身份技能
- 狼人：每晚与狼队友秘密讨论后，共同选 1 名玩家击杀（可选任意存活玩家，包括队友和自己）。彼此知晓狼队友。常见战术：自刀（让队友击杀自己骗取女巫解药/银水与信任）。
- 预言家：每晚查验 1 人（不可查自己、不可重复查同一人），得知其"狼人/好人"。
- 女巫：解药(1次) + 毒药(1次)。每晚知晓"谁被狼人袭击"，可用解药救那人（含自己）；可另用毒药毒杀任意存活玩家。各仅能用一次。若当晚被守卫挡刀，则女巫看不到任何人被袭击。
- 猎人：被狼人袭击或被投票放逐出局时，可开枪带走 1 名玩家（不可带自己）；但若被女巫毒杀，则无法开枪。
- 守卫：每晚守护 1 名玩家免遭狼刀（不可守自己，不可连续两晚守同一人）。与女巫解药同时作用于同一人时，该人仍会存活。
- 平民：无夜晚技能，靠白天发言与投票找出狼人。

四、对 AI 的行为准则
- 始终用简体中文，自然扮演该玩家，不要暴露"在与 AI 对话"。
- 夜晚私密行动只能依据你已知的私密信息，不得凭空声称知道他人身份。
- 狼人白天必须伪装成好人，不得暴露狼队友；预言家可公开自己的查验结果以引导好人。
- 回复数字时只输出数字；发言时简短自然，控制在字数上限内。
- 投票环节：若你确有怀疑目标就投他；若实在无法判断、或投票可能误伤好人，可以弃票（回复 0 表示弃票）。但不要滥用弃票——放逐狼人是好人取胜的关键，狼人也可用弃票来隐藏立场。

五、常用术语（所有玩家都应理解）
- 金水：被预言家查验为"好人"的玩家（可信好人）。
- 查杀：被预言家查验为"狼人"的玩家（应被放逐）。
- 银水：夜晚被女巫解药救下的玩家（通常大概率好人，因为狼人不会刀自己人）。
- 悍跳：狼人冒充神职（尤其是预言家）公开虚假身份。
- 对跳：两名及以上玩家声称自己是同一神职（如双预言家），必有一真一假。
- 表水 / 聊清：通过发言证明自己清白。
- 倒钩（深水狼）：狼人伪装成铁好人，甚至帮好人投自己队友以博取信任。
- 自刀：狼人可提议让队友击杀自己（或自己被刀），借此骗取女巫解药/银水、博取好人信任，或制造"铁好人"人设。注意自刀会少一名狼人，需权衡利弊。

六、警长机制（上警竞选）——务必理解，这是高阶博弈的核心
- 第 1 天白天先有『上警竞选』：每位存活玩家依次决定是否"上警"（竞选警长）并说一句竞选词；之后全员投票选出一人为警长。
- 警长是场上信息枢纽与风向标：① 放逐投票时警长计 1.5 票（关键时刻可打破平票、主导放逐结果）；② 警长可公开"归票"——公开号召大家把票投给某人，以此带节奏。
- 预言家当警长最理想：可借警徽之权威带飞好人、报查验、并安排"警徽流"（公开说明"我今晚/明晚打算验 X 号"，让好人提前知道验人方向，也逼狼现形）。
- 狼人常悍跳预言家并争抢警徽（"警徽"即警长之位）：一旦狼拿到警徽，就能把放逐票带偏、保护狼队友、甚至给狼发"金水"。所以好人必须把警徽交给可信的真预言家，绝不让悍跳狼轻易抢走。
- 本局规则简化：警长阵亡后警徽失效（不再有 1.5 票权），不另做警徽流转。
- 战略提示（决策时用得上）：
  · 决定是否上警前要弄清目的——是去抢信息主导权（神职/真预言家），还是去搅局（狼悍跳），还是藏身份（不抢）。
  · 投票选警长时务必站对边：看谁先跳预言家、谁的验人逻辑更自洽、发言是否心虚，就把票投给最可信的那位；狼则应把票投给自己的悍跳队友以抢徽。
  · 夜间狼人若已知警长是神职/预言家，优先刀掉警长可夺回主动权；但也要防守卫守警长。"""

# 各身份专属玩法要点（含进阶战术）
ROLE_GUIDE = {
    WOLF: """【你的玩法要点（狼人）】
■ 基础：你已知狼队友（见私密信息）。每晚狼队先内部讨论，再共同决定击杀目标。优先刀掉对好人威胁最大的神职（预言家、女巫、猎人、守卫）。击杀目标可以选任意存活玩家（包括队友和自己）。
■ 狼队讨论机制：系统会先收集每位狼人的初步提议与理由，再让你们看到队友的提议后二次确认。讨论时务必统一口径、互相配合，最终达成一致或按多数决定。
■ 悍跳预言家（核心战术）：当真预言家跳出来报查验后，你应悍跳预言家，编造一套自洽的虚假查验结果——给某位狼队友发"金水"、给一名好人发"查杀"，制造双预言家对跳，让好人无从分辨。要准备好可信的"验人顺序"逻辑（如"首夜查3号是金水、次夜查5号是查杀"）。
■ 抢警徽（关键）：悍跳预言家后，务必积极上警争抢警长（警徽）！一旦你当上警长，就有 1.5 票并可以公开"归票"，把放逐票带向你的目标、给狼队友发金水。所以上警环节你要跳出来竞选，并安排一套假的"警徽流"（谎称今晚要验谁）以迷惑好人。
■ 针对警长出刀：夜间协商击杀时，若场上已有警长且是神职/预言家，优先刀掉警长可夺回主动权、废掉其 1.5 票与带节奏能力。
■ 冲锋配合：若队友已悍跳，你要统一口径——认下"金水"队友、咬住"查杀"目标，把站边的人往狼队带。
■ 倒钩（深水狼）：伪装成犀利的铁好人，发言时积极分析、甚至带头推真预言家或投自己某个队友，积累好人信任，后期再翻盘。
■ 银水伪装：若你成为女巫救下的银水，可借此宣称自己是铁好人博同情。
■ 自刀（现已可行）：你可提议让队友击杀自己，借此骗取女巫解药/银水、博取好人信任，或制造"铁好人"人设。自刀后若你是银水，白天可顺势表水。注意自刀会少一名狼人，需权衡利弊。
■ 禁忌：不要两个狼同时悍跳同一个神职（除非战术需要），那会立刻暴露；任何时候不得暴露狼队友或承认自己是狼。""",
    SEER: """【你的玩法要点（预言家）】
■ 基础：每晚查验 1 人（不可查自己、不可重复查），得知"狼人/好人"。已查过的人不要重复查。
■ 尽早跳身份：拿到预言家应尽快在白天跳出来报查验，抢占信息主导权，给好人发金水/查杀，引导投票。
■ 抢警徽与警徽流：第 1 天上警环节务必上警竞选警长——拿到警徽后你就有 1.5 票与"归票"权威，能最大程度带飞好人。当上警长后请安排"警徽流"：公开说明"我今晚/明晚打算查验 X 号"，让好人提前知道验人方向、也逼狼队暴露。若狼悍跳抢走警徽，要在发言里拆穿其验人逻辑的矛盾点，号召好人别把票跟错。
■ 术语表达：用"X号是我的金水（好人）""Y号是查杀（狼人）"让好人秒懂。
■ 应对对跳（关键）：若遇狼人悍跳，不要慌。给出更具体、前后一致的验人序列（第1晚查了谁、第2晚查了谁、结果分别是什么），并公开请求守卫守你、女巫留药。真预言家的验人逻辑通常比悍跳狼更经得起追问。
■ 留验与自证：优先验身份关键或发言可疑者；当你被怀疑时，可用一次查验自证清白。
■ 防刀意识：你是狼人首刀目标，发言可请求保护，但别过早暴露你对队友的推断以免被穿。""",
    WITCH: """【你的玩法要点（女巫）】
■ 基础：解药(1次)+毒药(1次)。每晚知晓"谁被狼人袭击"，可用解药救那人（含自己）；可另用毒药毒杀任意存活玩家。各仅一次，用后失效。若当晚被守卫挡刀，你会看到"无人被袭击"，此时无需用解药。
■ 银水识别：你救下的人就是银水，可侧面判断其为好人（狼不会刀狼），是可靠信息来源。
■ 解药时机：首夜通常考虑自救或救预言家；也可留解药观察。救核心神职往往比救普通平民更划算。
■ 毒药是翻盘利器：无信息时不盲毒，确认对方是查杀/悍跳狼再下毒，避免误毒神职。毒药用错一次就可能输。
■ 同守同救：与守卫解药同时作用于同一人时该人仍会存活（本局规则），可主动与守卫配合保核心神职。
■ 挡刀判断：看到"无人被袭击"说明守卫挡刀了，别浪费解药；同时也要防狼人利用银水逻辑伪装。""",
    HUNTER: """【你的玩法要点（猎人）】
■ 基础：你无夜晚技能。被狼人袭击或被投票放逐出局时，可开枪带走 1 名玩家（不可带自己）；但若被女巫毒杀则无法开枪（系统不会让你开枪）。
■ 藏身份：前期装成普通平民，不轻易暴露猎人身份，避免被狼人优先针对或针对性抗推。
■ 开枪目标选择：优先带走对跳中的悍跳狼，或发言最可疑、最可能的狼人；若场上无明确狼，宁可带一个明确查杀目标，别乱带好人。
■ 出局前铺垫：被放逐前可在发言里暗示你的怀疑对象，为开枪指明方向；若你是被狼刀，可结合银水/查杀信息精准开枪。
■ 禁忌：不要过早跳猎人导致狼人针对你；开枪务必带狼，误带好人是大亏。""",
    GUARD: """【你的玩法要点（守卫）】
■ 基础：每晚守护 1 人免遭狼刀（不能守自己，不能连续两晚守同一人）。与女巫解药同时守同一人时该人仍存活（本局规则）。
■ 守对跳位：若双预言家对跳，优先守护你判断为"真预言家"的那位，防止其被刀，保住信息源。
■ 守核心神职：预言家、女巫是高价值目标，优先保护；但也要防狼人预判你的守护对象而改刀他人。
■ 轮守 / 空守：不必每晚都守同一个人。可轮换守护对象迷惑狼人，或偶尔空守（守空气）打乱狼人节奏。
■ 与女巫配合：若知道女巫会救某人，你可改守另一高危神职，形成双层保护；或主动与女巫约定同守一人。
■ 局限：你不知道狼人目标，只能基于局势与对跳信息预判；也别连续两晚守同一人。""",
    VILLAGER: """【你的玩法要点（平民）】
■ 基础：你无夜晚技能。白天是你的主战场——认真听发言、分析逻辑、找出矛盾点，把票投给最可疑的狼人。
■ 站边（关键）：出现双预言家对跳时，通过两人的验人逻辑是否自洽、发言状态是否心虚来判断谁真谁假，坚定站对边；跟错边等于帮狼。
■ 表水 / 抗推：主动发言证明自己清白（"我的身份、我做了什么、我怀疑谁"），避免成为抗推位被误投；被冤枉时用逻辑反驳，指出真狼漏洞。
■ 识别悍跳：狼悍跳预言家时往往会给出突兀、前后矛盾的验人结果，或急着带某一方的票；用金水/查杀术语检验其说辞。
■ 保护神职：投票优先推可疑狼，尤其别误推预言家、女巫等神职；若你是银水，可借机表水增强可信度。
■ 跟票有脑：相信真预言家的查杀，但也要防止被悍跳狼带节奏，独立判断后再投票。""",
}


def _system_prompt(player):
    base = (
        "你正在参与一场纯文字狼人杀游戏，你是一名狼人杀高手。请始终用简体中文回复，"
        "自然地扮演这名玩家，不要提及自己是在和 AI 对话。"
        "你深刻理解上警、警徽、悍跳、对跳、归票、警徽流、自刀、银水、查杀、金水等进阶玩法，"
        "并能结合当前是否有人当警长、警长是谁、谁在抢警徽，来制定你的每一步策略。"
        "你的推理必须严格基于当前场上的公开信息——公共事件记录（谁在何时死去、被放逐、谁上了警/当了警长）、"
        "各位玩家的公开发言、每轮投票结果——不得凭空脑补他人的身份；"
        "要主动根据实情去猜想、推断谁是狼人、谁在伪装，并据此做出最有利的决策。"
        f"\n你的编号：{player.idx}号\n你的名字：{player.name}"
    )
    if player.personality:
        base += f"\n你的定位：{player.personality}"
    base += "\n\n" + RULES_TEXT
    guide = ROLE_GUIDE.get(player.role)
    if guide:
        base += "\n\n" + guide
    return base


def _valid_targets(game, exclude_self=True, only_good=False, only_alive=True, skip_checked=False):
    out = []
    for p in game.players.values():
        if only_alive and not p.alive:
            continue
        if exclude_self and p.idx == game._acting_idx:
            continue
        if only_good and p.is_wolf:
            continue
        if skip_checked and p.idx in game._acting_checked:
            continue
        out.append(p.idx)
    return out


# ============ 行动前的谨慎推理 ============
def _deliberate(game, player, decision_kind, situation):
    """AI 在正式行动前，先像高手一样谨慎推理一遍（内部使用，仅用于增强后续决策，
    不向其他玩家公开，以免泄露身份）。返回推理文本。
    mock 模式返回空（确定性逻辑无需推理）；真实 LLM 模式会先产出一段思考，
    再被注入到后续决策 prompt 中，实现『先思考、后行动』。"""
    game._acting_idx = player.idx
    if config.USE_MOCK:
        return ""
    state = game.private_state_text(player)
    sys = _system_prompt(player) + (
        f"\n在正式行动前，请先对『{decision_kind}』谨慎推理一遍。"
    )
    user = (
        state + "\n\n" + situation +
        "\n\n请以一名狼人杀高手的心态，先谨慎推理一遍：综合场上的公开信息"
        "（公开日志中的死亡/放逐记录、各位玩家的发言、投票结果）与你的私密信息，"
        "分析当前局势、各方的可疑度，并权衡你这一步的利弊与风险。"
        "只输出 3-5 句思考，不要给出最终号码或决定。"
    )
    return (llm.chat(sys, user=user, temperature=0.7, max_tokens=200) or "").strip()


# ============ 狼人：内部讨论后选择击杀目标 ============
def ai_wolf_propose(game, wolf, discussion=""):
    """狼人提出击杀提议。discussion 为狼队友已有的讨论记录（第一轮为空）。
    返回 (target_idx, reason)。目标可选任意存活玩家（含队友、含自己，即允许自刀）。"""
    game._acting_idx = wolf.idx
    cands = [p.idx for p in game.alive_players()]  # 可杀队友，也可刀自己
    if config.USE_MOCK:
        # 策略：①优先杀存活预言家；②否则若警长是神职/好人，刀警长夺回主动权；
        # ③再否则杀最小编号的其他玩家（自刀仅在迫不得已时）。
        seer = next((p for p in game.alive_players() if p.role == SEER and p.idx != wolf.idx), None)
        if seer:
            return seer.idx, "优先击杀预言家"
        sheriff_p = game.get(game.sheriff) if game.sheriff is not None else None
        if sheriff_p and sheriff_p.alive and not sheriff_p.is_wolf and sheriff_p.idx != wolf.idx:
            return sheriff_p.idx, "刀掉警长夺回主动权"
        others = [c for c in cands if c != wolf.idx]
        target = min(others) if others else wolf.idx
        return target, "击杀最小编号玩家"

    # 真实 LLM：先谨慎思考，再决策
    situation = (
        "你是狼人，正在和狼队友（见私密信息）秘密协商今晚的击杀目标。"
        "目标可以选任意存活玩家（包括队友、也可以刀自己）。\n"
        "战略考量：优先刀掉对好人威胁最大的神职（预言家＞女巫＞猎人＞守卫）；"
        "若场上已有警长且是神职/预言家，刀警长可夺回 1.5 票主动权与带节奏能力（但要防守卫守警长）；"
        "也可考虑自刀骗取女巫解药/银水。权衡利弊后给出目标。"
    )
    if discussion:
        situation += "\n【狼队友的讨论记录】\n" + discussion
    think = _deliberate(game, wolf, "选择今晚击杀目标", situation)
    state = game.private_state_text(wolf)
    head = (
        state + "\n\n" + situation +
        ("\n\n【你刚才的谨慎推理】\n" + think if think else "") +
        "\n请参考以上推理，给出你的最终决定（可坚持或调整讨论意见）。"
        "\n可选号码：" + "、".join(str(c) for c in cands) +
        "\n请严格按下面格式回复（理由一句话）：\n提议：<玩家号码>\n理由：<一句话>"
    )
    sys = _system_prompt(wolf) + "\n现在协商今晚的击杀目标。"
    resp = llm.chat(sys, user=head, temperature=0.6, max_tokens=60)
    target = llm.parse_int(resp)
    if target not in cands:
        target = min(cands) if cands else None
    # 提取理由（"理由："之后到行尾，或整段 fallback）
    reason = ""
    for line in resp.splitlines():
        if "理由" in line:
            reason = line.split("：", 1)[-1].split(":", 1)[-1].strip()
            break
    if not reason:
        reason = resp.replace(str(target), "").strip()[:40]
    return target, reason


# ============ 狼人：与玩家（狼队友）自由讨论 ============
def ai_wolf_chat(game, wolf, discussion):
    """狼人（AI）基于与狼队友（可能含真人玩家）的讨论记录，回复一句简短回应，
    用于"玩家参与狼队讨论"环节：真人狼发言后，AI 狼队友据此回应。"""
    game._acting_idx = wolf.idx
    if config.USE_MOCK:
        return "我听你的，按大家讨论的来。"

    state = game.private_state_text(wolf)
    sys = _system_prompt(wolf) + "\n你们狼队正在秘密密谋今晚击杀谁。下面是你和狼队友（其中可能有一名真人玩家）的讨论记录。"
    user = (
        state + "\n\n【狼队讨论记录】\n" + discussion +
        "\n\n请基于以上讨论，用一句简短的话（30字以内）回应你的狼队友："
        "可以附和、反驳、补充击杀理由，或追问。只输出你的回应，"
        "不要重复报出最终击杀号码，也不要在回应里出现'狼人''队友''狼队'这类词（那会被外界听去）。"
    )
    resp = llm.chat(sys, user, temperature=0.7, max_tokens=60)
    return (resp or "……").strip()


# ============ 预言家：查验 ============
def ai_seer_check(game, seer):
    game._acting_idx = seer.idx
    game._acting_checked = list(seer.checked.keys())
    if config.USE_MOCK:
        cands = [p.idx for p in game.alive_players() if p.idx != seer.idx and p.idx not in seer.checked]
        return min(cands) if cands else None

    cands = [p.idx for p in game.alive_players() if p.idx != seer.idx and p.idx not in seer.checked]
    situation = "你是预言家，需要选择今晚查验的目标（不可查自己、不可重复查）。"
    think = _deliberate(game, seer, "选择查验目标", situation)
    state = game.private_state_text(seer)
    user = (
        state + "\n\n" + situation +
        ("\n\n【你刚才的谨慎推理】\n" + think if think else "") +
        "\n请基于推理，选择一名尚未查验、非自己的玩家查验。"
        "只回复要查验的玩家号码（仅数字）。可选号码："
        + "、".join(str(c) for c in cands)
    )
    sys = _system_prompt(seer) + "\n现在需要选择查验目标。"
    resp = llm.chat(sys, user, temperature=0.4, max_tokens=20)
    target = llm.parse_int(resp)
    if target not in cands:
        target = min(cands) if cands else None
    return target


# ============ 女巫：用药 ============
def ai_witch_action(game, witch, killed_idx):
    """返回 (save: bool, poison_idx: int or None)。"""
    if config.USE_MOCK:
        # 谨慎策略：第一晚优先救人（留住好人战力 / 保住预言家）；
        # 非首夜则通常不救，除非被杀的是已知神职（如预言家），把解药留给更关键的局点。
        if killed_idx is not None and witch.has_antidote:
            if game.day == 1:
                return True, None
            victim = game.get(killed_idx)
            if victim.role == SEER:
                return True, None
        return False, None

    killed_name = game.get(killed_idx).name if killed_idx else "无人"
    situation = (
        f"今晚被狼人袭击的是：{killed_name}（{killed_idx}号）。"
        "\n你拥有一瓶解药和一瓶毒药，各只能用一次。"
        "请特别谨慎地权衡——尤其要考虑：这是第几晚？第一晚是否要用药救人？"
        "（第一晚救人是常见策略，可留住好人战力或保住预言家；但也会暴露你、并消耗解药。）"
        "毒药则应在确有把握对方是狼时才使用，切勿盲毒误伤神职。"
    )
    think = _deliberate(game, witch, "决定是否用药（解药/毒药）", situation)
    state = game.private_state_text(witch)
    user = (
        state + "\n\n" + situation +
        ("\n\n【你刚才的谨慎推理】\n" + think if think else "") +
        "\n请基于推理，按下面格式回答（解药用/不用；毒药填要毒杀的玩家号码，不用填0）："
        "\n解药：(用/不用)\n毒药：(号码或0)"
    )
    sys = _system_prompt(witch) + "\n现在需要决定用药。"
    resp = llm.chat(sys, user, temperature=0.4, max_tokens=40)
    save = False
    if witch.has_antidote:
        save_line = next((l for l in resp.splitlines() if "解药" in l), "")
        if "不用" in save_line:
            save = False
        elif "用" in save_line:
            save = True
        else:
            save = ("不用" not in resp) and ("用" in resp)
    poison = None
    # 解析毒药号码（健壮：扫描全部数字，取第一个合法的）
    nums = [int(x) for x in re.findall(r"\d+", resp)]
    for n in nums:
        if n == 0:
            continue
        p = game.get(n)
        if p and p.alive and n != killed_idx and witch.has_poison:
            poison = n
            break
    return save, poison


# ============ 白天发言 ============
def ai_speak(game, player):
    if config.USE_MOCK:
        others = [p for p in game.alive_players() if p.idx != player.idx]
        if others:
            suspect = random.choice(others)
            return f"我是{persona_short(player)}，我觉得{suspect.idx}号{suspect.name}有点可疑。"
        return "我是好人，先听听大家怎么说。"

    situation = f"现在是白天发言环节（第 {game.day} 天）。"
    if game.sheriff is not None and player.idx == game.sheriff:
        situation += "\n你是现任警长（你放逐投票计 1.5 票，可公开'归票'带节奏）。"
        if player.role == SEER:
            situation += ("作为预言家警长，请安排'警徽流'：说明今晚/明晚打算查验谁，"
                          "让好人提前知道验人方向，并公开号召大家跟你的归票。")
        elif not player.is_wolf:
            situation += "请利用警长权威公开'归票'，号召大家把票投给你最怀疑的狼人。"
        else:
            situation += "（你是狼人却挂着警徽——借机带偏放逐票、给狼队友发金水，别露馅。）"
    elif game.sheriff is not None:
        situation += f"\n当前警长是 {game.sheriff}号。可针对警长的带队方向表态支持或反驳。"
    think = _deliberate(game, player, "白天公开发言", situation)
    state = game.private_state_text(player)
    user = (
        state + "\n\n" + situation +
        ("\n\n【你刚才的谨慎分析】\n" + think if think else "") +
        "\n请基于你的分析，用自然、符合你性格的口吻公开发言："
        "可以分析局势、表达怀疑、为自己辩护或附和他人。"
        f"控制在 {config.SPEECH_MAX_CHARS} 字以内。"
    )
    sys = _system_prompt(player) + "\n现在需要公开发言。"
    resp = llm.chat(sys, user, temperature=0.85, max_tokens=config.SPEECH_MAX_CHARS + 20)
    return resp[: config.SPEECH_MAX_CHARS] if resp else "（沉默）"


def persona_short(player):
    return player.name


# ============ 投票放逐 ============
def ai_vote(game, player, restrict=None):
    """放逐投票。restrict 给定时只能在 restrict 集合内选择（用于平票二次投票）。"""
    game._acting_idx = player.idx
    cands = [p.idx for p in game.alive_players() if p.idx != player.idx]
    if restrict:
        cands = [c for c in cands if c in restrict]
    if config.USE_MOCK:
        # 狼人投好人里最小编号；好人随机投别人
        if player.is_wolf:
            good = [c for c in cands if not game.get(c).is_wolf]
            return min(good) if good else min(cands)
        return random.choice(cands)

    situation = "现在是投票放逐环节，需要投票放逐一名你认为是狼人的玩家（也可弃票）。"
    if game.sheriff is not None:
        situation += f"（现任警长是 {game.sheriff}号，其放逐票计 1.5 票、并能公开'归票'带节奏；你的投票要考虑到这层风向。）"
    if restrict:
        situation += "（因上轮平票，本轮只能在平票玩家之间选择放逐对象。）"
    think = _deliberate(game, player, "投票放逐", situation)
    state = game.private_state_text(player)
    user = (
        state + "\n\n" + situation +
        ("\n\n【你刚才的谨慎推理】\n" + think if think else "") +
        "\n请基于推理，投票放逐你最怀疑的狼人（或弃票，回复 0）。"
        "只回复号码（仅数字）。可选号码："
        + "、".join(str(c) for c in cands)
        + "，或 0（弃票）。"
    )
    sys = _system_prompt(player) + "\n现在需要投票（可弃票）。"
    resp = llm.chat(sys, user, temperature=0.5, max_tokens=20)
    # 显式弃票
    if "弃票" in resp:
        return 0
    target = llm.parse_int(resp)
    if target == 0:
        return 0  # 0 表示弃票
    if target not in cands:
        target = random.choice(cands)
    return target


# ============ 遗言 ============
def ai_lastwill(game, player):
    """玩家出局时留下的遗言（简短公开）。"""
    if config.USE_MOCK:
        if player.role == SEER:
            return "我是预言家，狼人就在你们中间，信我者得永生！"
        if player.role == WITCH:
            return "我的药已用尽，剩下的交给你们了。"
        if player.is_wolf:
            return "我是被冤枉的，真正的狼人还藏在你们中间！"
        return "我是好人，狼人一定还藏着，别被骗了！"

    situation = "你已出局（死亡或被放逐）。请留下一句简短的遗言（不超过 40 字），"
    if player.role == SEER:
        situation += "你可以用最后的机会点名你查验出的狼人，给好人指路。"
    elif player.is_wolf:
        situation += "你是狼人，可以最后伪装或破防，随你。"
    else:
        situation += "你是好人，可以提醒活着的人注意谁可疑。"
    state = game.private_state_text(player)
    user = (
        state + "\n\n" + situation +
        "\n请只输出一句遗言（不超过 40 字）。"
    )
    sys = _system_prompt(player) + "\n这是你出局前的最后遗言。"
    resp = llm.chat(sys, user=user, temperature=0.8, max_tokens=60)
    return (resp or "（沉默）")[:40]


# ============ 上警竞选警长 ============
def ai_run_for_sheriff(game, player):
    """第 1 天『上警』环节：是否竞选警长，以及一句竞选发言。返回 (run: bool, speech: str)。"""
    if config.USE_MOCK:
        # 确定性策略：预言家必上（抢警徽带节奏）；神职积极；狼约半数上警搅局；平民少数上警。
        import random as _r
        if player.role == SEER:
            run = True
            speech = "我是预言家，有查杀，选我当警长带大家找狼！"
        elif player.role == WITCH:
            run = _r.random() < 0.7
            speech = "我是女巫，信息多，愿当警长组织讨论。"
        elif player.role == GUARD:
            run = _r.random() < 0.5
            speech = "我是守卫，愿当警长帮大家分析局势。"
        elif player.is_wolf:
            run = _r.random() < 0.5
            speech = "我逻辑在线，愿当警长带节奏。"
        else:
            run = _r.random() < 0.4
            speech = "我愿意当警长，认真听发言帮大家分析。"
        return run, speech

    # 真实 LLM：先谨慎推理"上警与否"的战略取舍，再决策
    role_hint = ""
    if player.is_wolf:
        role_hint = ("你是狼人——可考虑悍跳预言家并争抢警徽来搅局、保护队友；"
                     "若选择不上警，也要想好后续如何站边、是否冲锋真预言家。")
    elif player.role == SEER:
        role_hint = "你是预言家——强烈建议上警跳身份、抢警徽，用权威带节奏并安排警徽流。"
    elif player.role in (WITCH, GUARD, HUNTER):
        role_hint = "你是神职——上警能贡献信息、帮好人；不上警也可，但别让狼轻易抢走警徽。"
    else:
        role_hint = "你是平民——上警与否皆可，但选警长时一定要把票投给可信的真预言家。"
    situation = (
        "现在是第 1 天『上警竞选』环节。你要决定是否竞选警长（上警），并说一句竞选发言。\n"
        "【警长的战略价值】警长放逐投票计 1.5 票（可打破平票、主导风向），且能公开'归票'带节奏；"
        "真预言家拿到警徽能最大程度带飞好人。\n"
        f"【你的身份定位】{role_hint}"
    )
    think = _deliberate(game, player, "是否上警竞选警长", situation)
    state = game.private_state_text(player)
    user = (
        state + "\n\n" + situation +
        ("\n\n【你刚才的谨慎推理】\n" + think if think else "") +
        "\n请先一行给出决定，再附一句简短竞选发言（不超过 40 字）。\n"
        "格式（严格按此两行）：\n决定：上警（或 不上警）\n发言：<你的竞选发言>"
    )
    sys = _system_prompt(player) + "\n现在决定是否上警竞选警长。"
    resp = llm.chat(sys, user=user, temperature=0.7, max_tokens=80)
    run = True
    speech = ""
    for line in resp.splitlines():
        if "决定" in line:
            run = "不上警" not in line
        elif "发言" in line:
            speech = line.split("：", 1)[-1].split(":", 1)[-1].strip()
    if not speech:
        speech = resp.replace("决定：", "").replace("不上警", "").replace("上警", "").strip()[:40]
    return run, (speech or "我参选警长，请大家支持。")[:40]


def ai_vote_sheriff(game, player, candidates):
    """选举警长：在候选人中选择一人，或弃票（返回 0）。"""
    if not candidates:
        return 0
    if config.USE_MOCK:
        # 狼人把票投给狼队友候选人（抢警徽）；好人盲投非狼候选人（尽量不把警徽给狼）。
        import random as _r
        wolf_cands = [c for c in candidates if game.get(c).is_wolf]
        if player.is_wolf:
            return min(wolf_cands) if wolf_cands else (min(candidates) if candidates else 0)
        good_cands = [c for c in candidates if not game.get(c).is_wolf]
        return min(good_cands) if good_cands else (min(candidates) if candidates else 0)

    cands = [c for c in candidates if game.get(c) and game.get(c).alive]
    if not cands:
        return 0
    # 阵营提示：决定把警徽交给谁
    if player.is_wolf:
        hint = ("你是狼人：把票投给自己的悍跳狼队友（若有），帮他抢到警徽；"
                "若没有狼队友候选，可投一个能搅局或站错边的候选人，千万别把警徽轻易交给真预言家。"
                "看公共事件里谁在'上警'时跳了预言家。")
    else:
        hint = ("你是好人：把警徽交给最可信的预言家——看谁先跳预言家、验人逻辑是否自洽、发言是否心虚，"
                "把票投给他；若没有可信预言家候选，可弃票或投相对可信者。")
    situation = (
        "现在是『选举警长』投票环节。候选人的竞选发言见上面的公共事件记录（'X号 上警：…'）。\n"
        + hint
    )
    state = game.private_state_text(player)
    user = (
        state + "\n\n" + situation +
        "\n可选候选人：" + "、".join(f"{c}号{game.get(c).name}" for c in cands) +
        "\n请只回复你要投票的候选人号码（仅数字），或 0（弃票）。"
    )
    sys = _system_prompt(player) + "\n现在选举警长。"
    resp = llm.chat(sys, user=user, temperature=0.5, max_tokens=20)
    if "弃票" in resp:
        return 0
    target = llm.parse_int(resp)
    if target == 0 or target not in cands:
        return 0
    return target


# ============ 守卫：守护 ============
def ai_guard_protect(game, guard, valid):
    game._acting_idx = guard.idx
    if config.USE_MOCK:
        return min(valid) if valid else None

    situation = (
        "你是守卫，需要选择今晚守护的目标（不能守自己，不能连续两晚守同一人）。"
        "要基于局势判断谁最可能成为狼人的刀口。"
    )
    think = _deliberate(game, guard, "选择守护目标", situation)
    state = game.private_state_text(guard)
    user = (
        state + "\n\n" + situation +
        ("\n\n【你刚才的谨慎推理】\n" + think if think else "") +
        "\n请基于推理，回复要守护的玩家号码（仅数字）。可选号码："
        + "、".join(str(c) for c in valid)
    )
    sys = _system_prompt(guard) + "\n现在需要选择守护目标。"
    resp = llm.chat(sys, user, temperature=0.5, max_tokens=20)
    target = llm.parse_int(resp)
    if target not in valid:
        target = min(valid) if valid else None
    return target


# ============ 猎人：开枪 ============
def ai_hunter_shoot(game, hunter, valid):
    if config.USE_MOCK:
        known_wolves = [c for c in valid if game.get(c).is_wolf]
        return min(known_wolves) if known_wolves else min(valid)

    situation = (
        f"你（{hunter.idx}号猎人）出局了！可以开枪带走一名玩家。"
        "开枪务必带狼，优先带走你判断为狼人的目标；若场上无明确狼，宁可慎带。"
    )
    think = _deliberate(game, hunter, "开枪带走目标", situation)
    state = game.private_state_text(hunter)
    user = (
        state + "\n\n" + situation +
        ("\n\n【你刚才的谨慎推理】\n" + think if think else "") +
        "\n请基于推理，回复要带走的玩家号码（仅数字）。可选号码："
        + "、".join(str(c) for c in valid)
    )
    sys = _system_prompt(hunter) + "\n现在需要开枪。"
    resp = llm.chat(sys, user, temperature=0.5, max_tokens=20)
    target = llm.parse_int(resp)
    if target not in valid:
        target = random.choice(valid)
    return target
