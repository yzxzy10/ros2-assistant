# -*- coding: utf-8 -*-
"""没有 package.xml / setup.py / CMakeLists.txt 的项目，导进去能不能出图？

新开一段对话 → 真实接口导入一个「只有一个 src/demo 目录 + 两个 .py」的项目
→ 点左栏架构图 → 断言画布出 2 节点 1 连线 → 截图 → 删掉测试对话。
"""
import base64
import json
import os
import sys
import time

import urllib.parse
import urllib.request
import websocket

OUT = r"C:\Users\mike\WorkBuddy\2026-11-05-09-34-34\out"
BASE = "http://127.0.0.1:8125"
DBG = "http://127.0.0.1:9444"

TALKER = """import rclpy
from rclpy.node import Node
from std_msgs.msg import String


class Talker(Node):
    def __init__(self):
        super().__init__('talker_node')
        self.pub = self.create_publisher(String, '/chatter', 10)
"""

LISTENER = """import rclpy
from rclpy.node import Node
from std_msgs.msg import String


class Listener(Node):
    def __init__(self):
        super().__init__('listener_node')
        self.sub = self.create_subscription(String, '/chatter', self.cb, 10)

    def cb(self, msg):
        pass
"""

FILES = [
    {"path": "src/demo/talker_node.py", "content": TALKER},
    {"path": "src/demo/listener_node.py", "content": LISTENER},
    {"path": "README.md", "content": "# demo\n没有 package.xml，也没 setup.py / CMakeLists\n"},
]

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class CDP(object):
    def __init__(self, url):
        self.ws = websocket.create_connection(url, timeout=60, suppress_origin=True)
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
            return "__ERR__ " + json.dumps(r["exceptionDetails"].get("exception", {}),
                                           ensure_ascii=False)[:200]
        return r.get("result", {}).get("value")

    def shot(self, path):
        data = base64.b64decode(self.send("Page.captureScreenshot", {"format": "png"})["data"])
        open(path, "wb").write(data)
        return len(data)


def main():
    os.makedirs(OUT, exist_ok=True)
    req = urllib.request.Request(DBG + "/json/new?" + urllib.parse.quote(BASE + "/", safe=":/?=&"),
                                 method="PUT")
    t = json.loads(opener.open(req, timeout=10).read().decode())
    cdp = CDP(t["webSocketDebuggerUrl"])
    cdp.send("Page.enable")
    cdp.send("Runtime.enable")
    cdp.send("Emulation.setDeviceMetricsOverride",
             {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
    time.sleep(2.5)

    cdp.js("document.getElementById('new-chat').click()")
    time.sleep(1.5)
    sid = cdp.js("window.RT && window.RT.getSid()")
    print("测试会话:", sid)

    imported = cdp.js("""(async () => {
      const r = await fetch('/api/project/import', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({sessionId: %s, files: %s})
      });
      const d = await r.json();
      return d.message || d.error || '';
    })()""" % (json.dumps(sid), json.dumps(FILES, ensure_ascii=False)))
    print("导入摘要:\n" + str(imported))

    cdp.js("localStorage.setItem('ros2tutor_sid', %s)" % json.dumps(sid))
    cdp.send("Page.reload", {"ignoreCache": True})
    time.sleep(3.0)
    print("当前会话:", cdp.js("window.RT.getSid()"),
          "| 文件:", cdp.js("document.querySelectorAll('#files .file-item').length"))

    cdp.js("document.getElementById('open-graph').click()")
    time.sleep(2.5)
    g = cdp.js("""(() => {
      const pane = document.getElementById('graph-pane');
      return {paneVisible: pane && !pane.classList.contains('hidden'),
              nodes: [...document.querySelectorAll('[data-node]')].map(n =>
                     (n.querySelector('.gn-name') || n).textContent.trim().slice(0, 24)),
              edges: document.querySelectorAll('.ge-hit').length,
              zoom: (document.getElementById('gp-zoom') || {}).textContent};
    })()""")
    print("画布:", json.dumps(g, ensure_ascii=False))
    cdp.shot(os.path.join(OUT, "无清单项目-架构图.png"))

    ok = bool(g and g.get("paneVisible") and len(g.get("nodes") or []) == 2
              and g.get("edges") == 1)
    print("\n结果:", "通过" if ok else "不通过")

    gone = cdp.js("""(async () => {
      const r = await fetch('/api/session/' + %s + '/delete', {method: 'POST'});
      return (await r.json()).ok;
    })()""" % json.dumps(sid))
    print("删除测试会话:", gone)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
