# -*- coding: utf-8 -*-
"""CDP 端到端：发一句话 → 等生成 → 看邀请卡 → 点开架构图 → 验证开关。

真实调用模型，约 20-60 秒。
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
MSG = "帮我写一个节点：每隔 1 秒往 /hello 话题发布一条字符串消息"

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

    # 上一轮测试可能把开关关了，localStorage 会在同一浏览器里留下去 —— 先重置
    cdp.js("(() => { localStorage.setItem('rt_ask_graph','1'); return 1; })()")
    cdp.send("Page.reload", {"ignoreCache": True})
    time.sleep(3.0)

    # 开关默认应该是开的
    sw = cdp.js("""(() => {
      const b = document.getElementById('ask-graph-toggle');
      return b ? {pressed: b.getAttribute('aria-pressed'),
                  saved: localStorage.getItem('rt_ask_graph')} : null;
    })()""")
    print("开关初始:", sw)

    print("发送消息:", MSG)
    sent = cdp.js("""(() => {
      const i = document.getElementById('input'), b = document.getElementById('send');
      if (!i || !b) return 'no-ui';
      i.value = %s;
      i.dispatchEvent(new Event('input', {bubbles: true}));
      b.click();
      return 'ok';
    })()""" % json.dumps(MSG, ensure_ascii=False))
    print("发送:", sent)

    # 等邀请卡出现
    appeared, waited = False, 0
    while waited < 120:
        time.sleep(2)
        waited += 2
        if cdp.js("!!document.querySelector('.graph-offer')"):
            appeared = True
            break
        if cdp.js("!document.querySelector('.typing,.prog,.streaming') && document.querySelectorAll('.msg').length"):
            pass
    print("邀请卡出现:", appeared, "（等待 %ds）" % waited)
    if not appeared:
        tail = cdp.js("""(() => {
      const all = document.querySelectorAll('.msg');
      const last = all[all.length - 1];
      return last ? last.innerText.slice(0, 400) : '(没有消息)';
    })()""")
        print("最后一条回复:", tail)

    info = cdp.js("""(() => {
      const c = document.querySelector('.graph-offer');
      if (!c) return null;
      return {title: c.querySelector('.go-title').textContent,
              desc: c.querySelector('.go-desc').textContent.slice(0, 40),
              buttons: [...c.querySelectorAll('button')].map(b => b.textContent)};
    })()""")
    print("卡片内容:", json.dumps(info, ensure_ascii=False))
    cdp.shot(os.path.join(OUT, "生成后-邀请卡.png"))

    # 点「打开架构图」
    cdp.js("""(() => {
      const c = document.querySelector('.graph-offer');
      const b = c && c.querySelector('[data-act="yes"]');
      if (b) b.click();
      return !!b;
    })()""")
    time.sleep(2.5)
    after = cdp.js("""(() => {
      const pane = document.getElementById('graph-pane');
      const nodes = document.querySelectorAll('[data-node]').length;
      const gone = document.querySelector('.graph-offer.gone') !== null;
      return {paneVisible: pane && !pane.classList.contains('hidden'),
              nodes: nodes, cardHidden: gone};
    })()""")
    print("点开后:", after)
    cdp.shot(os.path.join(OUT, "生成后-架构图打开.png"))

    # 关掉开关，再确认它存住了
    cdp.js("""(() => {
      const b = document.getElementById('ask-graph-toggle');
      if (b.getAttribute('aria-pressed') === 'true') b.click();
      return true;
    })()""")
    time.sleep(0.4)
    off = cdp.js("""(() => ({
      saved: localStorage.getItem('rt_ask_graph'),
      pressed: document.getElementById('ask-graph-toggle').getAttribute('aria-pressed')
    }))()""")
    print("开关关闭后:", off)

    ok = (appeared and after and after["paneVisible"] and after["nodes"] >= 1
          and off and off["saved"] == "0" and off["pressed"] == "false")
    print("\n结果:", "通过" if ok else "不通过")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
