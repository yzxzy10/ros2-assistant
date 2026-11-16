# -*- coding: utf-8 -*-
"""骨架装配：参数表 + 模板 → 一组文件。

纯函数，不依赖网络、不依赖 LLM、不写盘。
这样单测可以直接验「给定参数表，产物是否合法」，不必等模型返回。
"""
from . import sim_spec
from . import sim_arm
from . import sim_panda
from . import sim_chassis
from . import sim_templates as T

_AXIS_XYZ = {"x": "1 0 0", "y": "0 1 0", "z": "0 0 1"}


def _render(tpl, **kw):
    """把 {{key}} 全部替换掉。未提供的占位符保持原样，交给校验层报错。"""
    out = tpl
    for k, v in kw.items():
        out = out.replace("{{%s}}" % k, str(v))
    return out


def build(spec):
    """返回 {相对路径: 文件内容}。路径相对包根目录。"""
    arch = spec["archetype"]
    pkg = spec["package"]
    files = {
        "package.xml": _package_xml(spec),
        "CMakeLists.txt": _render(T.CMAKE_LISTS, pkg=pkg,
                                  extra_dirs=" meshes" if _has_mesh(spec) else ""),
        "worlds/empty.sdf": T.WORLD_EMPTY,
        "config/display.rviz": _rviz(spec),
        "launch/display.launch.py": _render(T.DISPLAY_LAUNCH, pkg=pkg),
        "scripts/selfcheck.py": _selfcheck(spec),
    }
    if arch == "arm":
        files.update(_build_arm(spec))
    else:
        files.update(_build_diff(spec))
    files["README.md"] = _readme(spec)
    return files


# ---------- 公共 ----------

def _package_xml(spec):
    extra = T.EXTRA_DEPS_ARM if spec["archetype"] == "arm" else T.EXTRA_DEPS_DIFF
    desc = spec.get("brief") or ("%s（参数化仿真模型）" % spec["archetype_name"])
    return _render(T.PACKAGE_XML, pkg=spec["package"],
                   desc=desc.replace("&", "and").replace("<", "").replace(">", ""),
                   extra_deps=extra)


def _rviz(spec):
    if spec["archetype"] == "arm":
        return _render(T.RVIZ_DISPLAY, fixed_frame="world",
                       view_distance="1.2", view_z="0.2")
    return _render(T.RVIZ_DISPLAY, fixed_frame="base_footprint",
                   view_distance="1.6", view_z="0.1")


def _selfcheck(spec):
    if spec["archetype"] == "arm":
        names = [j["name"] for j in spec["joints"]]
        kind = "arm"
    else:
        names = ["left_wheel_joint", "right_wheel_joint"]
        kind = "diff"
    return _render(T.SELFCHECK, pkg=spec["package"], kind=kind,
                   joints_py=", ".join('"%s"' % n for n in names))


def _readme(spec):
    arch = spec["archetype"]
    if arch == "arm":
        ctrl = spec["controller_name"]
        demo = []
        for j in spec["joints"]:
            demo.append("%.2f" % (j["upper"] * 0.5 if j["type"] == "prismatic" else 0.4))
        ctrl_block = _render(T.CONTROL_ARM, controller_name=ctrl,
                             demo_values=", ".join(demo))
        extra = ("| `urdf/%s.ros2_control.xacro` | 关节接口声明（position） |\n"
                 "| `urdf/%s.gazebo.xacro` | gz_ros2_control 插件接线 |\n"
                 "| `config/controllers.yaml` | 控制器配置 |" % (spec["package"],
                                                                spec["package"]))
        faq = "`list_controllers` 里没有 %s" % ctrl
    elif arch == "ackermann":
        ctrl_block = T.CONTROL_ACK
        extra = "| `worlds/empty.sdf` | 空世界，含地面与光源 |"
        faq = "发 `/cmd_vel` 但车不动"
    else:
        ctrl_block = T.CONTROL_DIFF
        extra = "| `worlds/empty.sdf` | 空世界，含地面与光源 |"
        faq = "发 `/cmd_vel` 但车不动"

    desc = spec.get("brief") or spec["archetype_name"]
    if spec.get("profile_label"):
        desc = "%s（尺寸参考 %s：%s）" % (desc, spec["profile_label"],
                                        spec.get("profile_source") or "")
    return _render(T.README,
                   pkg=spec["package"],
                   desc=desc,
                   archetype=arch,
                   control_block=ctrl_block,
                   urdf_extra_row=extra,
                   controller_faq_from=faq)


# ---------- 机械臂 ----------

def _has_mesh(spec):
    """机械臂走真模型模式时要安装 meshes/，否则 colcon 装完运行期找不到网格。"""
    return bool(spec.get("real_mesh"))


def _build_arm(spec):
    """机械臂：偏置连杆 + 关节壳 + 可动夹爪（结构见 sim_arm）。"""
    pkg = spec["package"]

    if spec.get("real_mesh"):
        # 真模型模式：关节参数与网格都来自资料库原始文件
        urdf = sim_panda.build_urdf(pkg, spec["base_height"])
        js = sim_panda.spec_joints()
        refs = [T.ARM_GAZEBO_REF % {"link": j["link"], "mat": "Grey"} for j in js]
        mesh_files = sim_panda.mesh_files()
    else:
        urdf, refs = sim_arm.build_arm_urdf(spec)
        js = sim_arm.joints_with_offsets(spec)
        mesh_files = {}

    rc = [_render(T.ARM_RC_JOINT, joint_name=j["name"],
                  lower=j["lower"], upper=j["upper"]) for j in js]
    # 夹爪两指也进 ros2_control：0 张开、0.02 闭合
    finger_names = (("left_finger_joint", "right_finger_joint")
                    if spec.get("real_mesh") else sim_arm.GRIPPER_JOINTS)
    finger_hi = 0.04 if spec.get("real_mesh") else 0.02
    for name in finger_names:
        rc.append(_render(T.ARM_RC_JOINT, joint_name=name, lower=0.0, upper=finger_hi))

    joints_yaml = "\n".join("      - %s" % j["name"] for j in js)
    grip_yaml = "\n".join("      - %s" % n for n in finger_names)
    grip_ctrl = "gripper_controller"

    out = {
        "urdf/%s.urdf.xacro" % pkg: urdf,
        "urdf/%s.ros2_control.xacro" % pkg: _render(
            T.ARM_ROS2_CONTROL, pkg=pkg, joint_blocks="\n".join(rc)),
        "urdf/%s.gazebo.xacro" % pkg: _render(
            T.ARM_GAZEBO, ref_blocks="\n".join(refs)),
        "config/controllers.yaml": _render(
            T.ARM_CONTROLLERS, controller_name=spec["controller_name"],
            joints_yaml=joints_yaml,
            gripper_controller=grip_ctrl,
            gripper_joints_yaml=grip_yaml),
        "launch/sim.launch.py": _render(
            T.ARM_SIM_LAUNCH, pkg=pkg, controller_name=spec["controller_name"],
            gripper_controller=grip_ctrl),
    }
    out.update(mesh_files)      # 二进制网格：bytes
    return out


# ---------- 底盘（差速 / 阿克曼） ----------

def _build_diff(spec):
    """差速与阿克曼共用一套车体，区别在轮子布局与驱动插件（见 sim_chassis）。"""
    pkg = spec["package"]
    return {
        "urdf/%s.urdf.xacro" % pkg: sim_chassis.build_chassis_urdf(spec),
        "launch/sim.launch.py": _render(T.DIFF_SIM_LAUNCH, pkg=pkg),
    }


def describe_files(files):
    """把文件清单转成 [(路径, 行数或 None)]，供回复里展示。

    网格是二进制（bytes），不能按文本数行——返回 None 由调用方显示体积。
    """
    out = []
    for p in sorted(files):
        v = files[p]
        if isinstance(v, (bytes, bytearray)):
            out.append((p, None))
        else:
            out.append((p, v.count("\n") + 1))
    return out
