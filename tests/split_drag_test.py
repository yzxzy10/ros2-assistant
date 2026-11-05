# -*- coding: utf-8 -*-
"""CDP 验证：分屏分隔条拖动是否生效。

步骤：打开页面 → 页面内导入 zip → 截图 → 拖动分隔条 → 截图 → 读宽度。
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
        self.ws = websocket.create_connection(ws_url, timeout=30,
                                             suppress_origin=True)
        self.n = 0

    def send(self, method, params=None):
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": method,
                                 "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError("%s -> %s" % (method, msg["error"]))
                return msg.get("result", {})

    def js(self, expr):
        r = self.send("Runtime.evaluate", {
            "expression": expr, "returnByValue": True, "awaitPromise": True,
            "userGesture": True})
        return r.get("result", {}).get("value")

    def shot(self, path):
        r = self.send("Page.captureScreenshot", {"format": "png"})
        data = base64.b64decode(r["data"])
        with open(path, "wb") as f:
            f.write(data)
        return len(data)


def main():
    os.makedirs(OUT, exist_ok=True)
    t = new_target(BASE + "/")
    cdp = CDP(t["webSocketDebuggerUrl"])
    cdp.send("Page.enable")
    cdp.send("Runtime.enable")
    cdp.send("Emulation.setDeviceMetricsOverride", {
        "width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
    time.sleep(2.5)

    raw = open(ZIP, "rb").read()
    b64 = base64.b64encode(raw).decode("ascii")
    sid = cdp.js("localStorage.getItem('ros2tutor_sid')")
    print("页面会话 sid:", sid)

    res = cdp.js("""
(async () => {
  const b64 = "%s";
  const bin = atob(b64);
  const arr = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) arr[i] = bin.charCodeAt(i);
  const sid = localStorage.getItem('ros2tutor_sid');
  const r = await fetch('/api/project/import', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({sessionId: sid,
      files: [{path: 'demo_ros2_ws.zip', content: b64}]})
  });
  const d = await r.json();
  if (window.RTGraph) window.RTGraph.reload();
  return d.ok + ' | ' + (d.message || '').split('\\n')[0];
})()
""" % b64)
    print("导入结果:", res)
    time.sleep(2.5)

    geo = cdp.js("""(() => {
      const sp = document.getElementById('gp-split');
      const gp = document.getElementById('graph-pane');
      const cp = document.getElementById('chat-pane');
      if (!sp) return null;
      const r = sp.getBoundingClientRect();
      return {x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2),
              gw: Math.round(gp.getBoundingClientRect().width),
              cw: Math.round(cp.getBoundingClientRect().width),
              visible: getComputedStyle(sp).display !== 'none'};
    })()""")
    print("分隔条:", geo)
    if not geo or not geo.get("visible"):
        print("找不到分隔条")
        return 1

    n1 = cdp.shot(os.path.join(OUT, "分屏-拖动前.png"))
    print("截图1:", n1, "字节")

    sx, sy = geo["x"], geo["y"]
    # 往右拖 260px：分隔条在画布右侧，右移 = 画布变大
    cdp.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": sx, "y": sy,
                                          "button": "left", "clickCount": 1,
                                          "buttons": 1})
    for step in range(1, 14):
        cdp.send("Input.dispatchMouseEvent", {
            "type": "mouseMoved", "x": sx + step * 20, "y": sy,
            "button": "left", "buttons": 1})
        time.sleep(0.03)
    cdp.send("Input.dispatchMouseEvent", {"type": "mouseReleased",
                                          "x": sx + 260, "y": sy,
                                          "button": "left", "buttons": 0,
                                          "clickCount": 1})
    time.sleep(1.2)

    after = cdp.js("""(() => {
      const gp = document.getElementById('graph-pane');
      const cp = document.getElementById('chat-pane');
      return {gw: Math.round(gp.getBoundingClientRect().width),
              cw: Math.round(cp.getBoundingClientRect().width),
              inlineFlex: gp.style.flex,
              saved: localStorage.getItem('rt_gp_split'),
              resizing: document.body.classList.contains('gp-resizing')};
    })()""")
    print("拖动后:", after)
    n2 = cdp.shot(os.path.join(OUT, "分屏-拖动后.png"))
    print("截图2:", n2, "字节")

    ok = (after and after["gw"] > geo["gw"] + 150
          and after["saved"] and not after["resizing"])
    print("\n画布宽度: %d → %d" % (geo["gw"], after["gw"] if after else -1))
    print("结果:", "通过" if ok else "不通过")

    # 刷新一次，确认宽度被记住（这是最容易被布局时序搞坏的地方）
    cdp.send("Page.reload", {"ignoreCache": True})
    time.sleep(3.0)
    reloaded = cdp.js("""(() => {
      const gp = document.getElementById('graph-pane');
      return {gw: Math.round(gp.getBoundingClientRect().width),
              saved: localStorage.getItem('rt_gp_split')};
    })()""")
    print("刷新后:", reloaded)
    keep_ok = reloaded and abs(reloaded["gw"] - after["gw"]) <= 24
    print("宽度记忆:", "通过" if keep_ok else "不通过")

    # 再测双击复原（分隔条已经跟着动，要重新取坐标）
    geo2 = cdp.js("""(() => {
      const r = document.getElementById('gp-split').getBoundingClientRect();
      return {x: Math.round(r.left + r.width/2), y: Math.round(r.top + r.height/2)};
    })()""")
    dx, dy = geo2["x"], geo2["y"]
    print("复原点:", geo2)
    for cc in (1, 1, 2):
        cdp.send("Input.dispatchMouseEvent", {"type": "mousePressed",
                                              "x": dx, "y": dy,
                                              "button": "left", "clickCount": cc,
                                              "buttons": 1})
        cdp.send("Input.dispatchMouseEvent", {"type": "mouseReleased",
                                              "x": dx, "y": dy,
                                              "button": "left", "buttons": 0,
                                              "clickCount": cc})
        time.sleep(0.08)
    time.sleep(1.0)
    reset = cdp.js("""(() => {
      const gp = document.getElementById('graph-pane');
      return {gw: Math.round(gp.getBoundingClientRect().width),
              saved: localStorage.getItem('rt_gp_split'),
              inlineFlex: gp.style.flex || '(空=默认)'};
    })()""")
    print("双击复原:", reset)
    n3 = cdp.shot(os.path.join(OUT, "分屏-双击复原.png"))
    print("截图3:", n3, "字节")

    cdp.js("window.close && 0")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
