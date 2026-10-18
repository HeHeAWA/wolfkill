// AI 玩家决策逻辑。USE_MOCK=true 时使用确定性逻辑（不消耗 API），便于离线测试。
import java.io.File;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;
import java.util.*;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

public class AI {
    // ---------- 提示词加载（来自 prompts.txt，避免源码内转义双引号） ----------
    static Map<String, String> PROMPTS = loadPrompts();
    static String RULES_TEXT = PROMPTS.get("RULES");
    static Map<String, String> ROLE_GUIDE = new LinkedHashMap<>();
    static {
        ROLE_GUIDE.put(Game.WOLF, PROMPTS.get("ROLE_WOLF"));
        ROLE_GUIDE.put(Game.SEER, PROMPTS.get("ROLE_SEER"));
        ROLE_GUIDE.put(Game.WITCH, PROMPTS.get("ROLE_WITCH"));
        ROLE_GUIDE.put(Game.HUNTER, PROMPTS.get("ROLE_HUNTER"));
        ROLE_GUIDE.put(Game.GUARD, PROMPTS.get("ROLE_GUARD"));
        ROLE_GUIDE.put(Game.VILLAGER, PROMPTS.get("ROLE_VILLAGER"));
    }
    static Random RND = new Random();

    static Map<String, String> loadPrompts() {
        Map<String, String> m = new LinkedHashMap<>();
        try {
            String dir = classDir();
            byte[] bytes = Files.readAllBytes(Paths.get(dir, "prompts.txt"));
            String content = new String(bytes, StandardCharsets.UTF_8);
            String[] parts = content.split("###");
            String key = null;
            for (String p : parts) {
                p = p.trim();
                if (p.isEmpty()) continue;
                if (!p.contains("\n") && p.matches("[A-Z_]+")) { key = p; continue; }
                if (key != null) { m.put(key, p); key = null; }
            }
        } catch (Exception e) {
            System.out.println("[提示词加载失败] " + e);
        }
        return m;
    }

    static String classDir() {
        try {
            URL loc = AI.class.getProtectionDomain().getCodeSource().getLocation();
            File f = new File(loc.toURI());
            if (f.isFile()) return f.getParent();
            return f.getPath();
        } catch (Exception e) { return "."; }
    }

    static String joinNums(List<Integer> list) {
        List<String> s = new ArrayList<>();
        for (int x : list) s.add(String.valueOf(x));
        return String.join("、", s);
    }

    static String _system_prompt(Game.Player player) {
        String base = "你正在参与一场纯文字狼人杀游戏，你是一名狼人杀高手。请始终用简体中文回复，"
                + "自然地扮演这名玩家，不要提及自己是在和 AI 对话。"
                + "你深刻理解上警、警徽、悍跳、对跳、归票、警徽流、自刀、银水、查杀、金水等进阶玩法，"
                + "并能结合当前是否有人当警长、警长是谁、谁在抢警徽，来制定你的每一步策略。"
                + "你的推理必须严格基于当前场上的公开信息——公共事件记录（谁在何时死去、被放逐、谁上了警/当了警长）、"
                + "各位玩家的公开发言、每轮投票结果——不得凭空脑补他人的身份；"
                + "要主动根据实情去猜想、推断谁是狼人、谁在伪装，并据此做出最有利的决策。"
                + "\n你的编号：" + player.idx + "号\n你的名字：" + player.name;
        if (player.personality != null && !player.personality.isEmpty())
            base += "\n你的定位：" + player.personality;
        base += "\n\n" + RULES_TEXT;
        String guide = ROLE_GUIDE.get(player.role);
        if (guide != null) base += "\n\n" + guide;
        return base;
    }

    static String _deliberate(Game game, Game.Player player, String kind, String situation) {
        if (Config.USE_MOCK) return "";
        String state = game.privateStateText(player);
        String sys = _system_prompt(player) + "\n在正式行动前，请先对『" + kind + "』谨慎推理一遍。";
        String user = state + "\n\n" + situation
                + "\n\n请以一名狼人杀高手的心态，先谨慎推理一遍：综合场上的公开信息"
                + "（公开日志中的死亡/放逐记录、各位玩家的发言、投票结果）与你的私密信息，"
                + "分析当前局势、各方的可疑度，并权衡你这一步的利弊与风险。"
                + "只输出 3-5 句思考，不要给出最终号码或决定。";
        String r = LLM.chat(sys, user, 0.7, 200, null);
        return (r == null) ? "" : r.trim();
    }

    public static class KillDecision { public int target; public String reason; public KillDecision(int t, String r){ target=t; reason=r; } }
    public static class SheriffRun { public boolean run; public String speech; public SheriffRun(boolean r, String s){ run=r; speech=s; } }
    public static class WitchAction { public boolean save; public Integer poison; public WitchAction(boolean s, Integer p){ save=s; poison=p; } }

    public static KillDecision aiWolfPropose(Game game, Game.Player wolf, String discussion) {
        List<Integer> cands = new ArrayList<>();
        for (Game.Player p : game.alivePlayers()) cands.add(p.idx);
        if (Config.USE_MOCK) {
            Game.Player seer = null;
            for (Game.Player p : game.alivePlayers()) if (p.role.equals(Game.SEER) && p.idx != wolf.idx) { seer = p; break; }
            if (seer != null) return new KillDecision(seer.idx, "优先击杀预言家");
            Game.Player sp = (game.sheriff != null) ? game.get(game.sheriff) : null;
            if (sp != null && sp.alive && !sp.isWolf && sp.idx != wolf.idx)
                return new KillDecision(sp.idx, "刀掉警长夺回主动权");
            List<Integer> others = new ArrayList<>();
            for (int c : cands) if (c != wolf.idx) others.add(c);
            int t = others.isEmpty() ? wolf.idx : Collections.min(others);
            return new KillDecision(t, "击杀最小编号玩家");
        }
        String situation = "你是狼人，正在和狼队友（见私密信息）秘密协商今晚的击杀目标。"
                + "目标可以选任意存活玩家（包括队友、也可以刀自己）。\n"
                + "战略考量：优先刀掉对好人威胁最大的神职（预言家＞女巫＞猎人＞守卫）；"
                + "若场上已有警长且是神职/预言家，刀警长可夺回 1.5 票主动权与带节奏能力（但要防守卫守警长）；"
                + "也可考虑自刀骗取女巫解药/银水。权衡利弊后给出目标。";
        if (discussion != null && !discussion.isEmpty()) situation += "\n【狼队友的讨论记录】\n" + discussion;
        String think = _deliberate(game, wolf, "选择今晚击杀目标", situation);
        String state = game.privateStateText(wolf);
        String head = state + "\n\n" + situation
                + (think.isEmpty() ? "" : "\n\n【你刚才的谨慎推理】\n" + think)
                + "\n请参考以上推理，给出你的最终决定（可坚持或调整讨论意见）。"
                + "\n可选号码：" + joinNums(cands)
                + "\n请严格按下面格式回复（理由一句话）：\n提议：<玩家号码>\n理由：<一句话>";
        String sys = _system_prompt(wolf) + "\n现在协商今晚的击杀目标。";
        String resp = LLM.chat(sys, head, 0.6, 60, null);
        int target = LLM.parseInt(resp, -1);
        if (!cands.contains(target)) target = cands.isEmpty() ? -1 : Collections.min(cands);
        String reason = "";
        if (resp != null) for (String line : resp.split("\n")) {
            if (line.contains("理由")) {
                String[] sp = line.split("：", 2);
                if (sp.length < 2) sp = line.split(":", 2);
                reason = (sp.length > 1 ? sp[1] : "").trim();
                break;
            }
        }
        if (reason.isEmpty() && resp != null) reason = resp.replace(String.valueOf(target), "").trim();
        if (reason.length() > 40) reason = reason.substring(0, 40);
        return new KillDecision(target, reason);
    }

    public static String aiWolfChat(Game game, Game.Player wolf, String discussion) {
        if (Config.USE_MOCK) return "我听你的，按大家讨论的来。";
        String state = game.privateStateText(wolf);
        String sys = _system_prompt(wolf) + "\n你们狼队正在秘密密谋今晚击杀谁。下面是你和狼队友（其中可能有一名真人玩家）的讨论记录。";
        String user = state + "\n\n【狼队讨论记录】\n" + discussion
                + "\n\n请基于以上讨论，用一句简短的话（30字以内）回应你的狼队友："
                + "可以附和、反驳、补充击杀理由，或追问。只输出你的回应，"
                + "不要重复报出最终击杀号码，也不要在回应里出现'狼人''队友''狼队'这类词（那会被外界听去）。";
        String resp = LLM.chat(sys, user, 0.7, 60, null);
        return (resp == null || resp.isEmpty()) ? "……" : resp.trim();
    }

    public static Integer aiSeerCheck(Game game, Game.Player seer) {
        List<Integer> cands = new ArrayList<>();
        for (Game.Player p : game.alivePlayers())
            if (p.idx != seer.idx && !seer.checked.containsKey(p.idx)) cands.add(p.idx);
        if (Config.USE_MOCK) return cands.isEmpty() ? null : Collections.min(cands);
        String situation = "你是预言家，需要选择今晚查验的目标（不可查自己、不可重复查）。";
        String think = _deliberate(game, seer, "选择查验目标", situation);
        String state = game.privateStateText(seer);
        String user = state + "\n\n" + situation
                + (think.isEmpty() ? "" : "\n\n【你刚才的谨慎推理】\n" + think)
                + "\n请基于推理，选择一名尚未查验、非自己的玩家查验。只回复要查验的玩家号码（仅数字）。可选号码："
                + joinNums(cands);
        String sys = _system_prompt(seer) + "\n现在需要选择查验目标。";
        String resp = LLM.chat(sys, user, 0.4, 20, null);
        int target = LLM.parseInt(resp, -1);
        if (!cands.contains(target)) return cands.isEmpty() ? null : Collections.min(cands);
        return target;
    }

    public static WitchAction aiWitchAction(Game game, Game.Player witch, Integer killedIdx) {
        if (Config.USE_MOCK) {
            if (killedIdx != null && witch.hasAntidote) {
                if (game.day == 1) return new WitchAction(true, null);
                Game.Player victim = game.get(killedIdx);
                if (victim != null && victim.role.equals(Game.SEER)) return new WitchAction(true, null);
            }
            return new WitchAction(false, null);
        }
        String killedName = (killedIdx != null && game.get(killedIdx) != null) ? game.get(killedIdx).name : "无人";
        String situation = "今晚被狼人袭击的是：" + killedName + "（" + killedIdx + "号）。"
                + "\n你拥有一瓶解药和一瓶毒药，各只能用一次。"
                + "请特别谨慎地权衡——尤其要考虑：这是第几晚？第一晚是否要用药救人？"
                + "（第一晚救人是常见策略，可留住好人战力或保住预言家；但也会暴露你、并消耗解药。）"
                + "毒药则应在确有把握对方是狼时才使用，切勿盲毒误伤神职。";
        String think = _deliberate(game, witch, "决定是否用药（解药/毒药）", situation);
        String state = game.privateStateText(witch);
        String user = state + "\n\n" + situation
                + (think.isEmpty() ? "" : "\n\n【你刚才的谨慎推理】\n" + think)
                + "\n请基于推理，按下面格式回答（解药用/不用；毒药填要毒杀的玩家号码，不用填0）："
                + "\n解药：(用/不用)\n毒药：(号码或0)";
        String sys = _system_prompt(witch) + "\n现在需要决定用药。";
        String resp = LLM.chat(sys, user, 0.4, 40, null);
        boolean save = false;
        if (witch.hasAntidote && resp != null) {
            for (String line : resp.split("\n")) {
                if (line.contains("解药")) {
                    if (line.contains("不用")) save = false;
                    else if (line.contains("用")) save = true;
                    else save = !line.contains("不用") && line.contains("用");
                    break;
                }
            }
        }
        Integer poison = null;
        if (resp != null) {
            Matcher m = Pattern.compile("\\d+").matcher(resp);
            while (m.find()) {
                int n = Integer.parseInt(m.group());
                if (n == 0) continue;
                Game.Player p = game.get(n);
                int kx = (killedIdx == null) ? -1 : killedIdx;
                if (p != null && p.alive && n != kx && witch.hasPoison) { poison = n; break; }
            }
        }
        return new WitchAction(save, poison);
    }

    public static String aiSpeak(Game game, Game.Player player) {
        if (Config.USE_MOCK) {
            List<Game.Player> others = new ArrayList<>();
            for (Game.Player p : game.alivePlayers()) if (p.idx != player.idx) others.add(p);
            if (!others.isEmpty()) {
                Game.Player suspect = others.get(RND.nextInt(others.size()));
                return "我是" + player.name + "，我觉得" + suspect.idx + "号" + suspect.name + "有点可疑。";
            }
            return "我是好人，先听听大家怎么说。";
        }
        String situation = "现在是白天发言环节（第 " + game.day + " 天）。";
        if (game.sheriff != null && player.idx == game.sheriff.intValue()) {
            situation += "\n你是现任警长（你放逐投票计 1.5 票，可公开'归票'带节奏）。";
            if (player.role.equals(Game.SEER))
                situation += "作为预言家警长，请安排'警徽流'：说明今晚/明晚打算查验谁，让好人提前知道验人方向，并公开号召大家跟你的归票。";
            else if (!player.isWolf)
                situation += "请利用警长权威公开'归票'，号召大家把票投给你最怀疑的狼人。";
            else
                situation += "（你是狼人却挂着警徽——借机带偏放逐票、给狼队友发金水，别露馅。）";
        } else if (game.sheriff != null) {
            situation += "\n当前警长是 " + game.sheriff + "号。可针对警长的带队方向表态支持或反驳。";
        }
        String think = _deliberate(game, player, "白天公开发言", situation);
        String state = game.privateStateText(player);
        String user = state + "\n\n" + situation
                + (think.isEmpty() ? "" : "\n\n【你刚才的谨慎分析】\n" + think)
                + "\n请基于你的分析，用自然、符合你性格的口吻公开发言：可以分析局势、表达怀疑、为自己辩护或附和他人。"
                + "控制在 " + Config.SPEECH_MAX_CHARS + " 字以内。";
        String sys = _system_prompt(player) + "\n现在需要公开发言。";
        String resp = LLM.chat(sys, user, 0.85, Config.SPEECH_MAX_CHARS + 20, null);
        if (resp == null || resp.isEmpty()) return "（沉默）";
        return resp.length() > Config.SPEECH_MAX_CHARS ? resp.substring(0, Config.SPEECH_MAX_CHARS) : resp;
    }

    public static int aiVote(Game game, Game.Player player, Set<Integer> restrict) {
        List<Integer> cands = new ArrayList<>();
        for (Game.Player p : game.alivePlayers()) if (p.idx != player.idx) cands.add(p.idx);
        if (restrict != null) {
            List<Integer> f = new ArrayList<>();
            for (int c : cands) if (restrict.contains(c)) f.add(c);
            cands = f;
        }
        if (Config.USE_MOCK) {
            if (player.isWolf) {
                List<Integer> good = new ArrayList<>();
                for (int c : cands) if (!game.get(c).isWolf) good.add(c);
                return good.isEmpty() ? Collections.min(cands) : Collections.min(good);
            }
            return cands.get(RND.nextInt(cands.size()));
        }
        String situation = "现在是投票放逐环节，需要投票放逐一名你认为是狼人的玩家（也可弃票）。";
        if (game.sheriff != null)
            situation += "（现任警长是 " + game.sheriff + "号，其放逐票计 1.5 票、并能公开'归票'带节奏；你的投票要考虑到这层风向。）";
        if (restrict != null) situation += "（因上轮平票，本轮只能在平票玩家之间选择放逐对象。）";
        String think = _deliberate(game, player, "投票放逐", situation);
        String state = game.privateStateText(player);
        String user = state + "\n\n" + situation
                + (think.isEmpty() ? "" : "\n\n【你刚才的谨慎推理】\n" + think)
                + "\n请基于推理，投票放逐你最怀疑的狼人（或弃票，回复 0）。只回复号码（仅数字）。可选号码："
                + joinNums(cands) + "，或 0（弃票）。";
        String sys = _system_prompt(player) + "\n现在需要投票（可弃票）。";
        String resp = LLM.chat(sys, user, 0.5, 20, null);
        if (resp != null && resp.contains("弃票")) return 0;
        int target = LLM.parseInt(resp, -1);
        if (target == 0) return 0;
        if (!cands.contains(target)) target = cands.get(RND.nextInt(cands.size()));
        return target;
    }

    public static String aiLastwill(Game game, Game.Player player) {
        if (Config.USE_MOCK) {
            if (player.role.equals(Game.SEER)) return "我是预言家，狼人就在你们中间，信我者得永生！";
            if (player.role.equals(Game.WITCH)) return "我的药已用尽，剩下的交给你们了。";
            if (player.isWolf) return "我是被冤枉的，真正的狼人还藏在你们中间！";
            return "我是好人，狼人一定还藏着，别被骗了！";
        }
        String situation = "你已出局（死亡或被放逐）。请留下一句简短的遗言（不超过 40 字），";
        if (player.role.equals(Game.SEER)) situation += "你可以用最后的机会点名你查验出的狼人，给好人指路。";
        else if (player.isWolf) situation += "你是狼人，可以最后伪装或破防，随你。";
        else situation += "你是好人，可以提醒活着的人注意谁可疑。";
        String state = game.privateStateText(player);
        String user = state + "\n\n" + situation + "\n请只输出一句遗言（不超过 40 字）。";
        String sys = _system_prompt(player) + "\n这是你出局前的最后遗言。";
        String resp = LLM.chat(sys, user, 0.8, 60, null);
        String r = (resp == null || resp.isEmpty()) ? "（沉默）" : resp;
        return r.length() > 40 ? r.substring(0, 40) : r;
    }

    public static SheriffRun aiRunForSheriff(Game game, Game.Player player) {
        if (Config.USE_MOCK) {
            double d = RND.nextDouble();
            boolean run; String speech;
            if (player.role.equals(Game.SEER)) { run = true; speech = "我是预言家，有查杀，选我当警长带大家找狼！"; }
            else if (player.role.equals(Game.WITCH)) { run = d < 0.7; speech = "我是女巫，信息多，愿当警长组织讨论。"; }
            else if (player.role.equals(Game.GUARD)) { run = d < 0.5; speech = "我是守卫，愿当警长帮大家分析局势。"; }
            else if (player.isWolf) { run = d < 0.5; speech = "我逻辑在线，愿当警长带节奏。"; }
            else { run = d < 0.4; speech = "我愿意当警长，认真听发言帮大家分析。"; }
            return new SheriffRun(run, speech);
        }
        String roleHint;
        if (player.isWolf) roleHint = "你是狼人——可考虑悍跳预言家并争抢警徽来搅局、保护队友；若选择不上警，也要想好后续如何站边、是否冲锋真预言家。";
        else if (player.role.equals(Game.SEER)) roleHint = "你是预言家——强烈建议上警跳身份、抢警徽，用权威带节奏并安排警徽流。";
        else if (player.role.equals(Game.WITCH) || player.role.equals(Game.GUARD) || player.role.equals(Game.HUNTER)) roleHint = "你是神职——上警能贡献信息、帮好人；不上警也可，但别让狼轻易抢走警徽。";
        else roleHint = "你是平民——上警与否皆可，但选警长时一定要把票投给可信的真预言家。";
        String situation = "现在是第 1 天『上警竞选』环节。你要决定是否竞选警长（上警），并说一句竞选发言。\n"
                + "【警长的战略价值】警长放逐投票计 1.5 票（可打破平票、主导风向），且能公开'归票'带节奏；"
                + "真预言家拿到警徽能最大程度带飞好人。\n"
                + "【你的身份定位】" + roleHint;
        String think = _deliberate(game, player, "是否上警竞选警长", situation);
        String state = game.privateStateText(player);
        String user = state + "\n\n" + situation
                + (think.isEmpty() ? "" : "\n\n【你刚才的谨慎推理】\n" + think)
                + "\n请先一行给出决定，再附一句简短竞选发言（不超过 40 字）。\n"
                + "格式（严格按此两行）：\n决定：上警（或 不上警）\n发言：<你的竞选发言>";
        String sys = _system_prompt(player) + "\n现在决定是否上警竞选警长。";
        String resp = LLM.chat(sys, user, 0.7, 80, null);
        boolean run = true; String speech = "";
        if (resp != null) for (String line : resp.split("\n")) {
            if (line.contains("决定")) run = !line.contains("不上警");
            else if (line.contains("发言")) {
                String[] sp = line.split("：", 2);
                if (sp.length < 2) sp = line.split(":", 2);
                speech = (sp.length > 1 ? sp[1] : "").trim();
            }
        }
        if (speech.isEmpty() && resp != null)
            speech = resp.replace("决定：", "").replace("不上警", "").replace("上警", "").trim();
        if (speech.length() > 40) speech = speech.substring(0, 40);
        return new SheriffRun(run, speech.isEmpty() ? "我参选警长，请大家支持。" : speech);
    }

    public static int aiVoteSheriff(Game game, Game.Player player, List<Integer> candidates) {
        if (candidates == null || candidates.isEmpty()) return 0;
        if (Config.USE_MOCK) {
            List<Integer> wolfCands = new ArrayList<>();
            List<Integer> goodCands = new ArrayList<>();
            for (int c : candidates) {
                if (game.get(c).isWolf) wolfCands.add(c);
                else goodCands.add(c);
            }
            if (player.isWolf) return wolfCands.isEmpty() ? Collections.min(candidates) : Collections.min(wolfCands);
            return goodCands.isEmpty() ? Collections.min(candidates) : Collections.min(goodCands);
        }
        List<Integer> cands = new ArrayList<>();
        for (int c : candidates) { Game.Player p = game.get(c); if (p != null && p.alive) cands.add(c); }
        if (cands.isEmpty()) return 0;
        String hint;
        if (player.isWolf) hint = "你是狼人：把票投给自己的悍跳狼队友（若有），帮他抢到警徽；若没有狼队友候选，可投一个能搅局或站错边的候选人，千万别把警徽轻易交给真预言家。看公共事件里谁在'上警'时跳了预言家。";
        else hint = "你是好人：把警徽交给最可信的预言家——看谁先跳预言家、验人逻辑是否自洽、发言是否心虚，把票投给他；若没有可信预言家候选，可弃票或投相对可信者。";
        String situation = "现在是『选举警长』投票环节。候选人的竞选发言见上面的公共事件记录（'X号 上警：…'）。\n" + hint;
        String state = game.privateStateText(player);
        String user = state + "\n\n" + situation
                + "\n可选候选人：" + joinNums(cands)
                + "\n请只回复你要投票的候选人号码（仅数字），或 0（弃票）。";
        String sys = _system_prompt(player) + "\n现在选举警长。";
        String resp = LLM.chat(sys, user, 0.5, 20, null);
        if (resp != null && resp.contains("弃票")) return 0;
        int target = LLM.parseInt(resp, -1);
        if (target == 0 || !cands.contains(target)) return 0;
        return target;
    }

    public static Integer aiGuardProtect(Game game, Game.Player guard, List<Integer> valid) {
        if (Config.USE_MOCK) return valid.isEmpty() ? null : Collections.min(valid);
        String situation = "你是守卫，需要选择今晚守护的目标（不能守自己，不能连续两晚守同一人）。要基于局势判断谁最可能成为狼人的刀口。";
        String think = _deliberate(game, guard, "选择守护目标", situation);
        String state = game.privateStateText(guard);
        String user = state + "\n\n" + situation
                + (think.isEmpty() ? "" : "\n\n【你刚才的谨慎推理】\n" + think)
                + "\n请基于推理，回复要守护的玩家号码（仅数字）。可选号码：" + joinNums(valid);
        String sys = _system_prompt(guard) + "\n现在需要选择守护目标。";
        String resp = LLM.chat(sys, user, 0.5, 20, null);
        int target = LLM.parseInt(resp, -1);
        if (!valid.contains(target)) return valid.isEmpty() ? null : Collections.min(valid);
        return target;
    }

    public static int aiHunterShoot(Game game, Game.Player hunter, List<Integer> valid) {
        if (Config.USE_MOCK) {
            List<Integer> known = new ArrayList<>();
            for (int c : valid) if (game.get(c).isWolf) known.add(c);
            return known.isEmpty() ? Collections.min(valid) : Collections.min(known);
        }
        String situation = "你（" + hunter.idx + "号猎人）出局了！可以开枪带走一名玩家。开枪务必带狼，优先带走你判断为狼人的目标；若场上无明确狼，宁可慎带。";
        String think = _deliberate(game, hunter, "开枪带走目标", situation);
        String state = game.privateStateText(hunter);
        String user = state + "\n\n" + situation
                + (think.isEmpty() ? "" : "\n\n【你刚才的谨慎推理】\n" + think)
                + "\n请基于推理，回复要带走的玩家号码（仅数字）。可选号码：" + joinNums(valid);
        String sys = _system_prompt(hunter) + "\n现在需要开枪。";
        String resp = LLM.chat(sys, user, 0.5, 20, null);
        int target = LLM.parseInt(resp, -1);
        if (!valid.contains(target)) target = valid.get(RND.nextInt(valid.size()));
        return target;
    }
}
