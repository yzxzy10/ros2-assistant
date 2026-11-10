# -*- coding: utf-8 -*-
"""CDP 验证：导入项目后能否移除、移除后画布与文件列表是否清空、能否再导入。"""
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
        r = self.send("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                           "awaitPromise": True, "userGesture": True})
        if "exceptionDetails" in r:
            print("  JS 异常:", json.dumps(r["exceptionDetails"])[:200])
        return r.get("result", {}).get("value")

    def shot(self, path):
        r = self.send("Page.captureScreenshot", {"format": "png"})
        open(path, "wb").write(base64.b64decode(r["data"]))


IMPORT = """
(async () => {
  const sid = localStorage.getItem('ros2tutor_sid');
  const r = await fetch('/api/project/import', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({sessionId: sid,
      files:[{path:'demo_ros2_ws.zip', content:'%s'}]})});
  const d = await r.json();
  if (window.RTGraph) window.RTGraph.reload();
  window.RTRefreshFiles && window.RTRefreshFiles();
  return d.ok;
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

    b64 = base64.b64encode(open(ZIP, "rb").read()).decode("ascii")
    print("导入:", cdp.js(IMPORT % b64))
    time.sleep(2.5)
    cdp.js("""(() => { const r = document.getElementById('imported-badge');
                       if (r) { r.classList.remove('hidden'); } return true; })()""")

    before = cdp.js("""(() => {
      const acts = document.getElementById('project-acts');
      const rm = document.getElementById('remove-project');
      const dl = document.getElementById('download-zip');
      const ar = acts && acts.getBoundingClientRect();
      return {removeVisible: acts && !acts.classList.contains('hidden'),
              dlVisible: !!dl,
              files: document.querySelectorAll('#files .file-item').length,
              text: rm ? rm.textContent.trim() : '',
              actsBox: ar ? {x: Math.round(ar.x), y: Math.round(ar.y),
                             w: Math.round(ar.width), h: Math.round(ar.height)} : null,
              inViewport: ar ? (ar.top >= 0 && ar.bottom <= window.innerHeight) : false};
    })()""")
    print("移除前:", before)
    cdp.shot(os.path.join(OUT, "移除项目-按钮.png"))

    # 自动同意 confirm
    cdp.js("window.confirm = function () { return true; };")
    cdp.js("document.getElementById('remove-project').click()")
    time.sleep(2.5)

    after = cdp.js("""(() => {
      const acts = document.getElementById('project-acts');
      const g = document.getElementById('graph-pane');
      const last = [...document.querySelectorAll('.msg.assistant')].pop();
      return {removeHidden: acts.classList.contains('hidden'),
              files: document.querySelectorAll('#files .file-item').length,
              emptyText: (document.getElementById('files') || {}).textContent,
              graphHidden: g ? g.classList.contains('hidden') : null,
              nodes: document.querySelectorAll('#gNode [data-node]').length,
              lastMsg: last ? last.innerText.slice(0, 60).replace(/\\n/g, ' ') : ''};
    })()""")
    print("移除后:", after)
    cdp.shot(os.path.join(OUT, "移除项目-之后.png"))

    print("再导入:", cdp.js(IMPORT % b64))
    time.sleep(2.5)
    again = cdp.js("""(() => ({
      files: document.querySelectorAll('#files .file-item').length,
      nodes: document.querySelectorAll('#gNode [data-node]').length}))()""")
    print("重新导入后:", again)

    ok = (before["removeVisible"] and after["removeHidden"] and after["files"] == 0
          and after["nodes"] == 0 and again["nodes"] == 4)
    print("\n结果:", "通过" if ok else "不通过")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
