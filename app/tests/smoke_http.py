# -*- coding: utf-8 -*-
"""服务冒烟：在进程内起 HTTP 服务，走一遍「鉴权 + 真实模型」全链路。

    python -X utf8 tests/smoke_http.py

会真实调用一次大模型（qwen-max 直答，几秒），用来确认环境变量里的密钥可用。
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

os.environ["no_proxy"] = "127.0.0.1,localhost"        # 本机代理会把回环请求打歪
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

from app import auth, env, paths                        # noqa: E402
import server                                           # noqa: E402

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
COOKIE = [None]


def req(port, path, headers=None, body=None, use_cookie=True):
    h = dict(headers or {})
    if use_cookie and COOKIE[0]:
        h["Cookie"] = "ros2_id=" + COOKIE[0]
    data = json.dumps(body).encode("utf-8") if body is not None else None
    r = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                               data=data, headers=h)
    try:
        with OPENER.open(r, timeout=180) as resp:
            sc = resp.headers.get("Set-Cookie") or ""
            if "ros2_id=" in sc:
                COOKIE[0] = sc.split("ros2_id=", 1)[1].split(";")[0].strip()
            raw = resp.read()
            return resp.status, (json.loads(raw.decode("utf-8") or "{}")
                                 if raw[:1] in (b"{", b"[") else raw)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            return e.code, json.loads(raw.decode("utf-8") or "{}")
        except Exception:
            return e.code, {"_raw": raw[:80]}


def main():
    env.load()
    paths.ensure_all()
    token = auth.extract({}, {})
    table = auth.tokens()
    any_token = next(iter(table), "") if table else ""

    srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    print("临时端口: %d | 鉴权: %s | %s" % (port, auth.describe(), paths.describe()))

    st, body = req(port, "/api/identity", use_cookie=False)
    my_uid = body.get("user_id", "")
    print("identity        -> %s uid=%s" % (st, my_uid))

    st, body = req(port, "/api/health")
    print("health          -> %s %s" % (st, json.dumps(body, ensure_ascii=False)))

    st, body = req(port, "/")
    print("index.html      -> %s (%s bytes)" % (st, len(body) if isinstance(body, bytes) else "json"))

    hdr = {"X-Auth-Token": any_token} if any_token else {}
    st, body = req(port, "/api/profile?uid=u_someone_else", hdr)   # 故意传别人的 uid
    print("画像（传他人uid）-> %s user_id=%s（应为自己的）" % (st, body.get("user_id")))

    if not server.api_configured(server.current_config()):
        print("\n[跳过模型调用] 未配置密钥")
        srv.shutdown()
        return 0

    print("\n发起一次真实对话（qwen-max，可能需要 5-20s）…")
    st, body = req(port, "/api/chat",
                   dict(hdr, **{"Content-Type": "application/json"}),
                   {"sessionId": "smoke01", "message": "用一句话说明话题(topic)和服务的区别",
                    "userId": "u_someone_else"})
    reply = (body.get("reply") or body.get("error") or "") if isinstance(body, dict) else ""
    print("chat            -> %s stages=%s" % (st, body.get("stages") if isinstance(body, dict) else ""))
    print("回复片段        -> %s" % reply[:120].replace("\n", " "))

    bad = st != 200 or not reply or ("api key" in reply.lower() or "401" in reply)
    print("\n模型调用:", "失败" if bad else "成功")

    # 收尾：删掉冒烟产生的画像与会话
    try:
        from app import store
        store.delete(my_uid)
        import shutil
        shutil.rmtree(os.path.join(paths.sessions(), "smoke01"), ignore_errors=True)
    except Exception:
        pass
    srv.shutdown()
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
