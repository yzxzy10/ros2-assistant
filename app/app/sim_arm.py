# -*- coding: utf-8 -*-
"""机械臂生成：偏置连杆 + 关节壳 + 可动夹爪。

为什么要重写
------------
原来每节连杆都是一根沿 z 轴的圆柱首尾相接，出来就是「一摞管子」——
参数再准也不像市面上的协作臂。真实六轴臂（UR5e / Panda / xArm）有两个特征：
  1. **关节点之间有偏置**：肩部偏 0.138、肘部回摆 -0.131、腕部再偏 0.127，
     所以零位时不是一条直线，侧面看呈浅 S 形；
  2. **连杆是两点之间的斜杆**，不是垂直圆柱。

这里把「关节偏移量」作为一等参数，连杆按其两端关节连线渲染，
所以只要有真实尺寸，出来的就是那台机器的轮廓。

夹爪
----
掌 + 两个平移手指，各一个 prismatic 关节（0 为张开，0.02 为闭合），
单独一个 gripper_controller 控制，可以单独演示夹取动作。
"""
import math

from . import sim_templates as T

_MAT = ("accent", "metal")


def _f(v):
    """紧凑数字，避免 URDF 里出现 1.2000000000000002。"""
    return ("%.6f" % v).rstrip("0").rstrip(".") or "0"


def _vec(v):
    return "%s %s %s" % (_f(v[0]), _f(v[1]), _f(v[2]))


def _seg(v):
    """把「从原点到 v」这一段变成 (长度, rpy)：让沿 z 的圆柱指向 v。"""
    L = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    if L < 1e-9:
        return 0.0, (0.0, 0.0, 0.0)
    rho = math.hypot(v[0], v[1])
    if rho < 1e-9:
        rpy = (0.0, 0.0, 0.0) if v[2] >= 0 else (math.pi, 0.0, 0.0)
    else:
        # 先绕 y 转 atan2(rho, z)，再绕 z 转方位角
        rpy = (0.0, math.atan2(rho, v[2]), math.atan2(v[1], v[0]))
    return L, rpy


def _axis_xyz(axis):
    return {"x": "1 0 0", "y": "0 1 0", "z": "0 0 1"}.get(axis, "0 0 1")


def joints_with_offsets(spec):
    """给每个关节补上相对父关节的偏移（缺省沿 z 接上一节）。"""
    js = []
    for i, j in enumerate(spec["joints"]):
        item = dict(j)
        if not item.get("xyz"):
            prev = spec["joints"][i - 1] if i else None
            base = spec["base_height"] if i == 0 else prev["length"]
            item["xyz"] = [0.0, 0.0, float(base)]
        item["xyz"] = [float(x) for x in item["xyz"]]
        js.append(item)
    return js


def _link_block(idx, j, nxt_vec, spec):
    """一节连杆：主体斜杆 + 两端关节壳。"""
    link = j["link"]
    L, rpy = _seg(nxt_vec)
    mid = [c / 2.0 for c in nxt_vec]
    mat = _MAT[idx % 2]
    hx, hy, hz = rpy
    parts = ['  <link name="%s">' % link]

    if L > 1e-6:
        parts.append(
            '    <visual>\n'
            '      <origin xyz="%s" rpy="%s %s %s"/>\n'
            '      <geometry><cylinder radius="${%s_radius}" length="%s"/></geometry>\n'
            '      <material name="%s"/>\n'
            '    </visual>' % (_vec(mid), _f(hx), _f(hy), _f(hz), link, _f(L), mat))

    # 两端关节壳：放在真实关节位置，视觉上把连杆和关节连起来
    parts.append(
        '    <visual>\n'
        '      <origin xyz="0 0 0" rpy="0 0 0"/>\n'
        '      <geometry><cylinder radius="${%s_radius*1.5}" length="${%s_radius*1.3}"/></geometry>\n'
        '      <material name="joint"/>\n'
        '    </visual>' % (link, link))
    if L > 0.05:
        parts.append(
            '    <visual>\n'
            '      <origin xyz="%s" rpy="%s %s %s"/>\n'
            '      <geometry><cylinder radius="${%s_radius*1.35}" length="${%s_radius*1.1}"/></geometry>\n'
            '      <material name="joint"/>\n'
            '    </visual>' % (_vec(nxt_vec), _f(hx), _f(hy), _f(hz), link, link))

    if L > 1e-6:
        parts.append(
            '    <collision>\n'
            '      <origin xyz="%s" rpy="%s %s %s"/>\n'
            '      <geometry><cylinder radius="${%s_radius}" length="%s"/></geometry>\n'
            '    </collision>' % (_vec(mid), _f(hx), _f(hy), _f(hz), link, _f(L)))

    # 惯量直接算成字面量：斜杆的旋转惯量在 xacro 里写公式会很长，
    # 而且位置控制不参与动力学求解，按等效圆柱算足够。
    m = float(j["mass"])
    r = float(j["radius"])
    ixx = m * (3.0 * r * r + L * L) / 12.0
    izz = m * r * r / 2.0
    parts.append(
        '    <inertial>\n'
        '      <origin xyz="%s" rpy="%s %s %s"/>\n'
        '      <mass value="${%s_mass}"/>\n'
        '      <inertia ixx="%s" ixy="0" ixz="0" iyy="%s" iyz="0" izz="%s"/>\n'
        '    </inertial>' % (_vec(mid), _f(hx), _f(hy), _f(hz),
                          link, _f(ixx), _f(ixx), _f(izz)))
    parts.append('  </link>')
    return "\n".join(parts) + "\n\n"


def _joint_block(j, parent, is_last=False):
    jtype = j["type"]
    eff = 60 if jtype == "revolute" else 80
    vel = 1.5 if jtype == "revolute" else 0.1
    return ('  <joint name="%s" type="%s">\n'
            '    <parent link="%s"/>\n'
            '    <child link="%s"/>\n'
            '    <origin xyz="%s" rpy="0 0 0"/>\n'
            '    <axis xyz="%s"/>\n'
            '    <limit lower="%s" upper="%s" effort="%s" velocity="%s"/>\n'
            '    <dynamics damping="0.08" friction="0.0"/>\n'
            '  </joint>\n\n'
            % (j["name"], jtype, parent, j["link"], _vec(j["xyz"]),
               _axis_xyz(j.get("axis", "z")), _f(j["lower"]), _f(j["upper"]),
               eff, vel))


GRIPPER_BLOCK = """  <!-- 夹爪：掌 + 两个平移手指。0 是张开，0.02 是闭合，
       由 gripper_controller 单独控制（见 config/arm_controllers.yaml）。 -->
  <link name="gripper_base_link">
    <visual>
      <origin xyz="0 0 %(palm_z)s" rpy="0 0 0"/>
      <geometry><box size="0.075 0.055 0.030"/></geometry>
      <material name="joint"/>
    </visual>
    <visual>
      <origin xyz="0 0 %(wrist_z)s" rpy="0 0 0"/>
      <geometry><cylinder radius="0.026" length="0.020"/></geometry>
      <material name="metal"/>
    </visual>
    <collision>
      <origin xyz="0 0 %(palm_z)s" rpy="0 0 0"/>
      <geometry><box size="0.075 0.055 0.030"/></geometry>
    </collision>
    <inertial>
      <origin xyz="0 0 %(palm_z)s" rpy="0 0 0"/>
      <mass value="0.42"/>
      <inertia ixx="1.8e-04" ixy="0" ixz="0" iyy="2.3e-04" iyz="0" izz="2.9e-04"/>
    </inertial>
  </link>

  <joint name="gripper_joint" type="fixed">
    <parent link="tool0"/>
    <child link="gripper_base_link"/>
    <origin xyz="0 0 0" rpy="0 0 0"/>
  </joint>

  <link name="left_finger_link">
    <visual>
      <origin xyz="0 0 0.030" rpy="0 0 0"/>
      <geometry><box size="0.016 0.030 0.060"/></geometry>
      <material name="accent"/>
    </visual>
    <collision>
      <origin xyz="0 0 0.030" rpy="0 0 0"/>
      <geometry><box size="0.016 0.030 0.060"/></geometry>
    </collision>
    <inertial>
      <origin xyz="0 0 0.030" rpy="0 0 0"/>
      <mass value="0.06"/>
      <inertia ixx="3.0e-05" ixy="0" ixz="0" iyy="2.0e-05" iyz="0" izz="1.6e-05"/>
    </inertial>
  </link>

  <joint name="left_finger_joint" type="prismatic">
    <parent link="gripper_base_link"/>
    <child link="left_finger_link"/>
    <origin xyz="0 0.026 %(palm_z)s" rpy="0 0 0"/>
    <axis xyz="0 -1 0"/>
    <limit lower="0.0" upper="0.020" effort="20" velocity="0.05"/>
    <dynamics damping="0.20" friction="0.0"/>
  </joint>

  <link name="right_finger_link">
    <visual>
      <origin xyz="0 0 0.030" rpy="0 0 0"/>
      <geometry><box size="0.016 0.030 0.060"/></geometry>
      <material name="accent"/>
    </visual>
    <collision>
      <origin xyz="0 0 0.030" rpy="0 0 0"/>
      <geometry><box size="0.016 0.030 0.060"/></geometry>
    </collision>
    <inertial>
      <origin xyz="0 0 0.030" rpy="0 0 0"/>
      <mass value="0.06"/>
      <inertia ixx="3.0e-05" ixy="0" ixz="0" iyy="2.0e-05" iyz="0" izz="1.6e-05"/>
    </inertial>
  </link>

  <joint name="right_finger_joint" type="prismatic">
    <parent link="gripper_base_link"/>
    <child link="right_finger_link"/>
    <origin xyz="0 -0.026 %(palm_z)s" rpy="0 0 0"/>
    <axis xyz="0 1 0"/>
    <limit lower="0.0" upper="0.020" effort="20" velocity="0.05"/>
    <dynamics damping="0.20" friction="0.0"/>
  </joint>

"""

GRIPPER_JOINTS = ("left_finger_joint", "right_finger_joint")


def build_arm_urdf(spec):
    """返回 (urdf 文本, 关节列表, 夹爪关节名)。"""
    js = joints_with_offsets(spec)
    tool_len = max(spec["joints"][-1]["radius"] * 2.4, 0.05)

    props, links, joints, refs = [], [], [], []
    for i, j in enumerate(js):
        link = j["link"]
        props.append(
            '  <xacro:property name="%s_radius" value="%s"/>\n'
            '  <xacro:property name="%s_mass" value="%s"/>'
            % (link, _f(j["radius"]), link, _f(j["mass"])))

        nxt = js[i + 1]["xyz"] if i + 1 < len(js) else [0.0, 0.0, tool_len]
        links.append(_link_block(i, j, nxt, spec))
        parent = "base_link" if i == 0 else js[i - 1]["link"]
        joints.append(_joint_block(j, parent))
        refs.append(T.ARM_GAZEBO_REF % {
            "link": link, "mat": "Orange" if i % 2 == 0 else "Grey"})

    props.append('  <xacro:property name="tool_len" value="%s"/>' % _f(tool_len))

    gripper = GRIPPER_BLOCK % {"palm_z": _f(tool_len + 0.015),
                               "wrist_z": _f(tool_len + 0.004)}

    urdf = (T.ARM_URDF_HEAD
            .replace("{{pkg}}", spec["package"])
            .replace("{{base_radius}}", _f(spec["base_radius"]))
            .replace("{{base_height}}", _f(spec["base_height"]))
            .replace("{{base_mass}}", _f(spec["base_mass"]))
            .replace("{{properties}}", "\n".join(props))
            .replace("{{link_blocks}}", "".join(links) + "".join(joints) + gripper)
            .replace("{{last_link}}", js[-1]["link"])
            .replace("{{last_length_attr}}", "tool_len"))
    return urdf, refs
