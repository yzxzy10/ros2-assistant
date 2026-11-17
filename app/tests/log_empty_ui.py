# -*- coding: utf-8 -*-
"""空日志回归：节点一行日志都没有时，验证结束后不能停在「日志收集中」，
且日志文字必须完整落在框内（不压住或溢出边框）。

用法：python tests/log_empty_ui.py [端口]
前置：该端口以 ROS2_VERIFY_BACKEND=mock ROS2_CHAIN_MOCK_NOLOG=proc_node 启动。
"""
import base64
import json
import os
import sys
import time
import urllib.parse
import urllib.request

import websocket

SRC = sys.argv[1] if len(sys.argv) > 1 else r"C:/Users/mike/Desktop/demo_ros2_ws"
OUTDIR = r"C:/Users/mike/WorkBuddy/2026-11-05-09-34-34/out"
SID = "nolog%d" % (int(time.time()) % 100000)
# 默认打本地；传 ROS2_TEST_BASE 就打线上（形如 https://host/?sharecode=xxx）
HOST = os.environ.get("ROS2_TEST_BASE", "http://127.0.0.1:8145/")
BASE = HOST + ("&" if "?" in HOST else "?") + "sid=" + SID
EXPECT_EMPTY = os.environ.get("ROS2_EXPECT_EMPTY", "") == "1"
print("目标:", BASE)
DBG = "http://127.0.0.1:9444"


class CDP(object):
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

    def shot(self, name):
        r = self.send("Page.captureScreenshot", {"format": "png"})
        p = os.path.join(OUTDIR, name)
        open(p, "wb").write(base64.b64decode(r["data"]))
        print("  saved:", p)

    def shot_clip(self, name, rect, scale=3):
        clip = {"x": rect["x"], "y": rect["y"], "width": rect["width"],
                "height": rect["height"], "scale": scale}
        r = self.send("Page.captureScreenshot", {"format": "png", "clip": clip})
        p = os.path.join(OUTDIR, name)
        open(p, "wb").write(base64.b64decode(r["data"]))
        print("  saved(clip):", p)


def collect(src):
    files = []
    for dp, dn, fn in os.walk(src):
        for f in fn:
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, src).replace("\\", "/")
            try:
                files.append({"path": rel, "content": open(p, encoding="utf-8").read()})
            except Exception:
                pass
    return files


BOXES = """
(() => Array.from(document.querySelectorAll('.vy-log')).map(g => {
  const r = g.querySelector('.vy-log-box');
  const t = g.querySelector('.vy-log-t');
  const ry = +r.getAttribute('y'), rh = +r.getAttribute('height');
  const rx = +r.getAttribute('x'), rw = +r.getAttribute('width');
  const b = t.getBBox();               // 用户坐标，不受画布缩放影响
  return {
    id: g.getAttribute('data-log'),
    text: Array.from(g.querySelectorAll('.vy-log-t tspan')).map(s => s.textContent).join(''),
    box: [Math.round(ry), Math.round(ry + rh)],
    txt: [Math.round(b.y), Math.round(b.y + b.height)],
    padTop: Math.round(b.y - ry),
    padBottom: Math.round(ry + rh - (b.y + b.height)),
    padLeft: Math.round(b.x - rx),
    padRight: Math.round(rx + rw - (b.x + b.width)),
    inside: b.y >= ry + 1 && b.y + b.height <= ry + rh - 1
            && b.x >= rx + 1 && b.x + b.width <= rx + rw - 1
  };
}))()
"""


def main():
    os.makedirs(OUTDIR, exist_ok=True)
    files = collect(SRC)
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
    print("sid:", c.js("localStorage.getItem('ros2tutor_sid')"), "| 文件数:", len(files))
    payload = json.dumps({"sessionId": SID, "files": files}, ensure_ascii=False)
    print("导入:", c.js("fetch('/api/project/import',{method:'POST',headers:{'Content-Type':"
                        "'application/json'},body:%s}).then(r=>r.status)" % json.dumps(payload)))
    c.send("Page.reload", {"ignoreCache": True})
    for _ in range(40):
        time.sleep(0.5)
        if c.js("document.querySelectorAll('#gNode [data-node]').length"):
            break
    print("节点数:", c.js("document.querySelectorAll('#gNode [data-node]').length"))

    c.js("document.getElementById('gp-verify').click()")
    t0 = time.time()
    while time.time() - t0 < 150:
        time.sleep(0.4)
        if not c.js("document.getElementById('vy-result').classList.contains('hidden')"):
            break
    print("用时: %.1fs" % (time.time() - t0))
    print("结论:", c.js("(document.getElementById('vy-result-head')||{}).textContent"))

    time.sleep(1.0)
    boxes = c.js(BOXES) or []
    for b in boxes:
        print("  %-32s 上=%2d 下=%2d 左=%2d 右=%2d 在内=%s | %s"
              % (b["id"].split("/")[-1], b["padTop"], b["padBottom"],
                 b["padLeft"], b["padRight"], b["inside"], b["text"][:44]))

    stuck = [b for b in boxes if "日志收集中" in b["text"]]
    empty_ok = [b for b in boxes if "没输出" in b["text"]]
    outside = [b for b in boxes if not b["inside"]]
    lowpad = [b for b in boxes if b["padTop"] < 3 or b["padBottom"] < 2]

    print("\n结束仍显示收集中:", len(stuck), "| 正确提示无日志:", len(empty_ok),
          "| 越界:", len(outside), "| 上/下留白不足:", len(lowpad))
    if boxes:
        c.shot("空日志-全图.png")
        try:
            r = c.js("(function(){var g=document.querySelectorAll('.vy-log')[0];"
                     "var b=g.getBoundingClientRect();"
                     "return {x:b.x-20,y:b.y-16,width:b.width+40,height:b.height+32}})()")
            if r:
                c.shot_clip("空日志-特写.png", r)
        except Exception:
            pass

    ok = (len(boxes) >= 2 and not stuck and not outside and not lowpad
          and (len(empty_ok) >= 1 if EXPECT_EMPTY else True))
    print("\n结果:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
