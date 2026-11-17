# -*- coding: utf-8 -*-
"""账号体系：用户名 + 密码。登录后 uid 换成账号 uid。

为什么要账号
------------
原来每个人的身份是「浏览器 Cookie 里的随机 uid」（见 identity.py）。它不依赖 IP，
但绑在设备上：换电脑、换浏览器、清缓存，历史会话和学习画像就找不回来了。
用户的要求是「数据跟着账号走」，所以登录后把 uid 换成稳定的账号 uid，
之后所有会话、画像、归档都挂在这个 uid 下，与 IP 和浏览器无关。

设计取舍
--------
- 只做用户名 + 密码，不引第三方登录：教学场景没有邮箱/手机号可信来源。
- 密码用 PBKDF2-HMAC-SHA256（20 万轮）+ 每账号独立盐，比对用 compare_digest。
- 账号 uid 取 `acct_` + 用户名哈希，而不是直接用用户名：
  中文名、大小写、路径安全都不用额外处理，uid 也永远落在 identity 的合法字符集里。
- 登录失败按用户名限速，防止在线撞库。
"""
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time

from . import paths

RE_USER = re.compile(r"^[A-Za-z0-9_\-\u4e00-\u9fa5]{2,24}$")
MIN_PW = 6
MAX_PW = 64
ITERS = 200_000
FILE = "accounts.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS accounts (
  uid        TEXT PRIMARY KEY,
  username   TEXT NOT NULL UNIQUE COLLATE NOCASE,
  pw_hash    TEXT NOT NULL,
  salt       TEXT NOT NULL,
  created_at TEXT NOT NULL,
  last_login TEXT
);
"""

_READY = [None]
_LOCK = threading.Lock()

# 登录失败次数（进程内）：用户名 -> [时间戳, ...]
_FAILS = {}
FAIL_WINDOW = 600
FAIL_MAX = 8


def _conn():
    db = os.path.join(paths.data(), FILE)
    c = sqlite3.connect(db, timeout=10)
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
    except Exception:      # noqa: BLE001
        pass
    if _READY[0] != db:
        with _LOCK:
            c.executescript(_SCHEMA)
            c.commit()
            _READY[0] = db
    return c


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def uid_of(username):
    """用户名 -> 稳定的账号 uid（与大小写无关，且只含 identity 允许的字符）。"""
    key = (username or "").strip().lower().encode("utf-8")
    return "acct_" + hashlib.sha1(key).hexdigest()[:14]


def _hash(password, salt_hex):
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                               bytes.fromhex(salt_hex), ITERS).hex()


def _clean(username, password):
    u = (username or "").strip()
    p = password or ""
    if not RE_USER.match(u):
        return None, None, "用户名 2–24 位，只能用字母、数字、下划线、横线或中文"
    if len(p) < MIN_PW:
        return None, None, "密码至少 %d 位" % MIN_PW
    if len(p) > MAX_PW:
        return None, None, "密码最多 %d 位" % MAX_PW
    return u, p, ""


def _too_many(username):
    now = time.time()
    hits = [t for t in _FAILS.get(username.lower(), []) if now - t < FAIL_WINDOW]
    _FAILS[username.lower()] = hits
    return len(hits) >= FAIL_MAX


def _mark_fail(username):
    _FAILS.setdefault(username.lower(), []).append(time.time())


def register(username, password):
    """注册。返回 (ok, message, uid, username)。"""
    u, p, err = _clean(username, password)
    if err:
        return False, err, "", ""
    uid = uid_of(u)
    salt = secrets.token_hex(16)
    pw = _hash(p, salt)
    c = _conn()
    try:
        c.execute("INSERT INTO accounts (uid, username, pw_hash, salt, created_at, last_login)"
                  " VALUES (?,?,?,?,?,?)", (uid, u, pw, salt, _now(), _now()))
        c.commit()
    except sqlite3.IntegrityError:
        return False, "这个用户名已被注册，换一个，或直接登录", "", ""
    finally:
        c.close()
    return True, "", uid, u


def login(username, password):
    """登录。返回 (ok, message, uid, username)。"""
    u = (username or "").strip()
    p = password or ""
    if not u or not p:
        return False, "请输入用户名和密码", "", ""
    if _too_many(u):
        return False, "尝试次数过多，请 10 分钟后再试", "", ""

    c = _conn()
    try:
        row = c.execute("SELECT * FROM accounts WHERE username = ?", (u,)).fetchone()
        if not row:
            _mark_fail(u)
            return False, "用户名或密码不对", "", ""
        if not hmac.compare_digest(row["pw_hash"], _hash(p, row["salt"])):
            _mark_fail(u)
            return False, "用户名或密码不对", "", ""
        c.execute("UPDATE accounts SET last_login = ? WHERE uid = ?", (_now(), row["uid"]))
        c.commit()
        _FAILS.pop(u.lower(), None)
        return True, "", row["uid"], row["username"]
    finally:
        c.close()


def by_uid(uid):
    if not uid:
        return None
    c = _conn()
    try:
        row = c.execute("SELECT uid, username, created_at, last_login FROM accounts"
                        " WHERE uid = ?", (uid,)).fetchone()
        return dict(row) if row else None
    finally:
        c.close()


def exists(username):
    c = _conn()
    try:
        return c.execute("SELECT 1 FROM accounts WHERE username = ?",
                         ((username or "").strip(),)).fetchone() is not None
    finally:
        c.close()


def count():
    c = _conn()
    try:
        return c.execute("SELECT COUNT(*) AS n FROM accounts").fetchone()["n"]
    finally:
        c.close()
