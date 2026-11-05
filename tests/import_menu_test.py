# -*- coding: utf-8 -*-
"""验证「导入按钮 → 选 zip 压缩包」这条新路径。

之前点导入按钮只能弹目录选择框（input 带 webkitdirectory），
浏览器里 zip 文件是灰的点不动，所以用户「上传压缩包」一直失败。

本脚本用 CDP 的 DOM.setFileInputFiles 直接给隐藏 input 塞文件，
等价于用户真的在浏览框里选中了那个 zip。
"""
import base64
import json
import os
import time
import urllib.parse
import urllib.request

import websocket

BASE = "http://127.0.0.1:8125"
ZIP = os.path.abspath(os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "samples", "demo_ros2_ws.zip"))
OUT = r"C:\Users\mike\WorkBuddy\2026-11-05-09-34-34\out"
os.makedirs(OUT, exist_ok=True)

op = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def new_tab(url):
    t = json.loads(op.open(urllib.request.Request(
        "http://127.0.0.1:9444/json/new?" + urllib.parse.quote(url, safe=":/?=&"),
        method="PUT")).read())
    return t["webSocketDebuggerUrl"]


class CDP:
    def __init__(self, url):
        self.ws = websocket.create_connection(url, timeout=60, suppress_origin=True)
        self.n = 0

    def send(self, method, params=None):
        self.n += 1
        mid = self.n
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            r = json.loads(self.ws.recv())
            if r.get("id") == mid:
                if "error" in r:
                    raise RuntimeError("%s -> %s" % (method, r["error"]))
                return r.get("result", {})

    def js(self, expr):
        r = self.send("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                           "awaitPromise": True, "userGesture": True})
        return r.get("result", {}).get("value")

    def shot(self, path):
        d = self.send("Page.captureScreenshot", {"format": "png"})
        open(path, "wb").write(base64.b64decode(d["data"]))
        return path


def main():
    cdp = CDP(new_tab(BASE + "/"))
    cdp.send("Page.enable")
    cdp.send("Runtime.enable")
    cdp.send("DOM.enable")
    cdp.send("Emulation.setDeviceMetricsOverride",
             {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
    time.sleep(2.5)

    # 用全新会话，避免「已有项目，确认覆盖」的 confirm 卡住自动化
    cdp.js("localStorage.removeItem('ros2tutor_sid'); location.reload(); true")
    time.sleep(3.0)
    print("版本:", cdp.js("document.querySelector('script[src*=app]').getAttribute('src')"))

    # 1) 点导入按钮，菜单应该弹出来
    print("点导入按钮:", cdp.js("""(() => {
      const b = document.getElementById('import-project');
      b.click();
      const m = document.getElementById('import-menu');
      return {hidden: m.classList.contains('hidden'),
              items: [...m.querySelectorAll('.im-item b')].map(x => x.textContent),
              subs: [...m.querySelectorAll('.im-item i')].map(x => x.textContent)};
    })()"""))
    time.sleep(0.4)
    cdp.shot(os.path.join(OUT, "导入菜单.png"))

    # 2) 菜单里两个按钮都在、位置正常
    box = cdp.js("""(() => {
      const m = document.getElementById('import-menu').getBoundingClientRect();
      const b = document.getElementById('import-project').getBoundingClientRect();
      return {menu: {x: Math.round(m.x), y: Math.round(m.y),
                     w: Math.round(m.width), h: Math.round(m.height)},
              btn: {x: Math.round(b.x), y: Math.round(b.y)},
              above: m.bottom <= b.top + 2, inView: m.top >= 0 && m.bottom <= innerHeight};
    })()""")
    print("菜单位置:", box)

    # 3) 点「选择 zip 压缩包」——真实点击会开系统浏览框，这里改用 CDP 直接塞文件
    doc = cdp.send("DOM.getDocument", {"depth": -1})
    node = cdp.send("DOM.querySelector", {"nodeId": doc["root"]["nodeId"],
                                          "selector": "#project-zip"})["nodeId"]
    cdp.js("document.getElementById('pick-zip').click(); true")
    time.sleep(0.3)
    print("点 zip 项后菜单隐藏:", cdp.js(
        "document.getElementById('import-menu').classList.contains('hidden')"))

    cdp.send("DOM.setFileInputFiles", {"nodeId": node, "files": [ZIP]})
    print("已把 zip 塞进 input:", os.path.basename(ZIP))

    # 4) 等导入 + 出图
    nodes, waited = 0, 0.0
    while waited < 40:
        time.sleep(1.0)
        waited += 1
        st = cdp.js("""(() => {
          const sel = document.querySelectorAll('#gNode > g');
          return {nodes: sel.length,
                  pane: !document.getElementById('graph-pane').classList.contains('hidden'),
                  files: document.querySelectorAll('#files .file-item').length,
                  last: (() => {
                    const b = [...document.querySelectorAll('#chat .msg')];
                    return b.length ? b[b.length - 1].innerText.slice(0, 60) : '';
                  })()};
        })()""")
        nodes = st["nodes"]
        if nodes:
            print("导入完成，用时 %ds" % waited, st)
            break
    else:
        print("超时未出图:", st)

    fmt = cdp.js("""(() => {
      const g = window.RTGraph && window.RTGraph.state && window.RTGraph.state.graph;
      return g ? {nodes: g.nodes.length, edges: g.edges.length, stats: g.stats} : null;
    })()""")
    print("图形数据:", fmt)
    cdp.shot(os.path.join(OUT, "导入zip-出图.png"))

    ok = bool(box and not box["menu"]["w"] == 0 and box["inView"] and nodes >= 4)
    print("\n结果:", "通过" if ok else "失败")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
