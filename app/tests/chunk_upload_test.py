# -*- coding: utf-8 -*-
"""大包导入回归：分片上传要有进度、要能传完。

    python -X utf8 tests/chunk_upload_test.py [zip路径]

默认拿桌面上的 `navigation2-main.zip`（约 65MB，会切成 9 片）当样例；
没有这个文件就跳过（不编造小包冒充大包——大小才是这条路径的触发条件）。

为什么单独一个测试：原来界面只在「整片传完」时把 0/3 改成 1/3，一片 32MB 在
普通家宽上要传几十秒，用户看到的就是「一直卡在 0/3」。现在改成 8MB 一片 +
XHR 上传进度 + 无进展看门狗 + 失败重传，这个测试把这三件事一起钉住。
"""
import glob
import json
import os
import sys
import time
import urllib.parse
import urllib.request

import websocket

BASE = "http://127.0.0.1:8125"
DBG = "http://127.0.0.1:9444"
DATA = os.environ.get("ROS2_DATA_DIR") or "C:/Temp/gdata_chunk"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_ZIP = "C:/Users/mike/Desktop/navigation2-main.zip"
CHUNK = 8 * 1024 * 1024
FAILED = []

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def session_ws(sid):
    """找到这个会话的工作区。

    数据目录可能被 ROS2_DATA_DIR 指到别处（测试时常常换目录），
    找不到就按已知位置兜一圈——别让「目录猜错」冒充成功能失败。
    """
    cands = [os.path.join(DATA, "sessions", sid, "ros2_ws"),
             os.path.join(ROOT, "sessions", sid, "ros2_ws")]
    cands += sorted(glob.glob("C:/Temp/gdata*/sessions/%s/ros2_ws" % sid))
    for c in cands:
        if os.path.isdir(c):
            return c
    return cands[0]


def check(name, ok, extra=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + (("  " + str(extra)) if extra else ""))
    if not ok:
        FAILED.append(name)


class CDP(object):
    def __init__(self, url):
        self.ws = websocket.create_connection(url, timeout=180, suppress_origin=True)
        self.n = 0
        self.events = []

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
            self.events.append(msg)

    def js(self, expr):
        r = self.send("Runtime.evaluate", {"expression": expr, "returnByValue": True,
                                           "awaitPromise": False, "userGesture": True})
        if r.get("exceptionDetails"):
            return "__ERR__ " + json.dumps(r["exceptionDetails"], ensure_ascii=False)[:200]
        return r.get("result", {}).get("value")


def main():
    zip_path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_ZIP
    if not os.path.isfile(zip_path):
        print("跳过：找不到大 zip 样例 %s" % zip_path)
        return 0
    size = os.path.getsize(zip_path)
    expect_chunks = (size + CHUNK - 1) // CHUNK
    print("样例 %s（%.1f MB，预计 %d 片）" % (os.path.basename(zip_path),
                                            size / 1048576.0, expect_chunks))

    req = urllib.request.Request(
        DBG + "/json/new?" + urllib.parse.quote(BASE + "/", safe=":/?=&"), method="PUT")
    with opener.open(req, timeout=10) as r:
        target = json.loads(r.read().decode())
    cdp = CDP(target["webSocketDebuggerUrl"])
    cdp.send("Page.enable")
    cdp.send("Runtime.enable")
    cdp.send("Network.enable")
    time.sleep(3.0)
    sid = "ck" + str(int(time.time() * 1000))[-8:]
    cdp.js("localStorage.setItem('ros2tutor_sid', %r); 'ok'" % sid)
    cdp.send("Page.reload")
    time.sleep(3.5)
    cdp.js("document.getElementById('imported-badge').classList.add('hidden'); 'ok'")

    doc = cdp.send("DOM.getDocument", {"depth": -1})
    node = cdp.send("DOM.querySelector", {"nodeId": doc["root"]["nodeId"],
                                          "selector": "#project-zip"})
    cdp.send("DOM.setFileInputFiles", {"files": [zip_path], "nodeId": node["nodeId"]})
    cdp.js("document.getElementById('project-zip').onchange(); 'go'")

    # 上传期间盯住进度文案：必须出现「MB（x%）」，且百分比只能往上走
    seen_pct, t0, done_txt = [], time.time(), ""
    while time.time() - t0 < 300:
        txt = cdp.js("document.body.innerText") or ""
        for line in txt.splitlines():
            if "正在上传" in line and "%" in line:
                try:
                    pct = int(line.split("（")[1].split("%")[0])
                except Exception:      # noqa: BLE001
                    pct = None
                if pct is not None and pct not in seen_pct:
                    seen_pct.append(pct)
        if "已导入项目" in txt:
            done_txt = txt
            break
        if "项目导入失败" in txt or "未找到可导入的文件" in txt:
            done_txt = txt
            break
        time.sleep(0.5)

    print("      进度采样：", seen_pct[:14])
    check("上传过程中有百分比进度（不是只显示第几片）", len(seen_pct) >= 2, seen_pct[:6])
    check("进度是递增的", seen_pct == sorted(seen_pct), seen_pct[:6])
    check("最后到过 100%", bool(seen_pct) and max(seen_pct) >= 99, seen_pct[-3:])
    check("导入完成（没卡住）", "已导入项目" in (done_txt or ""),
          [ln for ln in (done_txt or "").splitlines() if ln.strip()][-3:])

    # 网络层：每一片都必须 200，片数要等于 ceil(size/8MB)
    reqs, ok_chunks = {}, []
    for ev in cdp.events:
        m, p = ev.get("method"), ev.get("params") or {}
        if m == "Network.requestWillBeSent":
            u = p["request"]["url"]
            if "import-chunk" in u:
                reqs[p["requestId"]] = {"url": u, "status": None}
        elif m == "Network.responseReceived":
            if p["requestId"] in reqs:
                reqs[p["requestId"]]["status"] = p["response"]["status"]
    for r in reqs.values():
        if r["status"] == 200:
            ok_chunks.append(r["url"])
    check("分片数 = ceil(大小/8MB)", len(reqs) == expect_chunks,
          "%s 片（期望 %s）" % (len(reqs), expect_chunks))
    check("所有分片都 200", len(ok_chunks) == len(reqs),
          [r["status"] for r in reqs.values()])

    ws = session_ws(sid)
    pkgs = 0
    for dirpath, _dirs, files in os.walk(ws):
        if "package.xml" in files:
            pkgs += 1
    check("工作区真的落了包（不是空目录）", pkgs > 10, "package.xml %d 个" % pkgs)

    print("\n%s（失败 %d 项）" % ("全部通过" if not FAILED else "有失败", len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
