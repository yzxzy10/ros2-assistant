# -*- coding: utf-8 -*-
"""端到端验证：改话题 → 提交 → 代码里真的改了。

用法：python tests/commit_test.py
前置：本地服务已在 8125 端口运行
"""
import base64
import http.cookiejar
import json
import os
import random
import string
import sys
import time
import urllib.request

BASE = os.environ.get("BASE") or "http://127.0.0.1:8125"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZIP = os.path.join(ROOT, "samples", "demo_ros2_ws.zip")

opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
)


def call(path, payload=None, method=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers,
                                 method=method)
    try:
        with opener.open(req, timeout=180) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read().decode("utf-8"))
        except Exception:
            return e.code, {}


def main():
    st, ident = call("/api/identity")
    print("identity:", st, ident.get("user_id"))

    sid = "cmt" + "".join(random.choice(string.ascii_lowercase) for _ in range(6))
    raw = open(ZIP, "rb").read()
    st, res = call("/api/project/import", {
        "sessionId": sid,
        "files": [{"path": "demo_ros2_ws.zip",
                   "content": base64.b64encode(raw).decode("ascii")}],
    })
    print("import:", st, res.get("ok"))

    st, g = call("/api/graph?sid=" + sid)
    print("graph:", st, "nodes", len(g.get("nodes", [])), "edges", len(g.get("edges", [])))
    topics = [e["topic"] for e in g.get("edges", [])]
    print("现有话题:", topics)
    old = "/detections" if "/detections" in topics else topics[0]
    new = old + "_renamed"

    # 1) 存草稿
    st, d = call("/api/graph/draft", {
        "sessionId": sid,
        "draft": {"renames": {old: new}},
    })
    print("draft:", st, d)

    st, g2 = call("/api/graph?sid=" + sid)
    print("draft 后 pending:", (g2.get("stats") or {}).get("pending"),
          "| 话题:", [t["name"] for t in g2.get("topics", [])])

    # 2) 提交
    t0 = time.time()
    st, c = call("/api/graph/commit", {"sessionId": sid})
    dt = time.time() - t0
    print("commit: %d  %.2fs" % (st, dt))
    print("  ok:", c.get("ok"))
    print("  changes:", json.dumps(c.get("changes"), ensure_ascii=False))
    print("  errors:", c.get("errors"))

    # 3) 直接读磁盘确认代码真的改了（数据目录由服务端决定）
    data_dir = os.environ.get("ROS2_DATA_DIR") or os.path.join(ROOT, "data")
    ws = os.path.join(data_dir, "sessions", sid, "ros2_ws")
    remain, total = 0, 0
    for dirpath, _dirs, names in os.walk(ws):
        for n in names:
            if not n.endswith(".py"):
                continue
            total += 1
            txt = open(os.path.join(dirpath, n), encoding="utf-8", errors="replace").read()
            if old in txt:
                remain += 1
                print("  仍含旧名:", os.path.relpath(os.path.join(dirpath, n), ws))
            if new in txt:
                print("  已改成新名:", os.path.relpath(os.path.join(dirpath, n), ws))
    print("py 文件数:", total, "| 仍含旧话题名:", remain)

    ok = bool(c.get("ok")) and bool(c.get("changes")) and remain == 0
    print("\n结果:", "通过" if ok else "不通过")
    print("会话 sid:", sid)
    return 0 if ok else 1


if __name__ == "__main__":
    import urllib.parse  # noqa: F401
    sys.exit(main())
