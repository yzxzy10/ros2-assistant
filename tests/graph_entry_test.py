# -*- coding: utf-8 -*-
"""左栏「架构图」常驻入口的回归测试（不调模型，秒级）。

校验：打开一个已经生成过代码的历史会话 → 左栏出现架构图按钮 → 点开有节点。
这条路径以前不存在：图只能从「生成完毕」那张一次性卡片进入，漏看就再也打不开。
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
            return "__ERR__ " + str(r["exceptionDetails"].get("text"))
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

    print("JS 版本:", cdp.js("document.querySelectorAll('script[src*=app.js]')[0].getAttribute('src')"))

    # 逐个打开历史会话，找到那个已经生成过代码的（有项目文件的）
    n = 0
    picked = cdp.js("document.querySelectorAll('#history-list .hist-item').length")
    print("历史会话数:", picked)
    found = False
    for i in range(int(picked or 0)):
        title = cdp.js("""(() => {
          const it = document.querySelectorAll('#history-list .hist-item')[%d];
          if (!it) return 'none';
          it.querySelector('.hist-body').click();
          return it.querySelector('.hist-title').innerText + ' | ' +
                 it.querySelector('.hist-meta').innerText;
        })()""" % i)
        time.sleep(1.8)
        fc = cdp.js("document.querySelectorAll('#files .file-item').length")
        print("  会话[%d] %s → 文件 %s" % (i, title, fc))
        if fc:
            found = True
            break
    if not found:
        print("没找到生成过代码的会话，无法验证入口")
        return 1
    time.sleep(1.0)

    state = cdp.js("""(() => {
      const acts = document.getElementById('project-acts');
      const btn = document.getElementById('open-graph');
      return {actsVisible: acts && !acts.classList.contains('hidden'),
              hasBtn: !!btn,
              btnText: btn ? btn.innerText.trim() : '',
              files: document.querySelectorAll('#files .file-item').length};
    })()""")
    print("左栏状态:", json.dumps(state, ensure_ascii=False))
    cdp.shot(os.path.join(OUT, "架构图-左栏入口.png"))

    if not state.get("hasBtn"):
        print("结果: 不通过（没有架构图按钮）")
        return 1

    clicked = cdp.js("""(() => { const b = document.getElementById('open-graph');
      if (!b) return 'no-btn'; b.click(); return 'ok'; })()""")
    print("点击:", clicked)
    time.sleep(2.5)

    after = cdp.js("""(() => {
      const pane = document.getElementById('graph-pane');
      return {paneVisible: pane && !pane.classList.contains('hidden'),
              nodes: document.querySelectorAll('[data-node]').length,
              edges: document.querySelectorAll('.ge-hit').length,
              empty: !!document.querySelector('.gp-empty:not(.hidden)')};
    })()""")
    print("打开后:", json.dumps(after, ensure_ascii=False))
    cdp.shot(os.path.join(OUT, "架构图-从入口打开.png"))

    ok = bool(state.get("actsVisible") and after and after.get("paneVisible")
              and after.get("nodes", 0) >= 1)
    print("\n结果:", "通过" if ok else "不通过")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
