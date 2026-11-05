# -*- coding: utf-8 -*-
"""CDP 验证：抽屉「代码」页签的编辑器是否撑满了剩余高度。"""
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
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class CDP(object):
    def __init__(self, url):
        self.ws = websocket.create_connection(url, timeout=30, suppress_origin=True)
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
        data = base64.b64decode(self.send("Page.captureScreenshot", {"format": "png"})["data"])
        open(path, "wb").write(data)
        return len(data)


def main():
    os.makedirs(OUT, exist_ok=True)
    req = urllib.request.Request(DBG + "/json/new?" + urllib.parse.quote(BASE + "/", safe=":/?=&"),
                                 method="PUT")
    with opener.open(req, timeout=10) as r:
        t = json.loads(r.read().decode())
    cdp = CDP(t["webSocketDebuggerUrl"])
    cdp.send("Page.enable")
    cdp.send("Runtime.enable")
    cdp.send("Emulation.setDeviceMetricsOverride",
             {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
    time.sleep(2.5)

    cdp.js("window.RTGraph && window.RTGraph.reload()")
    time.sleep(2.0)

    # 点开第一个节点
    opened = cdp.js("""(() => {
      const g = document.querySelector('[data-node]');
      if (!g) return 'no-node';
      g.dispatchEvent(new MouseEvent('click', {bubbles: true}));
      return 'ok';
    })()""")
    print("打开抽屉:", opened)
    time.sleep(0.8)

    # 切到「代码」页签
    tab = cdp.js("""(() => {
      const t = [...document.querySelectorAll('.gd-tab')].find(e => e.dataset.tab === 'code');
      if (!t) return 'no-tab';
      t.click();
      return 'ok';
    })()""")
    print("切代码页:", tab)
    time.sleep(1.6)

    m = cdp.js("""(() => {
      const d = document.getElementById('gp-drawer');
      const b = document.getElementById('gd-body');
      const ta = document.getElementById('gd-code');
      if (!d || !b || !ta) return null;
      const dr = d.getBoundingClientRect(), br = b.getBoundingClientRect();
      const tr = ta.getBoundingClientRect();
      return {
        drawerH: Math.round(dr.height),
        bodyH: Math.round(br.height),
        taH: Math.round(tr.height),
        fillRatio: +(tr.height / br.height).toFixed(2),
        bodyClass: b.className,
        hasHScroll: ta.scrollWidth > ta.clientWidth + 1,
        lineCount: (ta.value.match(/\\n/g) || []).length + 1
      };
    })()""")
    print("尺寸:", m)
    n = cdp.shot(os.path.join(OUT, "抽屉-代码页.png"))
    print("截图:", n, "字节")

    ok = m and m["fillRatio"] > 0.75 and m["taH"] > 300
    print("结果:", "通过" if ok else "不通过")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
