# -*- coding: utf-8 -*-
"""上传模型体检 / 自动修复 / 补控制器的离线单测。

跑法：
    cd ros2-assistant
    python tests/test_model_inspect.py

用真实样本（工作区里的 arm_description 三轴臂）+ 两个手工构造的坏样本。
不联网、不调模型、不动原始文件（全部在临时目录里复制后再改）。
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import model_inspect, model_scene  # noqa: E402

_FAIL = []
_HERE = os.path.dirname(os.path.abspath(__file__))
_WS = os.path.dirname(_HERE)
_ARM_SRC = os.path.join(os.path.dirname(_WS), "_tmp_arm", "arm_description")


def check(cond, msg):
    if cond:
        print("  ok   %s" % msg)
    else:
        print("  FAIL %s" % msg)
        _FAIL.append(msg)


class FakeSess(object):
    def __init__(self, root, models, pkg="arm_description"):
        self._root = root
        self.package_name = pkg
        self.imported_project = {"models": models}

    def ws_root(self):
        return self._root


BROKEN = """<?xml version="1.0"?>
<robot name="broken_bot">
  <link name="base_link">
    <visual><geometry><box size="0.2 0.15 0.06"/></geometry></visual>
  </link>
  <link name="arm_link">
    <visual>
      <origin xyz="0 0 0.1"/>
      <geometry><cylinder radius="0.02" length="0.2"/></geometry>
    </visual>
    <inertial>
      <mass value="0"/>
      <inertia ixx="0" ixy="0" ixz="0" iyy="0" iyz="0" izz="0"/>
    </inertial>
  </link>
  <joint name="shoulder" type="revolute">
    <parent link="base_link"/>
    <child link="arm_link"/>
    <origin xyz="0 0 0.03"/>
    <axis xyz="0 1 0"/>
  </joint>
  <joint name="ghost" type="fixed">
    <parent link="missing_link"/>
    <child link="arm_link2"/>
  </joint>
</robot>
"""


def _mk(root, rel, text):
    full = os.path.join(root, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return full


def case_broken():
    print("\n[1] 坏模型：该报的报、能修的修")
    tmp = tempfile.mkdtemp(prefix="mi1_")
    try:
        _mk(tmp, "pkg_broken/urdf/broken.urdf", BROKEN)
        _mk(tmp, "pkg_broken/package.xml",
            '<package format="3"><name>pkg_broken</name></package>')
        sess = FakeSess(tmp, ["pkg_broken/urdf/broken.urdf"], "pkg_broken")
        diag = model_inspect.diagnose(sess)
        msgs = " ".join(i["msg"] for i in diag["issues"])
        check(any("缺少 <limit>" in m for m in [i["msg"] for i in diag["issues"]]),
              "识别出缺 limit")
        check(any("质量无效" in m for m in [i["msg"] for i in diag["issues"]]),
              "识别出质量为零")
        check(any("missing_link" in m for m in [i["msg"] for i in diag["issues"]]),
              "识别出父子链断开")
        check("missing_link" in msgs, "错误信息里点名了缺失的 link")
        check(diag["control"]["state"] == "missing", "控制层判定为缺失")
        check(diag["actionable"] is True, "给出「可生成控制器」的建议")

        text, fixed, blocked = model_inspect.repair(
            diag["issues"], diag["main"]["text"], diag["main"]["info"])
        check(any("limit" in f["what"] for f in fixed), "自动补了 <limit>")
        check(any("inertial" in f["what"] for f in fixed), "自动补了 <inertial>")
        check(any("missing_link" in b["msg"] for b in blocked), "断链被列为需人工确认")
        check("<limit lower=" in text, "limit 真的写进文本了")
        check('value="0"' not in text, "零质量被替换掉了")

        # 修完再体检一次：这两类错误应该消失
        from app import sim_gate
        info2 = sim_gate.inspect_source(text)
        errs2 = [i["msg"] for i in info2["issues"]]
        check(not any("缺少 <limit>" in m for m in errs2), "复检：limit 问题已消失")
        check(not any("质量无效" in m for m in errs2), "复检：质量问题已消失")

        res = model_inspect.build_control(sess, diag)
        check(res["ok"], "能生成控制层")
        check(res["joints"] == ["shoulder"], "只控转动关节：%s" % res["joints"])
        check("ros2_control" in res["patch"], "补上了 ros2_control 声明")
        check("gz_ros2_control-system" in res["patch"], "补上了 gz 插件")
        check(any(f["path"].endswith("controllers.yaml") for f in res["files"]),
              "生成了 controllers.yaml")
        check(any(f["path"].endswith("_sim.launch.py") for f in res["files"]),
              "生成了 launch")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_real_arm():
    print("\n[2] 真实样本：arm_description（有模型、无控制层）")
    if not os.path.isdir(_ARM_SRC):
        print("  skip 找不到样本 %s" % _ARM_SRC)
        return
    tmp = tempfile.mkdtemp(prefix="mi2_")
    try:
        dst = os.path.join(tmp, "arm_description")
        shutil.copytree(_ARM_SRC, dst)
        sess = FakeSess(tmp, ["arm_description/urdf/arm.urdf"])
        diag = model_inspect.diagnose(sess)
        check(diag["main"]["path"].endswith("arm.urdf"), "选中了真正含 link 的 arm.urdf")
        check(len(diag["main"]["info"]["links"]) == 4, "4 个 link")
        check(diag["control"]["state"] == "missing", "控制层缺失")
        check(diag["actionable"], "可生成控制器")

        res = model_inspect.build_control(sess, diag)
        check(res["ok"], "生成控制层成功")
        check(res["joints"] == ["1_JOINT", "2_JOINT", "3_JOINT"],
              "三个关节都进控制器：%s" % res["joints"])

        written = model_inspect.apply_control(res)
        check(len(written) == 3, "落盘 3 个文件（yaml/launch/模型）")
        merged = open(os.path.join(dst, "urdf", "arm.urdf"), encoding="utf-8").read()
        check(merged.count("<ros2_control") == 1, "模型里只有一份 ros2_control")
        check("$(find arm_description)/config/controllers.yaml" in merged,
              "插件指向包内 yaml")

        # 重复执行不应重复插入
        diag2 = model_inspect.diagnose(sess)
        res2 = model_inspect.build_control(sess, diag2)
        check(diag2["control"]["state"] == "ready", "补完后复检为 ready")
        check(res2.get("added") == [], "二次执行没有重复插入")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_diff_drive():
    print("\n[3] 已有差速插件的模型：不应再补控制器")
    tmp = tempfile.mkdtemp(prefix="mi3_")
    try:
        txt = BROKEN.replace(
            "</robot>",
            """
  <gazebo>
    <plugin filename="gz-sim-diff-drive-system" name="gz::sim::systems::DiffDrive">
      <left_joint>shoulder</left_joint>
      <right_joint>shoulder</right_joint>
    </plugin>
  </gazebo>
</robot>""")
        _mk(tmp, "bot/urdf/bot.urdf", txt)
        _mk(tmp, "bot/package.xml", '<package format="3"><name>bot</name></package>')
        sess = FakeSess(tmp, ["bot/urdf/bot.urdf"], "bot")
        diag = model_inspect.diagnose(sess)
        check(diag["control"]["state"] == "diff_drive", "判定为差速插件")
        check(diag["actionable"] is False, "不提供「生成控制器」按钮")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_macro_include():
    print("\n[4] include 型骨架文件：不能误判成空模型")
    tmp = tempfile.mkdtemp(prefix="mi4_")
    try:
        _mk(tmp, "p/urdf/main.urdf.xacro", """<?xml version="1.0"?>
<robot name="p" xmlns:xacro="http://www.ros.org/wiki/xacro">
  <xacro:include filename="$(dirname)/part.xacro"/>
</robot>
""")
        _mk(tmp, "p/urdf/part.xacro", BROKEN)
        _mk(tmp, "p/package.xml", '<package format="3"><name>p</name></package>')
        sess = FakeSess(tmp, ["p/urdf/main.urdf.xacro", "p/urdf/part.xacro"], "p")
        diag = model_inspect.diagnose(sess)
        check(diag["main"]["path"].endswith("part.xacro"),
              "跳过 include 骨架，选中含 link 的文件")
        check(len(diag["main"]["info"]["links"]) >= 2, "在 part.xacro 里找到了 link")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_flow():
    print("\n[5] 对话流程：导入 → 体检 → 点按钮 → 补控制器")
    if not os.path.isdir(_ARM_SRC):
        print("  skip 找不到样本")
        return
    try:
        from app import orchestrator, project_import
    except Exception as e:
        print("  skip 无法导入 orchestrator：%s" % e)
        return

    cfg = {"baseUrl": "http://x", "apiKey": "k", "models": {}, "options": {},
           "ros": {"distro": "jazzy"}, "kb": {"top_k": 3, "max_context_chars": 2000},
           "memory": {"history_max": 30, "summary_keep": 20, "archive_days": 30}}
    sid = "mitest_flow"
    sess = orchestrator.get_session(sid)
    root = sess.ws_root()
    if os.path.isdir(root):
        shutil.rmtree(root, ignore_errors=True)
    try:
        files = []
        for base, dirs, fs in os.walk(_ARM_SRC):
            dirs[:] = [d for d in dirs if d not in (".git", "build", "install", "log")]
            for fn in fs:
                full = os.path.join(base, fn)
                rel = os.path.relpath(full, _ARM_SRC).replace(os.sep, "/")
                try:
                    files.append({"path": "arm_description/" + rel,
                                  "content": open(full, encoding="utf-8").read()})
                except Exception:
                    pass
        ok, _msg, _st = project_import.import_to_session(sess, files)
        check(ok, "导入成功")

        r = orchestrator.handle(cfg, sid, "检查一下我的模型")
        check("模型体检" in r["reply"], "回复是体检报告")
        check(len(r.get("options") or []) == 2, "给出两个按钮")
        check(sess.pending == "model_fix", "进入待选状态")

        r2 = orchestrator.handle(cfg, sid, "1")
        check("已补上控制层" in r2["reply"], "补控制器成功")
        check(any(x.get("action") == "zip" for x in (r2.get("options") or [])),
              "补完后给下载按钮")
        check(sess.pending is None, "待选状态已清空")
        check(any("controllers.yaml" in f for f in (r2.get("files") or [])),
              "yaml 出现在文件树里")
        check("ros2_control" in open(
            os.path.join(root, "arm_description", "urdf", "arm.urdf"),
            encoding="utf-8").read(), "模型文件真的被改了")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def case_scene():
    print("\n[6] 3D 预览：URDF → 场景描述")
    from app import sim_spec, sim_build
    for label, raw in (("机械臂", {"archetype": "arm", "package": "sc_arm"}),
                       ("差速车", {"archetype": "diff_drive", "package": "sc_bot",
                                "wheel_radius": 0.05})):
        spec = sim_spec.normalize(raw)
        text = sim_build.build(spec)["urdf/%s.urdf.xacro" % spec["package"]]
        sc = model_scene.parse_model(text)
        check(sc.get("ok"), "%s：解析成功" % label)
        check(sc.get("root"), "%s：有根节点 %s" % (label, sc.get("root")))
        withvis = [l for l in sc["links"] if l["visuals"]]
        check(len(withvis) >= 3, "%s：%d 个 link 有几何" % (label, len(withvis)))
        first = withvis[0]["visuals"][0]
        check(first["geometry"]["kind"] in ("box", "cylinder", "sphere"),
              "%s：几何类型 %s" % (label, first["geometry"]["kind"]))
        check(first["color"].startswith("#"), "%s：颜色已解析 %s" % (label, first["color"]))
        check(any(j["type"] in ("revolute", "prismatic", "continuous")
                  for j in sc["joints"]), "%s：存在可动关节" % label)

    # mesh 模型必须诚实标注，不能假装知道尺寸
    mesh_urdf = """<robot name="m">
  <link name="base"><visual><geometry><mesh filename="package://x/a.stl"/></geometry></visual></link>
</robot>"""
    sc = model_scene.parse_model(mesh_urdf)
    check(sc["links"][0]["visuals"][0]["geometry"]["kind"] == "mesh", "mesh 被识别")
    check(any("mesh" in w for w in sc["warnings"]), "mesh 有提示不会假装有尺寸")

    # 场景装配：生成的包里要能挑出模型文件
    tmp = tempfile.mkdtemp(prefix="mi6_")
    try:
        spec = sim_spec.normalize({"archetype": "arm", "package": "pick_arm"})
        for rel, text in sim_build.build(spec).items():
            _mk(tmp, "src/%s/%s" % (spec["package"], rel), text)
        sess = FakeSess(tmp, [], "pick_arm")
        built = model_scene.build_scene(sess)
        check(built.get("ok"), "build_scene 成功")
        check("urdf/" in (built.get("source") or ""), "选中了 urdf 下的模型：%s"
              % built.get("source"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    case_broken()
    case_real_arm()
    case_diff_drive()
    case_macro_include()
    case_flow()
    case_scene()
    print("\n" + "=" * 50)
    if _FAIL:
        print("失败 %d 项：" % len(_FAIL))
        for m in _FAIL:
            print("  - %s" % m)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
