# -*- coding: utf-8 -*-
"""CDP 验证：连线标签只随悬停出现、标签贴在线身上、提交有进度与结果。

覆盖用户反馈的三个问题：
1) 点了节点之后，连着它的线一直显示话题名（应该只在悬停时显示）
2) 节点拖远后，话题名飘在空白处（应该贴在线身上）
3) 点提交没任何反馈（应该出现「提交中」进度，结束后给出改了哪些文件）
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
        r = self.send("Runtime.evaluate", {
            "expression": expr, "returnByValue": True, "awaitPromise": True,
            "userGesture": True})
        if "exceptionDetails" in r:
            print("  JS 异常:", json.dumps(r["exceptionDetails"])[:300])
        return r.get("result", {}).get("value")

    def shot(self, path):
        r = self.send("Page.captureScreenshot", {"format": "png"})
        data = base64.b64decode(r["data"])
        with open(path, "wb") as f:
            f.write(data)
        return len(data)


LABEL_STATE = """(() => {
  const labels = [...document.querySelectorAll('.ge-label')].map(t => {
    const g = t.parentNode, r = g.querySelector('rect');
    return {text: t.textContent, op: g.getAttribute('opacity'),
            cx: +(r.getAttribute('x')) + (+r.getAttribute('width'))/2,
            cy: +(r.getAttribute('y')) + 9};
  });
  const visible = labels.filter(l => l.op === '1');
  return {total: labels.length, visible: visible.length, items: visible};
})()"""


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

    b64 = base64.b64encode(open(ZIP, "rb").read()).decode("ascii")
    res = cdp.js("""
(async () => {
  const b64 = "%s";
  const sid = localStorage.getItem('ros2tutor_sid');
  const r = await fetch('/api/project/import', {
    method: 'POST', headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({sessionId: sid, files: [{path: 'demo_ros2_ws.zip', content: b64}]})
  });
  const d = await r.json();
  if (window.RTGraph) window.RTGraph.reload();
  return d.ok + ' | ' + (d.message || '').split('\\n')[0];
})()""" % b64)
    print("导入:", res)
    time.sleep(3)

    print("\n--- 1) 点节点后连线标签是否还挂着 ---")
    before = cdp.js(LABEL_STATE)
    print("  初始可见标签:", before["visible"], "/", before["total"])
    cdp.js("""(() => {
      const g = document.querySelector('[data-node]');
      const r = g.getBoundingClientRect();
      g.dispatchEvent(new MouseEvent('click', {bubbles: true,
        clientX: r.left + r.width/2, clientY: r.top + r.height/2}));
      return true;
    })()""")
    time.sleep(0.8)
    after_click = cdp.js(LABEL_STATE)
    print("  选中节点后可见标签:", after_click["visible"], "/", after_click["total"])
    sel_ok = after_click["visible"] == 0

    print("\n--- 2) 悬停连线：标签出现且贴在线身上 ---")
    hover = cdp.js("""(() => {
      const hit = document.querySelector('.ge-hit');
      const g = hit.parentNode;
      const path = g.querySelector('.ge-path');
      const rr = path.getBoundingClientRect();
      hit.dispatchEvent(new PointerEvent('pointerenter', {bubbles: true,
        clientX: rr.left + rr.width/2, clientY: rr.top + rr.height/2}));
      return true;
    })()""")
    time.sleep(0.6)
    st = cdp.js(LABEL_STATE)
    on_path = cdp.js("""(() => {
      const lab = [...document.querySelectorAll('#gLabel > g')]
        .find(x => x.getAttribute('opacity') === '1');
      if (!lab) return null;
      const idx = +lab.getAttribute('data-edge');
      const gg = document.querySelectorAll('#gEdge > g')[idx];
      const rect = lab.querySelector('rect');
      const cx = +rect.getAttribute('x') + (+rect.getAttribute('width')) / 2;
      const cy = +rect.getAttribute('y') + 9;
      const p = gg.querySelector('.ge-path');
      const L = p.getTotalLength();
      let best = 1e9;
      for (let i = 0; i <= 200; i++) {
        const q = p.getPointAtLength(L * i / 200);
        const d = Math.hypot(q.x - cx, q.y - cy);
        if (d < best) best = d;
      }
      const mid = p.getPointAtLength(L * 0.5);
      return {dist: +best.toFixed(2), label: [+cx.toFixed(1), +cy.toFixed(1)],
              mid: [+mid.x.toFixed(1), +mid.y.toFixed(1)], len: +L.toFixed(1)};
    })()""")
    print("  悬停后可见标签:", st["visible"], st["items"][:1])
    print("  标签到线的最短距离:", on_path)
    path_ok = bool(on_path) and on_path["dist"] <= 12

    cdp.js("""(() => {
      const hit = document.querySelector('.ge-hit');
      hit.dispatchEvent(new PointerEvent('pointerleave', {bubbles: true}));
      return true;
    })()""")
    time.sleep(0.4)
    left = cdp.js(LABEL_STATE)
    print("  移开后可见标签:", left["visible"])
    hover_ok = st["visible"] == 1 and left["visible"] == 0

    print("\n--- 3) 改话题 → 提交（进度 + 结果）---")
    cdp.shot(os.path.join(OUT, "提交前-画布.png"))
    renamed = cdp.js("""(() => {
      const hit = document.querySelector('.ge-hit');
      const r = hit.getBoundingClientRect();
      hit.dispatchEvent(new MouseEvent('click', {bubbles: true,
        clientX: r.left + r.width/2, clientY: r.top + r.height/2}));
      const inp = document.querySelector('.gp-pop #rn-val');
      if (!inp) return 'no-popup';
      inp.value = '/detections_ui';
      document.querySelector('.gp-pop #rn-ok').click();
      return 'ok';
    })()""")
    print("  改名:", renamed)
    time.sleep(2.0)
    meta = cdp.js("""(() => {
      const b = document.getElementById('gp-commit');
      return {label: b.textContent, disabled: b.disabled};
    })()""")
    print("  提交按钮:", meta)

    cdp.js("document.getElementById('gp-commit').click()")
    time.sleep(0.45)
    prog = cdp.js("""(() => {
      const m = document.querySelector('.gp-commit-mask');
      if (!m) return null;
      return {head: m.querySelector('.gpc-head').innerText.replace(/\\n/g,' '),
              hint: (m.querySelector('#gpc-hint')||{}).innerText || '',
              spin: !!m.querySelector('.gpc-spin'),
              bar: !!m.querySelector('.gpc-bar')};
    })()""")
    print("  提交中的面板:", prog)
    cdp.shot(os.path.join(OUT, "提交中-进度.png"))

    done = None
    for _ in range(40):
        time.sleep(0.4)
        done = cdp.js("""(() => {
          const m = document.querySelector('.gp-commit-mask');
          if (!m || m.querySelector('.gpc-spin')) return null;
          return {head: m.querySelector('.gpc-head').innerText.replace(/\\n/g,' '),
                  items: [...m.querySelectorAll('.gpc-item')].map(x => x.innerText.replace(/\\n/g,' ')),
                  hasDl: !!m.querySelector('#gpc-dl'),
                  hasRetry: !!m.querySelector('#gpc-retry')};
        })()""")
        if done:
            break
    print("  结果面板:", json.dumps(done, ensure_ascii=False))
    cdp.shot(os.path.join(OUT, "提交后-结果.png"))

    sid = cdp.js("localStorage.getItem('ros2tutor_sid')")
    code = cdp.js("""(async () => {
      const sid = localStorage.getItem('ros2tutor_sid');
      const r = await fetch('/api/files/' + sid);
      const d = await r.json();
      return (d.files || []).filter(f => String(f).endsWith('.py')).length;
    })()""")
    print("  工作区 py 文件数:", code)
    ok_commit = bool(done) and done["hasDl"] and any("改名" in i or "→" in i for i in done["items"])

    print("\n结果:")
    print("  点节点后不显示标签:", "通过" if sel_ok else "不通过")
    print("  悬停显示/移开隐藏:", "通过" if hover_ok else "不通过")
    print("  标签贴在线身上:", "通过" if path_ok else "不通过")
    print("  提交有进度+结果+下载:", "通过" if ok_commit else "不通过")
    print("会话 sid:", sid)
    allok = sel_ok and hover_ok and path_ok and ok_commit
    print("总体:", "通过" if allok else "不通过")
    return 0 if allok else 1


if __name__ == "__main__":
    sys.exit(main())
