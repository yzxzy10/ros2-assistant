# -*- coding: utf-8 -*-
"""线上真机检查：在真实部署的页面里导入项目 → 点「编译运行」→ 截图。

不 mock、不走本地服务，页面里发的每个请求都打到 ros2-tutor 线上环境。
用法：python tests/live_ui_check.py [项目目录] [输出png]
"""
import base64
import json
import os
import sys
import time
import urllib.parse
import urllib.request

import websocket

BASE = "https://ros2-tutor-34167.app.workbuddy.host/?sharecode=OGI6JJJSMW"
DBG = "http://127.0.0.1:9444"
SRC = sys.argv[1] if len(sys.argv) > 1 else r"C:/Users/mike/Desktop/demo_ros2_ws"
OUT = sys.argv[2] if len(sys.argv) > 2 else \
    r"C:/Users/mike/WorkBuddy/2026-11-05-09-34-34/out/线上-真实失败诊断.png"


class CDP:
    def __init__(self, url):
        self.ws = websocket.create_connection(url, timeout=90, suppress_origin=True)
        self.n = 0

    def send(self, method, params=None):
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                return msg.get("result", {})

    def js(self, expr):
        r = self.send("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                           "awaitPromise": True, "userGesture": True})
        return r.get("result", {}).get("value")


def main():
    files = []
    for dp, dn, fn in os.walk(SRC):
        for f in fn:
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, SRC).replace("\\", "/")
            try:
                files.append({"path": rel, "content": open(p, encoding="utf-8").read()})
            except Exception:
                pass
    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(DBG + "/json/new?" + urllib.parse.quote(BASE, safe=":/?=&"),
                                 method="PUT")
    tab = json.loads(op.open(req, timeout=20).read().decode())
    c = CDP(tab["webSocketDebuggerUrl"])
    c.send("Page.enable")
    c.send("Runtime.enable")
    c.send("Emulation.setDeviceMetricsOverride", {"width": 1560, "height": 940,
                                                  "deviceScaleFactor": 1, "mobile": False})
    for _ in range(40):
        time.sleep(0.5)
        if c.js("!!document.getElementById('gp-verify')"):
            break
    sid = c.js("localStorage.getItem('ros2tutor_sid')")
    print("页面 sid:", sid)

    payload = json.dumps({"sessionId": sid, "files": files}, ensure_ascii=False)
    res = c.js("fetch('/api/project/import',{method:'POST',headers:{'Content-Type':"
               "'application/json'},body:%s}).then(r=>r.status)" % json.dumps(payload))
    print("导入:", res, "| 文件数:", len(files))

    c.send("Page.reload", {"ignoreCache": True})
    for _ in range(40):
        time.sleep(0.5)
        if (c.js("document.querySelectorAll('#gNode [data-node]').length") or 0) >= 2:
            break
    time.sleep(0.8)
    c.js("document.getElementById('gp-verify').click()")
    print("已点「编译运行」")

    t0 = time.time()
    while time.time() - t0 < 150:
        time.sleep(1.5)
        if c.js("!document.getElementById('vy-result').classList.contains('hidden')"):
            break
    time.sleep(1.2)
    print("结论:", c.js("document.getElementById('vy-result-head').textContent"))
    print("正文:", c.js("document.getElementById('vy-result-body').textContent"))
    print("诊断:", c.js("(document.getElementById('vy-result-hint')||{}).textContent"))
    print("橙/绿线:", c.js("document.querySelectorAll('.ge-path.vy-ok').length"),
          "/", c.js("document.querySelectorAll('.ge-path.vy-fail').length"))
    print("下位机:", c.js("!!document.getElementById('vy-mcu')"))
    detail = c.js("fetch('/api/graph/verify/status?sid=%s').then(r=>r.json())"
                  ".then(d=>JSON.stringify({reason:d.reason,hint:d.hint,"
                  "nodes:(d.nodes||[]).map(n=>[n.name,n.result,n.rc,"
                  "(n.log||'').slice(-150)])}))" % sid)
    print("节点明细:", detail)
    r = c.send("Page.captureScreenshot", {"format": "png"})
    open(OUT, "wb").write(base64.b64decode(r["data"]))
    print("saved:", OUT)


if __name__ == "__main__":
    main()
