# -*- coding: utf-8 -*-
"""UI 回归：选文件夹导入时，`resource/<包名>` 不能被前端过滤掉。

    python -X utf8 tests/folder_import_ui_test.py

背景（实测踩过）：ament_python 的包身份证是 `resource/<包名>`——一个**没有扩展名**
的空文件。前端 app.js 里有一份自己的扩展名白名单（和后端 project_import._keep
是两份），只要前端这份没跟上，选文件夹导入就会把它过滤掉，后端根本收不到。
用户点「编译运行」看到的是

    ✕ 编译没通过：can't copy '.../resource/control_pkg'

明明是自己写的最简单的 demo，怎么连都不过——排查方向被完全带偏。

本测试不信任任何一侧的声明，做法是：在浏览器里真的走一遍「选文件夹」的
导入路径（给 File 覆写 webkitRelativePath，模拟 webkitdirectory 的结果），
然后到磁盘上数文件。

前置：
1. 服务在 8125，ROS2_DATA_DIR 指向测试数据目录（默认 C:/Temp/gdata_repro）
2. 无头 Edge 带 CDP：--remote-debugging-port=9444
"""
import base64
import glob
import json
import os
import sys
import time
import urllib.parse
import urllib.request
import zipfile

import websocket

BASE = "http://127.0.0.1:8125"
DBG = "http://127.0.0.1:9444"
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("ROS2_DATA_DIR") or "C:/Temp/gdata_repro"
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


def wait_for(cdp, expr, timeout=30.0, step=0.4):
    t0 = time.time()
    while time.time() - t0 < timeout:
        v = cdp.js(expr)
        if v:
            return v
        time.sleep(step)
    return cdp.js(expr)


def folder_payload():
    """把样例 zip 摊成「选中了 demo_ros2_ws 这个文件夹」的文件列表。

    真实浏览器给的 webkitRelativePath 形如 `demo_ros2_ws/src/...`，
    前端会剥掉第一层。这里连这一层也一起造出来，路径处理逻辑一并测到。
    """
    items = []
    with zipfile.ZipFile(os.path.join(ROOT, "samples", "demo_ros2_ws.zip")) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            items.append(("demo_ros2_ws/" + info.filename,
                          base64.b64encode(z.read(info)).decode()))
    # 一个不该被导入的图片：证明过滤没被放宽成「什么都收」
    items.append(("demo_ros2_ws/docs/diagram.png",
                  base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"0" * 64).decode()))
    return items


def main():
    sid = "fi" + str(int(time.time() * 1000))[-8:]
    print("测试会话 %s" % sid)

    req = urllib.request.Request(
        DBG + "/json/new?" + urllib.parse.quote(BASE + "/", safe=":/?=&"), method="PUT")
    with opener.open(req, timeout=10) as r:
        target = json.loads(r.read().decode())
    cdp = CDP(target["webSocketDebuggerUrl"])
    cdp.send("Page.enable")
    cdp.send("Runtime.enable")
    time.sleep(3.0)
    cdp.js("localStorage.setItem('ros2tutor_sid', %r); 'ok'" % sid)
    cdp.send("Page.reload")
    time.sleep(3.5)
    cdp.js("document.getElementById('imported-badge').classList.add('hidden'); 'ok'")

    payload = folder_payload()
    has_marker_in = [p for p, _ in payload if "/resource/" in p]
    check("测试数据里确实带了标记文件", len(has_marker_in) == 2, has_marker_in)

    n = cdp.js("""
      (() => {
        const items = %s;
        const dt = new DataTransfer();
        for (const it of items) {
          const bin = atob(it[1]);
          const u8 = new Uint8Array(bin.length);
          for (let i = 0; i < bin.length; i++) u8[i] = bin.charCodeAt(i);
          const f = new File([u8], it[0].split('/').pop());
          Object.defineProperty(f, 'webkitRelativePath', {value: it[0]});
          dt.items.add(f);
        }
        const inp = document.getElementById('project-file');
        const total = dt.files.length;      // 赋值后 input 会被清空，先数
        inp.files = dt.files;
        inp.onchange();          // 与真实选完文件夹后触发的是同一个处理函数
        return total;
      })()
    """ % json.dumps(payload))
    check("文件已塞进 input 并触发导入", n and int(n) > 0, "共 %s 个" % n)

    done = wait_for(cdp, "(document.getElementById('imported-badge').textContent || '').length > 0")
    badge = cdp.js("document.getElementById('imported-badge').textContent") or ""
    check("导入完成", bool(done), badge[:40])

    # 摘要里出现「自动补了」就说明前端还是漏了、靠后端兜底补回来的
    chat = cdp.js("document.body.innerText") or ""
    check("前端自己把标记文件送上去了（没靠后端兜底）",
          "自动补了" not in chat, "摘要里出现了「自动补了」")
    check("摘要里列出两个包", "control_pkg" in chat and "perception_pkg" in chat)

    ws = session_ws(sid)
    for pkg in ("control_pkg", "perception_pkg"):
        p = os.path.join(ws, "src", pkg, "resource", pkg)
        check("%s/resource/%s 真的落盘了" % (pkg, pkg), os.path.isfile(p), p)
    check("图片仍然被挡在外面",
          not os.path.exists(os.path.join(ws, "docs", "diagram.png")))
    check("节点源码都在",
          all(os.path.isfile(os.path.join(ws, "src", pkg, pkg, m))
              for pkg, m in (("control_pkg", "planner_node.py"),
                             ("perception_pkg", "camera_node.py"))))

    print("\n%s（失败 %d 项）" % ("全部通过" if not FAILED else "有失败", len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
