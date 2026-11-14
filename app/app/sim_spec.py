# -*- coding: utf-8 -*-
"""仿真参数表（sim spec）：模型唯一需要填的东西。

为什么要有这一层
----------------
让模型直接写 URDF，风险不在「语法写不对」，而在「每次结构都不一样」：
有时拆 include，有时不拆；惯性有时写字面量，有时写表达式；关节名有时带
prefix，有时不带。结构一漂移，URDF / ros2_control / controllers.yaml
三处就对不齐，而在 Gazebo 里的表现是「模型出来了但不动」——最难查的一类。

所以这里只收十几个字段，其余交给模板。字段定下来，结构就是确定的，
校验也才能做成确定性的（纯 Python，毫秒级，不碰容器）。

字段设计遵循一条：**能算的都不让填**。
轮距由车宽 + 轮宽推导，惯量由几何推导，限位由关节类型给默认值。
模型只需要回答「什么机器人、多大、几个关节、怎么控」。
"""
import re

from . import sim_profiles

# 支持的机型。加新机型时，sim_templates 里必须同步有一套骨架。
ARCHETYPES = {
    "diff_drive": "差速底盘",
    "ackermann": "四轮阿克曼转向车",
    "arm": "固定基座机械臂",
}

# 中文/英文别名 → 内部机型标识
_ALIASES = {
    "diff_drive": "diff_drive", "diff": "diff_drive", "differential": "diff_drive",
    "chassis": "diff_drive", "mobile": "diff_drive",
    "ackermann": "ackermann", "汽车": "ackermann", "轿车": "ackermann",
    "前轮转向": "ackermann", "转向车": "ackermann", "遥控车": "ackermann",
    "car": "ackermann", "carlike": "ackermann",
    "差速": "diff_drive", "小车": "diff_drive", "底盘": "diff_drive",
    "移动机器人": "diff_drive", "两轮": "diff_drive",
    "arm": "arm", "manipulator": "arm", "robotic_arm": "arm",
    "机械臂": "arm", "手臂": "arm", "臂": "arm",
}

# 关节类型别名
_JOINT_ALIASES = {
    "revolute": "revolute", "rotate": "revolute", "转动": "revolute",
    "旋转": "revolute", "转动关节": "revolute",
    "prismatic": "prismatic", "slide": "prismatic", "linear": "prismatic",
    "平移": "prismatic", "移动": "prismatic", "升降": "prismatic",
}

# 关节默认限位（弧度 / 米）
_DEFAULT_LIMIT = {"revolute": (-3.1416, 3.1416), "prismatic": (0.0, 0.06)}

_MAX_LINKS = 6


def _num(v, default, lo, hi):
    """取数值并夹到合理区间，非法值一律回落到默认值。"""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return default
    if x != x or x in (float("inf"), float("-inf")):  # NaN / inf
        return default
    return max(lo, min(hi, x))


def _sanitize_pkg(name):
    """ROS 2 包名：小写字母开头，只允许小写字母/数字/下划线。"""
    s = str(name or "").strip().lower()
    s = re.sub(r"[^a-z0-9_]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    if not s or not s[0].isalpha():
        s = "sim_robot"
    return s[:40]


def _pick_arch(raw):
    """识别机型：先看 archetype 字段，再看别名表，最后按需求文本猜。"""
    v = str(raw.get("archetype") or raw.get("type") or raw.get("robot_type") or "").strip().lower()
    if v in _ALIASES:
        return _ALIASES[v]
    text = " ".join(str(raw.get(k) or "") for k in ("brief", "description", "requirement"))
    t = text.lower()
    for k, arch in _ALIASES.items():
        if k in t:
            return arch
    # 有 links/joints 字段的多半是机械臂
    if raw.get("joints") or raw.get("links"):
        return "arm"
    return "diff_drive"


def _norm_joint_type(v):
    s = str(v or "revolute").strip().lower()
    return _JOINT_ALIASES.get(s, "revolute")


def _norm_axis(v):
    s = str(v or "").strip().lower()
    if s in ("x", "y", "z"):
        return s
    return ""  # 空表示按位置自动 alternating


def normalize(raw):
    """把模型输出的原始参数表收敛成规范结构。

    返回 dict，含 archetype / package / 几何 / 关节列表。
    不做任何 IO，纯函数，方便离线单测。
    """
    if not isinstance(raw, dict):
        raise ValueError("参数表必须是 JSON 对象")

    arch = _pick_arch(raw)

    # 机型档案：学生只要说「像 turtlebot3 那样」，尺寸/质量就按真机来
    pname, prof = sim_profiles.pick(raw)
    explicit_arch = raw.get("archetype") or raw.get("type") or raw.get("robot_type")
    prof_arch = {"diff": "diff_drive", "ackermann": "ackermann",
                 "arm": "arm"}.get((prof or {}).get("kind"), "")
    if prof and not explicit_arch and prof_arch:
        arch = prof_arch
    if prof and prof_arch != arch:
        pname, prof = None, None      # 档案跟需求冲突时，以需求为准
    src = raw
    if prof:
        src = dict(prof)
        src.update({k: v for k, v in raw.items() if v not in (None, "", [], {})})

    pkg_raw = raw.get("package") or raw.get("pkg") or raw.get("name")
    if pkg_raw:
        pkg = _sanitize_pkg(pkg_raw)
    else:
        pkg = "demo_arm" if arch == "arm" else "demo_bot"

    spec = {
        "archetype": arch,
        "archetype_name": ARCHETYPES[arch],
        "package": pkg,
        "profile": pname or "",
        "profile_label": (prof or {}).get("label", ""),
        "profile_source": (prof or {}).get("source", ""),
        "brief": str(src.get("brief") or src.get("description") or "").strip()[:200],
    }

    if arch in ("diff_drive", "ackermann"):
        spec.update(_normalize_diff(src))
        if arch == "ackermann":
            spec["controller"] = "gz-sim-ackermann-steering-system"
    else:
        spec.update(_normalize_arm(src))
    return spec


def _normalize_diff(raw):
    base_w = _num(raw.get("base_width"), 0.18, 0.06, 1.2)
    wheel_w = _num(raw.get("wheel_width"), 0.03, 0.01, 0.15)
    wheel_r = _num(raw.get("wheel_radius"), 0.05, 0.01, 0.4)
    base_l = _num(raw.get("base_length"), max(0.22, wheel_r * 4), 0.10, 2.0)
    base_h = _num(raw.get("base_height"), 0.08, 0.02, 0.6)
    # 轮距 = 车宽 + 轮宽（轮心到轮心），允许显式覆盖
    # 轮子内侧面与车体侧面齐平：留出间隙的话轮子看起来是飘着的
    sep = _num(raw.get("wheel_separation"), base_w + wheel_w, 0.10, 1.5)

    return {
        "base_length": round(base_l, 4),
        "base_width": round(base_w, 4),
        "base_height": round(base_h, 4),
        "base_mass": round(_num(raw.get("base_mass"), 1.5, 0.10, 60.0), 3),
        "wheel_radius": round(wheel_r, 4),
        "wheel_width": round(wheel_w, 4),
        "wheel_separation": round(sep, 4),
        "wheel_mass": round(_num(raw.get("wheel_mass"), 0.2, 0.02, 10.0), 3),
        "wheel_offset_x": round(_num(raw.get("wheel_offset_x"), 0.0, -0.5, 0.5), 4),
        "caster_radius": round(_num(raw.get("caster_radius"), 0.015, 0.005, 0.05), 4),
        "lidar_height": round(_num(raw.get("lidar_z") or raw.get("lidar_height"),
                                    max(base_h / 2 + 0.06, 0.10), 0.02, 2.0), 4),
        "lidar_x": round(_num(raw.get("lidar_x"), 0.0, -0.6, 0.6), 4),
        "lidar_mass": round(_num(raw.get("lidar_mass"), 0.114, 0.01, 2.0), 3),
        "wheels": _wheel_count(raw),
        "wheelbase": round(_num(raw.get("wheelbase"),
                                max(base_l * 0.62, wheel_r * 4), 0.15, 2.0), 4),
        "steer_limit": round(_num(raw.get("steer_limit"), 0.5, 0.1, 0.9), 3),
        "cmd_topic": "/cmd_vel",
        "controller": "gz-sim-diff-drive-system",
    }


def _normalize_arm(raw):
    src = raw.get("joints") or raw.get("links") or []
    if not isinstance(src, list):
        src = []

    links = []
    for i, item in enumerate(src[:_MAX_LINKS]):
        if not isinstance(item, dict):
            continue
        jtype = _norm_joint_type(item.get("type") or item.get("joint_type"))
        default_len = 0.12 if i == 0 else 0.10
        length = _num(item.get("length"), default_len, 0.02, 0.8)
        lo, hi = _DEFAULT_LIMIT[jtype]
        if jtype == "prismatic":
            hi = _num(item.get("travel") or item.get("upper"), length * 0.5, 0.005, 0.4)
            lo = 0.0
        else:
            lo = _num(item.get("lower"), lo, -6.28, 6.28)
            hi = _num(item.get("upper"), hi, -6.28, 6.28)
            if hi <= lo:
                lo, hi = -3.1416, 3.1416
        links.append({
            "index": i + 1,
            "name": "joint%d" % (i + 1),
            "link": "link%d" % (i + 1),
            "type": jtype,
            "axis": _norm_axis(item.get("axis")) or ("z" if i % 2 == 0 else "y"),
            "length": round(length, 4),
            "radius": round(_num(item.get("radius"), 0.02, 0.005, 0.2), 4),
            "mass": round(_num(item.get("mass"), 0.3, 0.01, 20.0), 3),
            "lower": round(lo, 4),
            "upper": round(hi, 4),
            # 真实的关节点偏置（UR5e 肩 0.138 / 肘 -0.131 / 腕 0.127）。
            # 不传就由 sim_arm 沿 z 接上一节，等于老行为。
            "xyz": ([round(float(c), 4) for c in item["xyz"]]
                    if isinstance(item.get("xyz"), (list, tuple))
                    and len(item["xyz"]) == 3 else None),
        })

    if not links:
        # 没说清几轴时给最常见的三轴（2 转动 + 1 平移），和 arm_description 同构
        links = [
            {"index": 1, "name": "joint1", "link": "link1", "type": "revolute",
             "axis": "z", "length": 0.12, "radius": 0.02, "mass": 0.3,
             "lower": -3.1416, "upper": 3.1416},
            {"index": 2, "name": "joint2", "link": "link2", "type": "revolute",
             "axis": "y", "length": 0.10, "radius": 0.018, "mass": 0.25,
             "lower": -3.1416, "upper": 3.1416},
            {"index": 3, "name": "joint3", "link": "link3", "type": "prismatic",
             "axis": "z", "length": 0.06, "radius": 0.015, "mass": 0.12,
             "lower": 0.0, "upper": 0.06},
        ]

    return {
        "base_radius": round(_num(raw.get("base_radius"), 0.06, 0.02, 0.4), 4),
        "base_height": round(_num(raw.get("base_height"), 0.04, 0.01, 0.3), 4),
        "base_mass": round(_num(raw.get("base_mass"), 1.2, 0.1, 60.0), 3),
        "joints": links,
        "controller": "forward_command_controller",
        "controller_name": "arm_controller",
    }


def summary_lines(spec):
    """一句话摘要，用于进度条与回复开头。"""
    tag = ("（按 %s）" % spec["profile_label"]) if spec.get("profile_label") else ""
    if spec["archetype"] in ("diff_drive", "ackermann"):
        extra = ""
        if spec["archetype"] == "ackermann":
            extra = " · 轴距 %.0f mm · 最大转角 %.0f°" % (
                spec["wheelbase"] * 1000, spec["steer_limit"] * 180 / 3.14159)
        else:
            extra = " · %d 轮" % spec.get("wheels", 4)
        return ("%s%s · 轮径 %.0f mm · 轮距 %.0f mm · 车体 %.0f×%.0f×%.0f mm%s" % (
            spec["archetype_name"], tag,
            spec["wheel_radius"] * 1000, spec["wheel_separation"] * 1000,
            spec["base_length"] * 1000, spec["base_width"] * 1000,
            spec["base_height"] * 1000, extra))
    names = " + ".join("%s(%s)" % (j["name"], "转动" if j["type"] == "revolute" else "平移")
                       for j in spec["joints"])
    return "%s%s · %d 轴 · %s" % (spec["archetype_name"], tag,
                                  len(spec["joints"]), names)


def _wheel_count(raw):
    """轮数：优先听学生说的（两轮/三轮/四轮），没说就按四轮（更像真车）。"""
    text = " ".join(str(raw.get(k) or "")
                    for k in ("brief", "description", "requirement")).lower()
    if re.search(r"两轮|2\s*轮|二轮|三轮|3\s*轮", text):
        return 3
    if re.search(r"四轮|4\s*轮", text):
        return 4
    n = _num(raw.get("wheels"), 4, 3, 4)
    return 3 if n <= 3 else 4
