# -*- coding: utf-8 -*-
"""CDP 验证链路级验证前端：注入器出现 → 连线逐段变绿 → 日志框 → 结果条 → 退出清理。"""
import base64
import json
import os
import sys
import time

import urllib.parse
import urllib.request
import websocket

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = r"C:/Users/mike/WorkBuddy/2026-11-05-09-34-34/out"
BASE = os.environ.get("ROS2_TEST_BASE", "http://127.0.0.1:8125")
DBG = "http://127.0.0.1:9444"

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


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
        return r.get("result", {}).get("value")

    def shot(self, path):
        r = self.send("Page.captureScreenshot", {"format": "png"})
        data = base64.b64decode(r["data"])
        open(path, "wb").write(data)
        return len(data)


def new_target(url):
    req = urllib.request.Request(DBG + "/json/new?" + urllib.parse.quote(url, safe=":/?=&"),
                                 method="PUT")
    with opener.open(req, timeout=15) as r:
        return json.loads(r.read().decode())


def write_pkg(sid):
    """往这个会话的工作区放一个最小 ROS 包：scan_node 发 /scan_out，proc_node 收后发 /cmd_vel。"""
    pkg = os.path.join(ROOT, "sessions", sid, "ros2_ws", "src", "demo_pkg", "demo_pkg")
    os.makedirs(pkg, exist_ok=True)
    open(os.path.join(pkg, "__init__.py"), "w").close()
    open(os.path.join(pkg, "scan_node.py"), "w", encoding="utf-8").write(
        "import rclpy\nfrom rclpy.node import Node\n"
        "from sensor_msgs.msg import LaserScan\n\n\n"
        "class ScanNode(Node):\n"
        "    def __init__(self):\n"
        "        super().__init__('scan_node')\n"
        "        self.pub = self.create_publisher(LaserScan, '/scan_out', 10)\n")
    open(os.path.join(pkg, "proc_node.py"), "w", encoding="utf-8").write(
        "import rclpy\nfrom rclpy.node import Node\n"
        "from sensor_msgs.msg import LaserScan\nfrom geometry_msgs.msg import Twist\n\n\n"
        "class ProcNode(Node):\n"
        "    def __init__(self):\n"
        "        super().__init__('proc_node')\n"
        "        self.create_subscription(LaserScan, '/scan_out', self.cb, 10)\n"
        "        self.pub = self.create_publisher(Twist, '/cmd_vel', 10)\n")
    open(os.path.join(ROOT, "sessions", sid, "ros2_ws", "src", "demo_pkg", "package.xml"),
         "w", encoding="utf-8").write(
        '<package format="3"><name>demo_pkg</name><version>0.0.1</version></package>')
    return pkg


PROBE = """
(() => {
  const q = s => document.querySelector(s);
  const n = s => document.querySelectorAll(s).length;
  return {
    btn: (q('#gp-verify') || {}).textContent || '',
    compile: (q('#gp-compile-info') || {}).textContent || '',
    inj: !!q('#vy-inject'),
    mcu: !!q('#vy-mcu'),
    mcuOk: !!q('.vy-mcu.vy-mcu-ok'),
    okEdges: n('.ge-path.vy-ok'),
    failEdges: n('.ge-path.vy-fail'),
    okNodes: n('.gn-box.vy-ok'),
    logs: n('.vy-log'),
    resultHidden: (q('#vy-result') || {}).className ? q('#vy-result').classList.contains('hidden') : true,
    head: (q('#vy-result-head') || {}).textContent || '',
    body: (q('#vy-result-body') || {}).textContent || ''
  };
})()
"""


def main():
    os.makedirs(OUT, exist_ok=True)
    # 每轮用全新会话：旧会话的工作区里堆着上一轮的包，图里会混进无关节点，
    # 最后一跳也可能不是下发给硬件的指令 —— 那样测的就不是这条链路了。
    sid0 = "uit" + str(int(time.time() * 1000))[-10:]
    t = new_target(BASE + "/?sid=" + sid0)
    cdp = CDP(t["webSocketDebuggerUrl"])
    cdp.send("Page.enable")
    cdp.send("Runtime.enable")
    cdp.send("Emulation.setDeviceMetricsOverride",
             {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
    time.sleep(1.5)
    cdp.js("fetch('/api/identity').then(r=>r.json())")
    # 冷启动（新端口/新 profile）时首屏资源要现下载，1 秒未必够；
    # 拿不到就再导航一次，别把「没加载完」当成功能失败
    sid = ""
    for i in range(24):
        time.sleep(0.5)
        sid = cdp.js("localStorage.getItem('ros2tutor_sid')")
        if sid:
            break
        if i == 6:
            cdp.send("Page.navigate", {"url": BASE + "/"})
    print("会话 sid:", sid)
    if not sid:
        print("FAIL 页面没拿到 sid")
        return 1
    write_pkg(sid)

    cdp.send("Page.reload", {"ignoreCache": True})
    time.sleep(3.0)
    for _ in range(20):
        nodes = cdp.js("document.querySelectorAll('#gNode [data-node]').length")
        if nodes:
            break
        time.sleep(0.6)
    print("节点数:", cdp.js("document.querySelectorAll('#gNode [data-node]').length"))
    print("初始:", json.dumps(cdp.js(PROBE), ensure_ascii=False))

    cdp.js("document.getElementById('gp-verify').click()")
    # 采样要快过播放节奏（每段 360ms），否则看不出「逐段」；
    # 窗口按真实耗时给足：mock 每跳约 2s，5 跳项目要 20s 上下。
    frames = []
    t0 = time.time()
    i = 0
    while time.time() - t0 < 50:
        time.sleep(0.12)
        p = cdp.js(PROBE)
        frames.append(p)
        if i % 4 == 0 or (not p["resultHidden"] and not (frames[-2] or {}).get("resultHidden")):
            print("t=%4.1fs comp=%-8s inj=%-5s ok=%d/%d logs=%d mcu=%s %s"
                  % (time.time() - t0, p["compile"], p["inj"], p["okEdges"], p["okNodes"],
                     p["logs"], p["mcuOk"],
                     ("| " + p["head"]) if not p["resultHidden"] else ""))
        i += 1
        if not p["resultHidden"] and p["okEdges"] and p["logs"] and p["mcuOk"]:
            break
    for _ in range(25):
        if not frames[-1]["resultHidden"]:
            break
        time.sleep(0.2)
        frames.append(cdp.js(PROBE))
    cdp.shot(os.path.join(OUT, "链路验证-运行中.png"))

    # 每播一段，画布上的「已完成」总量才涨一点；一次性全绿会让序列只有两个值
    seq = [f["okEdges"] + f["okNodes"] + (1 if f["mcuOk"] else 0) for f in frames]
    grows = [seq[0]] + [b for a, b in zip(seq, seq[1:]) if b != a]
    last = frames[-1]
    print("\n=== 判定 ===")
    print("注入器出现:", any(f["inj"] for f in frames))
    print("进度序列:", grows, "→ 逐段推进:", len(set(grows)) >= 3)
    print("绿线:", last["okEdges"], "绿节点:", last["okNodes"],
          "日志框:", last["logs"], "下位机反应:", last["mcuOk"])
    print("结果条:", last["head"], "|", last["body"])
    print("按钮双态:", frames[0]["btn"], "→", last["btn"])

    cdp.js("document.getElementById('gp-verify').click()")
    time.sleep(1.2)
    after = cdp.js(PROBE)
    print("\n退出后:", json.dumps(after, ensure_ascii=False))
    cdp.shot(os.path.join(OUT, "链路验证-已退出.png"))

    ok = (any(f["inj"] for f in frames) and len(set(grows)) >= 3
          and last["logs"] >= 2 and last["okEdges"] >= 1 and last["mcuOk"]
          and last["head"].startswith("✓")
          and (not after["inj"]) and after["okEdges"] == 0 and after["btn"] == "编译运行")
    print("\n结果:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
