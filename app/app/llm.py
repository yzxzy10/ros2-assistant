# -*- coding: utf-8 -*-
"""极简 OpenAI 兼容 chat 客户端（urllib，无第三方依赖）。"""
import json
import time
import urllib.request
import urllib.error


class LLMError(Exception):
    pass


def chat_stream(cfg, role, system, user, timeout=180, history=None, temperature=None,
                top_p=None, extra_params=None):
    """流式调用：逐块 yield 文本增量（OpenAI 兼容 SSE）。"""
    base = cfg["baseUrl"].rstrip("/")
    key = cfg.get("apiKey", "")
    mdl = cfg["models"].get(role, "qwen-flash")
    opt = dict(cfg.get("options", {}).get(role, {}))
    if temperature is not None:
        opt["temperature"] = temperature
    if top_p is not None:
        opt["top_p"] = top_p
    if extra_params:
        opt.update(extra_params)

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    for h in (history or []):
        messages.append(h)
    messages.append({"role": "user", "content": user})

    body = json.dumps({
        "model": mdl,
        "messages": messages,
        "stream": True,
        **opt,
    }, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        base + "/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + key,
            "Accept": "text/event-stream",
        },
        method="POST",
    )

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line or not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if chunk == "[DONE]":
                break
            try:
                obj = json.loads(chunk)
            except Exception:
                continue
            choices = obj.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            text = delta.get("content") or ""
            if text:
                yield text


def chat_vision(cfg, role, system, user, images, timeout=150, temperature=None):
    """多模态调用：user 消息带若干图片（data URL 或公网 URL）。

    images 顺序即模型看到的顺序；不带图片时退化为普通 chat。
    DashScope 兼容模式走 OpenAI 的内容数组格式：
      content: [{"type":"text",...}, {"type":"image_url","image_url":{"url":...}}]
    """
    if not images:
        return chat(cfg, role, system, user, timeout=timeout,
                    temperature=temperature)

    base = cfg["baseUrl"].rstrip("/")
    key = cfg.get("apiKey", "")
    mdl = cfg["models"].get(role) or cfg["models"].get("vision") or "qwen-vl-max"
    opt = dict(cfg.get("options", {}).get(role, {}))
    if temperature is not None:
        opt["temperature"] = temperature
    opt.pop("stream", None)

    content = [{"type": "text", "text": user}]
    for u in images:
        content.append({"type": "image_url", "image_url": {"url": u}})

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": content})

    body = json.dumps({"model": mdl, "messages": messages, **opt},
                      ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        base + "/chat/completions",
        data=body,
        headers={"Content-Type": "application/json",
                 "Authorization": "Bearer " + key},
        method="POST",
    )

    last_err = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            msg = ((data.get("choices") or [{}])[0]).get("message") or {}
            text = msg.get("content") or ""
            if isinstance(text, list):        # 少数实现返回分段数组
                text = "".join(p.get("text", "") for p in text
                               if isinstance(p, dict))
            if text:
                return text
            last_err = LLMError("视觉模型返回空内容")
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:400]
            except Exception:
                pass
            last_err = LLMError("视觉模型 HTTP %s: %s" % (e.code, detail))
            if e.code in (400, 401, 403, 404):
                raise last_err          # 模型不存在/鉴权失败，重试无意义
        except Exception as e:          # noqa: BLE001
            last_err = LLMError("视觉模型调用失败: %r" % (e,))
        if attempt == 0:
            time.sleep(1.5)
    raise last_err or LLMError("视觉模型调用失败")


def chat(cfg, role, system, user, timeout=180, history=None, temperature=None,
         top_p=None, extra_params=None):
    """调用一次对话补全，返回助手文本。失败重试 1 次。"""
    base = cfg["baseUrl"].rstrip("/")
    key = cfg.get("apiKey", "")
    mdl = cfg["models"].get(role, "qwen-flash")
    opt = dict(cfg.get("options", {}).get(role, {}))
    if temperature is not None:
        opt["temperature"] = temperature
    if top_p is not None:
        opt["top_p"] = top_p
    if extra_params:
        opt.update(extra_params)

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    for h in (history or []):
        messages.append(h)
    messages.append({"role": "user", "content": user})

    body = json.dumps({
        "model": mdl,
        "messages": messages,
        **opt,
    }, ensure_ascii=False).encode("utf-8")

    req = urllib.request.Request(
        base + "/chat/completions",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": "Bearer " + key,
        },
        method="POST",
    )

    last_err = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            content = msg.get("content") or ""
            if not content and msg.get("reasoning_content"):
                content = msg["reasoning_content"]
            if not content:
                raise LLMError("模型返回空内容")
            return content
        except LLMError:
            raise
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", "replace")[:500]
            except Exception:
                pass
            last_err = LLMError("LLM HTTP %s: %s" % (e.code, detail))
            if e.code in (401, 403):
                raise last_err
        except Exception as e:  # noqa: BLE001
            last_err = LLMError("LLM 调用失败: %r" % (e,))
        if attempt == 0:
            time.sleep(1.5)
    raise last_err or LLMError("LLM 调用失败")
