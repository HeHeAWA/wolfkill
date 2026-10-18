// 房间 / 游戏状态 / 行动封装 / 游戏主流程（后台线程）。对应 Python 版 net/server.py。
import java.util.*;
import java.util.concurrent.*;
import java.util.function.*;

public class Room {
    public final java.util.concurrent.locks.ReentrantLock lock = new java.util.concurrent.locks.ReentrantLock();
    private final java.util.concurrent.locks.Condition wolfCond = lock.newCondition();

    public String state = "lobby";          // lobby | playing | ended
    public int version = 1;                  // 每次变更 +1，客户端据此轮询刷新
    public int total = 8;                    // 一桌总人数（真人 + AI）

    public LinkedHashMap<String, String> humans = new LinkedHashMap<>(); // token -> name
    public List<String> joinOrder = new ArrayList<>();
    public String hostToken = null;
    // 客户端幂等加入：浏览器持久化的 clientId -> token。刷新/重连后带同一 clientId 可找回原座位，避免重复创建玩家。
    public Map<String, String> clientIdToToken = new LinkedHashMap<>();

    public Game game = null;
    public Set<Integer> humanPids = new HashSet<>();
    public Map<Integer, String> pidToToken = new LinkedHashMap<>();
    public Map<String, Integer> tokenToPid = new LinkedHashMap<>();

    public List<String> publicLog = new ArrayList<>();
    public Map<Integer, List<String>> privateLog = new LinkedHashMap<>();
    public List<SecretEvent> secretEvents = new ArrayList<>();

    public static class SecretEvent { public Set<Integer> pids; public String text; public SecretEvent(Set<Integer> p, String t){ pids=p; text=t; } }

    public static class Pending { public Map<String, Object> spec; public Object answer; public CompletableFuture<Object> future = new CompletableFuture<>(); }

    public Map<Integer, Pending> pending = new LinkedHashMap<>();
    public Integer speaking = null;
    public String phaseText = "等待开始";
    public String winner = null;
    public Thread gameThread = null;

    public List<Map<String, Object>> wolfChat = new ArrayList<>();
    public boolean wolfDiscussionOpen = false;
    public boolean wolfDiscussionDone = false;
    public boolean night = false;            // 是否处于夜晚（前端据此关闭玩家亮框）
    public Set<Integer> left = new HashSet<>();
    // 心跳：玩家每次轮询 /api/state 即刷新 lastSeen(pid)。用于检测“挂机/关页但退出未生效”：
    // 长时间无心跳则 ask() 提前用默认值跳过，避免整局卡在某人待办上。
    public Map<Integer, Long> lastSeen = new ConcurrentHashMap<>();

    static Random RND = new Random();

    // ---------- 基础工具 ----------
    void bump() { version++; }

    void pub(String text) {
        lock.lock();
        try {
            publicLog.add(text);
            if (game != null) game.publicLog.add(text);
            bump();
        } finally { lock.unlock(); }
    }

    void notify(int pid, String text) {
        lock.lock();
        try {
            privateLog.computeIfAbsent(pid, k -> new ArrayList<>()).add(text);
            bump();
        } finally { lock.unlock(); }
    }

    void sec(String text, Set<Integer> pids) {
        lock.lock();
        try { secretEvents.add(new SecretEvent(pids, text)); bump(); }
        finally { lock.unlock(); }
    }

    void setPhase(String text, Integer speaking) {
        lock.lock();
        try { phaseText = text; this.speaking = speaking; bump(); }
        finally { lock.unlock(); }
    }

    void setPhase(String text) { setPhase(text, null); }

    void appendWolfChat(String name, String text, boolean isAi) {
        lock.lock();
        try {
            Map<String, Object> m = new LinkedHashMap<>();
            m.put("name", name); m.put("text", text); m.put("ai", isAi);
            wolfChat.add(m);
            bump();
        } finally { lock.unlock(); }
    }

    static Set<Integer> setOf(int x) { Set<Integer> s = new HashSet<>(); s.add(x); return s; }

    // ---------- 大厅 ----------
    static class JoinResult { String token; String error; }

    // clientId：浏览器持久化的稳定标识（可选）。同一 clientId 在 lobby 内重复加入时，直接复用已分配座位，
    // 这样即使上次加入的响应丢失/页面刷新，重新加入也不会多出一个玩家。
    JoinResult addHuman(String name, String clientId) {
        JoinResult r = new JoinResult();
        String token = null;
        lock.lock();
        try {
            if (!state.equals("lobby")) { r.error = "游戏已经开始，无法加入。"; return r; }
            // 幂等复用：clientId 已分配且座位仍在，直接返回原 token
            if (clientId != null && !clientId.isEmpty() && clientIdToToken.containsKey(clientId)) {
                String existing = clientIdToToken.get(clientId);
                if (humans.containsKey(existing)) {
                    r.token = existing;
                    return r;
                }
                clientIdToToken.remove(clientId); // 座位已失效，清理后重新分配
            }
            if (humans.size() >= total) { r.error = "房间已满。"; return r; }
            do { token = String.format("%016x", RND.nextLong()); } while (humans.containsKey(token));
            name = (name == null ? "" : name).trim();
            if (name.isEmpty()) name = "玩家" + (humans.size() + 1);
            if (name.length() > 12) name = name.substring(0, 12);
            humans.put(token, name);
            joinOrder.add(token);
            if (hostToken == null) hostToken = token;
            if (clientId != null && !clientId.isEmpty()) clientIdToToken.put(clientId, token);
            bump();
        } finally { lock.unlock(); }
        r.token = token;
        return r;
    }

    boolean setTotal(String token, int total) {
        lock.lock();
        try {
            if (!token.equals(hostToken) || !state.equals("lobby")) return false;
            this.total = Math.max(4, Math.min(total, 12));
            if (this.total < humans.size()) this.total = humans.size();
            bump();
            return true;
        } finally { lock.unlock(); }
    }

    // ---------- 人类行动：阻塞等待，超时/挂机兜底 ----------
    // ACTION_TIMEOUT：单次待办的绝对最长阻塞（避免真人迟迟不点导致整局卡死）。
    // IDLE_PASS：若玩家长时间不再轮询（关页但退出未生效），提前用默认值跳过。
    static final int ACTION_TIMEOUT = 45;
    static final int IDLE_PASS = 8;

    Object ask(int pid, Map<String, Object> spec) { return ask(pid, spec, ACTION_TIMEOUT); }
    Object ask(int pid, Map<String, Object> spec, int timeoutSec) {
        if (left.contains(pid)) return spec.get("default");
        Pending p = new Pending();
        p.spec = spec;
        pending.put(pid, p);
        bump();
        Object def = spec.get("default");
        long absDeadline = System.currentTimeMillis() + timeoutSec * 1000L;
        try {
            while (true) {
                long now = System.currentTimeMillis();
                long remain = absDeadline - now;
                if (remain <= 0) break;
                // 挂机检测：玩家不再轮询（关页但退出未生效），提前跳过，避免卡死
                Long ls = lastSeen.get(pid);
                if (ls != null && (now - ls) > IDLE_PASS * 1000L) break;
                long waitMs = Math.min(remain, 1000);
                try {
                    return p.future.get(waitMs, TimeUnit.MILLISECONDS);
                } catch (Exception ignore) {
                    // 未到点/被中断，继续循环检查挂机/绝对超时
                }
            }
            return def;
        } finally {
            pending.remove(pid);
            bump();
        }
    }

    boolean submit(int pid, Object value) {
        lock.lock();
        Pending p = pending.get(pid);
        if (p == null) { lock.unlock(); return false; }
        pending.remove(pid);
        lock.unlock();
        p.future.complete(value);
        bump();
        return true;
    }

    String exitPlayer(String token) {
        lock.lock();
        try {
            if (state.equals("lobby")) {
                if (!humans.containsKey(token)) return "你还没有加入房间。";
                humans.remove(token);
                joinOrder.remove(token);
                // 同步清理 clientId 映射，避免该 clientId 之后误复用已退出的座位
                String dead = token;
                clientIdToToken.entrySet().removeIf(e -> e.getValue().equals(dead));
                if (token.equals(hostToken)) hostToken = joinOrder.isEmpty() ? null : joinOrder.get(0);
                bump();
                return null;
            }
            Integer pid = tokenToPid.get(token);
            if (pid == null) return "无效身份";
            if (left.contains(pid)) { bump(); return null; }
            left.add(pid);
            String name = game.get(pid).name;
            Pending entry = pending.get(pid);
            if (entry != null) {
                pending.remove(pid);
                entry.future.complete(entry.spec.get("default"));
            }
            pub("🚪 " + name + " 退出了游戏，剩余行动由 AI 接管。");
            bump();
            wolfCond.signalAll();
        } finally { lock.unlock(); }
        return null;
    }

    // 房主踢人：房主可踢出任何已加入的玩家（大厅直接移除；游戏中则视为退出，由 AI 接管其行动）
    String kickPlayer(String hostToken, String targetToken) {
        lock.lock();
        try {
            if (!hostToken.equals(this.hostToken)) return "只有房主可以踢人。";
            if (targetToken == null || targetToken.isEmpty()) return "请指定要踢出的玩家。";
            if (targetToken.equals(this.hostToken)) return "不能踢出房主自己。";
            return exitPlayer(targetToken);
        } finally { lock.unlock(); }
    }

    void wolfChatFromHuman(String token, String text, boolean done) {
        if (game == null) return;
        Integer pid = tokenToPid.get(token);
        Game.Player p = (pid != null) ? game.get(pid) : null;
        if (p == null || !p.isWolf) return;
        if (!wolfDiscussionOpen) return;
        lock.lock();
        try {
            if (text != null && !text.isEmpty()) appendWolfChat(p.name, text, false);
            if (done) wolfDiscussionDone = true;
            wolfCond.signalAll();
        } finally { lock.unlock(); }
    }

    // ---------- 快照（按 token 过滤可见信息） ----------
    @SuppressWarnings("unchecked")
    Map<String, Object> snapshot(String token) {
        Map<String, Object> base = new LinkedHashMap<>();
        lock.lock();
        try {
            base.put("v", version);
            base.put("state", state);
            base.put("phase", phaseText);
            base.put("is_night", night);   // 前端据此在夜晚关闭玩家亮框
            // 本局身份构成（仅展示有哪些身份及数量，不暴露谁是什么身份）
            List<String> comp = new ArrayList<>();
            Map<String, Integer> rc = new LinkedHashMap<>();
            if (game != null) {
                for (Game.Player p : game.players.values()) rc.put(p.role, rc.getOrDefault(p.role, 0) + 1);
            } else {
                rc = makeRoles(total);
            }
            String[] rcOrder = {"狼人", "预言家", "女巫", "猎人", "守卫", "平民"};
            for (String r : rcOrder) {
                int c = rc.getOrDefault(r, 0);
                if (c > 0) comp.add(c > 1 ? (r + "×" + c) : r);
            }
            base.put("role_composition", comp);
            // AI 思考中：游戏进行中且当前没有真人在被点名行动（说明正在跑 AI 决策）。
            // 用于前端展示「🤖 AI 思考中…」，避免真人把较慢的 AI 回合误判为卡死。
            boolean aiThinking = state.equals("playing") && pending.isEmpty();
            base.put("ai_thinking", aiThinking);
            if (state.equals("lobby")) {
                Map<String, Object> L = new LinkedHashMap<>();
                List<String> names = new ArrayList<>();
                for (String t : joinOrder) names.add(humans.get(t));
                L.put("players", names);
                L.put("you_name", humans.getOrDefault(token, ""));
                L.put("is_host", token.equals(hostToken));
                L.put("total", total);
                L.put("n_humans", humans.size());
                L.put("n_ai", Math.max(0, total - humans.size()));
                // 房主可见成员 token，用于踢人
                if (token.equals(hostToken)) {
                    List<Map<String, Object>> members = new ArrayList<>();
                    for (String t : joinOrder) {
                        Map<String, Object> m = new LinkedHashMap<>();
                        m.put("name", humans.get(t));
                        m.put("token", t);
                        members.add(m);
                    }
                    L.put("members", members);
                }
                base.put("lobby", L);
                base.put("token_valid", humans.containsKey(token));
                return base;
            }
            int pid = tokenToPid.getOrDefault(token, -1);
            Game g = game;
            // 刷新心跳：该玩家仍在轮询，视为在线
            if (pid >= 0) lastSeen.put(pid, System.currentTimeMillis());
            base.put("is_host", token.equals(hostToken));
            List<Map<String, Object>> players = new ArrayList<>();
            for (int idx : g.order) {
                Game.Player p = g.get(idx);
                Map<String, Object> info = new LinkedHashMap<>();
                info.put("pid", idx);
                info.put("name", p.name);
                info.put("alive", p.alive);
                info.put("is_human", humanPids.contains(idx));
                info.put("is_you", idx == pid);
                if (state.equals("ended")) info.put("role", p.role);
                info.put("token", pidToToken.getOrDefault(idx, ""));  // 房主踢人用
                players.add(info);
            }
            base.put("players", players);
            base.put("public_log", publicLog);
            base.put("speaking", speaking);
            base.put("winner", winner);
            base.put("sheriff", g.sheriff);
            base.put("token_valid", pid >= 0);

            if (pid >= 0) {
                Game.Player p = g.get(pid);
                Map<String, Object> you = new LinkedHashMap<>();
                you.put("pid", pid);
                you.put("name", p.name);
                you.put("role", p.role);
                you.put("is_wolf", p.isWolf);
                you.put("alive", p.alive);
                if (p.isWolf) {
                    List<Map<String, Object>> mates = new ArrayList<>();
                    for (int m : p.wolfTeammates) {
                        Map<String, Object> mm = new LinkedHashMap<>();
                        mm.put("pid", m); mm.put("name", g.get(m).name);
                        mates.add(mm);
                    }
                    you.put("teammates", mates);
                }
                base.put("you", you);
                base.put("private_log", privateLog.getOrDefault(pid, new ArrayList<>()));
                List<String> vis = new ArrayList<>();
                for (SecretEvent se : secretEvents)
                    if (se.pids != null && se.pids.contains(pid)) vis.add(se.text);
                base.put("secret_log", vis);
                if (p.isWolf) {
                    base.put("wolf_chat", wolfChat);
                    base.put("wolf_discussion_open", wolfDiscussionOpen);
                } else {
                    base.put("wolf_chat", new ArrayList<>());
                    base.put("wolf_discussion_open", false);
                }
                Pending entry = pending.get(pid);
                base.put("pending", entry != null ? entry.spec : null);
            } else {
                base.put("you", null);
                base.put("pending", null);
            }
            return base;
        } finally { lock.unlock(); }
    }

    // ---------- 开局 ----------
    String start(String token) {
        lock.lock();
        try {
            if (token == null || !token.equals(hostToken)) return "只有房主可以开始游戏。";
            if (!state.equals("lobby")) return "游戏已经开始。";
            if (humans.isEmpty()) return "至少需要 1 名玩家。";
            total = Math.max(total, humans.size());
            Config.HUMAN_PLAYER = false;
            Map<String, Integer> roles = makeRoles(total);
            Config.ROLES_CONFIG = new LinkedHashMap<>();
            String[] orderRoles = {"狼人", "预言家", "女巫", "猎人", "守卫", "平民"};
            for (String r : orderRoles) if (roles.getOrDefault(r, 0) > 0) Config.ROLES_CONFIG.put(r, roles.get(r));
            Game g = new Game();
            List<Integer> allIdx = new ArrayList<>(g.players.keySet());
            Collections.shuffle(allIdx, RND);
            List<Integer> humanIdx = new ArrayList<>(allIdx.subList(0, humans.size()));
            Collections.sort(humanIdx);
            humanPids = new HashSet<>(humanIdx);
            List<String> tokens = new ArrayList<>(joinOrder);
            for (int i = 0; i < tokens.size(); i++) {
                int idx = humanIdx.get(i);
                pidToToken.put(idx, tokens.get(i));
                tokenToPid.put(tokens.get(i), idx);
                g.players.get(idx).name = humans.get(tokens.get(i));
                g.players.get(idx).personality = "";
                g.players.get(idx).isHuman = true;
            }
            // 重置日志（支持一进程多局）
            publicLog = new ArrayList<>();
            privateLog = new LinkedHashMap<>();
            secretEvents = new ArrayList<>();
            wolfChat = new ArrayList<>();
            wolfDiscussionOpen = false;
            wolfDiscussionDone = false;
            pending = new LinkedHashMap<>();
            left = new HashSet<>();
            lastSeen.clear();
            speaking = null;
            phaseText = "第 1 天";
            game = g;
            state = "playing";
            winner = null;
            night = false;
            bump();
        } finally { lock.unlock(); }
        gameThread = new Thread(() -> runGame());
        gameThread.setDaemon(true);
        gameThread.start();
        return null;
    }

    void end(String winner) {
        lock.lock();
        try {
            this.winner = winner;
            state = "ended";
            speaking = null;
            phaseText = "游戏结束";
            bump();
        } finally { lock.unlock(); }
    }

    String reset(String token) {
        lock.lock();
        try {
            if (!state.equals("ended")) return "当前不在游戏结束状态，无法重置。";
            if (!token.equals(hostToken) && !humans.containsKey(token)) return "你不是本局玩家，无法重置房间。";
            humans = new LinkedHashMap<>();
            joinOrder = new ArrayList<>();
            hostToken = null;
            clientIdToToken = new LinkedHashMap<>();
            game = null;
            humanPids = new HashSet<>();
            pidToToken = new LinkedHashMap<>();
            tokenToPid = new LinkedHashMap<>();
            publicLog = new ArrayList<>();
            privateLog = new LinkedHashMap<>();
            secretEvents = new ArrayList<>();
            pending = new LinkedHashMap<>();
            lastSeen.clear();
            speaking = null;
            phaseText = "等待开始";
            winner = null;
            wolfChat = new ArrayList<>();
            wolfDiscussionOpen = false;
            wolfDiscussionDone = false;
            left = new HashSet<>();
            night = false;
            state = "lobby";
            bump();
        } finally { lock.unlock(); }
        return null;
    }

    // 标准版型：狼人约占总人数 1/4，预言家/女巫必选，猎人≥6人、守卫≥7人，其余为平民
    static Map<String, Integer> makeRoles(int total) {
        int[][] boards = {
            // total, 狼人, 预言家, 女巫, 猎人, 守卫, 平民
            {4,  1, 1, 1, 0, 0, 1},
            {5,  1, 1, 1, 0, 0, 2},
            {6,  2, 1, 1, 1, 0, 1},
            {7,  2, 1, 1, 1, 1, 1},
            {8,  2, 1, 1, 1, 1, 2},
            {9,  3, 1, 1, 1, 1, 2},
            {10, 3, 1, 1, 1, 1, 3},
            {11, 3, 1, 1, 1, 1, 4},
            {12, 4, 1, 1, 1, 1, 4},
        };
        for (int[] b : boards) {
            if (total <= b[0]) {
                LinkedHashMap<String, Integer> roles = new LinkedHashMap<>();
                String[] names = {"狼人", "预言家", "女巫", "猎人", "守卫", "平民"};
                for (int i = 1; i < b.length; i++) if (b[i] > 0) roles.put(names[i - 1], b[i]);
                return roles;
            }
        }
        // 超出 12 人的兜底：按比例放大
        LinkedHashMap<String, Integer> roles = new LinkedHashMap<>();
        int w = Math.max(1, (total + 2) / 4);
        roles.put("狼人", w);
        roles.put("预言家", 1); roles.put("女巫", 1); roles.put("猎人", 1); roles.put("守卫", 1);
        int villagers = total - w - 4;
        if (villagers > 0) roles.put("平民", villagers);
        return roles;
    }

    // ============================ 行动封装（真人走 ask，AI 走 ai） ============================
    List<Map<String, Object>> aliveOptions(Collection<Integer> idxs) {
        List<Map<String, Object>> out = new ArrayList<>();
        for (int idx : idxs) {
            Game.Player p = game.get(idx);
            if (p == null) continue;
            Map<String, Object> o = new LinkedHashMap<>();
            o.put("value", idx);
            o.put("label", idx + "号 " + p.name);
            out.add(o);
        }
        return out;
    }

    AI.KillDecision doGuard(Game.Player guard, List<Integer> valid) {
        if (humanPids.contains(guard.idx)) {
            Map<String, Object> spec = new LinkedHashMap<>();
            spec.put("kind", "guard"); spec.put("input", "choice");
            spec.put("prompt", "🛡️ 你是守卫，选择今晚守护谁（不能守自己/连守同一人）");
            spec.put("options", aliveOptions(valid));
            int def = valid.isEmpty() ? 0 : valid.get(0);
            spec.put("default", def);
            Object v = ask(guard.idx, spec);
            return new AI.KillDecision((v instanceof Integer) ? (Integer) v : def, "");
        }
        Integer t = AI.aiGuardProtect(game, guard, valid);
        return new AI.KillDecision(t == null ? -1 : t, "");
    }

    Integer doSeer(Game.Player seer) {
        List<Integer> valid = new ArrayList<>();
        for (Game.Player p : game.alivePlayers())
            if (p.idx != seer.idx && !seer.checked.containsKey(p.idx)) valid.add(p.idx);
        if (valid.isEmpty()) return null;
        if (humanPids.contains(seer.idx)) {
            Map<String, Object> spec = new LinkedHashMap<>();
            spec.put("kind", "seer"); spec.put("input", "choice");
            spec.put("prompt", "🔮 你是预言家，选择今晚查验谁");
            spec.put("options", aliveOptions(valid));
            int def = valid.get(RND.nextInt(valid.size()));
            spec.put("default", def);
            Object v = ask(seer.idx, spec);
            return (v instanceof Integer) ? (Integer) v : def;
        }
        return AI.aiSeerCheck(game, seer);
    }

    AI.WitchAction doWitch(Game.Player witch, Integer killedIdx) {
        if (humanPids.contains(witch.idx)) {
            String killedName = (killedIdx != null && game.get(killedIdx) != null) ? game.get(killedIdx).name : "无人";
            List<Integer> excl = new ArrayList<>();
            if (killedIdx != null) excl.add(killedIdx);
            Map<String, Object> spec = new LinkedHashMap<>();
            spec.put("kind", "witch"); spec.put("input", "witch");
            spec.put("prompt", "🧪 你是女巫，请决定用药");
            spec.put("killed_idx", killedIdx == null ? 0 : killedIdx);
            spec.put("killed_name", killedName);
            spec.put("has_antidote", witch.hasAntidote);
            spec.put("has_poison", witch.hasPoison);
            List<Integer> poisonExcl = new ArrayList<>();
            if (killedIdx != null) poisonExcl.add(killedIdx);
            List<Integer> poisonOpts = new ArrayList<>();
            for (int idx : game.players.keySet()) if (!poisonExcl.contains(idx)) poisonOpts.add(idx);
            spec.put("poison_options", aliveOptions(poisonOpts));
            Map<String, Object> def = new LinkedHashMap<>();
            def.put("save", false); def.put("poison", 0);
            spec.put("default", def);
            Object v = ask(witch.idx, spec);
            @SuppressWarnings("unchecked")
            Map<String, Object> ans = (v instanceof Map) ? (Map<String, Object>) v : def;
            boolean save = Boolean.TRUE.equals(ans.get("save")) && witch.hasAntidote;
            Object po = ans.get("poison");
            Integer poison = (po instanceof Number) ? ((Number) po).intValue() : 0;
            poison = (poison != null && poison != 0 && witch.hasPoison) ? poison : null;
            return new AI.WitchAction(save, poison);
        }
        return AI.aiWitchAction(game, witch, killedIdx);
    }

    AI.KillDecision doWolf(Game.Player wolf, String discussion) {
        List<Integer> cands = new ArrayList<>();
        for (Game.Player p : game.alivePlayers()) cands.add(p.idx);
        if (humanPids.contains(wolf.idx)) {
            String mates = "";
            for (int m : wolf.wolfTeammates) mates += m + "号" + game.get(m).name + "、";
            if (mates.endsWith("、")) mates = mates.substring(0, mates.length() - 1);
            if (mates.isEmpty()) mates = "无";
            Map<String, Object> spec = new LinkedHashMap<>();
            spec.put("kind", "kill"); spec.put("input", "choice");
            spec.put("prompt", "🐺 狼队投票：你是狼人（队友：" + mates + "）。请投出你要击杀的目标（可选队友或自己；狼队多数决定，平票随机）");
            spec.put("options", aliveOptions(cands));
            spec.put("discussion", discussion);
            int def = cands.get(RND.nextInt(cands.size()));
            spec.put("default", def);
            Object v = ask(wolf.idx, spec);
            int t = (v instanceof Integer) ? (Integer) v : def;
            return new AI.KillDecision(t, "（真人狼人的选择）");
        }
        return AI.aiWolfPropose(game, wolf, discussion);
    }

    Integer doVote(Game.Player player, Set<Integer> restrict) {
        List<Integer> cands = new ArrayList<>();
        for (Game.Player p : game.alivePlayers()) if (p.idx != player.idx) cands.add(p.idx);
        if (restrict != null) {
            List<Integer> f = new ArrayList<>();
            for (int c : cands) if (restrict.contains(c)) f.add(c);
            cands = f;
        }
        if (humanPids.contains(player.idx)) {
            Map<String, Object> spec = new LinkedHashMap<>();
            spec.put("kind", "vote"); spec.put("input", "choice");
            spec.put("prompt", "🗳️ 投票放逐你怀疑的狼人（也可以弃票）");
            spec.put("options", aliveOptions(cands));
            spec.put("allow_abstain", true);
            spec.put("default", 0);
            Object v = ask(player.idx, spec);
            return (v instanceof Integer) ? (Integer) v : 0;
        }
        return AI.aiVote(game, player, restrict);
    }

    String doLastwill(Game.Player player) {
        if (humanPids.contains(player.idx)) {
            Map<String, Object> spec = new LinkedHashMap<>();
            spec.put("kind", "lastwill"); spec.put("input", "text");
            spec.put("prompt", "💬 " + player.idx + "号 " + player.name + "，请留下你的遗言（可留空）");
            spec.put("default", "");
            Object v = ask(player.idx, spec);
            String s = (v instanceof String) ? (String) v : "";
            return s.trim();
        }
        return AI.aiLastwill(game, player);
    }

    AI.SheriffRun doRunSheriff(Game.Player player) {
        if (humanPids.contains(player.idx)) {
            Map<String, Object> spec = new LinkedHashMap<>();
            spec.put("kind", "sheriff_run"); spec.put("input", "sheriff_run");
            spec.put("prompt", "🔔 " + player.idx + "号 " + player.name + "，是否上警竞选警长？");
            Map<String, Object> def = new LinkedHashMap<>();
            def.put("run", false); def.put("speech", "");
            spec.put("default", def);
            Object v = ask(player.idx, spec);
            @SuppressWarnings("unchecked")
            Map<String, Object> ans = (v instanceof Map) ? (Map<String, Object>) v : def;
            boolean run = Boolean.TRUE.equals(ans.get("run"));
            Object sp = ans.get("speech");
            return new AI.SheriffRun(run, (sp instanceof String) ? (String) sp : "");
        }
        return AI.aiRunForSheriff(game, player);
    }

    Integer doVoteSheriff(Game.Player player, List<Integer> candidates) {
        List<Integer> cands = new ArrayList<>();
        for (int c : candidates) { Game.Player p = game.get(c); if (p != null && p.alive) cands.add(c); }
        if (humanPids.contains(player.idx)) {
            Map<String, Object> spec = new LinkedHashMap<>();
            spec.put("kind", "vote"); spec.put("input", "choice");
            spec.put("prompt", "🗳️ 选举警长：投给你支持的候选人（也可弃票）");
            spec.put("options", aliveOptions(cands));
            spec.put("allow_abstain", true);
            spec.put("default", 0);
            Object v = ask(player.idx, spec);
            return (v instanceof Integer) ? (Integer) v : 0;
        }
        return AI.aiVoteSheriff(game, player, candidates);
    }

    String doSpeech(Game.Player player) {
        if (humanPids.contains(player.idx)) {
            Map<String, Object> spec = new LinkedHashMap<>();
            spec.put("kind", "speech"); spec.put("input", "text");
            spec.put("prompt", "💬 轮到你（" + player.idx + "号 " + player.name + "）发言");
            spec.put("default", "（沉默）");
            Object v = ask(player.idx, spec);
            String s = (v instanceof String) ? (String) v : "";
            return s.trim().isEmpty() ? "（沉默）" : s.trim();
        }
        return AI.aiSpeak(game, player);
    }

    Integer doHunter(Game.Player hunter) {
        List<Integer> valid = new ArrayList<>();
        for (Game.Player p : game.alivePlayers()) if (p.idx != hunter.idx) valid.add(p.idx);
        if (valid.isEmpty()) return null;
        if (humanPids.contains(hunter.idx)) {
            Map<String, Object> spec = new LinkedHashMap<>();
            spec.put("kind", "hunter"); spec.put("input", "choice");
            spec.put("prompt", "💥 你是猎人，出局了！开枪带走一名玩家");
            spec.put("options", aliveOptions(valid));
            int def = valid.get(RND.nextInt(valid.size()));
            spec.put("default", def);
            Object v = ask(hunter.idx, spec);
            return (v instanceof Integer) ? (Integer) v : def;
        }
        return AI.aiHunterShoot(game, hunter, valid);
    }

    // ============================ 游戏主流程（后台线程） ============================
    static String fmtW(double w) { return (w == (long) w) ? String.valueOf((long) w) : String.valueOf(w); }

    String wolfChatText() {
        StringBuilder sb = new StringBuilder();
        for (Map<String, Object> m : wolfChat) {
            if (sb.length() > 0) sb.append("\n");
            sb.append(m.get("name")).append("：").append(m.get("text"));
        }
        return sb.toString();
    }

    void applyNightDeaths(Integer killedIdx, Integer guardTarget, boolean saved, Integer poisonIdx) {
        List<Object[]> deaths = new ArrayList<>();
        if (killedIdx != null && !Objects.equals(guardTarget, killedIdx) && !saved) deaths.add(new Object[]{killedIdx, "wolf"});
        if (poisonIdx != null) deaths.add(new Object[]{poisonIdx, "poison"});
        List<Game.Player> pendingH = new ArrayList<>();
        List<String> deadNames = new ArrayList<>();
        List<Object[]> newlyDead = new ArrayList<>();
        for (Object[] d : deaths) {
            int idx = (Integer) d[0]; String cause = (String) d[1];
            Game.Player p = game.get(idx);
            if (!p.alive) continue;
            p.alive = false;
            deadNames.add(idx + "号" + p.name);
            newlyDead.add(new Object[]{p, cause});
            if (p.role.equals(Game.HUNTER) && !cause.equals("poison")) pendingH.add(p);
        }
        if (!deadNames.isEmpty()) pub("🌅 第 " + game.day + " 天清晨：昨晚 [" + String.join("、", deadNames) + "] 死了。");
        else pub("🌅 第 " + game.day + " 天清晨：昨夜风平浪静，无人死亡。");

        for (Object[] nd : newlyDead) {
            Game.Player p = (Game.Player) nd[0]; String cause = (String) nd[1];
            if (p.role.equals(Game.HUNTER)) continue;
            if (!(game.day == 1 && "wolf".equals(cause))) continue;
            String text = doLastwill(p);
            pub("💬 " + p.idx + "号" + p.name + " 的遗言：" + text);
        }
        while (!pendingH.isEmpty()) {
            Game.Player h = pendingH.remove(0);
            setPhase("猎人开枪", h.idx);
            Integer t = doHunter(h);
            if (t != null) {
                Game.Player tp = game.get(t);
                if (tp != null && tp.alive) {
                    tp.alive = false;
                    pub("💥 猎人 " + h.idx + "号" + h.name + " 开枪带走了 " + t + "号" + tp.name + "！");
                    if (tp.role.equals(Game.HUNTER)) pendingH.add(tp);
                }
            }
        }
    }

    void runWolfDiscussion(List<Game.Player> humanWolves, List<Game.Player> aiWolves) {
        List<Game.Player> hw = new ArrayList<>();
        for (Game.Player w : humanWolves) if (!left.contains(w.idx)) hw.add(w);
        if (hw.isEmpty()) return;
        lock.lock();
        wolfChat = new ArrayList<>();
        wolfDiscussionOpen = true;
        wolfDiscussionDone = false;
        lock.unlock();
        for (Game.Player w : aiWolves) {
            AI.KillDecision kd = AI.aiWolfPropose(game, w, "");
            if (kd.target >= 0) {
                Game.Player tgt = game.get(kd.target);
                appendWolfChat(w.idx + "号" + w.name, "提议杀 " + kd.target + "号" + tgt.name + "：" + kd.reason, true);
            }
        }
        int rounds = 0;
        while (rounds < 12) {
            lock.lock();
            if (wolfDiscussionDone) { lock.unlock(); break; }
            boolean allLeft = true;
            for (Game.Player w : hw) if (!left.contains(w.idx)) { allLeft = false; break; }
            if (allLeft) { wolfDiscussionDone = true; lock.unlock(); break; }
            // 挂机检测：所有真人狼都长时间未轮询（关页但退出未生效），提前结束讨论，避免卡死
            boolean allIdle = true;
            long nowMs = System.currentTimeMillis();
            for (Game.Player w : hw) {
                if (left.contains(w.idx)) continue;
                Long ls = lastSeen.get(w.idx);
                if (ls == null || (nowMs - ls) <= IDLE_PASS * 1000L) { allIdle = false; break; }
            }
            if (allIdle) { wolfDiscussionDone = true; lock.unlock(); break; }
            boolean signaled;
            try { signaled = wolfCond.await(30, TimeUnit.SECONDS); }
            catch (InterruptedException e) { signaled = false; }
            lock.unlock();
            if (!signaled) break;
            String disc = wolfChatText();
            for (Game.Player w : aiWolves) {
                String resp = AI.aiWolfChat(game, w, disc);
                if (resp != null && !resp.isEmpty()) appendWolfChat(w.idx + "号" + w.name, resp, true);
            }
            rounds++;
        }
        lock.lock();
        wolfDiscussionOpen = false;
        lock.unlock();
    }

    /** 并发执行 fn(item)->result，返回与 items 同序的结果列表。
     *  用于让多名 AI 玩家的决策（发言/投票/狼人提议）并行完成，
     *  大幅缩短「全员 AI」局（如所有狼都是 AI）的墙钟时间，避免体感卡死。
     *  每个 fn 仅读取 game 状态、互不修改共享可变状态（game._acting_idx 为死字段），
     *  故可安全并发；真人行动阻塞在各自线程的 ask() 上，不影响其他 AI 线程推进。 */
    private <T, R> List<R> parMap(List<T> items, Function<T, R> fn) {
        if (items == null || items.isEmpty()) return new ArrayList<>();
        int n = Math.min(items.size(), 8);
        ExecutorService ex = Executors.newFixedThreadPool(n);
        List<Future<R>> futures = new ArrayList<>();
        try {
            for (T it : items) futures.add(ex.submit(() -> fn.apply(it)));
        } finally {
            ex.shutdown();
        }
        List<R> out = new ArrayList<>();
        for (Future<R> f : futures) {
            try { out.add(f.get()); } catch (Exception e) { out.add(null); }
        }
        return out;
    }

    void nightPhase() {
        Game g = game;
        night = true;
        pub("🌙 ===== 第 " + g.day + " 天 · 黑夜降临 =====");
        setPhase("第 " + g.day + " 天 · 黑夜");
        pub("🌙 天黑请闭眼。");
        List<Game.Player> wolves = g.aliveWolves();
        Game.Player seer = null, witch = null, guard = null;
        // 在全部玩家中查找神职（即使已死亡也要保留引用），以便夜晚仍播报「XX请睁眼」，
        // 防止玩家由播报的缺失推断出死者身份。
        for (Game.Player p : g.players.values()) {
            if (p.role.equals(Game.SEER)) seer = p;
            else if (p.role.equals(Game.WITCH)) witch = p;
            else if (p.role.equals(Game.GUARD)) guard = p;
        }
        // 守卫（即使已死亡也照常播报，避免泄露身份）
        Integer guardTarget = null;
        if (guard != null) {
            pub("🛡️ 守卫请睁眼……");
            if (guard.alive) {
                setPhase("守卫行动中", humanPids.contains(guard.idx) ? guard.idx : null);
                List<Integer> valid = new ArrayList<>();
                for (Game.Player p : g.alivePlayers())
                    if (p.idx != guard.idx && !Objects.equals(p.idx, guard.lastProtected)) valid.add(p.idx);
                if (valid.isEmpty()) for (Game.Player p : g.alivePlayers()) if (p.idx != guard.idx) valid.add(p.idx);
                AI.KillDecision gd = doGuard(guard, valid);
                guardTarget = (gd.target >= 0) ? gd.target : null;
                if (guardTarget != null) {
                    guard.lastProtected = guardTarget;
                    sec("[守卫] 守护了 " + guardTarget + "号" + g.get(guardTarget).name, humanPids.contains(guard.idx) ? setOf(guard.idx) : null);
                }
            } else {
                // 已死亡：仍播报，并插入约 10 秒停顿，使存活玩家无法从时序看出守卫已死
                try { Thread.sleep(10000); } catch (InterruptedException e) {}
            }
            pub("🛡️ 守卫请闭眼。");
        }
        // 狼人
        Integer killedIdx = null;
        if (!wolves.isEmpty()) {
            pub("🐺 狼人请睁眼……");
            List<Game.Player> humanWolves = new ArrayList<>(), aiWolves = new ArrayList<>();
            for (Game.Player w : wolves) { if (humanPids.contains(w.idx)) humanWolves.add(w); else aiWolves.add(w); }
            if (!humanWolves.isEmpty()) runWolfDiscussion(humanWolves, aiWolves);
            String discussionText = wolfChatText();
            setPhase("狼人行动中", null);
            // 并行让各狼给出最终击杀决定，缩短全员 AI（狼）局的等待
            List<Game.Player> wolfList = new ArrayList<>(wolves);
            List<AI.KillDecision> kds = parMap(wolfList, w -> doWolf(w, discussionText));
            Map<Integer, Integer> proposals = new LinkedHashMap<>();
            for (int i = 0; i < wolfList.size(); i++) {
                AI.KillDecision kd = (kds.get(i) != null) ? kds.get(i) : new AI.KillDecision(-1, "");
                proposals.put(wolfList.get(i).idx, kd.target);
            }
            Game.WolfKill wk = g.resolveWolfKill(proposals);
            killedIdx = wk.target;
            Set<Integer> wolfHumanPids = new HashSet<>();
            for (Game.Player w : wolves) if (humanPids.contains(w.idx)) wolfHumanPids.add(w.idx);
            for (Map<String, Object> m : wolfChat) {
                String tag = Boolean.TRUE.equals(m.get("ai")) ? "🤖" : "👤";
                sec("[狼队讨论] " + tag + m.get("name") + "：" + m.get("text"), wolfHumanPids.isEmpty() ? null : wolfHumanPids);
            }
            for (Game.Player w : wolves) {
                Integer t = proposals.get(w.idx);
                Game.Player tgt = (t != null) ? g.get(t) : null;
                sec("[狼队投票] " + w.idx + "号" + w.name + " → 杀 " + t + "号" + (tgt != null ? tgt.name : "（无效）"), wolfHumanPids.isEmpty() ? null : wolfHumanPids);
            }
            if (wk.cnt != null && !wk.cnt.isEmpty()) {
                int maxc = 0; for (int c : wk.cnt.values()) if (c > maxc) maxc = c;
                List<Integer> tied = new ArrayList<>();
                for (Map.Entry<Integer, Integer> e : wk.cnt.entrySet()) if (e.getValue() == maxc) tied.add(e.getKey());
                if (tied.size() > 1)
                    sec("[狼队投票] 平票（各 " + maxc + " 票），随机选中击杀目标 → " + killedIdx + "号" + g.get(killedIdx).name, wolfHumanPids.isEmpty() ? null : wolfHumanPids);
                else
                    sec("[狼队] 今夜击杀 " + killedIdx + "号" + g.get(killedIdx).name, wolfHumanPids.isEmpty() ? null : wolfHumanPids);
            }
            wolfChat = new ArrayList<>();
            pub("🐺 狼人请闭眼。");
        }
        // 女巫（在预言家之前行动）
        boolean saved = false; Integer poisonIdx = null;
        Integer witchSees = (killedIdx != null && !Objects.equals(guardTarget, killedIdx)) ? killedIdx : null;
        if (witch != null) {
            pub("🧪 女巫请睁眼……");
            if (witch.alive && (witch.hasAntidote || witch.hasPoison)) {
                setPhase("女巫行动中", humanPids.contains(witch.idx) ? witch.idx : null);
                AI.WitchAction wa = doWitch(witch, witchSees);
                saved = wa.save; poisonIdx = wa.poison;
                if (saved && witch.hasAntidote) {
                    witch.hasAntidote = false;
                    sec("[女巫] 使用了解药", humanPids.contains(witch.idx) ? setOf(witch.idx) : null);
                    if (humanPids.contains(witch.idx)) notify(witch.idx, "🧪 你使用了解药。");
                }
                if (poisonIdx != null && witch.hasPoison) {
                    witch.hasPoison = false;
                    sec("[女巫] 使用了毒药 → " + poisonIdx + "号" + g.get(poisonIdx).name, humanPids.contains(witch.idx) ? setOf(witch.idx) : null);
                    if (humanPids.contains(witch.idx)) notify(witch.idx, "🧪 你使用了毒药，目标 " + poisonIdx + "号" + g.get(poisonIdx).name + "。");
                }
            } else {
                // 已死亡 / 无药可用：仍播报，并插入约 10 秒停顿，避免存活玩家从时序看出女巫已死
                try { Thread.sleep(10000); } catch (InterruptedException e) {}
            }
            pub("🧪 女巫请闭眼。");
        }
        // 预言家（即使已死亡也照常播报，避免泄露身份）
        if (seer != null) {
            pub("🔮 预言家请睁眼……");
            if (seer.alive) {
                setPhase("预言家行动中", humanPids.contains(seer.idx) ? seer.idx : null);
                Integer tid = doSeer(seer);
                if (tid != null) {
                    String result = g.get(tid).isWolf ? "狼人" : "好人";
                    seer.checked.put(tid, result);
                    if (humanPids.contains(seer.idx)) notify(seer.idx, "🔮 你查验了 " + tid + "号" + g.get(tid).name + "，结果：【" + result + "】");
                }
            } else {
                // 已死亡：仍播报，并插入约 10 秒停顿，避免泄露身份
                try { Thread.sleep(10000); } catch (InterruptedException e) {}
            }
            pub("🔮 预言家请闭眼。");
        }
        pub("☀️ 天亮了。");
        if (killedIdx == null && poisonIdx == null) pub("🌅 第 " + g.day + " 天清晨：昨夜风平浪静，无人死亡。");
        else applyNightDeaths(killedIdx, guardTarget, saved, poisonIdx);
        night = false;
    }

    void serverSheriffPhase() {
        Game g = game;
        pub("🔔 第 1 天 · 上警竞选警长环节");
        setPhase("上警竞选");
        List<Integer> candidates = new ArrayList<>();
        Map<Integer, AI.SheriffRun> runs = new LinkedHashMap<>();
        // 阶段一：所有玩家先一起决定是否上警（暂不公布，避免先后次序影响判断）
        for (int idx : g.order) {
            Game.Player p = g.get(idx);
            if (!p.alive) continue;
            setPhase("上警竞选", idx);
            AI.SheriffRun sr = doRunSheriff(p);
            runs.put(idx, sr);
            if (sr.run) candidates.add(idx);
        }
        // 阶段二：全部决定后，统一公布每个人是否上警
        pub("—— 上警结果 ——");
        for (Map.Entry<Integer, AI.SheriffRun> e : runs.entrySet()) {
            int idx = e.getKey();
            Game.Player p = g.get(idx);
            AI.SheriffRun sr = e.getValue();
            if (sr.run) {
                String speech = (sr.speech == null || sr.speech.isEmpty()) ? "（无发言）" : sr.speech;
                pub("  " + idx + "号" + p.name + " 上警：" + speech);
            } else {
                pub("  " + idx + "号" + p.name + " 不上警");
            }
        }
        if (candidates.isEmpty()) { pub("  无人上警，本轮无警长。"); return; }
        pub("—— 选举警长（上警者不参与投票）——");
        Map<Integer, Integer> votes = new LinkedHashMap<>();
        for (int idx : g.order) {
            Game.Player p = g.get(idx);
            if (!p.alive) continue;
            if (candidates.contains(idx)) {
                pub("  " + idx + "号" + p.name + "（上警者）不参与投票");
                continue;
            }
            setPhase("选举警长", idx);
            int t = doVoteSheriff(p, candidates);
            votes.put(idx, t);
            String disp = (t == 0) ? "弃票" : ("投给 " + t + "号");
            pub("  " + idx + "号" + p.name + " → " + disp);
        }
        Map<Integer, Integer> cnt = new LinkedHashMap<>();
        for (int v : votes.values()) if (v != 0) cnt.put(v, cnt.getOrDefault(v, 0) + 1);
        if (cnt.isEmpty()) { pub("  警长选举：全员弃票，本轮无警长。"); return; }
        int topVotes = 0; for (int c : cnt.values()) if (c > topVotes) topVotes = c;
        Integer top = null; List<Integer> tied = new ArrayList<>();
        for (Map.Entry<Integer, Integer> e : cnt.entrySet()) if (e.getValue() == topVotes) tied.add(e.getKey());
        if (tied.size() > 1) { pub("  警长选举平票（各 " + topVotes + " 票），本轮无警长。"); return; }
        top = tied.get(0);
        g.sheriff = top;
        pub("🚩 " + top + "号" + g.get(top).name + " 当选警长！（放逐投票计 1.5 票）");
    }

    void serverExileVictim(Game.Player victim, String nText) {
        victim.alive = false;
        if (nText != null && !nText.isEmpty())
            pub("🗳️ 投票结果：" + victim.idx + "号" + victim.name + " 被放逐出局（" + nText + " 票）。");
        else
            pub("🗳️ 投票结果：" + victim.idx + "号" + victim.name + " 被放逐出局。");
        if (!victim.role.equals(Game.HUNTER)) {
            String text = doLastwill(victim);
            pub("💬 " + victim.idx + "号" + victim.name + " 的遗言：" + text);
        }
        if (victim.role.equals(Game.HUNTER)) {
            setPhase("猎人开枪", victim.idx);
            Integer t = doHunter(victim);
            if (t != null) {
                Game.Player tp = game.get(t);
                if (tp != null && tp.alive) {
                    tp.alive = false;
                    pub("💥 猎人 " + victim.idx + "号" + victim.name + " 开枪带走了 " + t + "号" + tp.name + "！");
                }
            }
        }
    }

    void serverResolveExile(Map<Integer, Integer> votes) {
        Game g = game;
        Game.ExileTally t = g.exileTally(votes);
        int abstains = 0; for (int v : votes.values()) if (v == 0) abstains++;
        if (t.winner != null) { serverExileVictim(g.get(t.winner), fmtW(t.maxW)); return; }
        if (t.tied.isEmpty()) { pub("🗳️ 投票结果：全员弃票（" + abstains + "票弃票），流局，无人被放逐。"); return; }
        String names = "";
        for (int x : t.tied) names += x + "号" + g.get(x).name + "、";
        if (names.endsWith("、")) names = names.substring(0, names.length() - 1);
        pub("🗳️ 投票结果：" + names + " 平票（各 " + fmtW(t.maxW) + " 票），进行第二次投票。");
        Map<Integer, Integer> votes2 = new LinkedHashMap<>();
        Set<Integer> tiedSet = new HashSet<>(t.tied);
        for (int idx : g.order) {
            Game.Player p = g.get(idx);
            if (!p.alive) continue;
            setPhase("二次投票", idx);
            int v = doVote(p, tiedSet);
            votes2.put(idx, v);
            String disp = (v == 0) ? "弃票" : ("投给 " + v + "号");
            pub("  " + idx + "号" + p.name + " → " + disp);
        }
        Game.ExileTally t2 = g.exileTally(votes2);
        if (t2.winner != null) serverExileVictim(g.get(t2.winner), fmtW(t2.maxW));
        else pub("🗳️ 第二次投票仍平票，流局，无人被放逐。");
    }

    void dayPhase() {
        Game g = game;
        pub("☀️ ===== 第 " + g.day + " 天 · 白天 =====");
        if (g.day == 1 && g.sheriff == null) serverSheriffPhase();
        setPhase("第 " + g.day + " 天 · 白天发言");
        pub("—— 发言环节 ——");
        // 全体存活玩家并行发言（AI 并发、真人各自阻塞在自己的 ask 上），缩短全员 AI 局等待
        List<Game.Player> alive = new ArrayList<>();
        for (int idx : g.order) { Game.Player p = g.get(idx); if (p.alive) alive.add(p); }
        // 按编号依次发言：每人说完立即公布，而非全部发完再一起显示
        for (Game.Player p : alive) {
            setPhase("第 " + g.day + " 天 · 白天发言", p.idx);
            String text = doSpeech(p);
            pub("  " + p.idx + "号" + p.name + "：" + (text == null || text.isEmpty() ? "（沉默）" : text));
        }
        setPhase("第 " + g.day + " 天 · 投票");
        pub("—— 投票放逐环节 ——");
        Map<Integer, Integer> votes = new LinkedHashMap<>();
        List<Integer> voteVals = parMap(alive, p -> doVote(p, null));
        for (int i = 0; i < alive.size(); i++) {
            int v = (voteVals.get(i) != null) ? voteVals.get(i) : 0;
            votes.put(alive.get(i).idx, v);
            String disp = (v == 0) ? "弃票" : ("投给 " + v + "号");
            pub("  " + alive.get(i).idx + "号" + alive.get(i).name + " → " + disp);
        }
        serverResolveExile(votes);
    }

    void runGame() {
        Game g = game;
        String w;
        try {
            pub("🎮 游戏开始！祝各位好运。");
            while (true) {
                g.day++;
                nightPhase();
                w = g.checkWinner();
                if (w != null) break;
                dayPhase();
                w = g.checkWinner();
                if (w != null) break;
            }
        } catch (Exception e) {
            pub("[系统] 运行异常：" + e);
            w = game.checkWinner();
            if (w == null) w = "好人";
        }
        pub("==============================");
        pub(w.equals("狼人") ? "🐺 狼人阵营胜利！" : "🏆 好人阵营胜利！");
        pub("【最终身份揭示】");
        for (int idx : game.order) {
            Game.Player p = game.get(idx);
            String status = p.alive ? "存活" : "出局";
            String who = humanPids.contains(idx) ? "（真人）" : "";
            pub("  " + idx + "号" + p.name + who + "：" + p.role + "（" + status + "）");
        }
        end(w);
    }
}
