# -*- coding: utf-8 -*-
"""匿名身份：服务端签发 uid，前端只持有一个签名 Cookie。

公开分享时不能要求注册或输令牌，但又必须「各看各的」，所以身份不能由前端自报：
1. 首次访问 `/api/identity`，服务端随机生成 uid，用 HMAC-SHA256 签名后写入
   HttpOnly Cookie（`ros2_id=<uid>.<sig>`），前端拿不到也改不了；
2. 之后所有请求以 Cookie 为准，请求体里的 `userId` 一律忽略 —— 改成别人的也没用；
3. 签名密钥优先读环境变量 ROS2_ID_SECRET（部署时由 `.env` 注入，跨部署稳定），
   其次兜底读老的数据目录 `.id_secret`，都没有则进程内随机。
"""
import hashlib
import hmac
import os
import re
import secrets

from . import paths

COOKIE = "ros2_id"
MAX_AGE = 365 * 86400
SECRET_ENV = "ROS2_ID_SECRET"
_UID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
_CACHE = [None]

def _read_secret(path):
    try:
        if os.path.isfile(path):
            with open(path, "rb") as f:
                key = f.read().strip()
            if len(key) >= 32:
                return key
    except OSError:
        pass
    return None


def _secret():
    if _CACHE[0]:
        return _CACHE[0]
    env = (os.environ.get(SECRET_ENV) or "").strip()
    if env:
        _CACHE[0] = env.encode("utf-8")
        return _CACHE[0]
    # 数据目录 .id_secret 兜底（老部署遗留，或本地开发时手动放置的）。
    key = _read_secret(os.path.join(paths.data(), ".id_secret"))
    if key:
        _CACHE[0] = key
        return key
    # 都没有：进程内随机。重启后身份会重发，但功能不中断。
    _CACHE[0] = secrets.token_hex(32).encode("ascii")
    return _CACHE[0]


def _sig(uid):
    return hmac.new(_secret(), uid.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def sign(uid):
    return "%s.%s" % (uid, _sig(uid))


def verify(value):
    """校验 Cookie 值，合法则返回 uid，否则 None。"""
    value = (value or "").strip()
    if "." not in value:
        return None
    uid, sig = value.rsplit(".", 1)
    if not _UID_RE.match(uid):
        return None
    if hmac.compare_digest(sig, _sig(uid)):
        return uid
    return None


def new_uid():
    return "u" + secrets.token_hex(8)


def from_cookie(headers, peer_headers=None):
    """从请求头里取合法身份。headers 可以是 HTTPMessage，也可以是普通 dict。"""
    raw = ""
    try:
        raw = headers.get("Cookie") or headers.get("cookie") or ""
    except Exception:      # noqa: BLE001
        raw = ""
    for part in str(raw).split(";"):
        if "=" not in part:
            continue
        key, val = part.split("=", 1)
        if key.strip() == COOKIE:
            return verify(val.strip())
    return None


def cookie_header(uid, secure=False):
    h = "%s=%s; Path=/; Max-Age=%d; HttpOnly; SameSite=Lax" % (
        COOKIE, sign(uid), MAX_AGE)
    if secure:
        h += "; Secure"
    return h


def describe():
    if (os.environ.get(SECRET_ENV) or "").strip():
        src = "环境变量（.env）"
    elif os.path.isfile(os.path.join(paths.data(), ".id_secret")):
        src = "数据目录 .id_secret"
    else:
        src = "进程内随机（未持久化）"
    return "匿名身份已启用（签名密钥：%s）" % src
