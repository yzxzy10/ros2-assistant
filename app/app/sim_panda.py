# -*- coding: utf-8 -*-
"""真模型模式：用资料库里的原始关节参数 + 三角网格生成机械臂。

和参数化模式（sim_arm）的区别
------------------------------
- 参数化模式：连杆用圆柱拼，关节沿 z 接，靠「偏置」凑出外形 —— 形状是我们编的。
- 真模型模式：关节 origin 的 xyz/rpy、限位、网格文件**全部照抄资料库里的原文件**，
  出来就是那台机器本身。

数据来源：
    _ros2_models/panda_gz_moveit2/panda_description/urdf/panda_arm.xacro     关节 1–7
    _ros2_models/panda_gz_moveit2/panda_description/urdf/panda_gripper.xacro 掌与两指
    _ros2_models/panda_gz_moveit2/panda_description/panda/meshes/collision/  网格（约 80 KB）

为什么用 collision 网格而不是 visual
    visual 是 .dae，11 MB，教学包里太重；collision 是简化 STL，8 个件合计 80 KB，
    轮廓已经能看出是 Panda。等以后允许用户自带 mesh 时，把 visual 塞进来即可。
"""
import math
import os
import struct

MESH_NS = "panda"
MESH_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "assets", "meshes", MESH_NS)

# (关节名, 相对父关节的 xyz, rpy, 下限°, 上限°, 子 link, 网格文件名)
CHAIN = [
    ("joint1", "0 0 0.333", "0 0 0", -166, 166, "link1", "link1.stl"),
    ("joint2", "0 0 0", "-1.5707963 0 0", -101, 101, "link2", "link2.stl"),
    ("joint3", "0 -0.316 0", "1.5707963 0 0", -166, 166, "link3", "link3.stl"),
    ("joint4", "0.0825 0 0", "1.5707963 0 0", -176, -4, "link4", "link4.stl"),
    ("joint5", "-0.0825 0.384 0", "-1.5707963 0 0", -166, 166, "link5", "link5.stl"),
    ("joint6", "0 0 0", "1.5707963 0 0", -1, 215, "link6", "link6.stl"),
    ("joint7", "0.088 0 0", "1.5707963 0 0", -166, 166, "link7", "link7.stl"),
]

MASS = {"link1": 4.97, "link2": 3.71, "link3": 3.0, "link4": 2.6,
        "link5": 2.3, "link6": 1.6, "link7": 0.9, "panda_hand": 0.73}

MESHES = ("link0.stl", "link1.stl", "link2.stl", "link3.stl",
          "link4.stl", "link5.stl", "link6.stl", "link7.stl",
          "hand.stl", "finger.stl")


def _stl_bbox(path):
    """读二进制 STL 的包围盒（不引第三方库）。"""
    lo, hi = [1e9] * 3, [-1e9] * 3
    with open(path, "rb") as f:
        f.read(80)
        n = struct.unpack("<I", f.read(4))[0]
        for _ in range(n):
            rec = f.read(50)
            if len(rec) < 50:
                break
            for k in range(3):
                v = struct.unpack_from("<3f", rec, 12 + k * 12)
                for a in range(3):
                    lo[a] = min(lo[a], v[a])
                    hi[a] = max(hi[a], v[a])
    if lo[0] > hi[0]:
        return (0.05, 0.05, 0.05), (0.0, 0.0, 0.0)
    return tuple(h - l for h, l in zip(hi, lo)), tuple(lo)


def mesh_files():
    """要拷进生成包的网格：{包内相对路径: bytes}。"""
    out = {}
    for name in MESHES:
        p = os.path.join(MESH_DIR, name)
        if os.path.isfile(p):
            with open(p, "rb") as f:
                out["meshes/%s/%s" % (MESH_NS, name)] = f.read()
    return out


def spec_joints():
    """给 ros2_control / controllers.yaml 用的关节表（与 sim_arm 同结构）。"""
    out = []
    for name, xyz, rpy, lo, hi, link, _m in CHAIN:
        out.append({
            "name": name, "link": link, "type": "revolute", "axis": "z",
            "xyz": [float(v) for v in xyz.split()],
            "rpy": [float(v) for v in rpy.split()],
            "lower": math.radians(lo), "upper": math.radians(hi),
            "radius": 0.045, "mass": MASS.get(link, 2.0),
        })
    return out


def _link_xml(link, mesh, mass, pkg):
    """一个 link：网格 + 碰撞 + 惯量（按包围盒折算成等效长方体）。"""
    p = os.path.join(MESH_DIR, mesh)
    size, lo = _stl_bbox(p) if os.path.isfile(p) else ((0.05, 0.05, 0.05), (0, 0, 0))
    sx, sy, sz = size
    c = [lo[i] + size[i] / 2.0 for i in range(3)]
    ixx = mass * (sy * sy + sz * sz) / 12.0
    iyy = mass * (sx * sx + sz * sz) / 12.0
    izz = mass * (sx * sx + sy * sy) / 12.0
    mesh_ref = "package://%s/meshes/%s/%s" % (pkg, MESH_NS, mesh)
    return ('  <link name="%s">\n'
            '    <visual>\n'
            '      <origin xyz="0 0 0" rpy="0 0 0"/>\n'
            '      <geometry><mesh filename="%s"/></geometry>\n'
            '      <material name="metal"/>\n'
            '    </visual>\n'
            '    <collision>\n'
            '      <origin xyz="0 0 0" rpy="0 0 0"/>\n'
            '      <geometry><mesh filename="%s"/></geometry>\n'
            '    </collision>\n'
            '    <inertial>\n'
            '      <origin xyz="%.5f %.5f %.5f" rpy="0 0 0"/>\n'
            '      <mass value="%.3f"/>\n'
            '      <inertia ixx="%.6f" ixy="0" ixz="0" iyy="%.6f" iyz="0" izz="%.6f"/>\n'
            '    </inertial>\n'
            '  </link>\n\n' % (link, mesh_ref, mesh_ref, c[0], c[1], c[2],
                              mass, ixx, iyy, izz))


def build_urdf(pkg, base_height=0.05):
    """返回 (urdf 文本, 关节表)。pkg 用于 package:// 路径。"""
    out = ['<?xml version="1.0" ?>\n',
           '<robot name="%s" xmlns:xacro="http://www.ros.org/wiki/xacro">\n\n' % pkg,
           '  <material name="metal"><color rgba="0.86 0.87 0.90 1.0"/></material>\n',
           '  <material name="joint"><color rgba="0.30 0.32 0.36 1.0"/></material>\n',
           '  <material name="accent"><color rgba="0.92 0.45 0.13 1.0"/></material>\n\n',
           '  <link name="base_footprint"/>\n\n']

    # 底座：link0 网格
    out.append(_link_xml("link0", "link0.stl", 4.0, pkg))
    out.append('  <joint name="base_joint" type="fixed">\n'
               '    <parent link="base_footprint"/>\n'
               '    <child link="link0"/>\n'
               '    <origin xyz="0 0 0" rpy="0 0 0"/>\n'
               '  </joint>\n\n')

    parent = "link0"
    for name, xyz, rpy, lo, hi, link, mesh in CHAIN:
        out.append(_link_xml(link, mesh, MASS.get(link, 2.0), pkg))
        out.append('  <joint name="%s" type="revolute">\n'
                   '    <parent link="%s"/>\n'
                   '    <child link="%s"/>\n'
                   '    <origin xyz="%s" rpy="%s"/>\n'
                   '    <axis xyz="0 0 1"/>\n'
                   '    <limit lower="%.5f" upper="%.5f" effort="87" velocity="2.6"/>\n'
                   '    <dynamics damping="0.5" friction="0.1"/>\n'
                   '  </joint>\n\n'
                   % (name, parent, link, xyz, rpy,
                      math.radians(lo), math.radians(hi)))
        parent = link

    # 掌：接在 link7 上
    out.append(_link_xml("panda_hand", "hand.stl", MASS["panda_hand"], pkg))
    out.append('  <joint name="hand_joint" type="fixed">\n'
               '    <parent link="link7"/>\n'
               '    <child link="panda_hand"/>\n'
               '    <origin xyz="0 0 0" rpy="0 0 0"/>\n'
               '  </joint>\n\n')

    # 两指：0 张开，0.04 闭合（Panda 原厂行程）
    for side, sign in (("left", 1), ("right", -1)):
        out.append(_link_xml("%s_finger_link" % side, "finger.stl", 0.06, pkg))
        out.append('  <joint name="%s_finger_joint" type="prismatic">\n'
                   '    <parent link="panda_hand"/>\n'
                   '    <child link="%s_finger_link"/>\n'
                   '    <origin xyz="0 0 0.0584" rpy="0 0 0"/>\n'
                   '    <axis xyz="0 %d 0"/>\n'
                   '    <limit lower="0.0" upper="0.04" effort="20" velocity="0.2"/>\n'
                   '    <dynamics damping="0.2" friction="0.0"/>\n'
                   '  </joint>\n\n' % (side, side, sign))

    out.append('  <link name="tool0"/>\n'
               '  <joint name="tool_joint" type="fixed">\n'
               '    <parent link="panda_hand"/>\n'
               '    <child link="tool0"/>\n'
               '    <origin xyz="0 0 0.1034" rpy="0 0 0"/>\n'
               '  </joint>\n\n')
    out.append('</robot>\n')
    return "".join(out)
