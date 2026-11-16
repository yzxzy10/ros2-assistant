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

TITLE = "仿真模型预览"


def _mesh_stats(scene):
    """统计网格件数与三角面（二进制 STL 每面 50 字节）。"""
    n = tris = 0
    for l in scene["links"]:
        for v in l["visuals"]:
            g = v["geometry"]
            if g.get("kind") != "mesh":
                continue
            n += 1
            if g.get("bytes"):
                tris += g["bytes"] // 50
    return n, tris


def _auto_bits(scene):
    """从场景里认出这是什么，用来生成标题与一句话简介。"""
    names = [l["name"] for l in scene["links"]]
    joints = [j["name"] for j in scene["joints"]]
    steers = [n for n in joints if "steer_joint" in n]
    wheels = [n for n in names if n.endswith("wheel_link")]
    moving = [j for j in scene["joints"] if j["type"] in ("revolute", "prismatic")]
    n_mesh, n_tri = _mesh_stats(scene)
    mesh_note = ("，%d 件真实网格共 %s 个三角面" % (n_mesh, format(n_tri, ","))) if n_mesh else ""

    # 关节名不一定是 joint1 这种：真实 CAD 导出常见 1_JOINT / 2_JOINT。
    # 判据改成「有可动关节、又没有轮子」就是机械臂。
    if moving and not wheels:
        n_rev = len([j for j in moving if j["type"] == "revolute"])
        n_pri = len([j for j in moving if j["type"] == "prismatic"])
        kind = []
        if n_rev:
            kind.append("%d 个转动轴" % n_rev)
        if n_pri:
            kind.append("%d 个移动轴" % n_pri)
        return ("%d 轴机械臂 · 仿真模型" % len(moving),
                "共 %d 个可动关节（%s）%s。" % (len(moving), " + ".join(kind), mesh_note))
    if steers:
        return ("四轮阿克曼转向车 · 仿真模型",
                "后轮驱动、前轮打方向（%d 个转向关节 + %d 个轮子），"
                "车底是转向机 + 齿条 + 左右拉杆的梯形连杆造型。" % (len(steers), len(wheels)))
    if wheels:
        return ("差速底盘 · 仿真模型",
                "%d 个轮子（%d 个驱动），顶部带雷达。" % (len(wheels), len(wheels) // 2))
    return (TITLE, "")


def build(urdf_path, out_path, pkg="", title="", pose=None, intro=""):
    urdf = open(urdf_path, encoding="utf-8").read()
    scene = model_scene.parse_model(urdf)
    # URDF 同级目录的上一层就是包根（urdf/ -> pkg/），有 meshes/ 就把网格一起内联
    pkg_root = os.path.dirname(os.path.dirname(os.path.abspath(urdf_path)))
    if os.path.isdir(os.path.join(pkg_root, "meshes")):
        # 独立页面是本地文件，不用受服务端 1.2MB 的流量约束；
        # 真实 CAD 导出的网格动辄几 MB，按 24MB 放开
        model_scene._attach_mesh_data(scene, pkg_root, limit=24 * 1024 * 1024)

    # 零位往往是一条直杆，看不出形态；演示页允许注入一个姿态
    if pose:
        for j in scene["joints"]:
            if j["name"] in pose:
                j["value"] = pose[j["name"]]

    # 细长模型给高一点的画布
    has_wheel = any(l["name"].endswith("wheel_link") for l in scene["links"])
    if not has_wheel:
        scene["canvas_height"] = 420
        scene["view_az"] = 0.95
        # 俯角要留低一点：抬高会把这叠板件压成「一张饼」，看不出立柱和层高
        scene["view_el"] = 0.28

    auto_title, auto_intro = _auto_bits(scene)
    title = title or auto_title
    intro = intro or auto_intro

    css = open(os.path.join(APP, "public", "style.css"), encoding="utf-8").read()
    # 换引擎：内联 Babylon 查看器（三.js 版保留为 preview.js 以便回滚）
    pv = open(os.path.join(APP, "public", "viewer_babylon.js"), encoding="utf-8").read()

    rows = []
    for j in scene["joints"]:
        if j["type"] == "fixed":
            continue
        lim = j.get("limit") or [0, 0]
        if j["type"] == "prismatic":
            rng = "0 – %.0f mm" % (lim[1] * 1000)
            verb = "沿"
        else:
            rng = "±%.0f°" % (lim[1] * 180 / 3.14159)
            verb = "绕"
        axis = "xyz"[j["axis"].index(max(j["axis"]))]
        sign = "-" if j["axis"][j["axis"].index(max(j["axis"]))] < 0 else ""
        rpy = j.get("rpy") or [0, 0, 0]
        rpy_txt = ("0 0 0" if max(abs(v) for v in rpy) < 1e-6
                   else "%.3f %.3f %.3f" % (rpy[0], rpy[1], rpy[2]))
        rows.append("      <tr><td>%s</td><td>%s</td><td>%s%s</td><td>%s</td>"
                    "<td>%.1f  %.1f  %.1f</td><td>%s</td></tr>"
                    % (j["name"], "转动" if j["type"] == "revolute" else "平移",
                       verb, sign + axis, rng,
                       j["xyz"][0] * 1000, j["xyz"][1] * 1000, j["xyz"][2] * 1000,
                       rpy_txt))

    html = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<script type="importmap">
{
  "imports": {
    "three": "https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",
    "three/addons/": "https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"
  }
}
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
  /* CAD 导出的模型常是纯白材质，浅色舞台会把轮廓糊掉 */
  #slot .pv-canvas {
    background:
      radial-gradient(120% 90% at 50% 8%, #f4f6f9 0%, #e4e8ee 62%, #d8dee7 100%);
  }
  .demo .tbl { overflow-x: auto; }
  .demo .hint { font-size: 12px; color: #6b7280; margin: 8px 0 0; }
</style>
</head>
<body>
<div class="demo">
  <h1>__TITLE__</h1>
  <p class="sub">__INTRO__</p>
  <div id="slot"></div>

  <h2>关节链</h2>
  <div class="tbl"><table>
    <thead><tr><th>关节</th><th>类型</th><th>轴</th><th>行程</th>
      <th>原点 xyz (mm)</th><th>关节系 rpy (rad)</th></tr></thead>
    <tbody>
__ROWS__
    </tbody>
  </table></div>
  <p class="hint">工具栏可以切零位 / 演示姿态 / 坐标轴 / 自动旋转；下面的读数就是当前末端在 URDF 坐标系下的位姿。</p>
</div>

<script>
  // 自检用：把错误写进标题，便于无人值守时定位
  window.addEventListener("error", function (e) {
    document.title = "ERR " + String(e.message || e).slice(0, 90);
  });
  window.addEventListener("unhandledrejection", function (e) {
    var r = e.reason;
    document.title = "REJ " + String((r && r.message) || r).slice(0, 90);
  });
</script>
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
  (function poll(tries) {
    setTimeout(function () {
      var n = document.querySelectorAll("#slot input[type=range]").length;
      var cv = document.querySelectorAll("#slot canvas").length;
      if (cv || tries <= 0) {
        document.title = (cv ? "OK " : "FAIL ") + n + "/" + cv + " " +
          (window.RTPreview && window.RTPreview.engine || "");
        return;
      }
      poll(tries - 1);
    }, 700);
  })(14);
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
