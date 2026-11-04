# -*- coding: utf-8 -*-
"""截图理解（多模态）：把学生随手截的图变成可继续处理的文本。

为什么值得做：学生最常见的求助形态是「截个报错图丢过来」，而不是把报错一字不差
贴出来。旧链路只认文字，截图等于白屏。现在补上这一段。

链路：
  上传 → 魔数校验 → 落盘（按内容 sha256 命名）→ 视觉模型结构化识别
       → {kind, title, text, hint} → 交给主调度员按现有分支处理

设计取舍：
  1. 只做「结构化抄录」，不做诊断。诊断仍交给原有 SYS_MASTER_ERROR 等提示词，
     这样多模态只是加了一个入口，不引入第二套答题风格；
  2. 识别结果按图片内容的 sha256 缓存，同一张图重复上传不重复计费；
  3. 视觉模型不可用时（未配 vision 角色 / 上游 4xx）不阻断对话，退化为纯文本回答；
  4. 图片一律存会话目录内，随会话归档一起走，不额外污染全局存储。
"""
import base64
import hashlib
import json
import os
import re

from . import llm
from . import paths
from . import prompts
from . import progress

# 只收这几种，其余一律拒收（不做格式转换，避免引入图像库依赖）
_MAGIC = [
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"GIF87a", "gif", "image/gif"),
    (b"GIF89a", "gif", "image/gif"),
]

KINDS = ("error", "code", "diagram", "hardware", "slide", "other")

MAX_BYTES = 4 * 1024 * 1024          # 单图 4MB
MAX_IMAGES = 3                       # 单条消息最多 3 张


def sniff(data):
    """按魔数判断真实类型，返回 (ext, mime)；不认识则 (None, None)。

    不信任前端给的扩展名和 Content-Type —— 那是可以随便写的。
    """
    if not data:
        return None, None
    for magic, ext, mime in _MAGIC:
        if data.startswith(magic):
            return ext, mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None, None


def decode_data_url(s):
    """'data:image/png;base64,xxx' → bytes；非法返回 None。"""
    if not isinstance(s, str):
        return None
    m = re.match(r"^data:image/[A-Za-z0-9.+-]+;base64,(.+)$", s.strip(),
                 re.S)
    if not m:
        return None
    try:
        return base64.b64decode(m.group(1), validate=False)
    except Exception:                # noqa: BLE001
        return None


def upload_dir(sid):
    d = os.path.join(paths.sessions(), sid, "uploads")
    os.makedirs(d, exist_ok=True)
    return d


def save(sid, data, max_bytes=MAX_BYTES):
    """校验并落盘，返回 {ok, id, path, bytes, mime} 或 {ok:False, error}。"""
    ext, mime = sniff(data)
    if not ext:
        return {"ok": False, "error": "只支持 PNG / JPG / WEBP / GIF 图片"}
    if len(data) > max_bytes:
        return {"ok": False,
                "error": "图片太大了（%.1fMB），请压缩到 %dMB 以内"
                         % (len(data) / 1048576.0, max_bytes // 1048576)}
    digest = hashlib.sha256(data).hexdigest()
    img_id = digest[:20]
    path = os.path.join(upload_dir(sid), "%s.%s" % (img_id, ext))
    if not os.path.isfile(path):     # 内容寻址：同图只存一份
        with open(path, "wb") as f:
            f.write(data)
    return {"ok": True, "id": img_id, "path": path, "bytes": len(data),
            "mime": mime, "ext": ext}


def resolve(sid, img_id):
    """按 id 找回已上传图片的路径；不在本会话目录内的一律拒绝。"""
    if not re.fullmatch(r"[0-9a-f]{20}", img_id or ""):
        return None
    d = os.path.abspath(upload_dir(sid))
    for ext in ("png", "jpg", "webp", "gif"):
        p = os.path.abspath(os.path.join(d, "%s.%s" % (img_id, ext)))
        if os.path.isfile(p) and p.startswith(d + os.sep):
            return p
    return None


def resolve_many(sid, ids):
    """把前端传来的 id 列表解析成本地路径；非法/失效的直接丢弃。

    丢弃而不是报错：一张图失效不该让整条消息发不出去。
    """
    out = []
    for i in (ids or [])[:MAX_IMAGES]:
        p = resolve(sid, str(i or ""))
        if p:
            out.append(p)
    return out


def data_url(path, mime=None):
    """图片转 data URL 送给模型（本地文件无需公网可达，答辩断网也能演示）。"""
    raw = open(path, "rb").read()
    if not mime:
        _ext, mime = sniff(raw)
    mime = mime or "image/png"
    return "data:%s;base64,%s" % (mime, base64.b64encode(raw).decode("ascii"))


def _extract_json(text):
    """从可能带围栏的输出里抠出 JSON 对象。"""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        parts = t.split("```")
        if len(parts) >= 3:
            t = parts[1]
        if t.lstrip()[:4].lower() == "json":
            t = t.lstrip()[4:]
    left, right = t.find("{"), t.rfind("}")
    if left < 0 or right <= left:
        return None
    try:
        return json.loads(t[left:right + 1])
    except Exception:                # noqa: BLE001
        return None


def _cache_path(path):
    return os.path.splitext(path)[0] + ".json"


def describe(cfg, sid, img_paths, user_text=""):
    """识别若干图片，返回统一信封。

    返回 {ok, kind, title, text, hint, cached, n}
    ok=False 表示识别不可用（未配置视觉模型 / 上游失败），调用方按纯文本继续。
    """
    img_paths = [p for p in (img_paths or []) if p][:MAX_IMAGES]
    if not img_paths:
        return {"ok": False, "kind": "other", "text": "", "title": "", "hint": ""}

    mc = (cfg or {}).get("multimodal") or {}
    if mc.get("enabled") is False:
        return {"ok": False, "kind": "other", "text": "",
                "error": "多模态已关闭"}

    # 单图命中缓存就直接用（多图时不走缓存，避免组合语义错配）
    if len(img_paths) == 1:
        cp = _cache_path(img_paths[0])
        if os.path.isfile(cp):
            try:
                with open(cp, "r", encoding="utf-8") as f:
                    hit = json.load(f)
                if hit.get("ok"):
                    hit["cached"] = True
                    with progress.step("vision", "识别截图", "同一张图，用上次结果"):
                        pass
                    return hit
            except Exception:        # noqa: BLE001
                pass

    label = "识别截图" if len(img_paths) == 1 else "识别 %d 张截图" % len(img_paths)
    with progress.step("vision", label):
        err = ""
        data = None
        try:
            urls = [data_url(p) for p in img_paths]
            ask = ("学生上传了 %d 张图片，请按要求输出 JSON。\n学生同时说：%s"
                   % (len(img_paths), (user_text or "（没有文字）")[:300]))
            raw = llm.chat_vision(cfg, "vision", prompts.SYS_VISION, ask, urls)
            data = _extract_json(raw)
        except Exception as e:       # noqa: BLE001
            err = str(e)[:200]
        out = {
            "ok": data is not None,
            "kind": "other",
            "title": "",
            "text": "",
            "hint": "",
            "n": len(img_paths),
        }
        if data is None:
            out["error"] = err or "视觉模型未返回可解析结果"
            progress.note("识别失败，按文字继续")
            return out
        kind = str(data.get("kind") or "other").strip().lower()
        out["kind"] = kind if kind in KINDS else "other"
        out["title"] = str(data.get("title") or "")[:200]
        out["text"] = str(data.get("text") or "")[:2000]
        out["hint"] = str(data.get("hint") or "")[:300]
        if not out["text"]:
            out["ok"] = False
            out["error"] = "图里没抄到可用文本"
        progress.note("%s · %s" % (out["kind"], out["title"] or "已识别"))

    if out["ok"] and len(img_paths) == 1:
        try:
            with open(_cache_path(img_paths[0]), "w", encoding="utf-8") as f:
                json.dump(out, f, ensure_ascii=False)
        except Exception:            # noqa: BLE001
            pass
    return out


def looks_like_error(text):
    """识别结果是不是「报错」：先看 kind，再看文本特征兜底。"""
    if not text:
        return False
    return bool(re.search(r"Error|Exception|Traceback|错误|报错|failed|"
                          r"cannot|not found|No such", text, re.I))


def attach_to_message(msg, v):
    """把识别结果拼进用户消息，让下游提示词能直接读到。"""
    if not v or not v.get("ok"):
        return msg
    parts = [msg.strip()] if msg.strip() else []
    parts.append("【学生上传的图片：%s】" % (v.get("title") or v.get("kind")))
    parts.append(v.get("text") or "")
    if v.get("hint"):
        parts.append("【图像备注】%s" % v.get("hint"))
    return "\n\n".join(p for p in parts if p)
