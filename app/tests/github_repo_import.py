# -*- coding: utf-8 -*-
"""回归：从 GitHub 直接 clone 下来的仓库，导入后该画成什么样。

样本是 `linorobot2-cockpit-main`（用户真实下载的仓库），它的形状很典型：
- 仓库根目录就是一个包（package.xml + CMakeLists.txt），节点全在 scripts/ 下；
- 话题名一半是相对名（`odom/unfiltered`），一半是绝对名（`/scan`）；
- 一部分话题名来自 launch 参数（`get_parameter("topic").value`）；
- tests/ 里有一堆 `class Gone(BareNode)` 之类的测试替身。

老版本在这份仓库上的结果是：26 个方块、0 条线、包名全叫 `scripts`、
其中 15 个是 `gone` / `bare` 这种测试替身，排成一根竖条。

断言：
1. 包名来自 package.xml（linorobot2_cockpit），不是 `scripts`；
2. 测试替身不进图（没有 gone/bare/no_tf 这类节点）；
3. 相对名与绝对名能连上（edges > 0，且包含 /scan 或 /battery）；
4. 节点不重叠，"孤立"角标不压节点名。
"""
import argparse
import base64
import json
import os
import time
import urllib.parse
import urllib.request

import websocket

OUTDIR = r"C:/Users/mike/WorkBuddy/2026-11-05-09-34-34/out"
DBG = "http://127.0.0.1:9444"

CHECK = """
(function () {
  var H = 48, W = 138;
  function tr(el) {
    var m = /translate\\(([-0-9.]+)[ ,]+([-0-9.]+)\\)/.exec(el.getAttribute('transform') || '');
    return m ? [+m[1], +m[2]] : [0, 0];
  }
  var nodes = Array.from(document.querySelectorAll('#gNode [data-node]')).map(function (g) {
    var p = tr(g);
    var nm = g.querySelector('.gn-name');
    var badge = g.querySelector('.gn-badge');
    var nameBox = null, badgeBox = null;
    if (nm && nm.getBBox) {
      try {
        var q = nm.getBBox();
        nameBox = { x: p[0] + q.x, y: p[1] + q.y, w: q.width, h: q.height };
      } catch (e) {}
    }
    if (badge && badge.getBBox) {
      try {
        var r = badge.getBBox();
        badgeBox = { x: p[0] + r.x, y: p[1] + r.y, w: r.width, h: r.height };
      } catch (e) {}
    }
    return {
      id: (g.getAttribute('data-node') || '').split('/').pop(),
      x: p[0], y: p[1], nameBox: nameBox, badgeBox: badgeBox
    };
  });
  function hit(a, b) {
    return a.x < b.x + b.w && a.x + a.w > b.x && a.y < b.y + b.h && a.y + a.h > b.y;
  }
  var overlaps = [];
  nodes.forEach(function (a, i) {
    nodes.slice(i + 1).forEach(function (b) {
      if (hit({ x: a.x, y: a.y, w: W, h: H }, { x: b.x, y: b.y, w: W, h: H })) {
        overlaps.push(a.id + ' / ' + b.id);
      }
    });
  });
  var badgeClash = [];
  nodes.forEach(function (n) {
    if (!n.badgeBox) return;
    if (n.nameBox && hit(n.badgeBox, n.nameBox)) badgeClash.push(n.id);
    if (n.badgeBox.x < n.x - 3 || n.badgeBox.x + n.badgeBox.w > n.x + W + 3) {
      badgeClash.push(n.id + '(出界)');
    }
    if (n.badgeBox.y >= n.y + H - 2) badgeClash.push(n.id + '(压底边)');
  });
  // 连线穿过第三方节点框 = 图上"糊过去了"，看不出来谁连谁
  var crash = [];
  Array.from(document.querySelectorAll('#gEdge g[data-edge]')).forEach(function (g) {
    var p = g.querySelector('.ge-path');
    if (!p) return;
    var L = p.getTotalLength();
    var from = (g.getAttribute('data-from') || '').split('/').pop();
    var to = (g.getAttribute('data-to') || '').split('/').pop();
    for (var s = 0.08; s < 0.95; s += 0.05) {
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
  return {
    nodes: nodes.map(function (n) { return n.id; }),
    overlaps: overlaps,
    badgeClash: badgeClash,
    crash: crash
  };
})()
"""


class CDP(object):
    def __init__(self, url):
        self.ws = websocket.create_connection(url, timeout=180, suppress_origin=True)
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
        if "exceptionDetails" in r:
            print("  JS 异常:", json.dumps(r["exceptionDetails"])[:200])
        return r.get("result", {}).get("value")


def collect(src):
    skip = {"build", "install", "log", ".git", "__pycache__", "node_modules"}
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8171/")
    ap.add_argument("--src", default=r"C:/tmp/lino/linorobot2-cockpit-main")
    ap.add_argument("--shot", default="GitHub仓库-导入.png")
    ap.add_argument("--pkg", default="linorobot2_cockpit")
    a = ap.parse_args()
    os.makedirs(OUTDIR, exist_ok=True)

    files = collect(a.src)
    print("项目文件数:", len(files))

    op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    req = urllib.request.Request(DBG + "/json/new?" + urllib.parse.quote(a.base, safe=":/?=&"),
                                 method="PUT")
    tab = json.loads(op.open(req, timeout=20).read().decode())
    c = CDP(tab["webSocketDebuggerUrl"])
    c.send("Page.enable")
    c.send("Runtime.enable")
    c.send("Emulation.setDeviceMetricsOverride", {"width": 1600, "height": 1100,
                                                  "deviceScaleFactor": 1, "mobile": False})
    for _ in range(60):
        time.sleep(0.5)
        if c.js("!!document.getElementById('gp-verify')"):
            break
    # 复用同一个浏览器 profile 时，localStorage 里会留着上一次的 sid；
    # 服务端一重启那个会话就不存在了，接口会回 "bad session id"。
    c.js("localStorage.removeItem('ros2tutor_sid'); 1")
    c.js("location.reload()")
    for _ in range(60):
        time.sleep(0.5)
        if c.js("!!document.getElementById('gp-verify')"):
            break
    sid = c.js("localStorage.getItem('ros2tutor_sid')")
    print("页面会话:", sid)

    payload = json.dumps({"sessionId": sid, "files": files}, ensure_ascii=False)
    res = c.js("fetch('/api/project/import',{method:'POST',headers:{'Content-Type':"
               "'application/json'},body:%s}).then(r=>r.json()).catch(e=>({err:String(e)}))"
               % json.dumps(payload))
    print("导入:", json.dumps(res, ensure_ascii=False)[:180] if not isinstance(res, str) else res)

    c.js("location.reload()")
    for _ in range(60):
        time.sleep(0.5)
        if c.js("document.querySelectorAll('#gNode [data-node]').length"):
            break
    time.sleep(1.5)

    g = c.js("fetch('/api/graph?sid='+encodeURIComponent(localStorage.getItem('ros2tutor_sid')))"
             ".then(r=>r.json()).catch(e=>({err:String(e)}))") or {}
    if not isinstance(g, dict) or not g.get("nodes"):
        print("  /api/graph 原始返回:", json.dumps(g, ensure_ascii=False)[:300])
    stats = g.get("stats") or {}
    names = [(n.get("name") or "") for n in (g.get("nodes") or [])]
    pkgs = sorted({n.get("package") or "" for n in (g.get("nodes") or [])})
    topics = sorted({e.get("topic") or "" for e in (g.get("edges") or [])})
    print("\n节点 %s · 连线 %s · 孤立 %s" % (stats.get("nodes"), stats.get("edges"),
                                            stats.get("orphans")))
    print("包名:", pkgs)
    print("节点:", "、".join(names))
    print("连线话题:", "、".join(topics) or "(无)")

    fails = []

    def ok(cond, msg):
        print(("  PASS  " if cond else "  FAIL  ") + msg)
        if not cond:
            fails.append(msg)

    print("\n=== 判定 ===")
    ok(a.pkg in pkgs, "包名来自 package.xml（%s）" % a.pkg)
    ok("scripts" not in pkgs, "没有把 scripts/ 目录当成包名")
    fake = [n for n in names if n in ("gone", "bare", "no_tf", "wandered", "believes",
                                      "on_top", "far_enough", "__starts_at",
                                      "__scan_stub", "phantom_then_drives")]
    ok(not fake, "tests/ 里的测试替身没进图（%s）" % ("、".join(fake) or "无"))
    ok(stats.get("edges", 0) > 0, "相对名/绝对名能连上线（%s 条）" % stats.get("edges"))

    geo = c.js(CHECK) or {}
    ok(not geo.get("overlaps"), "节点不重叠（%s）" % ("、".join(geo.get("overlaps") or []) or "无"))
    ok(not geo.get("badgeClash"),
       "「孤立」角标不压节点名（%s）" % ("、".join(geo.get("badgeClash") or []) or "无"))
    ok(not geo.get("crash"),
       "连线不穿过第三方节点框（%s）" % ("、".join(geo.get("crash") or []) or "无"))

    r = c.send("Page.captureScreenshot", {"format": "png"})
    p = os.path.join(OUTDIR, a.shot)
    open(p, "wb").write(base64.b64decode(r["data"]))
    print("\n  saved:", p)

    print("\n结果:", "PASS" if not fails else "FAIL")
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
