# -*- coding: utf-8 -*-
"""补包壳的回归测试：src/<pkg>/ 只有 .py 时，必须能补成 colcon 认得的包。

线上教训（2026-11-17）：会话里写出来的节点直接落到 src/<pkg>/<pkg>/*.py，没有
package.xml，colcon 编译阶段 0 个包，ros2 run 报 `Package '<pkg>' not found`，
用户看到的是「写完节点，一运行就编译报错」。
"""
import os
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import pkg_scaffold                                    # noqa: E402

_OK, _BAD = [0], [0]

NODE = """import rclpy
from rclpy.node import Node
from sensor_msgs.msg import LaserScan
from std_msgs.msg import Float32


class ObstacleDetectorNode(Node):
    def __init__(self):
        super().__init__('obstacle_detector_node')


def main(args=None):
    rclpy.init(args=args)
"""

SETUP_USER = """from setuptools import setup
setup(entry_points={'console_scripts': [
    ],})
"""


def check(name, ok, detail=""):
    if ok:
        _OK[0] += 1
        print("  ✓ " + name)
    else:
        _BAD[0] += 1
        print("  ✗ " + name + ("  → " + str(detail) if detail else ""))


def _ws(files):
    root = tempfile.mkdtemp(prefix="scaffold_")
    for rel, body in files.items():
        p = os.path.join(root, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as f:
            f.write(body)
    return root


def test_bare_dir():
    print("\n[1] 只有 .py 的目录")
    root = _ws({"src/demo_pkg/demo_pkg/obstacle_detector_node.py": NODE})
    got = pkg_scaffold.ensure_ws(root)
    base = os.path.join(root, "src", "demo_pkg")
    check("被补成包", got == ["demo_pkg"], got)
    check("有 package.xml", os.path.isfile(os.path.join(base, "package.xml")))
    check("有 setup.py", os.path.isfile(os.path.join(base, "setup.py")))
    check("有 setup.cfg", os.path.isfile(os.path.join(base, "setup.cfg")))
    check("有 resource 标记", os.path.isfile(os.path.join(base, "resource", "demo_pkg")))
    check("有 __init__.py",
          os.path.isfile(os.path.join(base, "demo_pkg", "__init__.py")))

    ET.parse(os.path.join(base, "package.xml"))          # 解析不了会抛
    check("package.xml 合法", True)

    with open(os.path.join(base, "setup.py"), encoding="utf-8") as f:
        setup = f.read()
    compile(setup, "setup.py", "exec")
    check("setup.py 语法正确", True)
    check("入口指向真实文件",
          "'obstacle_detector_node = demo_pkg.obstacle_detector_node:main'" in setup,
          setup)
    with open(os.path.join(base, "package.xml"), encoding="utf-8") as f:
        xml = f.read()
    check("依赖按 import 补齐", "<depend>sensor_msgs</depend>" in xml
          and "<depend>std_msgs</depend>" in xml, xml)
    check("二次调用无改动", pkg_scaffold.ensure_ws(root) == [])


def test_entry_follows_files():
    print("\n[2] 加删节点后入口跟着变")
    root = _ws({"src/p/p/a_node.py": NODE})
    pkg_scaffold.ensure_ws(root)
    with open(os.path.join(root, "src/p/p/b_node.py"), "w", encoding="utf-8") as f:
        f.write(NODE)
    pkg_scaffold.ensure(root, "p")
    with open(os.path.join(root, "src/p/setup.py"), encoding="utf-8") as f:
        setup = f.read()
    check("新节点自动进入口", "'b_node = p.b_node:main'" in setup, setup)
    os.remove(os.path.join(root, "src/p/p/a_node.py"))
    pkg_scaffold.ensure(root, "p")
    with open(os.path.join(root, "src/p/setup.py"), encoding="utf-8") as f:
        setup = f.read()
    check("删掉的节点不再留入口", "'a_node = p.a_node:main'" not in setup, setup)


def test_user_setup_untouched():
    print("\n[3] 用户手写的 setup.py 不被覆盖")
    root = _ws({"src/mine/mine/a_node.py": NODE, "src/mine/setup.py": SETUP_USER})
    pkg_scaffold.ensure(root, "mine")
    with open(os.path.join(root, "src/mine/setup.py"), encoding="utf-8") as f:
        setup = f.read()
    check("原有内容还在", "from setuptools import setup" in setup, setup)
    check("只追加入口", "'a_node = mine.a_node:main'" in setup, setup)


def test_cpp_ignored():
    print("\n[4] C++ 包和非 ROS 脚本不动")
    root = _ws({"src/cpp_pkg/CMakeLists.txt": "cmake_minimum_required(VERSION 3.8)\n",
                "src/cpp_pkg/src/x.cpp": "int main(){return 0;}\n",
                "src/tools/hello.py": "print('hi')\n"})
    check("没有 package.xml 被凭空造出来", pkg_scaffold.ensure_ws(root) == [])


def main():
    test_bare_dir()
    test_entry_follows_files()
    test_user_setup_untouched()
    test_cpp_ignored()
    print("\n通过 %d 项，失败 %d 项" % (_OK[0], _BAD[0]))
    return 1 if _BAD[0] else 0


if __name__ == "__main__":
    sys.exit(main())
