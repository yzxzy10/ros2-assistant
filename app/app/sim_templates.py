# -*- coding: utf-8 -*-
"""仿真骨架模板：结构固定，模型只填参数，不重写结构。

为什么用 .py 里的字符串常量而不是 .tpl 文件
------------------------------------------
部署时整个 app/ 目录会被复制，数据文件（.tpl/.sdf）容易在打包环节漏掉，
而漏掉的表现是「运行时找不到模板」——上线才发现。放成字符串常量最稳。

占位符统一用 ``{{name}}``，由 sim_build 逐项 replace。
不用 str.format，因为模板里含大量 Python/XML 的 ``{}``，转义会出错。

模板来源
--------
- 机械臂骨架：ros2_control_demos/example_9（官方 CI 覆盖 Jazzy + Harmonic）
  改动三处：include 改 ``$(dirname)`` 相对包含、插件名去掉 lib/.so 前缀、
  控制器默认 forward_command_controller。
- 差速底盘骨架：andino_gz + linorobot2 的 gz-sim-diff-drive-system 用法。
- 两者的 ``<link name="world"/>`` 锚定法均来自 example_9 官方写法。
"""

PACKAGE_XML = """<?xml version="1.0"?>
<?xml-model href="http://download.ros.org/schema/package_format3.xsd" schematypens="http://www.w3.org/2001/XMLSchema"?>
<package format="3">
  <name>{{pkg}}</name>
  <version>0.1.0</version>
  <description>{{desc}}</description>
  <maintainer email="student@example.com">student</maintainer>
  <license>Apache-2.0</license>

  <buildtool_depend>ament_cmake</buildtool_depend>

  <exec_depend>robot_state_publisher</exec_depend>
  <exec_depend>joint_state_publisher_gui</exec_depend>
  <exec_depend>rviz2</exec_depend>
  <exec_depend>xacro</exec_depend>
  <exec_depend>ros_gz_sim</exec_depend>
  <exec_depend>ros_gz_bridge</exec_depend>
  <exec_depend>launch</exec_depend>
  <exec_depend>launch_ros</exec_depend>
{{extra_deps}}
  <export>
    <build_type>ament_cmake</build_type>
  </export>
</package>
"""

EXTRA_DEPS_ARM = """  <exec_depend>controller_manager</exec_depend>
  <exec_depend>joint_state_broadcaster</exec_depend>
  <exec_depend>forward_command_controller</exec_depend>
  <exec_depend>gz_ros2_control</exec_depend>"""

EXTRA_DEPS_DIFF = """  <exec_depend>teleop_twist_keyboard</exec_depend>"""

CMAKE_LISTS = """cmake_minimum_required(VERSION 3.8)
project({{pkg}})

find_package(ament_cmake REQUIRED)

# 仿真包没有可执行节点，全部是描述与配置，整目录安装即可
install(DIRECTORY urdf config launch worlds
        DESTINATION share/${PROJECT_NAME})

install(PROGRAMS scripts/selfcheck.py
        DESTINATION share/${PROJECT_NAME}/scripts)

ament_package()
"""

WORLD_EMPTY = """<?xml version="1.0" ?>
<sdf version="1.9">
  <world name="empty">

    <physics name="1ms" type="ignored">
      <max_step_size>0.001</max_step_size>
      <real_time_factor>1.0</real_time_factor>
    </physics>

    <plugin filename="gz-sim-physics-system" name="gz::sim::systems::Physics"/>
    <plugin filename="gz-sim-user-commands-system" name="gz::sim::systems::UserCommands"/>
    <plugin filename="gz-sim-scene-broadcaster-system" name="gz::sim::systems::SceneBroadcaster"/>
    <plugin filename="gz-sim-contact-system" name="gz::sim::systems::Contact"/>

    <light type="directional" name="sun">
      <cast_shadows>true</cast_shadows>
      <pose>0 0 10 0 0 0</pose>
      <diffuse>0.8 0.8 0.8 1</diffuse>
      <specular>0.2 0.2 0.2 1</specular>
      <direction>-0.5 0.1 -0.9</direction>
    </light>

    <model name="ground_plane">
      <static>true</static>
      <link name="link">
        <collision name="collision">
          <geometry>
            <plane><normal>0 0 1</normal><size>100 100</size></plane>
          </geometry>
        </collision>
        <visual name="visual">
          <geometry>
            <plane><normal>0 0 1</normal><size>100 100</size></plane>
          </geometry>
          <material>
            <ambient>0.8 0.8 0.8 1</ambient>
            <diffuse>0.8 0.8 0.8 1</diffuse>
          </material>
        </visual>
      </link>
    </model>

  </world>
</sdf>
"""

RVIZ_DISPLAY = """Panels:
  - Class: rviz_common/Displays
    Name: Displays
    Property Tree Widget:
      Expanded:
        - /Global Options1
      Splitter Ratio: 0.5
Visualization Manager:
  Class: ""
  Displays:
    - Class: rviz_default_plugins/Grid
      Enabled: true
      Name: Grid
      Cell Size: 0.1
      Plane Cell Count: 20
      Color: 160; 160; 164
    - Class: rviz_default_plugins/RobotModel
      Enabled: true
      Name: RobotModel
      Description Source: Topic
      Description Topic:
        Depth: 5
        Durability Policy: Transient Local
        History Policy: Keep Last
        Reliability Policy: Reliable
        Value: /robot_description
      Visual Enabled: true
    - Class: rviz_default_plugins/TF
      Enabled: true
      Name: TF
      Show Names: true
  Enabled: true
  Global Options:
    Fixed Frame: {{fixed_frame}}
    Frame Rate: 30
    Background Color: 250; 250; 250
  Tools:
    - Class: rviz_default_plugins/Interact
    - Class: rviz_default_plugins/MoveCamera
    - Class: rviz_default_plugins/Select
  Views:
    Current:
      Class: rviz_default_plugins/Orbit
      Distance: {{view_distance}}
      Focal Point:
        X: 0
        Y: 0
        Z: {{view_z}}
      Pitch: 0.5
      Yaw: 0.7
      Name: Current View
      Target Frame: <Fixed Frame>
    Saved: ~
Window Geometry:
  Height: 800
  Width: 1200
"""

SELFCHECK = '''#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""{{pkg}} 本地自检：在装了 ROS 2 Jazzy 的机器上跑一遍，代替服务端容器验证。

用法：
    python3 <本文件路径>
或者装好之后：
    ros2 run {{pkg}} selfcheck.py   # 若已加入 install(PROGRAMS)

检查四件事：
  1. xacro 能否展开（语法、表达式、include 路径）
  2. check_urdf 能否通过（关节树连通、无孤儿 link）
  3. 关节名在 URDF 与配置文件里是否一致
  4. 关键插件/依赖包是否安装
"""
import os
import re
import shutil
import subprocess
import sys

PKG = "{{pkg}}"
JOINTS = [{{joints_py}}]
KIND = "{{kind}}"


def run(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


def find_share():
    r = run("ros2 pkg prefix %s" % PKG)
    if r.returncode == 0 and r.stdout.strip():
        cand = os.path.join(r.stdout.strip(), "share", PKG)
        if os.path.isdir(cand):
            return cand
    here = os.path.dirname(os.path.abspath(__file__))
    for cand in (os.path.dirname(here), here):
        if os.path.isdir(os.path.join(cand, "urdf")):
            return cand
    return ""


def main():
    ok = True
    share = find_share()
    print("=" * 56)
    print("  %s 自检" % PKG)
    print("=" * 56)
    if not share:
        print("[失败] 找不到包目录，请先 colcon build 并 source install/setup.bash")
        return 2
    print("包目录: %s\\n" % share)

    # 1. xacro 展开
    xacro_file = os.path.join(share, "urdf", PKG + ".urdf.xacro")
    urdf_out = os.path.join("/tmp", PKG + "_check.urdf")
    if not shutil.which("xacro"):
        print("[跳过] 未安装 xacro，先执行 sudo apt install ros-jazzy-xacro")
    elif not os.path.isfile(xacro_file):
        print("[失败] 找不到 %s" % xacro_file)
        ok = False
    else:
        r = run("xacro %s -o %s" % (xacro_file, urdf_out))
        if r.returncode != 0:
            print("[失败] xacro 展开出错：")
            print(r.stderr.strip()[-1200:])
            ok = False
        else:
            print("[通过] xacro 展开 -> %s" % urdf_out)

    # 2. check_urdf
    if ok and os.path.isfile(urdf_out):
        if not shutil.which("check_urdf"):
            print("[跳过] 未安装 check_urdf（sudo apt install liburdfdom-tools）")
        else:
            r = run("check_urdf %s" % urdf_out)
            if r.returncode != 0 or "ERROR" in r.stdout.upper():
                print("[失败] check_urdf 未通过：")
                print((r.stdout + r.stderr).strip()[-1200:])
                ok = False
            else:
                print("[通过] check_urdf")

    # 3. 关节名一致性
    if os.path.isfile(urdf_out):
        text = open(urdf_out, encoding="utf-8", errors="replace").read()
        found = re.findall(r'<joint\\s+name="([^"]+)"', text)
        missing = [j for j in JOINTS if j not in found]
        if missing:
            print("[失败] URDF 里缺少这些关节: %s" % ", ".join(missing))
            ok = False
        else:
            print("[通过] 关节名 %s 在 URDF 中均存在" % ", ".join(JOINTS))

    # 4. 依赖包
    r = run("ros2 pkg list")
    have = set(r.stdout.split()) if r.returncode == 0 else set()
    need = ["ros_gz_sim", "ros_gz_bridge", "robot_state_publisher", "xacro"]
    if KIND == "arm":
        need += ["gz_ros2_control", "controller_manager", "forward_command_controller"]
    lack = [p for p in need if p and p not in have]
    if lack:
        print("[失败] 缺少依赖包: %s" % ", ".join(lack))
        print("       安装: sudo apt install " + " ".join(
            "ros-jazzy-" + p.replace("_", "-") for p in lack))
        ok = False
    else:
        print("[通过] 依赖包齐全")

    print("=" * 56)
    print("结论: %s" % ("全部通过，可以 launch 了" if ok else "有失败项，先按提示修"))
    print("=" * 56)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
'''


# ============ 机械臂骨架 ============

ARM_URDF_HEAD = """<?xml version="1.0"?>
<robot name="{{pkg}}" xmlns:xacro="http://www.ros.org/wiki/xacro">

  <xacro:arg name="controllers_yaml" default="$(find {{pkg}})/config/controllers.yaml"/>

  <xacro:property name="base_radius" value="{{base_radius}}"/>
  <xacro:property name="base_height" value="{{base_height}}"/>
  <xacro:property name="base_mass" value="{{base_mass}}"/>
{{properties}}

  <material name="metal"><color rgba="0.86 0.87 0.90 1.0"/></material>
  <material name="accent"><color rgba="0.92 0.45 0.13 1.0"/></material>
  <material name="joint"><color rgba="0.30 0.32 0.36 1.0"/></material>

  <!-- 固定基座：world 链路锚定来自 ros2_control_demos/example_9 -->
  <link name="world"/>

  <link name="base_link">
    <visual>
      <origin xyz="0 0 ${base_height/2}" rpy="0 0 0"/>
      <geometry><cylinder radius="${base_radius}" length="${base_height}"/></geometry>
      <material name="metal"/>
    </visual>
    <visual>
      <origin xyz="0 0 ${base_height - 0.008}" rpy="0 0 0"/>
      <geometry><box size="${base_radius*1.6} ${base_radius*1.6} 0.016"/></geometry>
      <material name="joint"/>
    </visual>
    <collision>
      <origin xyz="0 0 ${base_height/2}" rpy="0 0 0"/>
      <geometry><cylinder radius="${base_radius}" length="${base_height}"/></geometry>
    </collision>
    <inertial>
      <origin xyz="0 0 ${base_height/2}" rpy="0 0 0"/>
      <mass value="${base_mass}"/>
      <inertia ixx="${base_mass*(3*base_radius**2+base_height**2)/12}" ixy="0" ixz="0"
               iyy="${base_mass*(3*base_radius**2+base_height**2)/12}" iyz="0"
               izz="${base_mass*base_radius**2/2}"/>
    </inertial>
  </link>

  <joint name="base_fixed_joint" type="fixed">
    <parent link="world"/>
    <child link="base_link"/>
    <origin xyz="0 0 0" rpy="0 0 0"/>
  </joint>

{{link_blocks}}

  <link name="tool0"/>
  <joint name="tool_joint" type="fixed">
    <parent link="{{last_link}}"/>
    <child link="tool0"/>
    <origin xyz="0 0 ${{{last_length_attr}}}" rpy="0 0 0"/>
  </joint>

  <xacro:include filename="$(dirname)/{{pkg}}.ros2_control.xacro"/>
  <xacro:include filename="$(dirname)/{{pkg}}.gazebo.xacro"/>
</robot>
"""

# 连杆块用 %(name)s 格式化：模板里 ${} 太多，用 % 比 {{}} 替换更不容易错
ARM_LINK_BLOCK = """  <!-- %(joint)s: %(jtype_cn)s，绕 %(axis)s 轴 -->
  <link name="%(link)s">
    <visual>
      <origin xyz="0 0 ${%(link)s_length/2}" rpy="0 0 0"/>
      <geometry><cylinder radius="${%(link)s_radius}" length="${%(link)s_length}"/></geometry>
      <material name="%(mat)s"/>
    </visual>
    <visual>
      <origin xyz="0 0 ${%(link)s_radius*0.6}" rpy="0 0 0"/>
      <geometry><cylinder radius="${%(link)s_radius*1.55}" length="${%(link)s_radius*1.2}"/></geometry>
      <material name="joint"/>
    </visual>
    <visual>
      <origin xyz="0 0 ${%(link)s_length - %(link)s_radius*0.6}" rpy="0 0 0"/>
      <geometry><cylinder radius="${%(link)s_radius*1.45}" length="${%(link)s_radius*1.1}"/></geometry>
      <material name="joint"/>
    </visual>
    <collision>
      <origin xyz="0 0 ${%(link)s_length/2}" rpy="0 0 0"/>
      <geometry><cylinder radius="${%(link)s_radius}" length="${%(link)s_length}"/></geometry>
    </collision>
    <inertial>
      <origin xyz="0 0 ${%(link)s_length/2}" rpy="0 0 0"/>
      <mass value="${%(link)s_mass}"/>
      <inertia ixx="${%(link)s_mass*(3*%(link)s_radius**2+%(link)s_length**2)/12}" ixy="0" ixz="0"
               iyy="${%(link)s_mass*(3*%(link)s_radius**2+%(link)s_length**2)/12}" iyz="0"
               izz="${%(link)s_mass*%(link)s_radius**2/2}"/>
    </inertial>
  </link>

  <joint name="%(joint)s" type="%(jtype)s">
    <parent link="%(parent)s"/>
    <child link="%(link)s"/>
    <origin xyz="0 0 ${%(parent_len)s}" rpy="0 0 0"/>
    <axis xyz="%(axis_xyz)s"/>
    <limit lower="%(lower)s" upper="%(upper)s" effort="%(effort)s" velocity="%(velocity)s"/>
    <dynamics damping="0.05" friction="0.0"/>
  </joint>

"""

ARM_ROS2_CONTROL = """<?xml version="1.0"?>
<robot xmlns:xacro="http://www.ros.org/wiki/xacro">
  <ros2_control name="{{pkg}}" type="system">
    <hardware>
      <plugin>gz_ros2_control/GazeboSimSystem</plugin>
    </hardware>
{{joint_blocks}}
  </ros2_control>
</robot>
"""

ARM_RC_JOINT = """    <joint name="{{joint_name}}">
      <command_interface name="position">
        <param name="min">{{lower}}</param>
        <param name="max">{{upper}}</param>
      </command_interface>
      <state_interface name="position"/>
      <state_interface name="velocity"/>
    </joint>"""

ARM_GAZEBO = """<?xml version="1.0"?>
<robot xmlns:xacro="http://www.ros.org/wiki/xacro">
  <gazebo>
    <!-- 插件名不带 lib 前缀与 .so 后缀，这是 Harmonic 的规范写法 -->
    <plugin filename="gz_ros2_control-system"
            name="gz_ros2_control::GazeboSimROS2ControlPlugin">
      <parameters>$(arg controllers_yaml)</parameters>
    </plugin>
  </gazebo>
{{ref_blocks}}
</robot>
"""

ARM_GAZEBO_REF = """  <gazebo reference="%(link)s">
    <material>Gazebo/%(mat)s</material>
  </gazebo>"""

ARM_CONTROLLERS = """controller_manager:
  ros__parameters:
    update_rate: 100  # Hz

    joint_state_broadcaster:
      type: joint_state_broadcaster/JointStateBroadcaster

    {{controller_name}}:
      type: forward_command_controller/ForwardCommandController

    {{gripper_controller}}:
      type: forward_command_controller/ForwardCommandController

{{controller_name}}:
  ros__parameters:
    joints:
{{joints_yaml}}
    interface_name: position

{{gripper_controller}}:
  ros__parameters:
    joints:
{{gripper_joints_yaml}}
    interface_name: position
"""


ARM_GRIPPER = """  <!-- 末端夹爪：掌 + 两指，纯视觉占位；接真实夹爪时把这整块替换掉 -->
  <link name="gripper_link">
    <visual>
      <origin xyz="0 0 0.014" rpy="0 0 0"/>
      <geometry><box size="0.070 0.050 0.028"/></geometry>
      <material name="joint"/>
    </visual>
    <visual>
      <origin xyz="0.024 0 0.048" rpy="0 0 0"/>
      <geometry><box size="0.014 0.040 0.056"/></geometry>
      <material name="accent"/>
    </visual>
    <visual>
      <origin xyz="-0.024 0 0.048" rpy="0 0 0"/>
      <geometry><box size="0.014 0.040 0.056"/></geometry>
      <material name="accent"/>
    </visual>
    <collision>
      <origin xyz="0 0 0.014" rpy="0 0 0"/>
      <geometry><box size="0.070 0.050 0.028"/></geometry>
    </collision>
    <inertial>
      <origin xyz="0 0 0.030" rpy="0 0 0"/>
      <mass value="0.35"/>
      <inertia ixx="1.779e-04" ixy="0" ixz="0"
               iyy="2.479e-04" iyz="0" izz="2.158e-04"/>
    </inertial>
  </link>

  <joint name="gripper_joint" type="fixed">
    <parent link="tool0"/>
    <child link="gripper_link"/>
    <origin xyz="0 0 0" rpy="0 0 0"/>
  </joint>

"""


# ============ 差速底盘骨架 ============

CHASSIS_URDF = """<?xml version="1.0"?>
<robot name="{{pkg}}" xmlns:xacro="http://www.ros.org/wiki/xacro">

  <xacro:property name="base_length" value="{{base_length}}"/>
  <xacro:property name="base_width" value="{{base_width}}"/>
  <xacro:property name="base_height" value="{{base_height}}"/>
  <xacro:property name="base_mass" value="{{base_mass}}"/>
  <xacro:property name="wheel_radius" value="{{wheel_radius}}"/>
  <xacro:property name="wheel_width" value="{{wheel_width}}"/>
  <xacro:property name="wheel_separation" value="{{wheel_separation}}"/>
  <xacro:property name="wheel_mass" value="{{wheel_mass}}"/>
  <xacro:property name="wheel_offset_x" value="{{wheel_offset_x}}"/>
  <xacro:property name="caster_radius" value="{{caster_radius}}"/>
  <xacro:property name="caster_mass" value="0.05"/>
  <xacro:property name="lidar_height" value="{{lidar_height}}"/>
  <xacro:property name="lidar_x" value="{{lidar_x}}"/>
  <xacro:property name="lidar_mass" value="{{lidar_mass}}"/>

  <material name="chassis"><color rgba="0.16 0.42 0.68 1.0"/></material>
  <material name="plate"><color rgba="0.82 0.84 0.88 1.0"/></material>
  <material name="rubber"><color rgba="0.10 0.10 0.11 1.0"/></material>
  <material name="hub"><color rgba="0.92 0.62 0.18 1.0"/></material>
  <material name="sensor"><color rgba="0.22 0.24 0.28 1.0"/></material>
  <material name="spoke"><color rgba="0.96 0.96 0.98 1.0"/></material>
  <material name="joint"><color rgba="0.28 0.30 0.34 1.0"/></material>

  <link name="base_footprint"/>

  <!-- 底盘按「底板 + 立柱 + 顶板 + 配重」四件套搭，视觉上像真机而不是一个方块。
       惯量仍按外廓箱体算：这是静态包，位置控制不参与动力学求解。 -->
  <link name="base_link">
    <!-- 车体做成一个实体箱：原来是「底板 + 四根柱子 + 顶板」的夹空结构，
         视觉上到处是缝，看起来像零件没装好。实体化之后各模块直接贴合。 -->
    <visual>
      <origin xyz="0 0 0" rpy="0 0 0"/>
      <geometry><box size="${base_length} ${base_width} ${base_height}"/></geometry>
      <material name="chassis"/>
    </visual>
    <!-- 顶板：坐在车体顶面上 -->
    <visual>
      <origin xyz="0 0 ${base_height/2 + 0.006}" rpy="0 0 0"/>
      <geometry><box size="${base_length*0.94} ${base_width*0.94} 0.012"/></geometry>
      <material name="plate"/>
    </visual>
    <!-- 电池仓：贴在车体侧面，不再是悬空的盒子 -->
    <visual>
      <origin xyz="0 0 ${-base_height/2 + 0.014}" rpy="0 0 0"/>
      <geometry><box size="${base_length*0.72} ${base_width*0.86} 0.028"/></geometry>
      <material name="sensor"/>
    </visual>
    <!-- 雷达立柱：从顶板一路顶到雷达底座，中间不留缝 -->
    <visual>
      <origin xyz="${lidar_x} 0 ${base_height/2 + 0.012 + (lidar_height - base_height/2 - 0.012)/2}" rpy="0 0 0"/>
      <geometry><cylinder radius="0.016" length="${lidar_height - base_height/2 - 0.012}"/></geometry>
      <material name="plate"/>
    </visual>
{{axle_xml}}    <collision>
      <origin xyz="0 0 0"/>
      <geometry><box size="${base_length} ${base_width} ${base_height}"/></geometry>
    </collision>
    <inertial>
      <origin xyz="0 0 0" rpy="0 0 0"/>
      <mass value="${base_mass}"/>
      <inertia ixx="${base_mass*(base_width**2+base_height**2)/12}" ixy="0" ixz="0"
               iyy="${base_mass*(base_length**2+base_height**2)/12}" iyz="0"
               izz="${base_mass*(base_length**2+base_width**2)/12}"/>
    </inertial>
  </link>

  <joint name="base_joint" type="fixed">
    <parent link="base_footprint"/>
    <child link="base_link"/>
    <origin xyz="0 0 ${wheel_radius + base_height/2}" rpy="0 0 0"/>
  </joint>

{{wheels_xml}}

  <!-- 顶部雷达：支架 + 扫描头。没有装传感器插件，只占位，
       学生要出点云时把 gz-sim-sensors-system 挂到这个 link 上即可。 -->
  <link name="lidar_link">
    <visual>
      <origin xyz="0 0 0.015" rpy="0 0 0"/>
      <geometry><cylinder radius="0.020" length="0.030"/></geometry>
      <material name="plate"/>
    </visual>
    <visual>
      <origin xyz="0 0 0.050" rpy="0 0 0"/>
      <geometry><cylinder radius="0.035" length="0.040"/></geometry>
      <material name="sensor"/>
    </visual>
    <inertial>
      <origin xyz="0 0 0.04" rpy="0 0 0"/>
      <mass value="${lidar_mass}"/>
      <inertia ixx="${lidar_mass*(3*0.035**2+0.04**2)/12}" ixy="0" ixz="0"
               iyy="${lidar_mass*(3*0.035**2+0.04**2)/12}" iyz="0"
               izz="${lidar_mass*0.035**2/2}"/>
    </inertial>
  </link>

  <joint name="lidar_joint" type="fixed">
    <parent link="base_link"/>
    <child link="lidar_link"/>
    <origin xyz="${lidar_x} 0 ${lidar_height - base_height/2}" rpy="0 0 0"/>
  </joint>

{{plugin_xml}}
</robot>
"""


# ============ launch 文件 ============

DISPLAY_LAUNCH = '''import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import Command, LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue

PKG = "{{pkg}}"


def generate_launch_description():
    """只看模型：RViz + 关节滑块，不进 Gazebo。"""
    pkg_share = get_package_share_directory(PKG)
    xacro_file = os.path.join(pkg_share, "urdf", PKG + ".urdf.xacro")
    rviz_file = os.path.join(pkg_share, "config", "display.rviz")

    robot_description = ParameterValue(
        Command(["xacro ", xacro_file]), value_type=str)

    return LaunchDescription([
        DeclareLaunchArgument("gui", default_value="true",
                              description="是否启动关节滑块"),
        DeclareLaunchArgument("rviz", default_value="true"),
        Node(
            package="robot_state_publisher",
            executable="robot_state_publisher",
            parameters=[{"robot_description": robot_description}],
            output="screen",
        ),
        Node(
            package="joint_state_publisher_gui",
            executable="joint_state_publisher_gui",
            condition=IfCondition(LaunchConfiguration("gui")),
            output="screen",
        ),
        Node(
            package="rviz2",
            executable="rviz2",
            arguments=["-d", rviz_file],
            condition=IfCondition(LaunchConfiguration("rviz")),
            output="screen",
        ),
    ])
'''

ARM_SIM_LAUNCH = '''import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription, TimerAction
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import Command
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue

PKG = "{{pkg}}"
CONTROLLER = "{{controller_name}}"
GRIPPER_CONTROLLER = "{{gripper_controller}}"


def generate_launch_description():
    """进 Gazebo：模型 + 控制器自动激活，一条命令到位。"""
    pkg_share = get_package_share_directory(PKG)
    xacro_file = os.path.join(pkg_share, "urdf", PKG + ".urdf.xacro")
    yaml_file = os.path.join(pkg_share, "config", "controllers.yaml")
    world = os.path.join(pkg_share, "worlds", "empty.sdf")

    # controllers.yaml 的绝对路径显式传给 xacro，避免依赖 $(find) 的解析行为
    robot_description = ParameterValue(
        Command(["xacro ", xacro_file, " controllers_yaml:=", yaml_file]),
        value_type=str)

    gz_sim = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(
            get_package_share_directory("ros_gz_sim"), "launch", "gz_sim.launch.py")),
        launch_arguments={"gz_args": "-r -v3 " + world}.items())

    bridge = Node(
        package="ros_gz_bridge",
        executable="parameter_bridge",
        arguments=["/clock@rosgraph_msgs/msg/Clock]gz.msgs.Clock"],
        output="screen",
    )

    rsp = Node(
        package="robot_state_publisher",
        executable="robot_state_publisher",
        parameters=[{"robot_description": robot_description, "use_sim_time": True}],
        output="screen",
    )

    spawn = Node(
        package="ros_gz_sim",
        executable="create",
        arguments=["-topic", "robot_description", "-name", PKG, "-z", "0.0"],
        output="screen",
    )

    # 顺序很重要：先广播器，再控制器；早期版本同时起会抢 controller_manager
    load_jsb = Node(
        package="controller_manager",
        executable="spawner",
        arguments=["joint_state_broadcaster",
                   "--controller-manager", "/controller_manager"],
        output="screen",
    )
    load_ctrl = Node(
        package="controller_manager",
        executable="spawner",
        arguments=[CONTROLLER, "--controller-manager", "/controller_manager"],
        output="screen",
    )
    # 夹爪单独一个控制器：夹爪和臂的关节指令维度不同，混在一起发很难用
    load_gripper = Node(
        package="controller_manager",
        executable="spawner",
        arguments=[GRIPPER_CONTROLLER, "--controller-manager", "/controller_manager"],
        output="screen",
    )

    return LaunchDescription([
        gz_sim,
        bridge,
        rsp,
        spawn,
        TimerAction(period=6.0, actions=[
            load_jsb,
            TimerAction(period=3.0, actions=[load_ctrl, load_gripper]),
        ]),
    ])
'''

DIFF_SIM_LAUNCH = '''import os

from ament_index_python.packages import get_package_share_directory
from launch import LaunchDescription
from launch.actions import IncludeLaunchDescription
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import Command
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue

PKG = "{{pkg}}"


def generate_launch_description():
    """进 Gazebo：差速插件直接吃 /cmd_vel，不需要 ros2_control。"""
    pkg_share = get_package_share_directory(PKG)
    xacro_file = os.path.join(pkg_share, "urdf", PKG + ".urdf.xacro")
    world = os.path.join(pkg_share, "worlds", "empty.sdf")

    robot_description = ParameterValue(
        Command(["xacro ", xacro_file]), value_type=str)

    gz_sim = IncludeLaunchDescription(
        PythonLaunchDescriptionSource(os.path.join(
            get_package_share_directory("ros_gz_sim"), "launch", "gz_sim.launch.py")),
        launch_arguments={"gz_args": "-r -v3 " + world}.items())

    # [ 表示 ROS->GZ，] 表示 GZ->ROS。方向写反不会报错，只是永远没数据。
    bridge = Node(
        package="ros_gz_bridge",
        executable="parameter_bridge",
        arguments=[
            "/clock@rosgraph_msgs/msg/Clock]gz.msgs.Clock",
            "/cmd_vel@geometry_msgs/msg/Twist[gz.msgs.Twist",
            "/odom@nav_msgs/msg/Odometry]gz.msgs.Odometry",
            "/tf@tf2_msgs/msg/TFMessage]gz.msgs.Pose_V",
            "/joint_states@sensor_msgs/msg/JointState]gz.msgs.Model",
        ],
        output="screen",
    )

    rsp = Node(
        package="robot_state_publisher",
        executable="robot_state_publisher",
        parameters=[{"robot_description": robot_description, "use_sim_time": True}],
        output="screen",
    )

    spawn = Node(
        package="ros_gz_sim",
        executable="create",
        arguments=["-topic", "robot_description", "-name", PKG, "-z", "0.02"],
        output="screen",
    )

    return LaunchDescription([gz_sim, bridge, rsp, spawn])
'''

README = """# {{pkg}}

{{desc}}

由 ROS 2 学习助手根据参数表生成：机型 `{{archetype}}`，骨架来自
ros2_control_demos/example_9（机械臂）与 andino_gz / linorobot2（差速底盘）。

## 编译

```bash
cd ~/ros2_ws
colcon build --symlink-install --packages-select {{pkg}}
source install/setup.bash
```

## 先自检

```bash
python3 install/{{pkg}}/share/{{pkg}}/scripts/selfcheck.py
```

## 看模型（不进 Gazebo）

```bash
ros2 launch {{pkg}} display.launch.py
```

## 进 Gazebo

```bash
ros2 launch {{pkg}} sim.launch.py
```

{{control_block}}

## 文件说明

| 文件 | 作用 |
|---|---|
| `urdf/{{pkg}}.urdf.xacro` | 连杆与关节，尺寸都是 xacro 变量 |
| {{urdf_extra_row}}
| `config/display.rviz` | RViz 布局 |
| `launch/display.launch.py` | 只看模型 |
| `launch/sim.launch.py` | 进 Gazebo |
| `scripts/selfcheck.py` | 本地自检 |

## 改尺寸

打开 `urdf/{{pkg}}.urdf.xacro`，改顶部 `xacro:property` 的值即可，
惯量是按几何公式用这些变量算出来的，会跟着变——不要手动改 `<inertia>` 里的数字。

## 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| 模型在 Gazebo 里掉下去 / 抖 | 初始高度不对 | `sim.launch.py` 里 `-z` 调大 0.01 |
| 车轮转但车不动 | 摩擦太小 | 调大 `<mu1>/<mu2>` |
| {{controller_faq_from}} | 控制器没激活 | 看终端里 spawner 是否报错，核对关节名三处一致 |
| 编译报找不到 xacro | 少依赖 | `sudo apt install ros-jazzy-xacro` |
"""

CONTROL_ARM = """## 控制机械臂

控制器 `{{controller_name}}`（forward_command_controller，位置接口）。
发一条 Float64MultiArray 就能到位，顺序对应关节：

```bash
ros2 topic pub --once /{{controller_name}}/commands \\
  std_msgs/msg/Float64MultiArray "{data: [{{demo_values}}]}"
```

查看当前状态：

```bash
ros2 control list_controllers          # 两个都应是 active
ros2 topic echo /joint_states --once
```
"""

CONTROL_DIFF = """## 控制小车

差速插件直接订阅 `/cmd_vel`，用键盘遥控即可：

```bash
ros2 run teleop_twist_keyboard teleop_twist_keyboard
```

或者单发一条：

```bash
ros2 topic pub --rate 10 /cmd_vel geometry_msgs/msg/Twist \\
  "{linear: {x: 0.2}, angular: {z: 0.0}}"
```

里程计在 `/odom`，可以这样确认车真的在动：

```bash
ros2 topic echo /odom --field pose.pose.position
```
"""


# 兼容旧引用
DIFF_URDF = CHASSIS_URDF


CONTROL_ACK = """## 控制小车（阿克曼转向）

四轮汽车式：后轮驱动、前轮打方向，`/cmd_vel` 的角速度会被换算成前轮转角，
和真车打方向盘的手感一致。**不会原地自转**——这是阿克曼的特点，不是 bug。

车底是「转向机 + 齿条 + 左右拉杆 + 转向节臂」的梯形连杆造型。注意：
URDF 是树结构，**闭环连杆无法真正闭合**，所以拉杆挂在转向节上随关节摆动，
左右转角由阿克曼插件按几何算出（效果等同于连杆机构），齿条本身不横移。

```bash
ros2 run teleop_twist_keyboard teleop_twist_keyboard
```

或者发一条定圆指令（线速度 0.3 m/s + 角速度 0.5 rad/s）：

```bash
ros2 topic pub --rate 10 /cmd_vel geometry_msgs/msg/Twist \
  "{linear: {x: 0.3}, angular: {z: 0.5}}"
```

看前轮是否真的在打方向：

```bash
ros2 topic echo /joint_states --once
```

里程计在 `/odom`：

```bash
ros2 topic echo /odom --field pose.pose.position
```
"""
