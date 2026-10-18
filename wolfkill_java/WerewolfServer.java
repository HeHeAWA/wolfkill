// 狼人杀 · 多人联机服务端（Java 11+，纯 JDK，零第三方依赖）
// 浏览器进入：电脑开一个端口，其他人用浏览器打开即可加入。
// 用法：
//   java WerewolfServer                  # 真实对局（默认免费模型 tencent/hy3:free，端口 3000）
//   java WerewolfServer --port 3000     # 指定端口
//   java WerewolfServer --mock          # 离线：AI 用确定性逻辑，不联网、不消耗额度
//   java WerewolfServer --total 8       # 一桌总人数（真人+AI），默认 8
//   java WerewolfServer --model xxx     # 指定模型
import com.sun.net.httpserver.*;
import java.io.*;
import java.net.*;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.util.*;
import java.util.concurrent.Executors;

public class WerewolfServer {
    static Room ROOM = new Room();
    static File INDEX;

    static File indexFile() {
        try {
            URL loc = WerewolfServer.class.getProtectionDomain().getCodeSource().getLocation();
            File f = new File(loc.toURI());
            String dir = f.isFile() ? f.getParent() : f.getPath();
            return new File(dir, "index.html");
        } catch (Exception e) { return new File("index.html"); }
    }

    public static void main(String[] args) throws Exception {
        int port = 3000;
        boolean portGiven = false;
        List<String> argv = Arrays.asList(args);
        for (int i = 0; i < argv.size(); i++) {
            String a = argv.get(i);
            if (a.equals("--mock")) Config.USE_MOCK = true;
            else if (a.equals("--port") && i + 1 < argv.size()) { port = Integer.parseInt(argv.get(++i)); portGiven = true; }
            else if (a.startsWith("--port=")) { port = Integer.parseInt(a.split("=", 2)[1]); portGiven = true; }
            else if (a.equals("--total") && i + 1 < argv.size()) { ROOM.total = Math.max(4, Math.min(Integer.parseInt(argv.get(++i)), 12)); }
            else if (a.startsWith("--total=")) ROOM.total = Math.max(4, Math.min(Integer.parseInt(a.split("=", 2)[1]), 12));
            else if (a.equals("--model") && i + 1 < argv.size()) Config.MODEL = argv.get(++i);
            else if (a.startsWith("--model=")) Config.MODEL = a.split("=", 2)[1];
        }
        // 云平替常用环境变量 PORT 注入（仅当命令行未显式指定 --port 时生效）
        if (!portGiven) {
            String ep = System.getenv("PORT");
            if (ep != null && !ep.isEmpty()) {
                try { int p = Integer.parseInt(ep); if (p > 0) port = p; } catch (Exception ignore) {}
            }
        }
        // 环境变量覆盖（便于纯 JRE 部署，无需修改源码重新编译）
        String ek = System.getenv("WEREWOLF_API_KEY");
        if (ek != null && !ek.isEmpty()) Config.API_KEY = ek;
        String eb = System.getenv("WEREWOLF_BASE_URL");
        if (eb != null && !eb.isEmpty()) Config.BASE_URL = eb;
        String em = System.getenv("WEREWOLF_MODEL");
        if (em != null && !em.isEmpty()) Config.MODEL = em;
        String emk = System.getenv("WEREWOLF_MOCK");
        if (emk != null) Config.USE_MOCK = emk.equalsIgnoreCase("true") || emk.equals("1");
        INDEX = indexFile();

        HttpServer server = HttpServer.create(new InetSocketAddress("0.0.0.0", port), 0);
        server.createContext("/", new Handler());
        server.setExecutor(Executors.newCachedThreadPool());
        server.start();

        System.out.println("========================================================");
        System.out.println("🐺 狼人杀 · 多人联机服务（Java 版）已启动");
        System.out.println("========================================================");
        System.out.println("  本机访问：   http://127.0.0.1:" + port);
        System.out.println("  一桌人数：   " + ROOM.total + " 人（真人 + AI 补位）");
        System.out.println("  模型：       " + (Config.USE_MOCK ? "离线 mock" : Config.MODEL));
        System.out.println("  停止服务：   Ctrl + C");
        System.out.println("========================================================");

        try { Thread.currentThread().join(); } catch (InterruptedException e) {}
    }

    static class Handler implements HttpHandler {
        public void handle(HttpExchange ex) throws IOException {
            try {
                String path = ex.getRequestURI().getPath();
                if (ex.getRequestMethod().equals("GET")) {
                    if (path.equals("/") || path.equals("/index.html")) {
                        sendFile(ex, INDEX, "text/html; charset=utf-8");
                        return;
                    }
                    if (path.equals("/api/state")) {
                        String q = ex.getRequestURI().getQuery();
                        String token = "";
                        if (q != null) for (String kv : q.split("&")) {
                            String[] p = kv.split("=", 2);
                            if (p[0].equals("token") && p.length > 1) token = URLDecoder.decode(p[1], "UTF-8");
                        }
                        sendJson(ex, ROOM.snapshot(token));
                        return;
                    }
                    sendText(ex, 404, "not found", "text/plain; charset=utf-8");
                    return;
                }
                // POST
                byte[] body = ex.getRequestBody().readAllBytes();
                Map<String, Object> req = new LinkedHashMap<>();
                if (body.length > 0) {
                    Object o = Json.parse(new String(body, StandardCharsets.UTF_8));
                    Map<String, Object> m;
                    if (o instanceof Map) { @SuppressWarnings("unchecked") Map<String, Object> mm = (Map<String, Object>) o; m = mm; }
                    else { m = new LinkedHashMap<>(); }
                    req = m;
                }
                if (path.equals("/api/join")) {
                    String cid = (String) req.get("clientId");
                    Room.JoinResult r = ROOM.addHuman((String) req.get("name"), cid);
                    Map<String, Object> out = new LinkedHashMap<>();
                    if (r.token != null) { out.put("ok", true); out.put("token", r.token); }
                    else { out.put("ok", false); out.put("error", r.error); }
                    sendJson(ex, out);
                } else if (path.equals("/api/config")) {
                    boolean ok = ROOM.setTotal((String) req.get("token"), toInt(req.get("total"), 8));
                    sendJson(ex, mapOf("ok", ok));
                } else if (path.equals("/api/start")) {
                    String err = ROOM.start((String) req.get("token"));
                    Map<String, Object> out = new LinkedHashMap<>();
                    out.put("ok", err == null);
                    if (err != null) out.put("error", err);
                    sendJson(ex, out);
                } else if (path.equals("/api/action")) {
                    String token = (String) req.get("token");
                    Integer pid = ROOM.tokenToPid.get(token);
                    if (pid == null) { sendJson(ex, mapOf("ok", false, "error", "无效身份")); return; }
                    boolean ok = ROOM.submit(pid, req.get("value"));
                    sendJson(ex, mapOf("ok", ok));
                } else if (path.equals("/api/chat")) {
                    String token = (String) req.get("token");
                    String text = (String) req.get("text");
                    boolean done = Boolean.TRUE.equals(req.get("done"));
                    ROOM.wolfChatFromHuman(token, text, done);
                    sendJson(ex, mapOf("ok", true));
                } else if (path.equals("/api/exit")) {
                    String err = ROOM.exitPlayer((String) req.get("token"));
                    Map<String, Object> out = new LinkedHashMap<>();
                    out.put("ok", err == null);
                    if (err != null) out.put("error", err);
                    sendJson(ex, out);
                } else if (path.equals("/api/kick")) {
                    String target = (String) req.get("target");
                    String err = ROOM.kickPlayer((String) req.get("token"), target);
                    Map<String, Object> out = new LinkedHashMap<>();
                    out.put("ok", err == null);
                    if (err != null) out.put("error", err);
                    sendJson(ex, out);
                } else if (path.equals("/api/reset")) {
                    String err = ROOM.reset((String) req.get("token"));
                    Map<String, Object> out = new LinkedHashMap<>();
                    out.put("ok", err == null);
                    if (err != null) out.put("error", err);
                    sendJson(ex, out);
                } else {
                    sendText(ex, 404, "not found", "text/plain; charset=utf-8");
                }
            } catch (Exception e) {
                try { sendText(ex, 500, "error: " + e, "text/plain; charset=utf-8"); } catch (Exception ignore) {}
            } finally {
                ex.close();
            }
        }

        void sendJson(HttpExchange ex, Object obj) throws IOException {
            byte[] data = Json.stringify(obj).getBytes(StandardCharsets.UTF_8);
            ex.getResponseHeaders().add("Content-Type", "application/json; charset=utf-8");
            ex.getResponseHeaders().add("Cache-Control", "no-store");
            ex.sendResponseHeaders(200, data.length);
            ex.getResponseBody().write(data);
        }

        void sendText(HttpExchange ex, int code, String text, String ctype) throws IOException {
            byte[] data = text.getBytes(StandardCharsets.UTF_8);
            ex.getResponseHeaders().add("Content-Type", ctype);
            ex.sendResponseHeaders(code, data.length);
            ex.getResponseBody().write(data);
        }

        void sendFile(HttpExchange ex, File f, String ctype) throws IOException {
            if (f == null || !f.exists()) { sendText(ex, 404, "index.html not found", "text/plain; charset=utf-8"); return; }
            byte[] data = Files.readAllBytes(f.toPath());
            ex.getResponseHeaders().add("Content-Type", ctype);
            ex.sendResponseHeaders(200, data.length);
            ex.getResponseBody().write(data);
        }
    }

    static int toInt(Object o, int def) {
        if (o instanceof Number) return ((Number) o).intValue();
        if (o instanceof String) { try { return Integer.parseInt((String) o); } catch (Exception e) { return def; } }
        return def;
    }

    static Map<String, Object> mapOf(Object... kv) {
        Map<String, Object> m = new LinkedHashMap<>();
        for (int i = 0; i + 1 < kv.length; i += 2) m.put(String.valueOf(kv[i]), kv[i + 1]);
        return m;
    }
}
