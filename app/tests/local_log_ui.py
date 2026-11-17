# -*- coding: utf-8 -*-
"""本地日志框验收：折叠 → 展开（折行）→ 全文面板，逐张截图。

用法：python tests/local_log_ui.py [端口] [项目目录]
要求：本机已有 Chromium 调试端口 9444（或按 DBG 改）。
"""
import base64
import json
import os
import sys
import time
import urllib.parse
import urllib.request

import websocket

PORT = sys.argv[1] if len(sys.argv) > 1 else "8141"
SRC = sys.argv[2] if len(sys.argv) > 2 else r"C:/Users/mike/Desktop/demo_ros2_ws"
OUTDIR = r"C:/Users/mike/WorkBuddy/2026-11-05-09-34-34/out"
BASE = "http://127.0.0.1:%s/" % PORT
DBG = "http://127.0.0.1:9444"


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

    def shot_clip(self, name, rect, scale=3):
        clip = {"x": rect["x"], "y": rect["y"], "width": rect["width"],
                "height": rect["height"], "scale": scale}
        r = self.send("Page.captureScreenshot", {"format": "png", "clip": clip})
        p = os.path.join(OUTDIR, name)
        open(p, "wb").write(base64.b64decode(r["data"]))
        print("  saved(clip):", p)
        return p

    def shot(self, name):
        r = self.send("Page.captureScreenshot", {"format": "png"})
        p = os.path.join(OUTDIR, name)
        open(p, "wb").write(base64.b64decode(r["data"]))
        print("  saved:", p)
        return p


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
    sid = c.js("localStorage.getItem('ros2tutor_sid')")
    print("sid:", sid, "| 文件数:", len(files))

    payload = json.dumps({"sessionId": sid, "files": files}, ensure_ascii=False)
    print("导入:", c.js("fetch('/api/project/import',{method:'POST',headers:{'Content-Type':"
                        "'application/json'},body:%s}).then(r=>r.status)" % json.dumps(payload)))

    c.send("Page.reload", {"ignoreCache": True})
    for _ in range(40):
        time.sleep(0.5)
        if (c.js("document.querySelectorAll('#gNode [data-node]').length") or 0) >= 2:
            break
    time.sleep(0.8)
    c.js("document.getElementById('gp-verify').click()")
    print("已点「编译运行」")

    t0 = time.time()
    while time.time() - t0 < 120:
        time.sleep(1.2)
        if c.js("!document.getElementById('vy-result').classList.contains('hidden')"):
            break
    time.sleep(1.0)
    print("结论:", c.js("document.getElementById('vy-result-head').textContent"))
    lid = c.js("document.querySelector('.vy-log').getAttribute('data-log')")
    q = '[data-log="' + lid + '"]'
    print("日志框 id:", lid)
    print("折叠态行:", c.js("Array.from(document.querySelectorAll('%s .vy-log-t tspan'))"
                            ".map(t=>t.textContent)" % q))
    c.shot("日志-1折叠.png")

    c.js("document.querySelector('%s').dispatchEvent(new MouseEvent('click',{bubbles:true}))" % q)
    time.sleep(0.6)
    print("展开态行:", c.js("Array.from(document.querySelectorAll('%s .vy-log-t tspan'))"
                            ".map(t=>t.textContent)" % q))
    print("展开框宽高:", c.js("(function(){var r=document.querySelector('%s .vy-log-box');"
                              "return r?[r.getAttribute('width'),r.getAttribute('height')]:null})()" % q))
    c.shot("日志-2展开.png")
    rect = c.js("(function(){var g=document.querySelector('%s');var r=g.getBoundingClientRect();"
                "return {x:r.x-24,y:r.y-24,width:r.width+48,height:r.height+48}})()" % q)
    if rect:
        c.shot_clip("日志-2展开-特写.png", rect)

    c.js("document.querySelector('%s .vy-log-more').dispatchEvent("
         "new MouseEvent('click',{bubbles:true}))" % q)
    time.sleep(0.6)
    print("全文面板:", c.js("!!document.querySelector('.vy-logpanel')"))
    print("面板行数:", c.js("(document.querySelector('.vy-logpanel .vlp-body')||{textContent:''})"
                            ".textContent.split('\\n').length"))
    print("面板首行:", c.js("(document.querySelector('.vy-logpanel .vlp-body')||{textContent:''})"
                            ".textContent.split('\\n')[0]"))
    c.shot("日志-3全文.png")

    # 验证态锁定：点节点不该开抽屉；退出后才能编辑
    c.js("document.querySelector('.vy-logpanel .vlp-x').click()")
    c.js("var b=document.querySelector('[data-node]');if(b)b.dispatchEvent("
         "new MouseEvent('click',{bubbles:true}))")
    time.sleep(0.4)
    print("验证中画布锁定:", c.js("document.getElementById('gp-canvas').classList.contains('vy-locked')"))
    print("验证中点节点开抽屉了吗:", c.js("!document.getElementById('gp-drawer')"
                                            ".classList.contains('hidden')"))
    print("提示:", c.js("document.getElementById('gp-hint').textContent"))
    c.shot("日志-4锁定.png")
    c.js("document.getElementById('gp-verify').click()")
    time.sleep(1.2)
    c.js("var b=document.querySelector('[data-node]');if(b)b.dispatchEvent("
         "new MouseEvent('click',{bubbles:true}))")
    time.sleep(0.6)
    print("退出后开抽屉:", c.js("!document.getElementById('gp-drawer').classList.contains('hidden')"))


if __name__ == "__main__":
    main()
