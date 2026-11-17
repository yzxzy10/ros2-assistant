# -*- coding: utf-8 -*-
"""UI 回归：上次的验证结果在「退出验证 / 刷新页面」之后要能自己回到画布上。

    python -X utf8 tests/verify_restore_ui_test.py

前置：
1. 服务在 8125（mock 后端即可）：
   ROS2_DATA_DIR=C:/Temp/gdata_uitest PORT=8125 ROS2_CHAIN_MOCK=1 python -X utf8 server.py
2. 无头 Edge 带 CDP：
   msedge --headless=new --disable-gpu --no-sandbox --no-proxy-server \
          --remote-debugging-port=9444 --user-data-dir=C:/Temp/edgeprof

做法（全程在浏览器里真跑一遍，不看服务端日志）：
导入 demo → 跑一遍验证 → 等结论条 → 退出验证（画面清空）→ reload 架构图
→ 断言绿线、日志框、结论条自己回来了，并且标明「上次运行」。
之后整页刷新再来一次，模拟第二天开机。
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
            return "__ERR__ " + json.dumps(r["exceptionDetails"].get("exception") or
                                           r["exceptionDetails"], ensure_ascii=False)[:300]
        return r.get("result", {}).get("value")

    def shot(self, name):
        os.makedirs(SHOT, exist_ok=True)
        p = os.path.join(SHOT, name)
        data = base64.b64decode(self.send("Page.captureScreenshot", {"format": "png"})["data"])
        with open(p, "wb") as f:
            f.write(data)
        return p


def wait_for(cdp, expr, want=True, timeout=25.0, step=0.4):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = cdp.js(expr)
        if isinstance(v, str) and v.startswith("__ERR__"):
            return v
        if bool(v) == want:
            return v
        time.sleep(step)
    return cdp.js(expr)


def main():
    with open(os.path.join(ROOT, "samples", "demo_ros2_ws.zip"), "rb") as f:
        b64 = base64.b64encode(f.read()).decode()

    sid = "vr" + str(int(time.time() * 1000))[-8:]
    print("测试会话 %s" % sid)

    req = urllib.request.Request(
        DBG + "/json/new?" + urllib.parse.quote(BASE + "/", safe=":/?=&"), method="PUT")
    with opener.open(req, timeout=10) as r:
        target = json.loads(r.read().decode())
    cdp = CDP(target["webSocketDebuggerUrl"])
    cdp.send("Page.enable")
    cdp.send("Runtime.enable")
    cdp.send("Emulation.setDeviceMetricsOverride",
             {"width": 1440, "height": 900, "deviceScaleFactor": 1, "mobile": False})
    time.sleep(3.0)

    # 定位到测试会话，再导入 demo 项目（不用拖拽，直接走二进制直传接口）
    cdp.js("localStorage.setItem('ros2tutor_sid', %r); 'ok'" % sid)
    cdp.send("Page.reload")
    time.sleep(3.5)
    print("当前 sid:", cdp.js("localStorage.getItem('ros2tutor_sid')"))

    imp = cdp.js("""
      (async () => {
        const b = atob(%r);
        const u8 = new Uint8Array(b.length);
        for (let i = 0; i < b.length; i++) u8[i] = b.charCodeAt(i);
        const r = await fetch('/api/project/import-zip?sessionId=%s&name=demo.zip',
                              {method: 'POST', body: u8,
                               headers: {'Content-Type': 'application/octet-stream'}});
        const j = await r.json().catch(() => ({}));
        return JSON.stringify({status: r.status, msg: (j.message || j.error || '').slice(0, 90)});
      })()
    """ % (b64, sid))
    check("导入 demo 项目", imp and "200" in str(imp), imp)
    cdp.js("RTGraph.reload()")
    time.sleep(2.0)

    nodes = cdp.js("document.querySelectorAll('#gp-svg [data-node]').length")
    check("架构建出来了", bool(nodes), "节点 %s" % nodes)

    # ---- 真跑一遍（mock 后端，与服务端同一套解析/结论口径）----
    cdp.js("RTGraph.verify(); 'go'")
    shown = wait_for(cdp, "!document.getElementById('vy-result').classList.contains('hidden')", timeout=40)
    head1 = cdp.js("document.getElementById('vy-result-head').textContent")
    check("跑完出了结论条", bool(shown), head1)
    check("结论是「通过」", "通过" in str(head1), head1)
    check("刚跑完不该标「上次运行」", "上次运行" not in str(head1), head1)
    green = cdp.js("document.querySelectorAll('#gp-svg .vy-ok').length")
    check("连线变绿了", bool(green), "vy-ok=%s" % green)
    cdp.shot("restore_1_live.png")

    # ---- 退出验证：画面清空（这正是老版本「切走再切回就没了」的起点）----
    cdp.js("RTGraph.verifyExit(); 'exit'")
    time.sleep(1.0)
    hidden = cdp.js("document.getElementById('vy-result').classList.contains('hidden')")
    check("退出后结论条收起", bool(hidden))
    gone = cdp.js("document.querySelectorAll('#gp-svg .vy-ok').length")
    check("退出后绿线清空", not gone, "vy-ok=%s" % gone)

    # ---- 重新载入架构图：上次的结果要自己回来 ----
    cdp.js("RTGraph.reload()")
    back = wait_for(cdp, "!document.getElementById('vy-result').classList.contains('hidden')", timeout=15)
    head2 = cdp.js("document.getElementById('vy-result-head').textContent")
    check("重载后结论条自动回来", bool(back), head2)
    check("标明是上次运行", "上次运行" in str(head2), head2)
    green2 = cdp.js("document.querySelectorAll('#gp-svg .vy-ok').length")
    check("绿线也回来了", bool(green2), "vy-ok=%s" % green2)
    logs = cdp.js("document.querySelectorAll('#gp-svg .vy-log, #gp-svg [class*=vy-lg]').length")
    print("      日志框元素数 %s" % logs)
    locked = cdp.js("document.getElementById('gp-canvas').classList.contains('vy-locked')")
    check("回看时画布上锁（避免把旧结果贴到新图上改）", bool(locked))
    btn = cdp.js("document.getElementById('gp-verify').textContent")
    check("按钮显示「退出验证」", "退出验证" in str(btn), btn)
    cdp.shot("restore_2_restored.png")

    # ---- 整页刷新（模拟关掉明天再开）----
    cdp.send("Page.reload")
    time.sleep(6.0)
    back2 = wait_for(cdp, "!document.getElementById('vy-result').classList.contains('hidden')", timeout=20)
    head3 = cdp.js("document.getElementById('vy-result-head').textContent")
    check("刷新页面后结果还在", bool(back2), head3)
    check("刷新后仍标「上次运行」", "上次运行" in str(head3), head3)
    cdp.shot("restore_3_reload.png")

    # 图改了就作废：改一条连线的草稿不该把旧绿线贴上来
    cdp.js("RTGraph.verifyExit(); 'exit'")
    time.sleep(0.6)
    print("\n%s" % ("全部通过" if not FAILED else "失败 %d 项：%s" % (len(FAILED), FAILED)))
    print("截图目录 %s" % SHOT)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
