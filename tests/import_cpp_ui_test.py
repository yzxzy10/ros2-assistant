# -*- coding: utf-8 -*-
"""纯 C++ 项目导入后点「架构图」应该给出解释，而不是静默没反应。

步骤：新开一段对话 → 用真实接口导入一个 ament_cmake 包 → 点左栏架构图
→ 断言出现「C++」说明、且画布没有展开 → 删掉这段测试对话。
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

CPP = """#include "rclcpp/rclcpp.hpp"
#include "std_msgs/msg/string.hpp"

int main(int argc, char ** argv) {
  rclcpp::init(argc, argv);
  auto node = std::make_shared<rclcpp::Node>("cpp_talker");
  auto pub = node->create_publisher<std_msgs::msg::String>("/cpp_chatter", 10);
  rclcpp::spin(node);
  rclcpp::shutdown();
}
"""

FILES = [
    {"path": "src/nav_cpp/package.xml",
     "content": '<package format="3"><name>nav_cpp</name><version>0.0.1</version>'
                '<export><build_type>ament_cmake</build_type></export></package>'},
    {"path": "src/nav_cpp/CMakeLists.txt",
     "content": "cmake_minimum_required(VERSION 3.8)\nproject(nav_cpp)\n"
                "find_package(ament_cmake REQUIRED)\nadd_executable(nav_node src/nav_node.cpp)\n"
                "ament_package()\n"},
    {"path": "src/nav_cpp/src/nav_node.cpp", "content": CPP},
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

    # 新开一段对话，避免动到用户当前项目
    cdp.js("document.getElementById('new-chat').click()")
    time.sleep(1.5)
    sid = cdp.js("window.RT && window.RT.getSid()")
    print("测试会话:", sid)
    if not sid:
        print("结果: 不通过（拿不到会话号）")
        return 1

    imported = cdp.js("""(async () => {
      const r = await fetch('/api/project/import', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({sessionId: %s, files: %s})
      });
      const d = await r.json();
      return (d.message || d.error || '').slice(0, 120);
    })()""" % (json.dumps(sid), json.dumps(FILES, ensure_ascii=False)))
    print("导入结果:", imported)

    # 切到测试会话再刷新：应用是从 localStorage 的 ros2tutor_sid 恢复的
    cdp.js("localStorage.setItem('ros2tutor_sid', %s)" % json.dumps(sid))
    cdp.send("Page.reload", {"ignoreCache": True})
    time.sleep(3.0)
    print("当前会话:", cdp.js("window.RT.getSid()"),
          "| 文件数:", cdp.js("document.querySelectorAll('#files .file-item').length"),
          "| 有按钮:", cdp.js("!!document.getElementById('open-graph')"),
          "| 按钮可见:", cdp.js("""(() => { const b = document.getElementById('open-graph');
            return !!b && !b.closest('#project-acts').classList.contains('hidden'); })()"""))

    before = cdp.js("document.querySelectorAll('.msg').length")
    cdp.js("document.getElementById('open-graph').click()")
    time.sleep(2.5)
    after = cdp.js("""(() => {
      const all = document.querySelectorAll('.msg');
      const last = all[all.length - 1];
      const pane = document.getElementById('graph-pane');
      return {msgs: all.length, last: last ? last.innerText.slice(0, 120) : '',
              paneHidden: pane ? pane.classList.contains('hidden') : null};
    })()""")
    print("点之后:", json.dumps(after, ensure_ascii=False))
    cdp.shot(os.path.join(OUT, "C++项目-点架构图提示.png"))

    explained = bool(after and "C++" in (after.get("last") or ""))
    ok = bool(explained and before != after.get("msgs") and after.get("paneHidden") is True)
    print("\n结果:", "通过" if ok else "不通过")

    # 收拾现场：删掉这段测试对话
    gone = cdp.js("""(async () => {
      const r = await fetch('/api/session/' + %s + '/delete', {method: 'POST'});
      const d = await r.json();
      return JSON.stringify(d).slice(0, 80);
    })()""" % json.dumps(sid))
    print("删除测试会话:", gone)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
