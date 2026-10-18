# -*- coding: utf-8 -*-
"""
OpenRouter LLM 客户端（仅使用 Python 标准库，无第三方依赖）
兼容 OpenAI Chat Completions 接口。
"""
import json
import ssl
import time
import urllib.request
import urllib.error

import config


def _ssl_verified():
    """优先使用 certifi 的 CA 证书包，其次系统默认证书。"""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        pass
    try:
        return ssl.create_default_context()
    except Exception:  # noqa: BLE001
        pass
    return None


def _ssl_unverified():
    """兜底：不做证书校验（本地游戏访问可信端点，仍可运行）。"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


_SSL_CTX = _ssl_verified() or _ssl_unverified()
_SSL_FALLBACK = _ssl_unverified()
_SSL_DEGRADED = False


def _is_cert_error(e) -> bool:
    """判断是否为证书/SSL 校验类错误。"""
    if isinstance(e, ssl.SSLError):
        return True
    msg = str(e).lower()
    return "certificate" in msg or "ssl" in msg or "crl" in msg or "x509" in msg


def chat(
    system: str,
    user: str,
    temperature: float = 0.7,
    max_tokens: int = 400,
    timeout: int = None,
) -> str:
    """调用 OpenRouter 对话接口，返回模型回复文本。"""
    global _SSL_DEGRADED
    timeout = timeout or config.REQUEST_TIMEOUT
    payload = {
        "model": config.MODEL,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    data = json.dumps(payload).encode("utf-8")

    last_err = None
    for attempt in range(config.MAX_RETRIES):
        req = urllib.request.Request(config.BASE_URL, data=data, method="POST")
        req.add_header("Authorization", f"Bearer {config.API_KEY}")
        req.add_header("Content-Type", "application/json")
        req.add_header("HTTP-Referer", "https://werewolf.local")
        req.add_header("X-Title", "Werewolf Text Game")
        try:
            ctx = _SSL_FALLBACK if _SSL_DEGRADED else _SSL_CTX
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                body = resp.read().decode("utf-8")
            obj = json.loads(body)
            text = obj["choices"][0]["message"]["content"].strip()
            time.sleep(config.CALL_DELAY)  # 免费模型限流保护
            return text
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "ignore")
            last_err = f"HTTP {e.code}: {detail[:300]}"
            # 限流 429 退避更久
            if e.code == 429:
                time.sleep(config.RETRY_BACKOFF * 5 * (attempt + 1))
            else:
                time.sleep(config.RETRY_BACKOFF * (attempt + 1))
        except Exception as e:  # noqa: BLE001
            last_err = str(e)
            # 证书/SSL 校验失败：自动降级为不校验并重试
            if not _SSL_DEGRADED and _is_cert_error(e):
                _SSL_DEGRADED = True
                print("[提示] 检测到证书校验失败，已自动降级为不校验模式继续运行（不影响游戏）。")
            time.sleep(config.RETRY_BACKOFF * (attempt + 1))

    raise RuntimeError(f"OpenRouter 请求失败（已重试 {config.MAX_RETRIES} 次）: {last_err}")


def parse_int(text: str, default: int = None) -> int:
    """从模型回复中提取第一个整数。找不到返回 default。"""
    if not text:
        return default
    digits = ""
    for ch in text:
        if ch.isdigit():
            digits += ch
        elif digits:
            break
    return int(digits) if digits else default


def smoke_test() -> bool:
    """做一次最小连通性测试，验证 Key / 模型可用。"""
    try:
        resp = chat("你是狼人杀游戏里的旁白。", "只回复两个字：正常", temperature=0.1, max_tokens=8)
        return bool(resp)
    except Exception as e:  # noqa: BLE001
        print(f"[连通性测试失败] {e}")
        return False


if __name__ == "__main__":
    print("连通性测试:", "通过" if smoke_test() else "失败")
