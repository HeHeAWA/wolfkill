// 狼人杀 · Java 联机版 配置文件（纯 JDK，无第三方依赖）
import java.util.*;

public class Config {
    // ============ OpenRouter API ============
    // 注意：API Key 通过环境变量 WEREWOLF_API_KEY 注入（运行时 export），切勿硬编码提交到仓库
    public static String API_KEY = System.getenv("WEREWOLF_API_KEY");
    public static String BASE_URL = "https://openrouter.ai/api/v1/chat/completions";
    public static String MODEL = "nvidia/nemotron-3-super-120b-a12b:free"; // 原 tencent/hy3:free 已被 OpenRouter 下架（404）；当前选用免费且不受地区限制的强模型
    public static boolean DISABLE_REASONING = true; // 该免费模型默认走推理，关闭后当普通 instruct 用，小 max_tokens 决策调用也能正常拿到 content（若换成非推理模型请置 false）

    public static double CALL_DELAY = 0.5;      // 免费模型限流保护：每次调用后间隔秒数
    public static int MAX_RETRIES = 3;
    public static int RETRY_BACKOFF = 2;        // 重试退避秒数
    public static int REQUEST_TIMEOUT = 60;     // 单次请求超时秒数

    // 离线测试开关：true 时 AI 用确定性逻辑代替真实 API 调用（不消耗额度）
    public static boolean USE_MOCK = false;
    public static boolean AUTO_HUMAN = false;
    public static boolean REVEAL_SECRET = false;

    public static int WOLF_DISCUSS_ROUNDS = 6;

    // ============ 游戏配置 ============
    // 联机模式：开局时全部按 AI 建好、随机分配角色，再把前 N 个位置指定为真人
    public static boolean HUMAN_PLAYER = false;

    public static int SPEECH_MAX_CHARS = 120;

    public static String AI_EXPERT_PERSONA =
            "你是一名精通狼人杀的高手：逻辑缜密、极其擅长伪装与心理博弈，"
            + "会严格依据场上的发言、投票、死亡等公开信息去推断各方身份，"
            + "发言自然且有说服力，非常会玩、极其难缠。";

    public static List<String[]> AI_PROFILES = new ArrayList<>();
    static {
        String p = AI_EXPERT_PERSONA;
        AI_PROFILES.add(new String[]{"阿杰", p});
        AI_PROFILES.add(new String[]{"小敏", p});
        AI_PROFILES.add(new String[]{"老李", p});
        AI_PROFILES.add(new String[]{"婷婷", p});
        AI_PROFILES.add(new String[]{"大壮", p});
        AI_PROFILES.add(new String[]{"慧姐", p});
        AI_PROFILES.add(new String[]{"小天", p});
        AI_PROFILES.add(new String[]{"阿强", p});
        AI_PROFILES.add(new String[]{"丽丽", p});
        AI_PROFILES.add(new String[]{"胖虎", p});
        AI_PROFILES.add(new String[]{"雪儿", p});
        AI_PROFILES.add(new String[]{"志明", p});
    }

    // 角色配比（人数之和 = 总人数）。默认 8 人：2 狼 + 预言家 + 女巫 + 猎人 + 守卫 + 2 平民
    public static LinkedHashMap<String, Integer> ROLES_CONFIG = defaultRoles();

    public static LinkedHashMap<String, Integer> defaultRoles() {
        LinkedHashMap<String, Integer> m = new LinkedHashMap<>();
        m.put("狼人", 2);
        m.put("预言家", 1);
        m.put("女巫", 1);
        m.put("猎人", 1);
        m.put("守卫", 1);
        m.put("平民", 2);
        return m;
    }
}
