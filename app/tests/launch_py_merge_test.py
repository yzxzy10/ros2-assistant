# -*- coding: utf-8 -*-
"""「.py 后缀 launch + 归并 + include + ExecuteProcess 过滤」的回归测试。

合成一个最小 ROS2 工作区，验证本次改动：
1. xxx_launch.py（.py 后缀）被当 launch 解析，而不是当 rclpy 源码漏掉；
2. launch 外部节点按 executable/name/cls 归并回源码节点，不再误标外部；
3. ComposableNode 按 plugin 类名归并；
4. ExecuteProcess 的工具命令（xacro/gz/python3）不画节点；
5. IncludeLaunchDescription 引用的外部包标成 INCLUDE 外部节点；
6. nav2::LifecycleNode 这类自定义基类的节点名与类名都能提取。
"""
import os
import shutil
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import graph

_PKG_XML = '<?xml version="1.0"?><package format="3"><name>%s</name>\
<version>0.0.0</version><description>x</description>\
<maintainer email="x@x">x</maintainer><license>Apache-2.0</license>\
<build_type>ament_cmake</build_type></package>'


def _write(root, rel, content):
    p = os.path.join(root, rel)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        f.write(content)


def build_ws():
    root = tempfile.mkdtemp(prefix="launchpy_")
    _write(root, "pkg_a/package.xml", _PKG_XML % "pkg_a")
    # nav2 风格：自定义基类 nav2::LifecycleNode
    _write(root, "pkg_a/src/lifecycle.cpp",
           'class LifecycleManager : public nav2::LifecycleNode {\n'
           'public:\n'
           '  LifecycleManager(const rclcpp::NodeOptions & o)\n'
           '  : nav2::LifecycleNode("lifecycle_manager", "", o) {}\n'
           '};\n')
    # .py 后缀的 launch（不是 .launch.py）
    _write(root, "pkg_a/launch/bringup_launch.py",
           'from launch import LaunchDescription\n'
           'from launch_ros.actions import Node\n'
           'def generate_launch_description():\n'
           '    return LaunchDescription([\n'
           '        Node(package="pkg_a", executable="lifecycle_manager",\n'
           '             name="lifecycle_manager_nav"),\n'
           '        Node(package="pkg_a", executable="lifecycle_manager",\n'
           '             name="lifecycle_manager_loc"),\n'
           '    ])\n')

    _write(root, "pkg_b/package.xml", _PKG_XML % "pkg_b")
    # map_saver_server 可执行名 vs map_saver 节点名
    _write(root, "pkg_b/src/map_saver.cpp",
           'class MapSaver : public rclcpp::Node {\n'
           'public:\n'
           '  MapSaver() : rclcpp::Node("map_saver") {}\n'
           '};\n')
    _write(root, "pkg_b/launch/map_saver_server.launch.py",
           'from launch import LaunchDescription\n'
           'from launch_ros.actions import Node\n'
           'def generate_launch_description():\n'
           '    return LaunchDescription([\n'
           '        Node(package="pkg_b", executable="map_saver_server"),\n'
           '    ])\n')

    _write(root, "pkg_c/package.xml", _PKG_XML % "pkg_c")
    # ComposableNode 按 plugin 类名归并
    _write(root, "pkg_c/src/map_server.cpp",
           'class MapServer : public rclcpp::Node {\n'
           'public:\n'
           '  MapServer() : rclcpp::Node("map_server") {}\n'
           '};\n')
    _write(root, "pkg_c/launch/comp.launch.py",
           'from launch import LaunchDescription\n'
           'from launch_ros.actions import ComposableNode\n'
           'from launch_ros.actions import ComposableNodeContainer\n'
           'def generate_launch_description():\n'
           '    return LaunchDescription([\n'
           '        ComposableNode(package="pkg_c",\n'
           '                       plugin="pkg_c::MapServer",\n'
           '                       name="comp_map_server"),\n'
           '    ])\n')

    _write(root, "pkg_d/package.xml", _PKG_XML % "pkg_d")
    # ExecuteProcess 工具命令过滤 + include 外部包
    _write(root, "pkg_d/launch/sim_launch.py",
           'from launch import LaunchDescription\n'
           'from launch.actions import ExecuteProcess, IncludeLaunchDescription\n'
           'from launch.launch_description_sources import PythonLaunchDescriptionSource\n'
           'import os\n'
           'from ament_index_python.packages import get_package_share_directory\n'
           'def generate_launch_description():\n'
           '    xacro_cmd = ExecuteProcess(cmd=["xacro", "a.xacro"])\n'
           '    gz_cmd = ExecuteProcess(cmd=["gz", "sim", "-r", "w.sdf"])\n'
           '    inc = IncludeLaunchDescription(\n'
           '        PythonLaunchDescriptionSource(os.path.join(\n'
           '            get_package_share_directory("slam_toolbox"),\n'
           '            "launch", "online.launch.py")))\n'
           '    return LaunchDescription([xacro_cmd, gz_cmd, inc])\n')
    return root


def main():
    root = build_ws()
    try:
        res = graph.analyze(root)
        names = {n["name"] for n in res["nodes"]}
        ext = {n["name"] for n in res["nodes"] if n.get("external")}
        src = {n["name"]: n for n in res["nodes"] if not n.get("external")}

        checks = []
        def check(cond, msg):
            checks.append((cond, msg))

        # 1. .py launch 被解析：lifecycle_manager_nav/loc 归并到 lifecycle_manager 源码节点
        check("lifecycle_manager_nav" not in ext, "lifecycle_manager_nav 归并（不标外部）")
        check("lifecycle_manager_loc" not in ext, "lifecycle_manager_loc 归并（不标外部）")
        check("lifecycle_manager" in src, "lifecycle_manager 是源码节点")
        lm = src.get("lifecycle_manager", {})
        check(lm.get("cls") == "LifecycleManager", "nav2::LifecycleNode 提取类名")
        check(set(lm.get("launch_names", [])) ==
              {"lifecycle_manager_nav", "lifecycle_manager_loc"},
              "归并记录 launch 别名")

        # 2. map_saver_server 去后缀归并到 map_saver
        check("map_saver_server" not in ext, "map_saver_server 归并（不标外部）")
        check("map_saver" in src, "map_saver 是源码节点")

        # 3. ComposableNode plugin 类名归并
        check("comp_map_server" not in ext, "composable 按 plugin 类名归并")

        # 4. ExecuteProcess 工具命令不画节点
        check("xacro" not in names, "xacro 工具命令不画节点")
        check("gz" not in names, "gz 工具命令不画节点")

        # 5. include 外部包标 INCLUDE 外部节点
        check("slam_toolbox" in ext, "include 的 slam_toolbox 标为外部节点")

        # 6. 真正的外部节点（Node 拉起的第三方包）仍保留
        _write(root, "pkg_d/launch/third.launch.py",
               'from launch import LaunchDescription\n'
               'from launch_ros.actions import Node\n'
               'def generate_launch_description():\n'
               '    return LaunchDescription([\n'
               '        Node(package="rviz2", executable="rviz2"),\n'
               '    ])\n')
        res2 = graph.analyze(root)
        ext2 = {n["name"] for n in res2["nodes"] if n.get("external")}
        check("rviz2" in ext2, "第三方 Node（rviz2）仍标外部")

        failed = 0
        for ok, msg in checks:
            print(("  PASS  " if ok else "  FAIL  ") + msg)
            if not ok:
                failed += 1
        print("结果:", "PASS" if failed == 0 else ("%d 项失败" % failed))
        return 1 if failed else 0
    finally:
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
