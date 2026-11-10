# -*- coding: utf-8 -*-
"""探针：没有 package.xml / CMakeLists / 包名不叫 *_pkg 时，导入会得到什么。

四种形态各建一个临时会话，跑真实的导入 + 建图，把结果打印出来。
不调模型，不碰用户数据（ROS2_DATA_DIR 指向临时目录）。
"""
import os
import shutil
import sys
import tempfile

TMP = tempfile.mkdtemp(prefix="ros2_probe_")
os.environ["ROS2_DATA_DIR"] = TMP
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import graph_api, orchestrator, project_import  # noqa: E402

NODE_PUB = """import rclpy
from rclpy.node import Node
from std_msgs.msg import String


class Talker(Node):
    def __init__(self):
        super().__init__('talker_node')
        self.pub = self.create_publisher(String, '/chatter', 10)


def main():
    rclpy.init()
    rclpy.spin(Talker())
"""

NODE_SUB = """import rclpy
from rclpy.node import Node
from std_msgs.msg import String


class Listener(Node):
    def __init__(self):
        super().__init__('listener_node')
        self.sub = self.create_subscription(String, '/chatter', self.cb, 10)

    def cb(self, msg):
        pass
"""

CPP_NODE = """#include "rclcpp/rclcpp.hpp"
#include "std_msgs/msg/string.hpp"

int main(int argc, char ** argv) {
  rclcpp::init(argc, argv);
  auto node = std::make_shared<rclcpp::Node>("cpp_talker");
  auto pub = node->create_publisher<std_msgs::msg::String>("/cpp_chatter", 10);
  rclcpp::spin(node);
  rclcpp::shutdown();
}
"""

CMAKE = """cmake_minimum_required(VERSION 3.8)
project(nav_cpp)
find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
add_executable(nav_node src/nav_node.cpp)
target_link_libraries(nav_node rclcpp::rclcpp)
ament_package()
"""

CASES = {
    # 1. 没有 package.xml，但有 setup.py（最常见的「别人只给了源码」）
    "无package.xml-有setup.py": [
        {"path": "src/myrobot/setup.py",
         "content": "from setuptools import setup\nsetup(name='myrobot', version='0.1.0')\n"},
        {"path": "src/myrobot/myrobot/talker_node.py", "content": NODE_PUB},
    ],
    # 2. 三个清单文件全没有，纯目录 + 节点
    "三件套全无-只有目录和节点": [
        {"path": "src/demo/talker_node.py", "content": NODE_PUB},
        {"path": "src/demo/listener_node.py", "content": NODE_SUB},
    ],
    # 3. 纯 C++ 包：只有 CMakeLists + .cpp
    "纯C++包-CMakeLists": [
        {"path": "src/nav_cpp/CMakeLists.txt", "content": CMAKE},
        {"path": "src/nav_cpp/package.xml",
         "content": '<package format="3"><name>nav_cpp</name><version>0.0.1</version>'
                    '<export><build_type>ament_cmake</build_type></export></package>'},
        {"path": "src/nav_cpp/src/nav_node.cpp", "content": CPP_NODE},
    ],
    # 4. 包名不叫 *_pkg，而是一个普通名字
    "包名不叫pkg": [
        {"path": "src/robot_control/package.xml",
         "content": '<package format="3"><name>robot_control</name><version>0.0.1</version>'
                    '<export><build_type>ament_python</build_type></export></package>'},
        {"path": "src/robot_control/setup.py",
         "content": "from setuptools import setup\nsetup(name='robot_control', version='0.1.0')\n"},
        {"path": "src/robot_control/robot_control/talker_node.py", "content": NODE_PUB},
        {"path": "src/robot_control/robot_control/listener_node.py", "content": NODE_SUB},
    ],
}


def run(name, files):
    print("\n" + "=" * 70)
    print("场景:", name)
    sess = orchestrator.get_session("probe_" + str(abs(hash(name)) % 100000))
    ok, msg, struct = project_import.import_to_session(sess, files)
    print("  导入:", ok, "|", (msg or "").splitlines()[0][:80])
    if not ok:
        return
    ip = sess.imported_project or {}
    print("  项目名:", ip.get("name"), "| package_name:", sess.package_name)
    print("  识别到的包:", [(p["name"], p.get("build_type"), p.get("inferred"))
                          for p in ip.get("packages") or []])
    print("  识别到的节点:", [(n["name"], n.get("package")) for n in ip.get("nodes") or []])
    print("  话题:", ip.get("topics"))
    g = graph_api.load(sess)
    print("  建图: nodes=%d edges=%d" % (len(g.get("nodes") or []), len(g.get("edges") or [])))
    for n in g.get("nodes") or []:
        print("    -", n["id"], "|", n.get("package"), "|", n.get("file"))
    for e in g.get("edges") or []:
        print("    ->", e["from"], "--%s-->" % e["topic"], e["to"])
    if g.get("warnings"):
        print("  warnings:", g["warnings"][:3])


def main():
    print("临时数据目录:", TMP)
    for name, files in CASES.items():
        run(name, files)
    shutil.rmtree(TMP, ignore_errors=True)
    print("\n已清理临时目录")


if __name__ == "__main__":
    main()
