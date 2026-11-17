# -*- coding: utf-8 -*-
"""补充验证：未提交三选一弹窗、日志框展开收起。"""
import base64
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import websocket

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = r"C://Users//mike//WorkBuddy//2026-11-05-09-34-34//out"
SCAN = """import rclpy
from rclpy.node import Node
from sensor_msgs.msg import LaserScan


class ScanNode(Node):
    def __init__(self):
        super().__init__('scan_node')
        self.pub = self.create_publisher(LaserScan, '/scan_out', 10)
"""
PROC = """import rclpy
from rclpy.node import Node
from sensor_msgs.msg import LaserScan
from geometry_msgs.msg import Twist


class ProcNode(Node):
    def __init__(self):
        super().__init__('proc_node')
        self.create_subscription(LaserScan, '/scan_out', self.cb, 10)
        self.pub = self.create_publisher(Twist, '/cmd_vel', 10)
"""
BASE = os.environ.get("ROS2_TEST_BASE", "http://127.0.0.1:8125")
DBG = "http://127.0.0.1:9444"
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class C(object):
    def __init__(self, u):
        self.ws = websocket.create_connection(u, timeout=40, suppress_origin=True)
        self.n = 0

    def send(self, m, p=None):
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": m, "params": p or {}}))
        while True:
            r = json.loads(self.ws.recv())
            if r.get("id") == mid:
                return r.get("result", {})

    def js(self, e):
        r = self.send("Runtime.evaluate", {"expression": e, "returnByValue": True,
                                           "awaitPromise": True, "userGesture": True})
        return r.get("result", {}).get("value")

    def shot(self, path):
        r = self.send("Page.captureScreenshot", {"format": "png"})
        open(path, "wb").write(base64.b64decode(r["data"]))


def new_target(url):
    req = urllib.request.Request(DBG + "/json/new?" + urllib.parse.quote(url, safe=":/?=&"),
                                 method="PUT")
    return json.loads(op.open(req, timeout=15).read().decode())


SAVE_DRAFT = """(async()=>{
  const sid = localStorage.getItem('ros2tutor_sid');
  const id = document.querySelector('#gNode [data-node]').getAttribute('data-node');
  const r = await fetch('/api/graph/draft', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({sessionId: sid, draft: {renames: {[id]: '扫描节点'}}})});
  const g = await (await fetch('/api/graph?sid='+sid)).json();
  return JSON.stringify({status: r.status, pending: (g.stats||{}).pending});
})()"""

CLEAR_DRAFT = """(async()=>{
  const sid = localStorage.getItem('ros2tutor_sid');
  await fetch('/api/graph/draft', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({sessionId: sid, draft: {renames: {}}})});
  const g = await (await fetch('/api/graph?sid='+sid)).json();
  return JSON.stringify({pending: (g.stats||{}).pending});
})()"""


def wait_nodes(c, n=2, tries=25):
    for _ in range(tries):
        cnt = c.js("document.querySelectorAll('#gNode [data-node]').length") or 0
        if cnt >= n:
            return cnt
        time.sleep(0.5)
    return c.js("document.querySelectorAll('#gNode [data-node]').length") or 0


def main():
    os.makedirs(OUT, exist_ok=True)
    t = new_target(BASE + "/")
    c = C(t["webSocketDebuggerUrl"])
    c.send("Page.enable")
    c.send("Runtime.enable")
    c.send("Emulation.setDeviceMetricsOverride",
           {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
    time.sleep(1.5)
    c.js("fetch('/api/identity').then(r=>r.json())")
    time.sleep(0.8)
    sid = c.js("localStorage.getItem('ros2tutor_sid')")
    print("sid:", sid)
    pkg = os.path.join(ROOT, "sessions", sid, "ros2_ws", "src", "demo_pkg", "demo_pkg")
    os.makedirs(pkg, exist_ok=True)
    open(os.path.join(pkg, "__init__.py"), "w").close()
    io2 = __import__("io")
    io2.open(os.path.join(pkg, "scan_node.py"), "w", encoding="utf-8").write(SCAN)
    io2.open(os.path.join(pkg, "proc_node.py"), "w", encoding="utf-8").write(PROC)
    io2.open(os.path.join(ROOT, "sessions", sid, "ros2_ws", "src", "demo_pkg", "package.xml"),
             "w", encoding="utf-8").write(
        '<package format="3"><name>demo_pkg</name><version>0.0.1</version></package>')
    c.send("Page.reload", {"ignoreCache": True})
    print("节点数:", wait_nodes(c))
    print("存草稿:", c.js(SAVE_DRAFT))

    c.send("Page.reload", {"ignoreCache": True})
    wait_nodes(c)
    print("meta:", c.js("document.getElementById('gp-meta').textContent"))
    c.js("document.getElementById('gp-verify').click()")
    time.sleep(0.8)
    dlg = c.js("!document.getElementById('vy-draft').classList.contains('hidden')")
    num = c.js("document.getElementById('vy-draft-n').textContent")
    print("未提交弹窗:", dlg, "| 改动数:", num)
    c.shot(os.path.join(OUT, "链路验证-未提交弹窗.png"))
    if dlg:
        c.js("document.querySelector('#vy-draft [data-act=\"committed\"]').click()")
        time.sleep(2.0)
        print("  选「按已提交版本运行」→ 注入器:", c.js("!!document.querySelector('#vy-inject')"),
              "| 按钮:", c.js("document.getElementById('gp-verify').textContent"))
        c.js("document.getElementById('gp-verify').click()")
        time.sleep(1.5)

    # 清掉草稿，跑一轮干净的验证，然后点日志框看能否展开
    print("清草稿:", c.js(CLEAR_DRAFT))
    c.send("Page.reload", {"ignoreCache": True})
    wait_nodes(c)
    time.sleep(0.6)
    c.js("document.getElementById('gp-verify').click()")
    h1 = h2 = 0
    lid = ""
    for _ in range(70):
        time.sleep(0.4)
        # 等真实日志到齐（占位只有 1 行），否则点展开看不出差别
        if c.js("(function(){var e=document.querySelector('.vy-log');"
                "return e?e.querySelectorAll('tspan').length:0;})()") >= 4:
            # 必须锁定同一个 id：展开时会「先删后建」，DOM 顺序变了，
            # 用 querySelector('.vy-log') 会量到另一个节点的框（之前就栽在这）
            lid = c.js("document.querySelector('.vy-log').getAttribute('data-log')")
            sel = '[data-log="%s"]' % lid.replace('"', '\\"')
            h1 = c.js("(function(){var e=document.querySelector('%s');"
                      "return e&&e.getBBox?Math.round(e.getBBox().height):0;})()" % sel)
            # 用真实鼠标事件点：日志文字在矩形上层，dispatchEvent 打不到同一路径
            pos = json.loads(c.js(
                "(function(){var e=document.querySelector('%s .vy-log-box');"
                "var r=e.getBoundingClientRect();"
                "return JSON.stringify([r.x+r.width/2,r.y+r.height/2]);})()" % sel))
            for tp in ("mouseMoved", "mousePressed", "mouseReleased"):
                c.send("Input.dispatchMouseEvent",
                       {"type": tp, "x": pos[0], "y": pos[1],
                        "button": "left", "clickCount": 1 if tp != "mouseMoved" else 0})
            time.sleep(0.6)
            h2 = c.js("(function(){var e=document.querySelector('%s');"
                      "return e&&e.getBBox?Math.round(e.getBBox().height):0;})()" % sel)
            break
    print("日志框 id:", lid)
    print("日志框 收起→展开:", h1, "→", h2, "| 生效:", h2 > h1)
    c.shot(os.path.join(OUT, "链路验证-日志展开.png"))
    ok = bool(dlg) and h2 > h1
    print("结果:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
