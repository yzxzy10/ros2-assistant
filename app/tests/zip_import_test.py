# -*- coding: utf-8 -*-
"""端到端验证：把 zip 当项目上传（模拟前端拖拽），检查识别与架构图。

用法：python tests/zip_import_test.py
前置：本地服务已在 8125 端口运行
"""
import base64
import http.cookiejar
import json
import os
import random
import string
import sys
import urllib.request

BASE = os.environ.get("BASE") or "http://127.0.0.1:8125"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZIP = os.path.join(ROOT, "samples", "demo_ros2_ws.zip")

# 绕过系统代理：本机服务走代理会 403
opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
)


def call(path, payload=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers)
    try:
        with opener.open(req, timeout=120) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode("utf-8"))


def main():
    st, ident = call("/api/identity")
    print("identity:", st, ident.get("user_id"))
    if st != 200:
        print("拿不到身份，服务没起来？")
        return 1

    sid = "ziptest" + "".join(random.choice(string.ascii_lowercase) for _ in range(6))
    raw = open(ZIP, "rb").read()
    st, res = call("/api/project/import", {
        "sessionId": sid,
        "files": [{"path": "demo_ros2_ws.zip",
                   "content": base64.b64encode(raw).decode("ascii")}],
    })
    print("import:", st, "ok=", res.get("ok"))
    if not res.get("ok"):
        print("  失败原因:", res.get("message"))
        return 1
    print("  摘要首行:", (res.get("message") or "").splitlines()[0])
    print("  ---- 摘要全文 ----")
    for line in (res.get("message") or "").splitlines():
        print("  |", line)
    print("  ----------------")
    ip = res.get("imported_project") or {}
    print("  包数量:", ip.get("package_count"), " 节点数量:", ip.get("node_count"))
    print("  展开的压缩包数:", ip.get("zipped"))
    print("  落盘文件数:", ip.get("file_count"))

    st, g = call("/api/graph?sid=" + sid)
    print("graph:", st, "nodes=", len(g.get("nodes", [])),
          "edges=", len(g.get("edges", [])))
    for e in g.get("edges", []):
        print("   ", e["from"].split("::")[-1], "->", e["to"].split("::")[-1],
              "|", e["topic"], "|", e.get("type"))
    print("  warnings:", g.get("warnings"))
    print("  stats:", g.get("stats"))

    ok = (len(g.get("nodes", [])) == 4 and len(g.get("edges", [])) == 3)
    print("\n结果:", "通过" if ok else "不通过")
    print("会话 sid:", sid)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
