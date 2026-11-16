# -*- coding: utf-8 -*-
"""把一份 URDF 场景渲染成一个独立可打开的演示页（不依赖后端）。

为什么需要它
------------
网页里的预览要先生成模型、再取场景 JSON。答辩/文档里需要的是「一个文件双击就能看」，
所以这里把 style.css + preview.js 内联进 HTML，再把场景 JSON 塞进 <script>，
用 stub 的 fetch 喂给预览模块 —— 预览代码一行都不用改，不会出现「演示版和线上版不一致」。

用法：
    python scripts/make_preview_page.py <urdf文件> <输出html> [包名]

    python scripts/make_preview_page.py output/demo_arm_6dof/ros2_ws/src/arm_6dof/urdf/arm_6dof.urdf.xacro \
        output/arm_6dof_preview.html arm_6dof
"""
import json
import os
import re
import sys

# 本文件在 <app>/scripts/ 下，往上一级就是应用根（含 server.py、app 包、public/）
APP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, APP)

from app import model_scene  # noqa: E402

TITLE = "六轴机械臂 · 仿真模型"


def build(urdf_path, out_path, pkg="", title=TITLE, pose=None, intro=""):
    urdf = open(urdf_path, encoding="utf-8").read()
    scene = model_scene.parse_model(urdf)

    # 零位往往是一条直杆，看不出形态；演示页允许注入一个姿态
    if pose:
        for j in scene["joints"]:
            if j["name"] in pose:
                j["value"] = pose[j["name"]]

    css = open(os.path.join(APP, "public", "style.css"), encoding="utf-8").read()
    pv = open(os.path.join(APP, "public", "preview.js"), encoding="utf-8").read()

    rows = []
    for j in scene["joints"]:
        if j["type"] == "fixed":
            continue
        lim = j.get("limit") or [0, 0]
        rng = ("0 – %.0f mm" % (lim[1] * 1000)) if j["type"] == "prismatic" \
            else "±%.0f°" % (lim[1] * 180 / 3.14159)
        axis = "xyz"[j["axis"].index(max(j["axis"]))]
        rows.append("      <tr><td>%s</td><td>%s</td><td>绕 %s</td><td>%s</td></tr>"
                    % (j["name"], "转动" if j["type"] == "revolute" else "平移", axis, rng))

    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<script type="importmap">
{"imports": {"three": "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js"}}
</script>
<style>
__CSS__
  body { margin: 0; padding: 24px; background: #f6f7f9; }
  .demo { max-width: 900px; margin: 0 auto; }
  .demo h1 { font-size: 22px; margin: 0 0 6px; color: #1f2430; }
  .demo .sub { color: #5b6472; font-size: 13px; margin: 0 0 16px; line-height: 1.75; }
  .demo table { width: 100%; border-collapse: collapse; margin: 14px 0 6px; font-size: 13px; }
  .demo th, .demo td { text-align: left; padding: 7px 10px; border-bottom: 1px solid #e6e8ec; }
  .demo th { color: #4a5261; font-weight: 600; background: #fafbfc; }
  .demo td { color: #2b313d; font-family: ui-monospace, Menlo, Consolas, monospace; }
  .demo h2 { font-size: 15px; margin: 22px 0 8px; color: #1f2430; }
  .demo .hint { font-size: 12px; color: #6b7280; margin: 8px 0 0; }
</style>
</head>
<body>
<div class="demo">
  <h1>__TITLE__</h1>
  <p class="sub">__INTRO__</p>
  <div id="slot"></div>

  <h2>关节链</h2>
  <table>
    <thead><tr><th>关节</th><th>类型</th><th>轴</th><th>行程</th></tr></thead>
    <tbody>
__ROWS__
    </tbody>
  </table>
  <p class="hint">工具栏可以切零位 / 演示姿态 / 坐标轴 / 自动旋转；下面的读数就是当前末端在 URDF 坐标系下的位姿。</p>
</div>

<script id="scene-data" type="application/json">__SCENE__</script>
<script>__PREVIEW__</script>
<script>
  var SCENE = JSON.parse(document.getElementById("scene-data").textContent);
  window.fetch = function () {
    return Promise.resolve({
      ok: true, status: 200,
      json: function () { return Promise.resolve(SCENE); }
    });
  };
  window.RTPreview.mount(document.getElementById("slot"), "demo", "__PKG__");

  // 自检：把渲染结果写进标题，方便无人值守时确认是否真的画出来了
  setTimeout(function () {
    var n = document.querySelectorAll("#slot input[type=range]").length;
    var cv = document.querySelectorAll("#slot canvas").length;
    document.title = (cv ? "OK " : "FAIL ") + n + "/" + cv;
  }, 1800);
</script>
</body>
</html>
"""
    html = (html.replace("__TITLE__", title)
                .replace("__INTRO__", intro)
                .replace("__CSS__", css)
                .replace("__ROWS__", "\n".join(rows))
                .replace("__SCENE__", json.dumps(scene, ensure_ascii=False))
                .replace("__PREVIEW__", pv)
                .replace("__PKG__", pkg))
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(html)
    return out_path, scene


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    urdf, out = sys.argv[1], sys.argv[2]
    pkg = sys.argv[3] if len(sys.argv) > 3 else ""
    path, sc = build(urdf, out, pkg)
    print("生成 %s（%d link / %d joint）" % (path, len(sc["links"]), len(sc["joints"])))
