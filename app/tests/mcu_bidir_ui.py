# -*- coding: utf-8 -*-
"""下位机双向回归：图上要出现下位机框，且「指令下去一条线、状态回来一条线」。

对应两个真实出过的问题：
1. 以前只有一条「末端尾巴」，图上根本看不出下位机在哪，也看不出反馈回到谁手里；
2. 加了反向通道（planner→detector 的 /detector_enable）之后，拓扑出现环，
   布局把 detector 和 planner 排进同一列，所有边都变成了大拱形。

断言：下位机数量/双向线、结论含「下位机回传」、框不重叠、线不穿节点框。

用法：python tests/mcu_bidir_ui.py [项目目录]
前置：ROS2_TEST_BASE 指向以 ROS2_VERIFY_BACKEND=mock 启动的服务。
"""
import base64
import json
import os
import sys
import time
import urllib.parse
import urllib.request

import websocket

SRC = sys.argv[1] if len(sys.argv) > 1 else r"C:/Users/mike/Desktop/demo_multi_chain_ws"
OUTDIR = r"C:/Users/mike/WorkBuddy/2026-11-05-09-34-34/out"
SID = "mcu%d" % (int(time.time()) % 100000)
HOST = os.environ.get("ROS2_TEST_BASE", "http://127.0.0.1:8162/")
BASE = HOST + ("&" if "?" in HOST else "?") + "sid=" + SID
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


def collect(src):
    skip = {"build", "install", "log", ".git", "__pycache__"}
    files = []
    for dp, dn, fn in os.walk(src):
        dn[:] = [d for d in dn if d not in skip]
        for f in fn:
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, src).replace("\\", "/")
            try:
                files.append({"path": rel, "content": open(p, encoding="utf-8").read()})
            except Exception:                                    # noqa: BLE001
                pass
    return files


# 纯几何断言：框有没有互相压、线有没有从别的节点框里穿过去
GEOM = """
(function () {
  var W = 138, H = 48;
  function tr(el) {
    var m = /translate\\(([-0-9.]+)[ ,]+([-0-9.]+)\\)/.exec(el.getAttribute('transform') || '');
    return m ? [+m[1], +m[2]] : [0, 0];
  }
  var nodes = Array.from(document.querySelectorAll('#gNode [data-node]')).map(function (g) {
    var p = tr(g);
    return { id: (g.getAttribute('data-node') || '').split('/').pop(), x: p[0], y: p[1], w: W, h: H };
  });
  var logs = Array.from(document.querySelectorAll('.vy-log')).map(function (g) {
    var b = g.getBBox();
    return { x: b.x, y: b.y, w: b.width, h: b.height };
  });
  var mcus = Array.from(document.querySelectorAll('.vy-mcu')).map(function (g) {
    var r = g.querySelector('.vy-mcu-box');
    return {
      id: g.getAttribute('data-mcu'),
      box: r ? { x: +r.getAttribute('x'), y: +r.getAttribute('y'),
                 w: +r.getAttribute('width'), h: +r.getAttribute('height') } : null,
      wires: g.querySelectorAll('.vy-mcu-wire').length,
      backs: g.querySelectorAll('.vy-mcu-back').length
    };
  });
  function hit(a, b) {
    return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
  }
  var bad = [];
  mcus.forEach(function (m) {
    if (!m.box) { bad.push('下位机 ' + m.id + ' 没有框'); return; }
    nodes.forEach(function (nd) { if (hit(m.box, nd)) bad.push('下位机 ' + m.id + ' 压住节点 ' + nd.id); });
    logs.forEach(function (l) { if (hit(m.box, l)) bad.push('下位机 ' + m.id + ' 压住日志框'); });
    mcus.forEach(function (o) {
      if (o !== m && o.box && hit(m.box, o.box)) bad.push('下位机 ' + m.id + ' 与 ' + o.id + ' 重叠');
    });
  });
  nodes.forEach(function (a, i) {
    nodes.slice(i + 1).forEach(function (b) {
      if (hit(a, b)) bad.push('节点重叠 ' + a.id + ' / ' + b.id);
    });
  });
  var crash = [];
  Array.from(document.querySelectorAll('#gEdge g[data-edge]')).forEach(function (g) {
    var p = g.querySelector('.ge-path');
    if (!p) return;
    var L = p.getTotalLength(), from = g.getAttribute('data-from'), to = g.getAttribute('data-to');
    for (var s = 0.06; s < 0.95; s += 0.06) {
      var pt = p.getPointAtLength(L * s);
      nodes.forEach(function (nd) {
        if (nd.id === from || nd.id === to) return;
        if (pt.x > nd.x + 2 && pt.x < nd.x + W - 2 && pt.y > nd.y + 2 && pt.y < nd.y + H - 2) {
          var k = g.getAttribute('data-topic') + ' 穿过 ' + nd.id;
          if (crash.indexOf(k) < 0) crash.push(k);
        }
      });
    }
  });
  return { mcus: mcus, bad: bad, crash: crash,
           edges: document.querySelectorAll('#gEdge g[data-edge]').length };
})()
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
    c.send("Emulation.setDeviceMetricsOverride", {"width": 1800, "height": 1040,
                                                  "deviceScaleFactor": 1, "mobile": False})
    for _ in range(40):
        time.sleep(0.5)
        if c.js("!!document.getElementById('gp-verify')"):
            break
    payload = json.dumps({"sessionId": SID, "files": files}, ensure_ascii=False)
    c.js("fetch('/api/project/import',{method:'POST',headers:{'Content-Type':"
         "'application/json'},body:%s}).then(r=>r.status)" % json.dumps(payload))
    c.send("Page.reload", {"ignoreCache": True})
    for _ in range(40):
        time.sleep(0.5)
        if c.js("document.querySelectorAll('#gNode [data-node]').length"):
            break
    print("节点数:", c.js("document.querySelectorAll('#gNode [data-node]').length"))

    c.js("document.getElementById('gp-verify').click()")
    t0 = time.time()
    while time.time() - t0 < 180:
        time.sleep(0.4)
        if not c.js("document.getElementById('vy-result').classList.contains('hidden')"):
            break
    print("用时: %.1fs" % (time.time() - t0))
    head = c.js("(document.getElementById('vy-result-head')||{}).textContent") or ""
    body = c.js("(document.getElementById('vy-result-body')||{}).textContent") or ""
    print("结论:", head)
    print("详情:", body[:160])
    time.sleep(1.0)

    g = c.js(GEOM) or {}
    mcus = g.get("mcus") or []
    print("\n下位机: %d 台" % len(mcus))
    for m in mcus:
        print("  %-8s 下行=%d 条 回传=%d 条 框=%s"
              % (m["id"], m["wires"], m["backs"], "有" if m["box"] else "无"))

    by_id = {m["id"]: m for m in mcus}
    fails = []
    if len(mcus) != 2:
        fails.append("应该有 2 台下位机（motor / battery），实际 %d" % len(mcus))
    motor = by_id.get("motor") or {}
    if motor.get("wires") != 1:
        fails.append("motor 下位机应该有一条指令下行线，实际 %s" % motor.get("wires"))
    if motor.get("backs") != 1:
        fails.append("motor 下位机应该有一条状态回传线，实际 %s" % motor.get("backs"))
    if "下位机回传" not in (head + body):
        fails.append("结论里没有提到下位机回传")
    for x in (g.get("bad") or []) + (g.get("crash") or []):
        fails.append(x)

    c.js("document.getElementById('gp-fit').click()")
    time.sleep(0.6)
    c.shot("下位机双向-回归.png")

    print("\n=== 判定 ===")
    print("重叠/穿框:", (g.get("bad") or []) + (g.get("crash") or []) or "无")
    print("结果:", "PASS" if not fails else "FAIL")
    for f in fails:
        print("  ✗", f)
    sys.exit(0 if not fails else 1)


if __name__ == "__main__":
    main()
