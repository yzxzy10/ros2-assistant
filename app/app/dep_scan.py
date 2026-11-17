"""扫源码，算出「这个项目需要哪些 apt 包」。

只认两类线索，够用且不猜：
  1. `from vision_msgs.msg import X` —— 消息包缺了节点启动就崩，必须先补；
  2. package.xml 里的 <depend> / <exec_depend> —— 声明写了的，运行时大概率要用。

产出的是 apt 包名（ros-jazzy-vision-msgs / python3-opencv）。容器里用
`dpkg -s` 判断装没装，缺的从本地包池 dpkg -i 补上 —— 全程不需要网络。
"""
import os
import re

# ROS 之外的常见 import：python 模块名和 apt 包名对不上，单独列一张表
_PY_APT = {
    "cv2": "python3-opencv",
    "yaml": "python3-yaml",
    "numpy": "python3-numpy",
    "PIL": "python3-pil",
    "serial": "python3-serial",
    "scipy": "python3-scipy",
    "transforms3d": "python3-transforms3d",
    "matplotlib": "python3-matplotlib",
}

# ROS 的 python 包名和 apt 包名一一对应（下划线换连字符），列出来免得漏
_ROS_MODS = {
    "cv_bridge", "image_transport", "message_filters", "tf2_ros",
    "tf2_geometry_msgs", "tf2_sensor_msgs", "action_msgs", "rosbag2_py",
    "launch_ros", "xacro", "robot_state_publisher", "joint_state_publisher",
    "sensor_msgs_py", "rosidl_runtime_py", "composition_interfaces",
}

# from <pkg>.msg/.srv/.action import ... —— 直接就是 ROS 包名
_INTF = re.compile(r"^\s*from\s+([A-Za-z_]\w*)\.(?:msg|srv|action)\s+import\b", re.M)
# <depend>xxx</depend>
_DEP = re.compile(r"<\s*(?:depend|exec_depend)\s*>\s*([\w\-]+)\s*<", re.I)
# <name>xxx</name> —— 工作空间自己声明的包，是本地包，不能去 apt 里找
_NAME = re.compile(r"<\s*name\s*>\s*([\w\-]+)\s*<", re.I)
# 任意 import，用来对上面的表
_IMP = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_]\w*)", re.M)

# 这些目录是我们的产物或缓存，扫了只会自找麻烦
_SKIP = {"build", "install", "log", ".git", "__pycache__", "pkgcache", "node_modules"}


def _walk(root):
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in _SKIP]
        for f in fn:
            if f.endswith((".py", ".xml")):
                yield os.path.join(dp, f)


def scan(root):
    """返回 (ros 包集合, 系统包集合, 工作空间内部包集合)，都还是「裸名字」阶段。"""
    ros, sys_pkg, local = set(), set(), set()
    for path in _walk(root):
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        if path.endswith(".xml"):
            # 先把工作空间自己的包名收齐：robot_msgs 这种是本地产物，
            # 去 apt 里找 ros-<distro>-robot-msgs 永远找不到，会把整次验证掐掉
            for name in _NAME.findall(text):
                local.add(name.strip().replace("-", "_"))
            for name in _DEP.findall(text):
                ros.add(name.replace("-", "_"))
        for name in _INTF.findall(text):
            ros.add(name)
        for mod in _IMP.findall(text):
            if mod in _ROS_MODS:
                ros.add(mod)
                continue
            apt = _PY_APT.get(mod)
            if apt:
                sys_pkg.add(apt)
    return ros - local, sys_pkg, local


def needed(root, distro="jazzy"):
    """返回可直接交给容器的 apt 包名列表（排好序，去重）。"""
    ros, sys_pkg, _local = scan(root)
    names = set(sys_pkg)
    for name in ros:
        # package.xml 里可能直接写系统包（python3-serial / libboost-dev），
        # 这类不能再套 ros-<distro>- 前缀，否则会变成不存在的包名。
        # 注意 name 来自 package.xml 已经被统一成下划线形式（python3_numpy），
        # 所以判断前得先把下划线还原回连字符 —— 否则会拼出
        # ros-jazzy-python3-numpy 这种根本不存在的包名，管理员照着装永远装不上。
        probe = name.replace("_", "-")
        if probe.startswith(("python3-", "lib", "boost", "ros-")) or "." in name:
            names.add(probe)
        elif name in _PY_APT:
            # <depend>serial</depend> 指的是 python3-serial，不是 ros-jazzy-serial
            names.add(_PY_APT[name])
        else:
            names.add("ros-%s-%s" % (distro, name.replace("_", "-")))
    return sorted(names)


def explain(names, distro="jazzy"):
    """给日志看的一句话，说明这些包是怎么来的。"""
    ros = [n for n in names if n.startswith("ros-%s-" % distro)]
    other = [n for n in names if n not in ros]
    return {"ros": ros, "system": other, "total": len(names)}
