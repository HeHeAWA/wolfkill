// 极简 JSON 解析 / 序列化（纯 JDK，无第三方依赖）
// 解析结果：对象->LinkedHashMap<String,Object>，数组->ArrayList<Object>，
// 字符串->String，整数->Integer，小数->Double，布尔->Boolean，空->null
import java.util.*;

public class Json {
    @SuppressWarnings("unchecked")
    public static Object parse(String s) {
        if (s == null) return null;
        Parser p = new Parser(s.trim());
        p.skipWs();
        return p.parseValue();
    }

    static class Parser {
        String s; int i;
        Parser(String s) { this.s = s; this.i = 0; }
        void skipWs() { while (i < s.length() && Character.isWhitespace(s.charAt(i))) i++; }
        Object parseValue() {
            skipWs();
            if (i >= s.length()) return null;
            char c = s.charAt(i);
            if (c == '{') return parseObject();
            if (c == '[') return parseArray();
            if (c == '"') return parseString();
            if (c == 't' || c == 'f') return parseBool();
            if (c == 'n') { i += 4; return null; }
            return parseNumber();
        }
        Map<String, Object> parseObject() {
            Map<String, Object> m = new LinkedHashMap<>();
            i++; // {
            skipWs();
            if (i < s.length() && s.charAt(i) == '}') { i++; return m; }
            while (true) {
                skipWs();
                String key = parseString();
                skipWs();
                if (i < s.length() && s.charAt(i) == ':') i++;
                Object val = parseValue();
                m.put(key, val);
                skipWs();
                if (i < s.length() && s.charAt(i) == ',') { i++; continue; }
                if (i < s.length() && s.charAt(i) == '}') { i++; break; }
                break;
            }
            return m;
        }
        List<Object> parseArray() {
            List<Object> l = new ArrayList<>();
            i++; // [
            skipWs();
            if (i < s.length() && s.charAt(i) == ']') { i++; return l; }
            while (true) {
                Object val = parseValue();
                l.add(val);
                skipWs();
                if (i < s.length() && s.charAt(i) == ',') { i++; continue; }
                if (i < s.length() && s.charAt(i) == ']') { i++; break; }
                break;
            }
            return l;
        }
        String parseString() {
            StringBuilder sb = new StringBuilder();
            i++; // opening quote
            while (i < s.length()) {
                char c = s.charAt(i++);
                if (c == '\\') {
                    char e = s.charAt(i++);
                    if (e == 'n') sb.append('\n');
                    else if (e == 't') sb.append('\t');
                    else if (e == 'r') sb.append('\r');
                    else if (e == 'b') sb.append('\b');
                    else if (e == 'f') sb.append('\f');
                    else if (e == 'u') { String hex = s.substring(i, i + 4); sb.append((char) Integer.parseInt(hex, 16)); i += 4; }
                    else sb.append(e);
                } else if (c == '"') { break; }
                else sb.append(c);
            }
            return sb.toString();
        }
        Object parseNumber() {
            int start = i;
            while (i < s.length()) {
                char c = s.charAt(i);
                if (Character.isDigit(c) || c == '.' || c == '-' || c == '+' || c == 'e' || c == 'E') i++;
                else break;
            }
            String num = s.substring(start, i);
            if (num.contains(".") || num.contains("e") || num.contains("E")) return Double.parseDouble(num);
            try { return Integer.parseInt(num); } catch (Exception e) { return Long.parseLong(num); }
        }
        Boolean parseBool() {
            if (s.startsWith("true", i)) { i += 4; return Boolean.TRUE; }
            i += 5; return Boolean.FALSE;
        }
    }

    public static String stringify(Object o) {
        StringBuilder sb = new StringBuilder();
        write(o, sb);
        return sb.toString();
    }

    @SuppressWarnings("unchecked")
    static void write(Object o, StringBuilder sb) {
        if (o == null) { sb.append("null"); return; }
        if (o instanceof Map) {
            sb.append("{");
            boolean f = false;
            for (Map.Entry<Object, Object> e : ((Map<Object, Object>) o).entrySet()) {
                if (f) sb.append(",");
                f = true;
                write(String.valueOf(e.getKey()), sb);
                sb.append(":");
                write(e.getValue(), sb);
            }
            sb.append("}");
            return;
        }
        if (o instanceof List) {
            sb.append("[");
            boolean f = false;
            for (Object v : (List<Object>) o) {
                if (f) sb.append(",");
                f = true;
                write(v, sb);
            }
            sb.append("]");
            return;
        }
        if (o instanceof String) {
            sb.append("\"");
            escape((String) o, sb);
            sb.append("\"");
            return;
        }
        if (o instanceof Boolean || o instanceof Number) {
            sb.append(o.toString());
            return;
        }
        sb.append("\"");
        escape(String.valueOf(o), sb);
        sb.append("\"");
    }

    static void escape(String s, StringBuilder sb) {
        for (int k = 0; k < s.length(); k++) {
            char c = s.charAt(k);
            if (c == '"') sb.append("\\\"");
            else if (c == '\\') sb.append("\\\\");
            else if (c == '\n') sb.append("\\n");
            else if (c == '\r') sb.append("\\r");
            else if (c == '\t') sb.append("\\t");
            else sb.append(c);
        }
    }
}
