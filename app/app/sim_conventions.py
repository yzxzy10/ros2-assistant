# -*- coding: utf-8 -*-
"""从资料库的真实包里抽出来的 URDF 编写规范，以及对应的检查器（闸门 G7）。

为什么要单独一份
----------------
生成器容易「自成一派」：标签顺序随便写、材质先用了再定义、传感器只挂插件不定义 sensor。
这样出来的模型在自己的预览里看着没问题，进 Gazebo 才发现雷达是死的。

本文件的规则**不是拍脑袋**，全部对标 `_ros2_models/` 里的实际文件：
  - turtlebot3_description/urdf/turtlebot3_waffle.urdf   → 声明、材质、link/joint 顺序
  - andino_gz/urdf/andino_gz.urdf.xacro                  → 传感器的 <gazebo reference><sensor> 写法
  - andino_description/urdf/include/andino_control.xacro → ros2_control 的接口与 min/max
"""
import re
import xml.etree.ElementTree as ET

# link 内部子元素的规范顺序（TB3 就是这个顺序：先长什么样，再撞什么，最后多重）
LINK_ORDER = ("visual", "collision", "inertial")
# joint 内部子元素的规范顺序
JOINT_ORDER = ("parent", "child", "origin", "axis", "limit", "dynamics")

# 传感器必须同时满足：URDF 里定义 + launch 里桥接，否则 Gazebo 里没有数据
SENSOR_BRIDGE = {
    "lidar_link": {
        "topic": "/scan",
        "ros": "sensor_msgs/msg/LaserScan",
        "gz": "gz.msgs.LaserScan",
        # Harmonic 里 2D 雷达用 gpu_lidar（andino 的写法）；ray/lidar 是旧名
        "types": ("gpu_lidar", "lidar", "ray"),
    },
}


def _order_ok(tag_names, canonical):
    """检查子元素相对顺序是否与规范一致（缺项跳过，多余项忽略）。"""
    idx = [canonical.index(t) for t in tag_names if t in canonical]
    return idx == sorted(idx)


def check(urdf_text, launch_text="", pkg="", files=None):
    """返回 (problems, checks)。problems 为违规说明列表，checks 为完成的检查项数。"""
    problems = []
    checks = 0

    # 1. 声明与根元素
    checks += 1
    if not urdf_text.lstrip().startswith("<?xml"):
        problems.append("缺少 XML 声明（规范写法：<?xml version=\"1.0\" ?>）")
    checks += 1
    m = re.search(r'<robot\s+name="([^"]+)"', urdf_text)
    if not m:
        problems.append("根元素 <robot> 缺少 name 属性")
    elif pkg and m.group(1) != pkg:
        problems.append("robot name「%s」与包名「%s」不一致" % (m.group(1), pkg))

    # 2. 材质必须先定义后使用
    checks += 1
    defined = set(re.findall(r'<material\s+name="([^"]+)"', urdf_text))
    used = set(re.findall(r'<material\s+name="([^"]+)"\s*/>', urdf_text))
    missing = sorted(used - defined)
    if missing:
        problems.append("材质未定义就被引用：%s" % "、".join(missing))

    # 3. 子元素顺序
    try:
        root = ET.fromstring(urdf_text)
    except ET.ParseError as e:
        return problems + ["XML 解析失败：%s" % e], checks + 1

    bad_link, bad_joint = [], []
    for el in root.iter("link"):
        tags = [c.tag for c in el if c.tag in LINK_ORDER]
        if not _order_ok(tags, LINK_ORDER):
            bad_link.append(el.get("name"))
    for el in root.iter("joint"):
        tags = [c.tag for c in el if c.tag in JOINT_ORDER]
        if not _order_ok(tags, JOINT_ORDER):
            bad_joint.append(el.get("name"))
    checks += 1
    if bad_link:
        problems.append("link 子元素顺序应为 visual→collision→inertial：%s" % "、".join(bad_link[:4]))
    checks += 1
    if bad_joint:
        problems.append("joint 子元素顺序应为 parent→child→origin→axis→limit：%s" % "、".join(bad_joint[:4]))

    # 4. 传感器：URDF 里要有真实 <gazebo reference><sensor>，launch 里要有桥接
    sensor_refs = {}
    for gz in root.iter("gazebo"):
        ref = gz.get("reference")
        sen = gz.find("sensor")
        if ref and sen is not None:
            sensor_refs[ref] = sen.get("type", "")
    for link, want in SENSOR_BRIDGE.items():
        topic, ros_type, gz_type = want["topic"], want["ros"], want["gz"]
        has_link = any(el.get("name") == link for el in root.iter("link"))
        if not has_link:
            continue
        checks += 1
        if link not in sensor_refs:
            problems.append("%s 只有外形、没有定义 <gazebo reference> 里的 sensor，"
                            "Gazebo 里不会有数据" % link)
        elif sensor_refs[link] not in want["types"]:
            problems.append("%s 的 sensor 类型是「%s」，应为 %s"
                            % (link, sensor_refs[link], "/".join(want["types"])))
        if launch_text:
            checks += 1
            if topic + "@" + ros_type not in launch_text:
                problems.append("传感器话题 %s 没在 launch 的 ros_gz_bridge 里桥接" % topic)
            elif gz_type not in launch_text:
                problems.append("桥接 %s 的 Gazebo 消息类型缺失（应为 %s）" % (topic, gz_type))

    # 4.5 mesh 引用必须真实存在（否则 Gazebo/RViz 报 "Unable to find file"）
    mesh_refs = re.findall(r'<mesh\s+filename="package://([^/"]+)/([^"]+)"', urdf_text)
    if mesh_refs:
        checks += 1
        pkg_name = pkg or (m.group(1) if m else "")
        for m in re.finditer(r'<mesh\s+filename="package://([^/"]+)/([^"]+)"', urdf_text):
            ref_pkg, rel = m.group(1), m.group(2)
            if pkg_name and ref_pkg != pkg_name:
                problems.append("mesh 引用包名「%s」与包名「%s」不一致" % (ref_pkg, pkg_name))
                break
            if files is not None and rel not in files:
                problems.append("mesh 文件缺失：%s（URDF 引用了但包里没有）" % rel)
                break

    # 5. ros2_control 接口规范（有才查）
    rc = root.find(".//ros2_control")
    if rc is not None:
        for joint in rc.iter("joint"):
            for ci in joint.findall("command_interface"):
                checks += 1
                if ci.find("param") is None:
                    problems.append("ros2_control 关节 %s 的 command_interface 缺少 min/max"
                                    % joint.get("name"))
                    break
            states = [s.get("name") for s in joint.findall("state_interface")]
            checks += 1
            if "position" not in states:
                problems.append("ros2_control 关节 %s 缺少 position 状态接口" % joint.get("name"))
                break

    return problems, checks
