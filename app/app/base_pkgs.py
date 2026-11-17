# -*- coding: utf-8 -*-
"""ros_base 变体自带哪些 ROS 包 —— 由 tools/dump_base_pkgs.py 从软件源算出，别手改。

验证镜像的基础就是 `ros:jazzy-ros-base`，所以「镜子里一定有的包」=
`ros-<distro>-ros-base` 这个元包递归展开后的集合。用户项目缺的包落在不在
这个集合里，处置完全不同：

  · 在集合里 → 镜像本该有，缺了要么镜像不完整，要么包名写错（用户自己能查）；
  · 不在集合里 → 需要**额外安装**，用户装不了（容器无外网），只能找管理员。

判错了的代价是给人指错方向，所以这份清单必须从软件源算，不要凭印象手写。
重新生成：`python tools/dump_base_pkgs.py`（有网机器上跑一次即可，产出
app/base_pkgs.json；存在就覆盖下面内置这份，不用改代码）。
"""
import json
import os

# jazzy: ros_base 变体自带的 ROS 包（裸包名，不带 ros-<distro>- 前缀）
_BASE = {
    "jazzy": frozenset("""
        action_msgs actionlib_msgs ament_cmake ament_cmake_auto ament_cmake_copyright ament_cmake_core ament_cmake_cppcheck ament_cmake_cpplint
        ament_cmake_export_definitions ament_cmake_export_dependencies ament_cmake_export_include_directories ament_cmake_export_interfaces ament_cmake_export_libraries ament_cmake_export_link_flags ament_cmake_export_targets ament_cmake_flake8
        ament_cmake_gen_version_h ament_cmake_gmock ament_cmake_gtest ament_cmake_include_directories ament_cmake_libraries ament_cmake_lint_cmake ament_cmake_pep257 ament_cmake_pytest
        ament_cmake_python ament_cmake_ros ament_cmake_target_dependencies ament_cmake_test ament_cmake_uncrustify ament_cmake_version ament_cmake_xmllint ament_copyright
        ament_cppcheck ament_cpplint ament_flake8 ament_index_cpp ament_index_python ament_lint ament_lint_auto ament_lint_cmake
        ament_lint_common ament_package ament_pep257 ament_uncrustify ament_xmllint builtin_interfaces class_loader common_interfaces
        composition_interfaces console_bridge_vendor diagnostic_msgs domain_coordinator eigen3_cmake_module fastcdr fastrtps fastrtps_cmake_module
        foonathan_memory_vendor geometry2 geometry_msgs gmock_vendor gtest_vendor kdl_parser keyboard_handler launch
        launch_ros launch_testing launch_testing_ament_cmake launch_testing_ros launch_xml launch_yaml liblz4_vendor libstatistics_collector
        libyaml_vendor lifecycle_msgs mcap_vendor message_filters nav_msgs orocos_kdl_vendor osrf_pycommon pluginlib
        pybind11_vendor python_cmake_module python_orocos_kdl_vendor rcl rcl_action rcl_interfaces rcl_lifecycle rcl_logging_interface
        rcl_logging_spdlog rcl_yaml_param_parser rclcpp rclcpp_action rclcpp_components rclcpp_lifecycle rclpy rcpputils
        rcutils rmw rmw_dds_common rmw_fastrtps_cpp rmw_fastrtps_shared_cpp rmw_implementation rmw_implementation_cmake robot_state_publisher
        ros2action ros2bag ros2cli ros2cli_common_extensions ros2component ros2doctor ros2interface ros2launch
        ros2lifecycle ros2multicast ros2node ros2param ros2pkg ros2plugin ros2run ros2service
        ros2topic ros_base ros_core ros_environment ros_workspace rosbag2 rosbag2_compression rosbag2_compression_zstd
        rosbag2_cpp rosbag2_interfaces rosbag2_py rosbag2_storage rosbag2_storage_default_plugins rosbag2_storage_mcap rosbag2_storage_sqlite3 rosbag2_transport
        rosgraph_msgs rosidl_adapter rosidl_cli rosidl_cmake rosidl_core_generators rosidl_core_runtime rosidl_default_generators rosidl_default_runtime
        rosidl_dynamic_typesupport rosidl_dynamic_typesupport_fastrtps rosidl_generator_c rosidl_generator_cpp rosidl_generator_py rosidl_generator_rs rosidl_generator_type_description rosidl_parser
        rosidl_pycommon rosidl_runtime_c rosidl_runtime_cpp rosidl_runtime_py rosidl_typesupport_c rosidl_typesupport_cpp rosidl_typesupport_fastrtps_c rosidl_typesupport_fastrtps_cpp
        rosidl_typesupport_interface rosidl_typesupport_introspection_c rosidl_typesupport_introspection_cpp rpyutils sensor_msgs sensor_msgs_py service_msgs shape_msgs
        spdlog_vendor sqlite3_vendor sros2 sros2_cmake statistics_msgs std_msgs std_srvs stereo_msgs
        tf2 tf2_bullet tf2_eigen tf2_eigen_kdl tf2_geometry_msgs tf2_kdl tf2_msgs tf2_py
        tf2_ros tf2_ros_py tf2_sensor_msgs tf2_tools tinyxml2_vendor tracetools trajectory_msgs type_description_interfaces
        uncrustify_vendor unique_identifier_msgs urdf urdf_parser_plugin urdfdom urdfdom_headers visualization_msgs yaml_cpp_vendor
        zstd_vendor
    """.split()),
}


def _load_generated():
    """tools/dump_base_pkgs.py 生成过清单就接管内置这份 —— 发行版升级后
    重跑一次脚本即可，不必手改代码里的长字符串。"""
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "base_pkgs.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return
    for k, v in (data or {}).items():
        if isinstance(v, list) and len(v) > 20:      # 太短的多半是抓坏了
            _BASE[str(k)] = frozenset(str(x) for x in v)


_load_generated()


def _bare(name, distro="jazzy"):
    """把 apt 名或 ros 包名统一成裸包名：ros-jazzy-tf2-ros / tf2_ros -> tf2_ros。"""
    n = str(name or "").strip().lower()
    pre = "ros-%s-" % distro
    if n.startswith(pre):
        n = n[len(pre):]
    return n.replace("-", "_")


def is_base(name, distro="jazzy"):
    """这个包是基础镜像自带的吗？

    返回 True / False；清单里没有这个发行版时返回 None（不知道就别乱下结论）。
    """
    table = _BASE.get(distro)
    if not table:
        return None
    return _bare(name, distro) in table


def classify(names, distro="jazzy"):
    """把一批包分成「基础包缺失」和「需要额外安装」。

    返回 (base_miss, extra, unknown)：unknown 是发行版没有清单时的全部包，
    调用方据此走「不确定」的措辞，而不是硬塞一个结论。
    """
    base, extra, unknown = [], [], []
    for n in names or []:
        verdict = is_base(n, distro)
        if verdict is None:
            unknown.append(n)
        elif verdict:
            base.append(n)
        else:
            extra.append(n)
    return base, extra, unknown


def count(distro="jazzy"):
    return len(_BASE.get(distro) or ())
