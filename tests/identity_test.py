# -*- coding: utf-8 -*-
"""匿名身份与限流验证：公开分享时「各看各的、改 uid 无效、刷不快」。

    python -X utf8 tests/identity_test.py
"""
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

PASS, FAIL = [], []


def ok(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    line = "  [%s] %s" % ("PASS" if cond else "FAIL", name)
    if not cond and extra != "":
        line += "   -> %r" % (extra,)
    print(line)


from app import env, identity, ratelimit       # noqa: E402
import server                                   # noqa: E402

env.load()
ratelimit.reset()
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Client(object):
    """最小 HTTP 客户端：自己管 Cookie，方便伪造测试。"""

    def __init__(self, port):
        self.port = port
        self.cookie = None

    def call(self, path, headers=None, body=None, use_cookie=True):
        h = dict(headers or {})
        if use_cookie and self.cookie:
            h["Cookie"] = identity.COOKIE + "=" + self.cookie
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            "http://127.0.0.1:%d%s" % (self.port, path), data=data, headers=h)
        try:
            with opener.open(req, timeout=15) as r:
                self._eat(r.headers)
                raw = r.read()
                return r.status, self._parse(raw)
        except urllib.error.HTTPError as e:
            self._eat(e.headers)
            return e.code, self._parse(e.read())

    def _eat(self, headers):
        sc = headers.get("Set-Cookie") or ""
        if identity.COOKIE + "=" in sc:
            self.cookie = sc.split(identity.COOKIE + "=", 1)[1].split(";")[0].strip()

    @staticmethod
    def _parse(raw):
        try:
            return json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return {"_raw": True}


def main():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("临时端口 %d | %s\n" % (port, identity.describe()))

    # ---------- 1. 身份签发 ----------
    print("1. 身份签发与持久")
    alice = Client(port)
    st, body = alice.call("/api/identity", use_cookie=False)
    a_uid = body.get("user_id", "")
    ok("首次访问签发新身份", st == 200 and body.get("fresh") is True and a_uid.startswith("u"), body)
    ok("响应下发 HttpOnly Cookie", bool(alice.cookie) and "." in alice.cookie, alice.cookie)

    st, body = alice.call("/api/identity")
    ok("再次访问沿用同一身份", st == 200 and body.get("fresh") is False and body.get("user_id") == a_uid, body)

    # ---------- 2. 没有身份不能访问 ----------
    print("\n2. 没有身份就进不去")
    anon = Client(port)
    st, body = anon.call("/api/profile")
    ok("无 Cookie 访问画像 -> 401", st == 401 and body.get("need_identity") is True, (st, body))
    st, body = anon.call("/api/chat", {"Content-Type": "application/json"},
                         {"sessionId": "anon0001", "message": "hi"})
    ok("无 Cookie 发消息 -> 401", st == 401, (st, body))

    # ---------- 3. 伪造身份无效 ----------
    print("\n3. 改 uid / 改签名都没用")
    victim = Client(port)
    st, vbody = victim.call("/api/identity", use_cookie=False)
    v_uid = vbody.get("user_id", "")

    forged = Client(port)
    forged.cookie = v_uid + "." + "0" * 32                 # 用户名对了，签名是假的
    st, body = forged.call("/api/profile")
    ok("签名错误的 Cookie -> 401", st == 401, (st, body))

    bad = Client(port)
    bad.cookie = "u_xxxxxx.deadbeef"
    st, body = bad.call("/api/profile")
    ok("乱写的 Cookie -> 401", st == 401, (st, body))

    # ---------- 4. 画像互相隔离 ----------
    print("\n4. 各看各的（同一浏览器会话内也隔离）")
    from app import store
    store.record_turn(a_uid, 3)
    store.add_concepts(a_uid, ["话题(topic)"])

    st, body = alice.call("/api/profile?uid=" + v_uid)     # 故意请求别人的 uid
    ok("请求别人的 uid 也只返回自己",
       st == 200 and body.get("user_id") == a_uid and (body.get("stats") or {}).get("turns") == 3,
       (st, body.get("user_id"), body.get("stats")))

    st, body = victim.call("/api/profile")
    ok("另一个身份看不到 A 的画像",
       st == 200 and body.get("user_id") == v_uid and (body.get("stats") or {}).get("turns") == 0,
       (st, body.get("user_id"), body.get("stats")))

    # ---------- 5. 会话归属 ----------
    print("\n5. 会话不能被别人接手")
    from app import orchestrator
    sess = orchestrator.get_session("sessid0001")
    sess.user_id = a_uid
    sess.save()

    st, body = alice.call("/api/session/sessid0001")
    ok("本人可读自己的会话", st == 200 and body.get("userId") == a_uid, (st, body.get("userId")))
    st, body = victim.call("/api/session/sessid0001")
    ok("别人读同一会话 -> 403", st == 403, (st, body))
    st, body = victim.call("/api/files/sessid0001/zip")
    ok("别人打包下载 -> 403", st == 403, (st, body))

    # ---------- 6. 限流 ----------
    print("\n6. 限流（防脚本刷爆）")
    os.environ["ROS2_RATE_API_PER_MIN"] = "5"
    ratelimit.reset()
    codes = []
    for _ in range(7):
        st, _b = alice.call("/api/profile")
        codes.append(st)
    ok("超过每分钟上限出现 429", 429 in codes and codes.count(200) == 5, codes)

    os.environ["ROS2_RATE_CHAT_PER_MIN"] = "2"
    ratelimit.reset()
    for _ in range(2):
        ratelimit.hit("chat:" + a_uid, 2)      # 先把对话额度用光（不真调模型）
    st, body = alice.call("/api/chat", {"Content-Type": "application/json"},
                          {"sessionId": "rate0001", "message": "x"})
    ok("对话接口单独限速 -> 429", st == 429, (st, body))

    st, body = alice.call("/api/identity")       # 身份接口不受限流影响，否则用户会被永久锁死
    ok("身份接口不被限流挡住", st == 200, (st, body))

    print("\n7. 每日总量保险丝")
    os.environ["ROS2_DAILY_CHAT_MAX"] = "2"
    ratelimit.reset()
    ratelimit.hit_daily()
    ratelimit.hit_daily()
    allowed, used, cap = ratelimit.hit_daily()
    ok("用满每日总量后拒绝", allowed is False and used == 2 and cap == 2, (allowed, used, cap))
    os.environ.pop("ROS2_DAILY_CHAT_MAX", None)
    ratelimit.reset()

    os.environ.pop("ROS2_RATE_API_PER_MIN", None)
    os.environ.pop("ROS2_RATE_CHAT_PER_MIN", None)

    # 清理
    store.delete(a_uid)
    store.delete(v_uid)
    store.delete("u_deadbeefdeadbeef")
    try:
        import shutil
        from app import paths
        shutil.rmtree(os.path.join(paths.sessions(), "sessid0001"), ignore_errors=True)
        shutil.rmtree(os.path.join(paths.sessions(), "rate0001"), ignore_errors=True)
    except Exception:
        pass
    srv.shutdown()

    print("\n" + "=" * 56)
    print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
    for n in FAIL:
        print("  失败: " + n)
    print("=" * 56)
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
