// 狼人杀游戏引擎：角色、玩家状态、公共/私密信息、胜负判定、状态文本格式化。
// 本文件只包含「纯逻辑与状态」，不发起任何 LLM 调用。
import java.util.*;

public class Game {
    // 角色常量
    public static final String WOLF = "狼人";
    public static final String SEER = "预言家";
    public static final String WITCH = "女巫";
    public static final String HUNTER = "猎人";
    public static final String GUARD = "守卫";
    public static final String VILLAGER = "平民";

    public static final Set<String> GOOD_ROLES = new HashSet<>(Arrays.asList(SEER, WITCH, HUNTER, GUARD, VILLAGER));

    public static final Random RND = new Random();

    public static class Player {
        public int idx;            // 1-based 玩家号
        public String name;
        public boolean isHuman = false;
        public String personality = "";
        public String role = null;
        public boolean isWolf = false;
        public boolean alive = true;
        public Map<Integer, String> checked = new LinkedHashMap<>(); // 被查玩家idx -> "狼人"/"好人"
        public boolean hasAntidote = true;
        public boolean hasPoison = true;
        public Integer lastProtected = null;                         // 守卫上一晚守护的玩家 idx
        public List<Integer> wolfTeammates = new ArrayList<>();

        public Player(int idx, String name) { this.idx = idx; this.name = name; }
        public Player(int idx, String name, boolean isHuman, String personality) {
            this.idx = idx; this.name = name; this.isHuman = isHuman; this.personality = personality;
        }
        public String repr() { return idx + "号[" + (isHuman ? "你" : name) + "]"; }
    }

    public Map<Integer, Player> players = new LinkedHashMap<>(); // idx -> Player
    public List<Integer> order = new ArrayList<>();              // 玩家 idx 顺序
    public int day = 0;                                          // 第几天（从 1 开始）
    public List<String> publicLog = new ArrayList<>();           // 所有人可见的公共事件
    public Integer sheriff = null;                               // 警长玩家 idx（放逐投票计 1.5 票）

    public Game() {
        _build_players();
        _assign_roles();
    }

    // ---------------- 初始化 ----------------
    private void _build_players() {
        List<String> roleList = new ArrayList<>();
        for (Map.Entry<String, Integer> e : Config.ROLES_CONFIG.entrySet())
            for (int i = 0; i < e.getValue(); i++) roleList.add(e.getKey());
        int total = roleList.size();

        int idx = 1, aiIdx = 1;
        for (int i = 0; i < total; i++) {
            String name, personality;
            if (Config.HUMAN_PLAYER) {
                name = "你";
                personality = "";
                Player p = new Player(idx, name, true, personality);
                players.put(idx, p); order.add(idx); idx++;
            } else {
                if (i < Config.AI_PROFILES.size()) {
                    name = Config.AI_PROFILES.get(i)[0];
                    personality = Config.AI_PROFILES.get(i)[1];
                } else {
                    name = "玩家" + aiIdx; personality = "沉默寡言";
                }
                Player p = new Player(idx, name, false, personality);
                players.put(idx, p); order.add(idx); aiIdx++; idx++;
            }
        }
    }

    private void _assign_roles() {
        List<String> roleList = new ArrayList<>();
        for (Map.Entry<String, Integer> e : Config.ROLES_CONFIG.entrySet())
            for (int i = 0; i < e.getValue(); i++) roleList.add(e.getKey());
        Collections.shuffle(roleList, RND);
        for (int k = 0; k < order.size(); k++) {
            int idx = order.get(k);
            Player p = players.get(idx);
            p.role = roleList.get(k);
            p.isWolf = p.role.equals(WOLF);
        }
        List<Integer> wolves = new ArrayList<>();
        for (Player p : players.values()) if (p.isWolf) wolves.add(p.idx);
        for (Player p : players.values()) {
            if (p.isWolf) {
                p.wolfTeammates = new ArrayList<>();
                for (int w : wolves) if (w != p.idx) p.wolfTeammates.add(w);
            }
        }
    }

    // ---------------- 查询 ----------------
    public Player get(Integer idx) { return players.get(idx); }
    public List<Player> alivePlayers() {
        List<Player> r = new ArrayList<>();
        for (Player p : players.values()) if (p.alive) r.add(p);
        return r;
    }
    public List<Player> aliveWolves() {
        List<Player> r = new ArrayList<>();
        for (Player p : players.values()) if (p.alive && p.isWolf) r.add(p);
        return r;
    }
    public List<Player> aliveGood() {
        List<Player> r = new ArrayList<>();
        for (Player p : players.values()) if (p.alive && !p.isWolf) r.add(p);
        return r;
    }

    public void record(String text) {
        publicLog.add(text);
        System.out.println(text);
    }

    // ---------------- 胜负 ----------------
    public String checkWinner() {
        int aw = aliveWolves().size();
        int ag = aliveGood().size();
        if (aw == 0) return "好人";
        if (aw >= ag) return "狼人";
        return null;
    }

    // ---------------- 状态文本 ----------------
    private String aliveLine() {
        List<String> parts = new ArrayList<>();
        for (int idx : order) {
            Player p = players.get(idx);
            String mark = p.isHuman ? (idx + "号你(玩家)") : (idx + "号" + p.name);
            if (!p.alive) mark += "【已出局】";
            parts.add(mark);
        }
        return String.join("、", parts);
    }

    public String publicStateText() {
        List<String> lines = new ArrayList<>();
        lines.add("【当前天数】第 " + day + " 天");
        lines.add("【存活玩家】" + aliveLine());
        if (sheriff != null) {
            Player sp = players.get(sheriff);
            String sname = (sp != null) ? sp.name : "?";
            lines.add("【警长】" + sheriff + "号" + sname + "（放逐投票计 1.5 票）");
        }
        lines.add("【公共事件记录】");
        if (!publicLog.isEmpty()) {
            for (String e : publicLog) lines.add("  - " + e);
        } else {
            lines.add("  （暂无）");
        }
        return String.join("\n", lines);
    }

    public double voteWeight(int voterPid) {
        if (sheriff != null && voterPid == sheriff.intValue()) return 1.5;
        return 1.0;
    }

    // 放逐计票（带警长 1.5 票权）。votes: {投票人pid -> 目标pid(0/弃票不计)}。
    // 返回 winner（唯一最高票者，平票为 null）、tied（同票目标列表）、maxW（最高票权）
    public static class ExileTally {
        public Integer winner; public List<Integer> tied = new ArrayList<>(); public double maxW = 0.0;
    }
    public ExileTally exileTally(Map<Integer, Integer> votes) {
        ExileTally t = new ExileTally();
        Map<Integer, Double> weight = new LinkedHashMap<>();
        for (Map.Entry<Integer, Integer> e : votes.entrySet()) {
            Integer tgt = e.getValue();
            if (tgt != null && tgt != 0) {
                weight.put(tgt, weight.getOrDefault(tgt, 0.0) + voteWeight(e.getKey()));
            }
        }
        if (weight.isEmpty()) { t.winner = null; return t; }
        double maxW = 0.0;
        for (double w : weight.values()) if (w > maxW) maxW = w;
        t.maxW = maxW;
        for (Map.Entry<Integer, Double> e : weight.entrySet())
            if (e.getValue() == maxW) t.tied.add(e.getKey());
        t.winner = (t.tied.size() == 1) ? t.tied.get(0) : null;
        return t;
    }

    // 狼队刀人投票：votes = {狼人pid -> 目标pid}。得票最高者被击杀；平票随机选一个。
    public static class WolfKill { public Integer target; public Map<Integer, Integer> cnt = new LinkedHashMap<>(); }
    public WolfKill resolveWolfKill(Map<Integer, Integer> votes) {
        WolfKill wk = new WolfKill();
        Map<Integer, Integer> cnt = new LinkedHashMap<>();
        for (int v : votes.values()) if (v != 0) cnt.put(v, cnt.getOrDefault(v, 0) + 1);
        if (cnt.isEmpty()) { wk.target = null; wk.cnt = cnt; return wk; }
        int maxn = 0;
        for (int c : cnt.values()) if (c > maxn) maxn = c;
        List<Integer> tied = new ArrayList<>();
        for (Map.Entry<Integer, Integer> e : cnt.entrySet()) if (e.getValue() == maxn) tied.add(e.getKey());
        wk.target = (tied.size() > 1) ? tied.get(RND.nextInt(tied.size())) : tied.get(0);
        wk.cnt = cnt;
        return wk;
    }

    public String privateStateText(Player player) {
        List<String> lines = new ArrayList<>();
        lines.add(publicStateText());
        lines.add("【你的私密信息】");
        if (player.isWolf) {
            String mates = "";
            for (int m : player.wolfTeammates) mates += m + "号" + players.get(m).name + "、";
            if (mates.endsWith("、")) mates = mates.substring(0, mates.length() - 1);
            if (mates.isEmpty()) mates = "无";
            lines.add("  - 你的身份：狼人");
            lines.add("  - 你的狼队友：" + mates);
        } else {
            lines.add("  - 你的身份：" + player.role + "（好人阵营）");
        }
        if (player.role.equals(SEER) && !player.checked.isEmpty()) {
            List<String> parts = new ArrayList<>();
            for (Map.Entry<Integer, String> e : player.checked.entrySet())
                parts.add(e.getKey() + "号" + players.get(e.getKey()).name + "=" + e.getValue());
            lines.add("  - 你查验过：" + String.join("；", parts));
        }
        if (player.role.equals(WITCH)) {
            lines.add("  - 你的药剂：解药" + (player.hasAntidote ? "有" : "已用") + " / 毒药" + (player.hasPoison ? "有" : "已用"));
        }
        if (player.role.equals(GUARD)) {
            String last = (player.lastProtected != null) ? (player.lastProtected + "号" + players.get(player.lastProtected).name) : "无";
            lines.add("  - 你上一晚守护的是：" + last + "（不能连续两晚守护同一人）");
        }
        return String.join("\n", lines);
    }

    public String roleRevealText() {
        List<String> lines = new ArrayList<>();
        lines.add("【最终身份揭示】");
        for (int idx : order) {
            Player p = players.get(idx);
            String status = p.alive ? "存活" : "出局";
            String name = p.isHuman ? "你" : p.name;
            lines.add("  " + idx + "号" + name + "：" + p.role + "（" + status + "）");
        }
        return String.join("\n", lines);
    }
}
