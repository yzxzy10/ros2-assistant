# -*- coding: utf-8 -*-
"""把「一个目录 + 几个 .py」补成一个 colcon 认得的 ament_python 包。

为什么要有这个模块：会话里生成节点时，代码是直接写到 `src/<pkg>/<pkg>/xxx.py` 的。
只要这个目录缺 `package.xml`，colcon 就把它当成普通目录 —— 编译日志里一个包都没有，
`install/` 里自然也没有它，`ros2 run <pkg> xxx` 立刻报 `Package '<pkg>' not found`。
用户看到的结论是「编译报错、链路跑不通」，但他写的节点代码一行问题都没有，缺的是包的壳。

壳由四样东西组成，少一样都当不成包：
  · package.xml   —— colcon 认包的**唯一**依据（只认这个文件）
  · setup.py      —— entry_points 里注册 `可执行名 = 包名.文件名:main`
  · setup.cfg     —— 把 script 落到 lib/<pkg>，否则 ros2 run 找不到可执行
  · resource/<pkg>—— ament 的包索引标记（空文件）
"""
import os
import re

from .chain_msg import _snake

# 谁生成的就认谁的：带这个标记的 setup.py 可以整份重写（入口永远跟目录里的文件一致）；
# 用户自己写的 setup.py 绝不动，只往里追加缺的入口。
MARK = "# generated-by: ros2-assistant/pkg_scaffold"

_PKG_XML = """<?xml version="1.0"?>
<?xml-model href="http://download.ros.org/schema/package_format3.xsd" schematypens="http://www.w3.org/2001/XMLSchema"?>
<!-- %(mark)s：缺了这个文件 colcon 就不认这个包 -->
<package format="3">
  <name>%(name)s</name>
  <version>0.1.0</version>
  <description>%(desc)s</description>
  <maintainer email="student@example.com">student</maintainer>
  <license>Apache-2.0</license>
%(deps)s
  <test_depend>ament_copyright</test_depend>
  <test_depend>ament_flake8</test_depend>
  <test_depend>ament_pep257</test_depend>
  <test_depend>python3-pytest</test_depend>

  <export>
    <build_type>ament_python</build_type>
  </export>
</package>
"""

_SETUP_PY = """%(__mark__)s
from setuptools import find_packages, setup

package_name = '%(name)s'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='student',
    maintainer_email='student@example.com',
    description='%(desc)s',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
%(entries)s        ],
    },
)
"""

_SETUP_CFG = """[develop]
script_dir=$base/lib/%(name)s
[install]
install_scripts=$base/lib/%(name)s
"""

# from <pkg>.msg/srv/action import ... —— 这些是真正的 ROS 依赖
_INTF = re.compile(r"^\s*from\s+([A-Za-z_]\w*)\.(?:msg|srv|action)\s+import\b", re.M)
# from/import <mod>：只用来认 rclpy / std_msgs 这种顶层名
_IMP = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_]\w*)", re.M)
_SKIP = {"build", "install", "log", "__pycache__", "pkgcache", "node_modules", ".git"}


def _nodes(inner):
    """包里的节点文件（不含 __init__），可执行名用文件名 —— 和 setup.py 的模块名天然一致。"""
    out = []
    if not os.path.isdir(inner):
        return out
    for fn in sorted(os.listdir(inner)):
        if not fn.endswith(".py") or fn.startswith("_"):
            continue
        out.append(_snake(fn[:-3]))
    return out


def _deps(inner):
    """按源码里的 import 算依赖。包名不写全不影响编译，但会影响运行时找接口。"""
    deps = {"rclpy"}
    if not os.path.isdir(inner):
        return deps
    for fn in os.listdir(inner):
        if not fn.endswith(".py"):
            continue
        try:
            with open(os.path.join(inner, fn), encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        deps |= set(_INTF.findall(text))
        for m in _IMP.findall(text):
            if m in ("rclpy", "std_msgs", "sensor_msgs", "geometry_msgs",
                     "nav_msgs", "tf2_ros", "builtin_interfaces"):
                deps.add(m)
    return deps


def _entries(pkg, nodes):
    return "".join("            '%s = %s.%s:main',\n" % (n, pkg, n) for n in nodes)


def _write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def ensure(root, pkg, desc="ROS2 nodes"):
    """保证 `src/<pkg>` 是一个能 colcon build 的合法包，返回（补了哪些文件的）列表。

    幂等：已经齐了的包不会动；自己生成的 setup.py 会按目录现状重建，
    用户手写的只追加缺的 console_scripts（永远不覆盖他改过的内容）。
    """
    if not pkg:
        return []
    fixed = []
    base = os.path.join(root, "src", pkg)
    inner = os.path.join(base, pkg)
    os.makedirs(inner, exist_ok=True)
    nodes = _nodes(inner)

    init = os.path.join(inner, "__init__.py")
    if not os.path.isfile(init):
        open(init, "w").close()
        fixed.append("__init__.py")

    px = os.path.join(base, "package.xml")
    deps = _deps(inner)
    if not os.path.isfile(px):
        body = "".join("  <depend>%s</depend>\n" % d for d in sorted(deps))
        _write(px, _PKG_XML % {"name": pkg, "desc": desc or "ROS2 nodes",
                               "mark": MARK, "deps": body.rstrip("\n")})
        fixed.append("package.xml")

    res = os.path.join(base, "resource", pkg)
    if not os.path.isfile(res):
        _write(res, "")
        fixed.append("resource/" + pkg)

    cfg = os.path.join(base, "setup.cfg")
    if not os.path.isfile(cfg):
        _write(cfg, _SETUP_CFG % {"name": pkg})
        fixed.append("setup.cfg")

    sp = os.path.join(base, "setup.py")
    if not os.path.isfile(sp):
        _write(sp, _SETUP_PY % {"__mark__": MARK, "name": pkg,
                                "desc": desc or "ROS2 nodes",
                                "entries": _entries(pkg, nodes)})
        fixed.append("setup.py")
    else:
        try:
            with open(sp, encoding="utf-8", errors="replace") as f:
                txt = f.read()
        except OSError:
            txt = ""
        if MARK in txt:
            # 我们自己生成的：整份重建，入口永远等于目录里真实存在的文件
            new = _SETUP_PY % {"__mark__": MARK, "name": pkg,
                               "desc": desc or "ROS2 nodes",
                               "entries": _entries(pkg, nodes)}
            want = len(nodes)
            # 缺入口（新加了节点）或多入口（删了节点）都要重建
            if any(("'%s = %s.%s:main'" % (n, pkg, n)) not in txt for n in nodes) \
                    or txt.count(":main',") != want:
                _write(sp, new)
                fixed.append("setup.py 入口")
        else:
            add = "".join("            '%s = %s.%s:main',\n" % (n, pkg, n)
                          for n in nodes if ("'%s =" % n) not in txt
                          and ('"%s =' % n) not in txt)
            if add and "console_scripts': [" in txt:
                _write(sp, txt.replace("console_scripts': [",
                                       "console_scripts': [\n" + add, 1))
                fixed.append("setup.py 入口")
    return fixed


def ensure_ws(root):
    """扫一遍工作区的 src/：有 .py 却没 package.xml 的目录，补成包。

    给「导入进来的 / 老版本生成的工作区」兜底 —— 它们的节点代码是好的，
    只是当年没人给它壳。返回补过壳的包名列表。
    """
    src = os.path.join(root, "src")
    if not os.path.isdir(src):
        return []
    out = []
    for name in sorted(os.listdir(src)):
        d = os.path.join(src, name)
        if not os.path.isdir(d) or name in _SKIP or name.startswith("."):
            continue
        if os.path.isfile(os.path.join(d, "package.xml")):
            continue
        if os.path.isfile(os.path.join(d, "CMakeLists.txt")) or \
                os.path.isfile(os.path.join(d, "setup.py")):
            continue          # 已经有构建脚本，是正经包（或 C++ 包），别插手
        # 只碰「一眼是 python 节点」的目录：本体在 d 下或 d/<同名>/ 下都行
        inner = os.path.join(d, name) if os.path.isdir(os.path.join(d, name)) else d
        pys = [f for f in os.listdir(inner) if f.endswith(".py")]
        if not pys:
            continue
        if not any(_looks_like_node(os.path.join(inner, f)) for f in pys):
            continue          # 一堆普通脚本，不是 ROS 节点，凭空造包只会添乱
        if ensure(root, name):
            out.append(name)
    return out


def _looks_like_node(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return False
    return "rclpy" in text or "Node" in text
