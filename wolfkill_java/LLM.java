// OpenRouter LLM 客户端（纯 JDK，兼容 OpenAI Chat Completions 接口）
// mock 模式下不会调用本类（AI 直接用确定性逻辑）。
import java.net.*;
import java.net.http.*;
import java.net.http.HttpRequest.BodyPublishers;
import java.net.http.HttpResponse.BodyHandlers;
import java.time.Duration;
import java.io.IOException;
import java.util.*;
import javax.net.ssl.*;

public class LLM {
    private static HttpClient CLIENT;

    private static synchronized HttpClient client() {
        if (CLIENT != null) return CLIENT;
        try {
            // 宽松 SSL：本地游戏访问可信端点，避免部署环境证书问题导致请求失败
            SSLContext ctx = SSLContext.getInstance("TLS");
            ctx.init(null, new TrustManager[]{new X509TrustManager() {
                public void checkClientTrusted(java.security.cert.X509Certificate[] c, String a) {}
                public void checkServerTrusted(java.security.cert.X509Certificate[] c, String a) {}
                public java.security.cert.X509Certificate[] getAcceptedIssuers() { return new java.security.cert.X509Certificate[0]; }
            }}, new java.security.SecureRandom());
            // 正常直连请求，不经代理
            CLIENT = HttpClient.newBuilder().sslContext(ctx).build();
        } catch (Exception e) {
            CLIENT = HttpClient.newHttpClient();
        }
        return CLIENT;
    }

    public static String chat(String system, String user, double temperature, int maxTokens, Integer timeout) {
        if (Config.USE_MOCK) return "";
        int to = (timeout != null) ? timeout : Config.REQUEST_TIMEOUT;
        Map<String, Object> msgSys = new LinkedHashMap<>();
        msgSys.put("role", "system"); msgSys.put("content", system);
        Map<String, Object> msgUser = new LinkedHashMap<>();
        msgUser.put("role", "user"); msgUser.put("content", user);
        Map<String, Object> payload = new LinkedHashMap<>();
        payload.put("model", Config.MODEL);
        List<Object> msgs = new ArrayList<>();
        msgs.add(msgSys); msgs.add(msgUser);
        payload.put("messages", msgs);
        payload.put("temperature", temperature);
        payload.put("max_tokens", maxTokens);
        if (Config.DISABLE_REASONING) {
            // 关掉推理模型的思维链，让答案直接落到 content，且小 max_tokens 决策调用不会因推理预算被榨干而返回空
            Map<String, Object> rb = new LinkedHashMap<>();
            rb.put("enabled", false);
            payload.put("reasoning", rb);
        }
        String data = Json.stringify(payload);

        HttpClient client = client();
        for (int attempt = 0; attempt < Config.MAX_RETRIES; attempt++) {
            try {
                HttpRequest req = HttpRequest.newBuilder()
                        .uri(URI.create(Config.BASE_URL))
                        .timeout(Duration.ofSeconds(to))
                        .header("Content-Type", "application/json")
                        .header("Authorization", "Bearer " + Config.API_KEY)
                        .header("HTTP-Referer", "https://werewolf.local")
                        .header("X-Title", "Werewolf Text Game")
                        .POST(BodyPublishers.ofString(data, java.nio.charset.StandardCharsets.UTF_8))
                        .build();
                HttpResponse<String> resp = client.send(req, BodyHandlers.ofString());
                int code = resp.statusCode();
                String body = resp.body();
                if (code >= 400) {
                    // 关键诊断：把 OpenRouter 返回的真实错误体打印出来，便于第一时间定位（如模型下架、额度不足等）
                    if (attempt == 0)
                        System.out.println("[LLM] OpenRouter 返回 HTTP " + code + "：" + (body == null ? "(空)" : body));
                    if (code == 429) Thread.sleep(Config.RETRY_BACKOFF * 2000L * (attempt + 1));
                    else Thread.sleep(Config.RETRY_BACKOFF * 1000L * (attempt + 1));
                    continue;
                }
                @SuppressWarnings("unchecked")
                Map<String, Object> obj = (Map<String, Object>) Json.parse(body);
                if (obj == null || obj.containsKey("error")) {
                    if (attempt == 0)
                        System.out.println("[LLM] OpenRouter 返回业务错误：" + (body == null ? "(空)" : body));
                    Thread.sleep(Config.RETRY_BACKOFF * 1000L * (attempt + 1));
                    continue;
                }
                @SuppressWarnings("unchecked")
                List<Object> choices = (List<Object>) obj.get("choices");
                if (choices == null || choices.isEmpty()) {
                    if (attempt == 0)
                        System.out.println("[LLM] 响应缺少 choices：" + (body == null ? "(空)" : body));
                    Thread.sleep(Config.RETRY_BACKOFF * 1000L * (attempt + 1));
                    continue;
                }
                @SuppressWarnings("unchecked")
                Map<String, Object> ch = (Map<String, Object>) choices.get(0);
                @SuppressWarnings("unchecked")
                Map<String, Object> msg = (Map<String, Object>) ch.get("message");
                String text = (String) msg.get("content");
                if (text != null) text = text.trim();
                Thread.sleep((long) (Config.CALL_DELAY * 1000));
                return text == null ? "" : text;
            } catch (Exception e) {
                if (attempt == 0)
                    System.out.println("[LLM] 请求 openrouter 失败（" + Config.BASE_URL + "）：" + e.getClass().getSimpleName() + " - " + e.getMessage());
                try { Thread.sleep(Config.RETRY_BACKOFF * 1000L * (attempt + 1)); } catch (InterruptedException ignored) {}
            }
        }
        // 不再抛异常：返回 null，交由 AI.* 的各决策函数做安全兜底，
        // 保证任何一次 LLM 失败/超时/限流都不会让整局游戏卡死或异常终止。
        System.out.println("[警告] OpenRouter 请求连续失败 " + Config.MAX_RETRIES + " 次（已降级为安全默认值继续）");
        return null;
    }

    public static Integer parseInt(String text, Integer def) {
        if (text == null) return def;
        StringBuilder digits = new StringBuilder();
        for (int i = 0; i < text.length(); i++) {
            char c = text.charAt(i);
            if (Character.isDigit(c)) digits.append(c);
            else if (digits.length() > 0) break;
        }
        if (digits.length() == 0) return def;
        try { return Integer.parseInt(digits.toString()); } catch (Exception e) { return def; }
    }

    public static boolean smokeTest() {
        try {
            String r = chat("你是狼人杀游戏里的旁白。", "只回复两个字：正常", 0.1, 8, null);
            return r != null && !r.isEmpty();
        } catch (Exception e) {
            System.out.println("[连通性测试失败] " + e);
            return false;
        }
    }
}
