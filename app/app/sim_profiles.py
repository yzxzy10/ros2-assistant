# -*- coding: utf-8 -*-
"""机型档案：把资料库里真实机器人的尺寸搬过来当默认值。

为什么要这一层
--------------
之前生成的小车，「轮径 5 厘米、车体 22×18×8」看着能跑，但跟真实机器人不像：
质量 1.5 kg 的小车配 5 cm 轮子，惯量跟真机差一截，底盘也不像任何一台真机。
学生拿它做 SLAM 参数整定时，得到的数在真机上完全不能用。

所以这里把资料库里能查到的真机参数固化成档案：
  - TB3 Waffle / Burger —— 取自 turtlebot3/turtlebot3_description/urdf/*.urdf（实测）
  - linorobot2 2WD —— 取自 linorobot2_description/urdf/2wd_properties.urdf.xacro
  - Andino —— 取自 andino_gz/urdf/andino_gz.urdf.xacro 的插件参数
  - UR5e / Panda —— 工业臂公开的连杆长度（d1/a2/a3/d4…）

用法：模型只要给出 "profile": "tb3_waffle"，剩余字段全部从这里取；
学生自己说的尺寸会覆盖档案值 —— 档案是默认值，不是限制。
"""
import re

# kind: diff = 差速底盘，arm = 固定基座机械臂
PROFILES = {
    "tb3_waffle": {
        "kind": "diff", "label": "TurtleBot3 Waffle",
        "source": "turtlebot3_waffle.urdf（资料库）",
        "base_length": 0.266, "base_width": 0.266, "base_height": 0.094,
        "base_mass": 1.373,
        "wheel_radius": 0.033, "wheel_width": 0.018,
        "wheel_separation": 0.288, "wheel_mass": 0.0285,
        "caster_radius": 0.010,
        "lidar_z": 0.122, "lidar_x": -0.064, "lidar_mass": 0.114,
        "brief": "TurtleBot3 Waffle 尺寸的两轮差速底盘",
    },
    "tb3_burger": {
        "kind": "diff", "label": "TurtleBot3 Burger",
        "source": "turtlebot3_burger.urdf（资料库）",
        "base_length": 0.140, "base_width": 0.140, "base_height": 0.143,
        "base_mass": 0.826,
        "wheel_radius": 0.033, "wheel_width": 0.018,
        "wheel_separation": 0.160, "wheel_mass": 0.0285,
        "caster_radius": 0.010,
        "lidar_z": 0.172, "lidar_x": -0.032, "lidar_mass": 0.114,
        "brief": "TurtleBot3 Burger 尺寸的两轮差速底盘",
    },
    "linorobot2_2wd": {
        "kind": "diff", "label": "linorobot2 2WD",
        "source": "linorobot2_description/urdf/2wd_properties.urdf.xacro（资料库）",
        "base_length": 0.240, "base_width": 0.275, "base_height": 0.060,
        "base_mass": 1.0,
        "wheel_radius": 0.045, "wheel_width": 0.010,
        "wheel_separation": 0.260, "wheel_mass": 0.1,
        "caster_radius": 0.012,
        "lidar_z": 0.33, "lidar_x": 0.12, "lidar_mass": 0.114,
        "brief": "linorobot2 尺寸的两轮差速底盘（薄板 + 立柱结构）",
    },
    "andino": {
        "kind": "diff", "label": "Andino",
        "source": "andino_gz/urdf/andino_gz.urdf.xacro（资料库）",
        "base_length": 0.220, "base_width": 0.150, "base_height": 0.080,
        "base_mass": 1.1,
        "wheel_radius": 0.033, "wheel_width": 0.020,
        "wheel_separation": 0.137, "wheel_mass": 0.03,
        "caster_radius": 0.010,
        "lidar_z": 0.18, "lidar_x": 0.0, "lidar_mass": 0.114,
        "brief": "Andino 尺寸的小型差速底盘",
    },
    "ur5e": {
        "kind": "arm", "label": "UR5e 尺度六轴臂",
        "source": "UR5e 公开连杆长度（d1=0.1625, a2=0.425, a3=0.392, d4=0.133）",
        "base_radius": 0.075, "base_height": 0.08, "base_mass": 4.0,
        # xyz 是「本关节相对父关节」的偏移，正是这些偏置让 UR5e 侧面看呈浅 S 形
        "joints": [
            {"type": "revolute", "axis": "z", "length": 0.1625, "radius": 0.055,
             "mass": 3.7, "xyz": [0.0, 0.0, 0.1625]},
            {"type": "revolute", "axis": "y", "length": 0.425, "radius": 0.050,
             "mass": 8.4, "xyz": [0.0, 0.138, 0.0]},
            {"type": "revolute", "axis": "y", "length": 0.392, "radius": 0.045,
             "mass": 2.3, "xyz": [0.0, -0.131, 0.425]},
            {"type": "revolute", "axis": "z", "length": 0.133, "radius": 0.040,
             "mass": 1.2, "xyz": [0.0, 0.0, 0.392]},
            {"type": "revolute", "axis": "y", "length": 0.100, "radius": 0.035,
             "mass": 1.2, "xyz": [0.0, 0.127, 0.0]},
            {"type": "revolute", "axis": "z", "length": 0.100, "radius": 0.030,
             "mass": 0.4, "xyz": [0.0, 0.0, 0.1]},
        ],
        "brief": "UR5e 尺度的六轴机械臂",
    },
    "panda": {
        "kind": "arm", "label": "Franka Panda 尺度七轴臂",
        "source": "Panda 公开连杆长度（d1=0.333, a3=0.316, d4=0.384, d5=0.107）",
        "base_radius": 0.070, "base_height": 0.07, "base_mass": 4.0,
        "joints": [
            {"type": "revolute", "axis": "z", "length": 0.333, "radius": 0.055,
             "mass": 4.3, "xyz": [0.0, 0.0, 0.333]},
            {"type": "revolute", "axis": "y", "length": 0.120, "radius": 0.050,
             "mass": 2.7, "xyz": [0.0, 0.0, 0.06]},
            {"type": "revolute", "axis": "z", "length": 0.316, "radius": 0.045,
             "mass": 2.2, "xyz": [0.0, 0.0, 0.316]},
            {"type": "revolute", "axis": "y", "length": 0.384, "radius": 0.040,
             "mass": 2.0, "xyz": [0.0, 0.0825, 0.20]},
            {"type": "revolute", "axis": "z", "length": 0.107, "radius": 0.035,
             "mass": 1.2, "xyz": [0.0, -0.0825, 0.20]},
            {"type": "revolute", "axis": "y", "length": 0.103, "radius": 0.032,
             "mass": 0.9, "xyz": [0.0, 0.0, 0.107]},
            {"type": "revolute", "axis": "z", "length": 0.060, "radius": 0.030,
             "mass": 0.4, "xyz": [0.0, 0.088, 0.103]},
        ],
        "brief": "Panda 尺度的七轴机械臂",
    },
}

# 关键词 → 档案。命中即用，不要求模型显式给 profile 字段。
_KEYWORDS = [
    ("tb3_waffle", ("waffle", "华夫")),
    ("tb3_burger", ("burger", "汉堡")),
    ("turtlebot3", ("turtlebot3", "turtlebot")),   # 不指定型号时给 waffle
    ("linorobot2", ("linorobot",)),
    ("andino", ("andino",)),
    ("ur5e", ("ur5", "ur 5", "优傲")),
    ("panda", ("panda", "franka", "熊猫臂")),
]


def pick(raw):
    """从参数表或需求文本里认机型，返回 (档案名, 档案 dict) 或 (None, None)。"""
    if not isinstance(raw, dict):
        return None, None
    key = str(raw.get("profile") or raw.get("model") or "").strip().lower()
    key = re.sub(r"[\s\-]+", "_", key)
    if key in PROFILES:
        return key, PROFILES[key]
    for name, words in _KEYWORDS:
        if any(w in key for w in words):
            return name, PROFILES[name]

    text = " ".join(str(raw.get(k) or "") for k in ("brief", "description", "requirement")).lower()
    if text:
        for name, words in _KEYWORDS:
            if any(w in text for w in words):
                return name, PROFILES[name]
    return None, None


def list_for_prompt():
    """给提示词用的一句话清单。"""
    lines = []
    for name, p in PROFILES.items():
        lines.append("%s = %s（%s）" % (name, p["label"], p["kind"]))
    return "\n".join(lines)
