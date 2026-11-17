# -*- coding: utf-8 -*-
"""画布交互回归：端口拖拽连线 / 滚轮缩放 / 空格抓手平移。

    python -X utf8 tests/canvas_gesture_test.py

交接文档里这三条一直写着「代码写了、未验证」，这里补上真机证据：
全部用 CDP 发真鼠标/键盘事件（不是直接调内部函数），走的是用户真会走的那条路。

前置：服务在 8125（mock 即可）+ 无头 Edge 的 CDP 在 9444。
"""
import base64
import json
import os
import sys
import tempfile
import time
import urllib.parse
import urllib.request

import websocket

BASE = "http://127.0.0.1:8125"
DBG = "http://127.0.0.1:9444"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SHOT = os.path.join(tempfile.gettempdir(), "ros2shots")
FAILED = []

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def check(name, ok, extra=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + (("  " + str(extra)) if extra else ""))
    if not ok:
        FAILED.append(name)


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
                if "error" in msg:
                    raise RuntimeError("%s -> %s" % (method, msg["error"]))
                return msg.get("result", {})

    def js(self, expr):
        r = self.send("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                          "awaitPromise": True, "userGesture": True})
        if r.get("exceptionDetails"):
            return "__ERR__ " + json.dumps(r["exceptionDetails"], ensure_ascii=False)[:300]
        return r.get("result", {}).get("value")

    def mouse(self, kind, x, y, button="left", click=1):
        p = {"type": kind, "x": x, "y": y, "button": button, "clickCount": click}
        if kind == "mouseMoved" and button == "none":
            p["button"] = "none"
        self.send("Input.dispatchMouseEvent", p)

    def shot(self, name):
        os.makedirs(SHOT, exist_ok=True)
        p = os.path.join(SHOT, name)
        data = base64.b64decode(self.send("Page.captureScreenshot", {"format": "png"})["data"])
        with open(p, "wb") as f:
            f.write(data)
        return p


def wait_for(cdp, expr, timeout=25.0, step=0.4):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = cdp.js(expr)
        if isinstance(v, str) and v.startswith("__ERR__"):
            return v
        if v:
            return v
        time.sleep(step)
    return cdp.js(expr)


def main():
    with open(os.path.join(ROOT, "samples", "demo_ros2_ws.zip"), "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    sid = "cg" + str(int(time.time() * 1000))[-8:]
    print("测试会话 %s" % sid)

    req = urllib.request.Request(
        DBG + "/json/new?" + urllib.parse.quote(BASE + "/", safe=":/?=&"), method="PUT")
    with opener.open(req, timeout=10) as r:
        target = json.loads(r.read().decode())
    cdp = CDP(target["webSocketDebuggerUrl"])
    cdp.send("Page.enable")
    cdp.send("Runtime.enable")
    cdp.send("Emulation.setDeviceMetricsOverride",
             {"width": 1600, "height": 1000, "deviceScaleFactor": 1, "mobile": False})
    time.sleep(3.0)
    cdp.js("localStorage.setItem('ros2tutor_sid', %r); 'ok'" % sid)
    cdp.send("Page.reload")
    time.sleep(3.5)
    cdp.js("""
      (async () => {
        const b = atob(%r);
        const u8 = new Uint8Array(b.length);
        for (let i = 0; i < b.length; i++) u8[i] = b.charCodeAt(i);
        await fetch('/api/project/import-zip?sessionId=%s&name=demo.zip',
                    {method: 'POST', body: u8,
                     headers: {'Content-Type': 'application/octet-stream'}});
        return 'done';
      })()
    """ % (b64, sid))
    cdp.js("RTGraph.reload()")
    time.sleep(2.0)

    n_nodes = cdp.js("document.querySelectorAll('#gp-svg [data-node]').length")
    n_edges = cdp.js("document.querySelectorAll('#gp-svg g[data-edge]').length")
    check("图上有节点和连线", bool(n_nodes) and bool(n_edges),
          "nodes=%s edges=%s" % (n_nodes, n_edges))

    # ---------- 1. 端口拖拽连线 ----------
    # 取两个「还没连起来」的节点：从 A 的输出端口拖到 B 的输入端口
    pick = cdp.js("""
      (() => {
        const ns = [...document.querySelectorAll('#gp-svg [data-node]')];
        const es = [...document.querySelectorAll('#gp-svg g[data-edge]')]
          .map(g => [g.getAttribute('data-from'), g.getAttribute('data-to')]);
        for (let i = 0; i < ns.length; i++) {
          for (let j = 0; j < ns.length; j++) {
            if (i === j) continue;
            const a = ns[i].getAttribute('data-node'), b = ns[j].getAttribute('data-node');
            if (es.some(e => e[0] === a && e[1] === b)) continue;
            const c = ns[i].querySelectorAll('circle.gport')[1];   // 输出端（右侧）
            const d = ns[j].querySelectorAll('circle.gport')[0];   // 输入端（左侧）
            if (!c || !d) continue;
            const r1 = c.getBoundingClientRect(), r2 = d.getBoundingClientRect();
            return JSON.stringify({from: a, to: b,
              x1: r1.left + r1.width / 2, y1: r1.top + r1.height / 2,
              x2: r2.left + r2.width / 2, y2: r2.top + r2.height / 2});
          }
        }
        return '';
      })()
    """)
    check("找到一对可连的节点", bool(pick) and "x1" in str(pick), pick)
    if not pick or "x1" not in str(pick):
        print("\n无法继续，失败 %d 项：%s" % (len(FAILED), FAILED))
        return 1
    g = json.loads(pick)

    # 悬停让端口显形（真实用户也是先移过去才看得到）
    cdp.mouse("mouseMoved", g["x1"], g["y1"], button="none")
    time.sleep(0.3)
    cdp.mouse("mousePressed", g["x1"], g["y1"])
    for k in range(1, 9):
        cdp.mouse("mouseMoved",
                  g["x1"] + (g["x2"] - g["x1"]) * k / 8.0,
                  g["y1"] + (g["y2"] - g["y1"]) * k / 8.0, button="none")
        time.sleep(0.06)
    cdp.mouse("mouseReleased", g["x2"], g["y2"])
    time.sleep(0.6)

    pop = cdp.js("!!document.querySelector('.gp-pop')")
    check("松手弹出「话题名/消息类型」确认卡", bool(pop))
    if pop:
        print("      弹出的默认话题：", cdp.js(
            "(document.querySelector('.gp-pop input')||{}).value || ''"))
        cdp.shot("gesture_1_connect_pop.png")
        # 确认按钮是「连上」：按 id 点，别按文案猜
        clicked = cdp.js("(() => { const b = document.getElementById('pp-ok');"
                         " if (!b) return 'missing'; b.click(); return 'ok'; })()")
        check("确认卡上点了「连上」", clicked == "ok", clicked)
        time.sleep(1.2)
    n_edges2 = cdp.js("document.querySelectorAll('#gp-svg g[data-edge]').length")
    check("确认后图纸多了一条连线", n_edges2 == n_edges + 1, "%s → %s" % (n_edges, n_edges2))
    # 待提交计数是存完草稿、服务端重新算过之后才回来的，等一会儿而不是立刻读
    pending = ""
    for _ in range(15):
        pending = cdp.js("(document.getElementById('gp-commit')||{}).textContent || ''")
        if "（" in str(pending):
            break
        time.sleep(0.4)
    check("「提交」上出现待提交计数", "（" in str(pending), pending)
    cdp.shot("gesture_2_connected.png")

    # ---------- 2. 滚轮缩放 ----------
    z0 = cdp.js("document.getElementById('gp-zoom').textContent")
    rect = cdp.js("""(() => { const r = document.getElementById('gp-canvas').getBoundingClientRect();
                    return JSON.stringify({x: r.left + r.width/2, y: r.top + r.height/2}); })()""")
    c = json.loads(rect)
    cdp.send("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": c["x"], "y": c["y"],
                                          "deltaX": 0, "deltaY": -240})
    time.sleep(0.4)
    z1 = cdp.js("document.getElementById('gp-zoom').textContent")
    check("滚轮上滚放大", z0 != z1 and int(str(z1).rstrip("%")) > int(str(z0).rstrip("%")),
          "%s → %s" % (z0, z1))
    cdp.send("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": c["x"], "y": c["y"],
                                          "deltaX": 0, "deltaY": 480})
    time.sleep(0.4)
    z2 = cdp.js("document.getElementById('gp-zoom').textContent")
    check("滚轮下滚缩小", int(str(z2).rstrip("%")) < int(str(z1).rstrip("%")), "%s → %s" % (z1, z2))

    # ---------- 3. 空格 + 拖拽 = 平移 ----------
    # 先把焦点从聊天输入框挪开（真实用户点一下画布就发生了这件事）。
    # 焦点还在输入框里时敲空格必须当成打空格，画布不能抢。
    meta = json.loads(cdp.js("""(() => {
        const r = document.getElementById('gp-meta').getBoundingClientRect();
        return JSON.stringify({x: r.left + 6, y: r.top + r.height / 2}); })()"""))
    cdp.mouse("mousePressed", meta["x"], meta["y"])
    cdp.mouse("mouseReleased", meta["x"], meta["y"])
    time.sleep(0.3)
    print("      当前焦点：", cdp.js("document.activeElement && document.activeElement.tagName"))
    # 手势前先整体适应一次：缩放/平移过后节点可能已经不在视野里，
    # 点不到就是测试没瞄准，不是功能坏了
    cdp.js("RTGraph.fit(); 'ok'")
    time.sleep(0.8)
    cbox = json.loads(cdp.js("""(() => {
        const r = document.getElementById('gp-canvas').getBoundingClientRect();
        return JSON.stringify({x: r.left, y: r.top, w: r.width, h: r.height}); })()"""))
    cx = cbox["x"] + cbox["w"] * 0.5
    cy = cbox["y"] + cbox["h"] * 0.9          # 画布下方的空白处，避开节点
    t0 = cdp.js("document.getElementById('gRoot').getAttribute('transform')")
    np_prev = cdp.js("""(() => { const n = document.querySelector('#gp-svg [data-node]');
                          return n ? n.getAttribute('transform') : ''; })()""")
    # 空格按下（keyDown 走 CDP，等于真按键盘）
    cdp.send("Input.dispatchKeyEvent", {"type": "keyDown", "key": " ", "code": "Space",
                                        "windowsVirtualKeyCode": 32, "nativeVirtualKeyCode": 32,
                                        "text": " "})
    time.sleep(0.2)
    ready = cdp.js("document.getElementById('gp-canvas').classList.contains('pan-ready')")
    check("按住空格进入抓手模式", bool(ready))
    # 从「节点身上」起步也应该平移（抓手优先于拖节点），所以故意按在节点上
    node_pt = cdp.js("""(() => { const n = document.querySelector('#gp-svg [data-node]');
                        const r = n.getBoundingClientRect();
                        return JSON.stringify({x: r.left + r.width/2, y: r.top + r.height/2}); })()""")
    np = json.loads(node_pt)
    cdp.mouse("mousePressed", np["x"], np["y"])
    for k in range(1, 7):
        cdp.mouse("mouseMoved", np["x"] + 14 * k, np["y"] + 9 * k, button="none")
        time.sleep(0.06)
    cdp.mouse("mouseReleased", np["x"] + 84, np["y"] + 54)
    time.sleep(0.4)
    t1 = cdp.js("document.getElementById('gRoot').getAttribute('transform')")
    check("空格拖动平移了画布", t0 != t1, "%s → %s" % (t0, t1))
    node_after_pan = cdp.js("""(() => { const n = document.querySelector('#gp-svg [data-node]');
                                 return n ? n.getAttribute('transform') : ''; })()""")
    check("抓手模式下节点没被拖走（平移不是拖节点）", node_after_pan == np_prev, node_after_pan)
    cdp.send("Input.dispatchKeyEvent", {"type": "keyUp", "key": " ", "code": "Space",
                                        "windowsVirtualKeyCode": 32, "nativeVirtualKeyCode": 32})
    time.sleep(0.2)
    still = cdp.js("document.getElementById('gp-canvas').classList.contains('pan-ready')")
    check("松开空格退出抓手模式", not still)
    cdp.shot("gesture_3_pan.png")

    # 松手后普通拖拽要恢复成「拖节点」（否则抓手会吃掉所有交互）
    cdp.js("RTGraph.fit(); 'ok'")
    time.sleep(0.8)
    before = cdp.js("""(() => { const n = document.querySelector('#gp-svg [data-node]');
                       return n.getAttribute('transform'); })()""")
    np2 = json.loads(cdp.js("""(() => { const n = document.querySelector('#gp-svg [data-node]');
                        const r = n.getBoundingClientRect();
                        return JSON.stringify({x: r.left + r.width/2, y: r.top + r.height/2}); })()"""))
    cdp.mouse("mousePressed", np2["x"], np2["y"])
    for k in range(1, 5):
        cdp.mouse("mouseMoved", np2["x"] + 10 * k, np2["y"] + 12 * k, button="none")
        time.sleep(0.06)
    cdp.mouse("mouseReleased", np2["x"] + 40, np2["y"] + 48)
    time.sleep(0.5)
    after = cdp.js("""(() => { const n = document.querySelector('#gp-svg [data-node]');
                      return n.getAttribute('transform'); })()""")
    check("不按空格时仍然是拖节点", before != after, "%s → %s" % (before, after))

    print("\n%s" % ("全部通过" if not FAILED else "失败 %d 项：%s" % (len(FAILED), FAILED)))
    print("截图目录 %s" % SHOT)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
