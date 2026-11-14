# -*- coding: utf-8 -*-
"""上传模型的体检与补控制器。

场景
----
学生把自己（或从网上抄来、从 CAD 导出）的模型拖进来，问题往往是三类：
  1. 结构本身有错 —— URDF 不合法，Gazebo 里根本不出现；
  2. 结构没问题但没有控制层 —— 模型立在那儿，怎么发指令都不动；
  3. 有控制层但接错了 —— 插件名、关节名、yaml 路径三处对不上。

本模块只做「看得见的事」：能静态判定的直接判，判不了的明说（骨骼文件含宏、
含 include、用 mesh 无法估质量）。不做任何运行期验证 —— 服务端不跑 Gazebo。

修复策略遵循一条：**只改有把握的，其余交给用户决定**。
自动修的三类都是「缺失型」，补上不会改变作者意图：
  缺 <limit> → 按关节类型补默认限位
  缺 <inertial> / 质量为零 → 按 primitive 几何 + 密度估算
  惯量不满足三角不等式 → 用同一套几何重算
像「父子链断裂」「两个关节抢同一个 child」这类有歧义的，只报不修。
"""
import os
import re

from . import sim_gate

# 估算密度：PLA/ABS 类 3D 打印件取 800，铝件可在调用处调高
DEFAULT_DENSITY = 800.0

_MOVABLE = ("revolute", "prismatic", "continuous")
_POSITIONAL = ("revolute", "prismatic")


# ---------- 收集 ----------

def _read(full):
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def collect(sess):
    """找出会话里所有模型文件。优先用导入摘要里的清单，兜底扫全工作区。"""
    root = sess.ws_root()
    rels = []
    ip = getattr(sess, "imported_project", None) or {}
    for item in (ip.get("models") or []):
        # 导入摘要里存的是 {path: ...} 字典；手工构造的会话可能直接给路径字符串
        if isinstance(item, dict):
            rels.append(item.get("path") or "")
        else:
            rels.append(str(item or ""))
    rels = [r for r in rels if r]
    if not rels and os.path.isdir(root):
        for dirpath, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in ("build", "install", "log", ".git")]
            for fn in files:
                if fn.lower().endswith((".urdf", ".xacro", ".sdf")):
                    rels.append(os.path.relpath(os.path.join(dirpath, fn), root)
                                .replace(os.sep, "/"))
    out = []
    for rel in rels:
        full = os.path.join(root, rel.replace("/", os.sep))
        if os.path.isfile(full):
            out.append({"path": rel, "full": full, "text": _read(full)})
    return out


# ---------- 诊断 ----------

def diagnose(sess):
    """返回 {models, main, issues, control, actionable, note}。"""
    models = collect(sess)
    if not models:
        return {"models": [], "main": None, "issues": [],
                "control": {"state": "unknown", "reason": "没找到 .urdf/.xacro/.sdf"},
                "actionable": False,
                "note": "会话里没有模型文件，先上传一个（zip 或整包文件夹都行）"}

    for m in models:
        ext = os.path.splitext(m["path"])[1].lower()
        if ext == ".sdf":
            m["info"] = {"ok": True, "links": [], "joints": [], "movable": [],
                         "limited": [], "issues": [], "props": {}, "geometry": {},
                         "control": {"ros2_control": False, "gz_plugin": False,
                                     "diff_drive": False}}
            m["skip"] = "SDF 只做存在性记录，结构深度检查仅支持 URDF/xacro"
            continue
        m["info"] = sim_gate.inspect_source(m["text"])
        m["has_macro"] = "<xacro:macro" in m["text"]
        m["has_include"] = "<xacro:include" in m["text"]

    # 主模型 = 字面 link 最多的那个。include 型骨架文件本身没有 link，
    # 挑到它会得到「零 link」的假结论，所以按 link 数排序而不是按文件名。
    main = max(models, key=lambda m: len(m["info"].get("links") or []))
    if not main["info"].get("links"):
        return {"models": models, "main": main, "issues": [],
                "control": {"state": "unknown",
                            "reason": "所有模型文件里都没有解析到 <link>（可能全是 include/宏拼的）"},
                "actionable": False,
                "note": "模型结构是宏或 include 拼出来的，静态检查看不到内部，"
                        "需要你在虚拟机里 `xacro xx.urdf.xacro` 展开后再传一次"}

    issues = []
    combined = 0
    for m in models:
        # include 型组合文件里的关节指向被包含文件中的 link，单独看必然「断链」。
        # 这类文件不判定结构，否则会给用户一堆假错误。
        if m is not main and m.get("has_include"):
            combined += 1
            continue
        for it in (m["info"].get("issues") or []):
            row = dict(it)
            row["path"] = m["path"]
            issues.append(row)
    if combined:
        issues.append({"level": "warn", "code": "M2", "target": "", "fixable": False,
                       "path": "", "msg": "%d 个组合文件（含 xacro:include）未单独判定结构，"
                                          "以被包含文件为准" % combined})
    if main.get("has_macro"):
        issues.append({"level": "warn", "code": "M1", "target": "", "fixable": False,
                       "path": main["path"],
                       "msg": "该文件含 xacro 宏，宏体内部的 link 不会出现在解析结果里；"
                              "自动修复只对外层生效"})

    ctrl = main["info"].get("control") or {}
    if ctrl.get("diff_drive"):
        state, reason = "diff_drive", "已有差速驱动插件，车轮由 /cmd_vel 直接驱动，不需要 ros2_control"
    elif ctrl.get("ros2_control") and ctrl.get("gz_plugin"):
        state, reason = "ready", "已有 ros2_control 块与 gz 插件"
    elif ctrl.get("ros2_control") and not ctrl.get("gz_plugin"):
        state, reason = "no_plugin", "有 ros2_control 声明，但没有 gz_ros2_control 插件，Gazebo 里控制不生效"
    elif ctrl.get("gz_plugin") and not ctrl.get("ros2_control"):
        state, reason = "no_decl", "有 gz 插件，但没有 ros2_control 声明，控制器无处挂载"
    else:
        state, reason = "missing", "没有控制层：模型能出现，但发指令不会动"

    positional = [j for j in main["info"].get("joints") or [] if j["type"] in _POSITIONAL]
    actionable = state in ("missing", "no_plugin", "no_decl") and bool(positional)
    if state in ("missing", "no_plugin", "no_decl") and not positional:
        reason += "；且没有转动/平移关节（只有 continuous 轮子），这类应改用差速插件"

    return {"models": models, "main": main, "issues": issues,
            "control": {"state": state, "reason": reason},
            "actionable": actionable,
            "positional": [j["name"] for j in positional],
            "continuous": [j["name"] for j in (main["info"].get("joints") or [])
                           if j["type"] == "continuous"]}


# ---------- 修复 ----------

def _upsert_child(text, parent_tag, name, child_tag, snippet):
    """在 <parent_tag name="name"> 块内新增或替换 <child_tag>，返回 (新文本, 是否命中)。

    必须做成 upsert：模型里已经有 <inertial> 但数值是 0 的情况很常见，
    只做插入会留下两份 inertial，解析器取第一个，等于没修。
    """
    ppat = re.compile(r"<%s\s+name=\"%s\"[^>]*>(.*?)</%s>"
                      % (parent_tag, re.escape(name), parent_tag), re.S)
    pm = ppat.search(text)
    if not pm:
        return text, False
    block = pm.group(1)
    cpat = re.compile(r"<%s\b[^>]*(?:/>|>.*?</%s>)" % (child_tag, child_tag), re.S)
    cm = cpat.search(block)
    body = snippet.strip()
    if cm:
        new_block = block[:cm.start()] + body + block[cm.end():]
    else:
        new_block = block.rstrip() + "\n    " + body + "\n  "
    return text[:pm.start(1)] + new_block + text[pm.end(1):], True


def _limit_snippet(jtype, joint_name):
    if jtype == "prismatic":
        return ('<limit lower="0.0" upper="0.10" effort="50" velocity="0.1"/>'
                '<!-- 自动补：行程按 0.1 m 估，按需改 -->')
    return ('<limit lower="-3.1416" upper="3.1416" effort="30" velocity="1.5"/>'
            '<!-- 自动补：限位按 ±π 估，按需改 -->')


def _inertial_snippet(geo, density=DEFAULT_DENSITY):
    got = sim_gate.geometry_mass_inertia(geo, density)
    if not got:
        return None
    m, ixx, iyy, izz = got
    return ("<inertial>\n"
            "      <origin xyz=\"0 0 0\" rpy=\"0 0 0\"/>\n"
            "      <mass value=\"%.6f\"/>\n"
            "      <inertia ixx=\"%.6e\" ixy=\"0\" ixz=\"0\"\n"
            "               iyy=\"%.6e\" iyz=\"0\" izz=\"%.6e\"/>\n"
            "    </inertial>\n"
            "    <!-- 自动补：按 %s 几何 + 密度 %.0f kg/m³ 估算，实际称重后请替换 -->"
            % (m, ixx, iyy, izz, geo.get("kind", "primitive"), density))


def repair(issue_list, text, info, density=DEFAULT_DENSITY):
    """按 issues 里的 fixable 项修文本。返回 (新文本, 已修[], 修不了[])。"""
    fixed, blocked = [], []
    by_name = {}
    for j in info.get("joints") or []:
        by_name[j["name"]] = j

    seen = set()
    for it in issue_list:
        target = it.get("target") or ""
        key = (it["code"], target, it["msg"][:20])
        if key in seen:
            continue
        seen.add(key)
        if not it.get("fixable"):
            if it["level"] == "error":
                blocked.append(it)
            continue

        if it["code"] == "G3" and target in by_name:
            j = by_name[target]
            new_text, ok = _upsert_child(text, "joint", target, "limit",
                                         _limit_snippet(j["type"], target))
            if ok:
                text = new_text
                fixed.append({"target": target, "what": "补 <limit>"})
            else:
                blocked.append(it)
            continue

        if it["code"] == "G3" and it["msg"].startswith("link"):
            geo = (info.get("geometry") or {}).get(target)
            snippet = _inertial_snippet(geo, density) if geo else None
            if snippet:
                new_text, ok = _upsert_child(text, "link", target, "inertial", snippet)
                if ok:
                    text = new_text
                    fixed.append({"target": target,
                                  "what": "补 <inertial>（按 %s 估算）" % geo["kind"]})
                    continue
            blocked.append(it)
            continue

        blocked.append(it)

    return text, fixed, blocked


# ---------- 补控制器 ----------

_CTRL_BLOCK = """
  <!-- ===== 自动生成的 ros2_control 配置 =====
       由 ROS 2 学习助手补全，用于在 Gazebo 里用 forward_command_controller 控制关节。
       位置控制不参与动力学求解，所以质量可以粗略，但别改成 0。 -->
  <ros2_control name="%(pkg)s" type="system">
    <hardware>
      <plugin>gz_ros2_control/GazeboSimSystem</plugin>
    </hardware>
%(joints)s
  </ros2_control>
"""

_PLUGIN_BLOCK = """
  <gazebo>
    <plugin filename="gz_ros2_control-system"
            name="gz_ros2_control::GazeboSimROS2ControlPlugin">
      <parameters>$(find %(pkg)s)/config/controllers.yaml</parameters>
    </plugin>
  </gazebo>
"""

_RC_JOINT = """    <joint name="%(name)s">
      <command_interface name="position"/>
      <state_interface name="position"/>
      <state_interface name="velocity"/>
    </joint>"""

_CTRL_YAML = """controller_manager:
  ros__parameters:
    update_rate: 100  # Hz

    joint_state_broadcaster:
      type: joint_state_broadcaster/JointStateBroadcaster

    %(ctrl)s:
      type: forward_command_controller/ForwardCommandController

%(ctrl)s:
  ros__parameters:
    joints:
%(joints)s
    interface_name: position
"""

_MODEL_SIM_LAUNCH = '''import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription, TimerAction
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import Command
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue

PKG = "%(pkg)s"
MODEL = "%(model_rel)s"
CONTROLLER = "%(ctrl)s"


def generate_launch_description():
    """把你的模型放进 Gazebo，并自动激活控制器。"""
    share = get_package_share_directory(PKG)
    model_file = os.path.join(share, MODEL)
    yaml_file = os.path.join(share, "config", "controllers.yaml")

    # 用 xacro 而不是直接读文件：$(find ...) 这类替换要靠 xacro 展开
    robot_description = ParameterValue(
        Command(["xacro ", model_file]), value_type=str)

    gz_sim = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(
            get_package_share_directory("ros_gz_sim"), "launch", "gz_sim.launch.py")),
        launch_arguments={"gz_args": "-r -v3 empty.sdf"}.items())

    return LaunchDescription([
        gz_sim,
        Node(package="ros_gz_bridge", executable="parameter_bridge",
             arguments=["/clock@rosgraph_msgs/msg/Clock]gz.msgs.Clock"],
             output="screen"),
        Node(package="robot_state_publisher", executable="robot_state_publisher",
             parameters=[{"robot_description": robot_description,
                          "use_sim_time": True}], output="screen"),
        Node(package="ros_gz_sim", executable="create",
             arguments=["-topic", "robot_description", "-name", PKG],
             output="screen"),
        TimerAction(period=6.0, actions=[
            Node(package="controller_manager", executable="spawner",
                 arguments=["joint_state_broadcaster",
                            "--controller-manager", "/controller_manager"],
                 output="screen"),
            TimerAction(period=3.0, actions=[
                Node(package="controller_manager", executable="spawner",
                     arguments=[CONTROLLER,
                                "--controller-manager", "/controller_manager"],
                     output="screen"),
            ]),
        ]),
    ])
'''


def _pkg_dir_of(model_full, sess):
    """从模型文件向上找最近的 package.xml，返回 (包目录, 包名)。"""
    cur = os.path.dirname(os.path.abspath(model_full))
    root = os.path.abspath(sess.ws_root())
    for _ in range(8):
        pj = os.path.join(cur, "package.xml")
        if os.path.isfile(pj):
            txt = _read(pj)
            m = re.search(r"<name>\s*([^<\s]+)\s*</name>", txt)
            return cur, (m.group(1) if m else os.path.basename(cur))
        parent = os.path.dirname(cur)
        if parent == cur or not parent.startswith(root):
            break
        cur = parent
    # 没有 package.xml：用会话里认定的包名，落到 src/<pkg> 下
    pkg = getattr(sess, "package_name", "") or "model_pkg"
    return os.path.join(root, "src", pkg), pkg


def build_control(sess, diag, controller_name="joint_position_controller"):
    """生成 controllers.yaml + 往模型里插 ros2_control 与插件 + 生成 launch。

    返回 {ok, files:[{path, action}], patch: 新模型文本, model_full, pkg, ctrl}
    """
    main = diag.get("main")
    if not main:
        return {"ok": False, "reason": "没有可分析的模型"}
    info = main["info"]
    joints = [j["name"] for j in info.get("joints") or [] if j["type"] in _POSITIONAL]
    if not joints:
        return {"ok": False, "reason": "模型里没有转动/平移关节，位置控制无从挂载"
                                      "（只有 continuous 轮子的模型请改用差速插件）"}

    pkg_dir, pkg = _pkg_dir_of(main["full"], sess)
    text = main["text"]
    ctrl = info.get("control") or {}

    joint_blocks = "\n".join(_RC_JOINT % {"name": n} for n in joints)
    added = []
    if not ctrl.get("ros2_control"):
        text = _append_before_close(text, _CTRL_BLOCK % {"pkg": pkg, "joints": joint_blocks})
        added.append("ros2_control 声明（%d 个关节）" % len(joints))
    if not ctrl.get("gz_plugin"):
        text = _append_before_close(text, _PLUGIN_BLOCK % {"pkg": pkg})
        added.append("gz_ros2_control 插件")

    yaml_text = _CTRL_YAML % {
        "ctrl": controller_name,
        "joints": "\n".join("      - %s" % n for n in joints)}

    model_rel = os.path.relpath(main["full"], pkg_dir).replace(os.sep, "/")
    launch_text = _MODEL_SIM_LAUNCH % {"pkg": pkg, "model_rel": model_rel,
                                       "ctrl": controller_name}

    files = [
        {"path": os.path.join(pkg_dir, "config", "controllers.yaml"),
         "content": yaml_text, "action": "写入控制器配置"},
        {"path": os.path.join(pkg_dir, "launch", "%s_sim.launch.py" % pkg),
         "content": launch_text, "action": "生成启动文件"},
    ]
    return {"ok": True, "files": files, "patch": text, "model_full": main["full"],
            "pkg": pkg, "ctrl": controller_name, "joints": joints, "added": added,
            "pkg_dir": pkg_dir}


def _append_before_close(text, block):
    idx = text.rfind("</robot>")
    if idx < 0:
        return text + "\n" + block
    return text[:idx] + block + "\n" + text[idx:]


def apply_control(result):
    """把 build_control 的结果真正落盘，返回写入的文件路径列表。"""
    written = []
    if not result.get("ok"):
        return written
    for f in result["files"]:
        os.makedirs(os.path.dirname(f["path"]), exist_ok=True)
        with open(f["path"], "w", encoding="utf-8", newline="\n") as fh:
            fh.write(f["content"])
        written.append(f["path"])
    with open(result["model_full"], "w", encoding="utf-8", newline="\n") as fh:
        fh.write(result["patch"])
    written.append(result["model_full"])
    return written


# ---------- 报告 ----------

_CONTROL_LABEL = {
    "ready": "已就绪",
    "diff_drive": "差速插件（无需控制器）",
    "missing": "缺失",
    "no_plugin": "有声明缺插件",
    "no_decl": "有插件缺声明",
    "unknown": "无法判定",
}


def render_report(diag, fixed=None, blocked=None, ctrl_result=None, compact=False):
    out = []
    models = diag["models"]
    main = diag.get("main")
    out.append("**模型体检** · 共找到 %d 个模型文件" % len(models))
    out.append("")
    if main:
        info = main["info"]
        out.append("主模型：`%s` · %d 个 link / %d 个关节（可控 %d 个）"
                   % (main["path"], len(info["links"]), len(info["joints"]),
                      len([j for j in info["joints"] if j["type"] in _MOVABLE])))
    out.append("")
    out.append("| 项目 | 结论 |")
    out.append("|---|---|")
    out.append("| 控制层 | %s |" % _CONTROL_LABEL.get(diag["control"]["state"], "?"))
    out.append("| 说明 | %s |" % diag["control"]["reason"])
    out.append("")

    issues = diag["issues"]
    errors = [i for i in issues if i["level"] == "error"]
    warns = [i for i in issues if i["level"] == "warn"]
    if compact:
        errors, warns = [], []
    if errors:
        out.append("**发现 %d 个问题会直接影响运行**" % len(errors))
        out.append("")
        out.append("| 位置 | 问题 | 能否自动修 |")
        out.append("|---|---|---|")
        for it in errors[:12]:
            out.append("| `%s` | %s | %s |" % (
                it.get("path", ""), it["msg"], "能" if it.get("fixable") else "需你确认"))
        out.append("")
    if warns:
        out.append("提示 %d 条（不影响启动）：" % len(warns))
        for it in warns[:5]:
            out.append("- %s" % it["msg"])
        out.append("")
    if not errors and not warns:
        out.append("结构检查没有发现问题。")
        out.append("")

    if fixed:
        out.append("**已自动修复 %d 处**：" % len(fixed))
        for f in fixed[:8]:
            out.append("- `%s` %s" % (f["target"], f["what"]))
        out.append("")

    if ctrl_result and ctrl_result.get("ok"):
        out.append("**已补上控制层**：%s" % "、".join(ctrl_result["added"]))
        out.append("")
        out.append("```bash")
        out.append("colcon build --symlink-install --packages-select %s" % ctrl_result["pkg"])
        out.append("source install/setup.bash")
        out.append("ros2 launch %s %s_sim.launch.py" % (ctrl_result["pkg"], ctrl_result["pkg"]))
        out.append("```")
        out.append("")
        demo = ", ".join(["0.4"] * len(ctrl_result["joints"]))
        out.append("控制器 `%s` 起来后：" % ctrl_result["ctrl"])
        out.append("")
        out.append("```bash")
        out.append("ros2 topic pub --once /%s/commands std_msgs/msg/Float64MultiArray \\"
                   % ctrl_result["ctrl"])
        out.append('  "{data: [%s]}"' % demo)
        out.append("```")
    elif ctrl_result and not ctrl_result.get("ok"):
        out.append("**控制层没生成**：%s" % ctrl_result.get("reason", ""))
    return "\n".join(out)


def prepare(sess):
    """一体式入口：诊断 → 修可修的 → 返回 (diag, fixed, blocked)。

    上传后与「请检查我的模型」两条路径共用，保证同一份模型得到同一份结论。
    修复结果直接落回原文件，学生的文件树里能立刻看到变化。
    """
    diag = diagnose(sess)
    fixed, blocked = [], []
    main = diag.get("main")
    if not main or not diag.get("issues"):
        return diag, fixed, blocked
    text, fixed, blocked = repair(diag["issues"], main["text"], main["info"])
    if fixed:
        try:
            with open(main["full"], "w", encoding="utf-8", newline="\n") as fh:
                fh.write(text)
            main["text"] = text
        except OSError:
            fixed = []
    return diag, fixed, blocked
