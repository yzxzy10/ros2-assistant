# -*- coding: utf-8 -*-
"""CDP 验证：把节点拖远后，连线标签是否仍然贴在线身上。

对应用户截图里的场景：节点被拉开，连线绕成长折线，
标签原来按「两个节点的几何中点」算，结果落在空白处。
"""
import base64
import json
import os
import sys
import time

import websocket
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ZIP = os.path.join(ROOT, "samples", "demo_ros2_ws.zip")
OUT = r"C:\Users\mike\WorkBuddy\2026-11-05-09-34-34\out"
BASE = "http://127.0.0.1:8125"
DBG = "http://127.0.0.1:9444"
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def new_target(url):
    req = urllib.request.Request(DBG + "/json/new?" + urllib.parse.quote(url, safe=":/?=&"),
                                method="PUT")
    with opener.open(req, timeout=10) as r:
        return json.loads(r.read().decode())


class CDP(object):
    def __init__(self, ws_url):
        self.ws = websocket.create_connection(ws_url, timeout=40, suppress_origin=True)
        self.n = 0

    def send(self, method, params=None):
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError("%s -> %s" % (method, msg["error"]))
                return msg.get("result", {})

    def js(self, expr):
        r = self.send("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                           "awaitPromise": True, "userGesture": True})
        if "exceptionDetails" in r:
            print("  JS 异常:", json.dumps(r["exceptionDetails"])[:200])
        return r.get("result", {}).get("value")

    def shot(self, path):
        r = self.send("Page.captureScreenshot", {"format": "png"})
        data = base64.b64decode(r["data"])
        open(path, "wb").write(data)
        return len(data)

    def drag(self, x1, y1, x2, y2, steps=18):
        self.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x1, "y": y1,
                                               "button": "left", "clickCount": 1, "buttons": 1})
        for i in range(1, steps + 1):
            self.send("Input.dispatchMouseEvent", {
                "type": "mouseMoved",
                "x": x1 + (x2 - x1) * i / steps, "y": y1 + (y2 - y1) * i / steps,
                "button": "left", "buttons": 1})
            time.sleep(0.02)
        self.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x2, "y": y2,
                                               "button": "left", "buttons": 0, "clickCount": 1})


def main():
    os.makedirs(OUT, exist_ok=True)
    t = new_target(BASE + "/")
    cdp = CDP(t["webSocketDebuggerUrl"])
    cdp.send("Page.enable")
    cdp.send("Runtime.enable")
    cdp.send("Emulation.setDeviceMetricsOverride", {
        "width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
    time.sleep(2.5)
    cdp.js("try{localStorage.removeItem('rt_gp_split')}catch(e){}")
    cdp.js("try{localStorage.setItem('rt_gp_split','980')}catch(e){}")
    cdp.send("Page.reload")
    time.sleep(3)

    b64 = base64.b64encode(open(ZIP, "rb").read()).decode("ascii")
    print("导入:", cdp.js("""
(async () => {
  const b64 = "%s";
  const sid = localStorage.getItem('ros2tutor_sid');
  const r = await fetch('/api/project/import', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({sessionId: sid, files: [{path: 'demo_ros2_ws.zip', content: b64}]})
  });
  const d = await r.json();
  if (window.RTGraph) window.RTGraph.reload();
  return d.ok;
})()""" % b64))
    time.sleep(3)
    # 抽屉关掉，画布宽一点（注意别点 gp-collapse，那会把整个画布收掉）
    cdp.js("""(() => {
      const d = document.querySelector('.gp-drawer');
      const x = d && d.querySelector('.gd-close');
      if (x) x.click();
      return true;
    })()""")
    time.sleep(0.6)

    # 把某个节点拖到远处，制造长折线
    pos = cdp.js("""(() => {
      const gs = [...document.querySelectorAll('[data-node]')];
      const g = gs[gs.length - 1];
      const r = g.getBoundingClientRect();
      return {x: Math.round(r.left + r.width / 2), y: Math.round(r.top + r.height / 2),
              name: g.getAttribute('data-node')};
    })()""")
    print("拖动节点:", pos["name"])
    cdp.drag(pos["x"], pos["y"], pos["x"] + 300, pos["y"] + 210)
    time.sleep(1.2)
    cdp.shot(os.path.join(OUT, "标签-拖远后未悬停.png"))

    # 悬停最长的那条线
    info = cdp.js("""(() => {
      const hits = [...document.querySelectorAll('.ge-hit')];
      let best = null;
      hits.forEach((h, i) => {
        h.dispatchEvent(new PointerEvent('pointerenter', {bubbles: true}));
        const lab = [...document.querySelectorAll('#gLabel > g')]
          .find(x => x.getAttribute('data-edge') === String(i));
        if (!lab) return;
        const rect = lab.querySelector('rect');
        const cx = +rect.getAttribute('x') + (+rect.getAttribute('width')) / 2;
        const cy = +rect.getAttribute('y') + 9;
        const p = h.parentNode.querySelector('.ge-path');
        const L = p.getTotalLength();
        let d = 1e9;
        for (let k = 0; k <= 240; k++) {
          const q = p.getPointAtLength(L * k / 240);
          const dd = Math.hypot(q.x - cx, q.y - cy);
          if (dd < d) d = dd;
        }
        const mid = p.getPointAtLength(L * 0.5);
        // 旧的错误算法：两个节点框的几何中点
        const nodes = [...document.querySelectorAll('[data-node]')];
        const gs = h.closest('#gEdge').children;
        if (!best || L > best.len) {
          best = {i: i, len: +L.toFixed(0), distOnPath: +d.toFixed(2),
                  label: [+cx.toFixed(0), +cy.toFixed(0)],
                  pathMid: [+mid.x.toFixed(0), +mid.y.toFixed(0)],
                  text: lab.querySelector('.ge-label').textContent};
        }
        h.dispatchEvent(new PointerEvent('pointerleave', {bubbles: true}));
      });
      return best;
    })()""")
    print("最长连线:", info)

    # 只悬停最长那条，截图
    cdp.js("""(() => {
      const h = [...document.querySelectorAll('.ge-hit')][%d];
      const r = h.getBoundingClientRect();
      h.dispatchEvent(new PointerEvent('pointerenter', {bubbles: true,
        clientX: r.left + r.width/2, clientY: r.top + r.height/2}));
      return true;
    })()""" % info["i"])
    time.sleep(0.7)
    cdp.shot(os.path.join(OUT, "标签-悬停长连线.png"))

    ok = info["distOnPath"] <= 12
    print("\n标签到线的最短距离: %.2f px（≤12 视为贴在线身上）" % info["distOnPath"])
    print("结果:", "通过" if ok else "不通过")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
