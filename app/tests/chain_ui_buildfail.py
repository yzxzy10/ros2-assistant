# -*- coding: utf-8 -*-
"""编译失败路径：注入器要变红、结论条要能摊开 colcon 原始输出、不能有绿线。"""
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
BASE = os.environ.get("ROS2_TEST_BASE", "http://127.0.0.1:8127")
DBG = "http://127.0.0.1:9444"
op = urllib.request.build_opener(urllib.request.ProxyHandler({}))
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


def main():
    t = new_target(BASE + "/")
    c = C(t["webSocketDebuggerUrl"])
    c.send("Page.enable")
    c.send("Runtime.enable")
    c.send("Emulation.setDeviceMetricsOverride",
           {"width": 1560, "height": 960, "deviceScaleFactor": 1, "mobile": False})
    time.sleep(1.5)
    c.js("fetch('/api/identity').then(r=>r.json())")
    sid = ""
    for i in range(24):
        time.sleep(0.5)
        sid = c.js("localStorage.getItem('ros2tutor_sid')")
        if sid:
            break
        if i == 6:
            c.send("Page.navigate", {"url": BASE + "/"})
    if not sid:
        print("FAIL 没拿到 sid")
        return 1
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
    for _ in range(30):
        if (c.js("document.querySelectorAll('#gNode [data-node]').length") or 0) >= 2:
            break
        time.sleep(0.5)
    time.sleep(0.6)
    c.js("document.getElementById('gp-verify').click()")

    for _ in range(50):
        time.sleep(0.4)
        if c.js("!document.getElementById('vy-result').classList.contains('hidden')"):
            break
    time.sleep(0.6)

    head = c.js("document.getElementById('vy-result-head').textContent")
    body = c.js("document.getElementById('vy-result-body').textContent")
    inj1 = c.js("(document.querySelector('#vy-inject .vy-inj-t1')||{}).textContent")
    inj2 = c.js("(document.querySelector('#vy-inject .vy-inj-t2')||{}).textContent")
    injfail = c.js("(document.getElementById('vy-inject')||{}).classList && "
                   "document.getElementById('vy-inject').classList.contains('vy-inj-fail')")
    more = c.js("!!document.getElementById('vy-result-more')")
    log_hidden = c.js("(document.getElementById('vy-result-log')||{}).className || ''")
    c.shot(os.path.join(OUT, "编译失败-收起.png"))
    if more:
        c.js("document.getElementById('vy-result-more').click()")
        time.sleep(0.4)
    logtxt = c.js("(document.getElementById('vy-result-log')||{}).textContent || ''")
    c.shot(os.path.join(OUT, "编译失败-摊开.png"))
    ok_edges = c.js("document.querySelectorAll('.ge-path.vy-ok').length")
    logs = c.js("document.querySelectorAll('.vy-log').length")

    print("结论:", head)
    print("详情:", body)
    print("注入器:", inj1, "/", inj2, "| 变红:", injfail)
    print("查看编译输出按钮:", more, "| 初始:", log_hidden)
    print("日志内容:", (logtxt or "").strip().splitlines()[:4])
    print("绿线:", ok_edges, "| 节点日志框:", logs)
    ok = (head.startswith("✕") and "编译" in head and injfail
          and more and "not found" in logtxt and ok_edges == 0 and logs == 0)
    print("结果:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
