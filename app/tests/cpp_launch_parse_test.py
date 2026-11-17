# -*- coding: utf-8 -*-
"""C++（rclcpp）与 launch 解析的纯单元测试（不依赖服务/CDP）。

覆盖：
1. C++ 节点：类内构造 + 类外构造两种写法，pub/sub/srv/action/参数/timer。
2. .launch.py：Node / ComposableNode / ExecuteProcess(-m 模块)。
3. .launch XML：<node pkg type name>。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.graph import parse_cpp, parse_launch_file  # noqa: E402

CPP_INLINE = r"""
#include <rclcpp/rclcpp.hpp>
#include <sensor_msgs/msg/laser_scan.hpp>
#include <geometry_msgs/msg/twist.hpp>
#include <std_srvs/srv/set_bool.hpp>

namespace demo {
class ScanEcho : public rclcpp::Node {
public:
  explicit ScanEcho() : Node("scan_echo") {
    scan_sub_ = this->create_subscription<sensor_msgs::msg::LaserScan>(
        "/scan", 10, std::bind(&ScanEcho::cb, this, std::placeholders::_1));
    vel_pub_ = this->create_publisher<geometry_msgs::msg::Twist>("/cmd_vel", 10);
    svc_ = this->create_service<std_srvs::srv::SetBool>("toggle", &ScanEcho::on_toggle);
    this->declare_parameter<double>("gain", 1.0);
    timer_ = this->create_wall_timer(std::chrono::milliseconds(100), [this]() {});
  }
  rclcpp::Subscription<sensor_msgs::msg::LaserScan>::SharedPtr scan_sub_;
  rclcpp::Publisher<geometry_msgs::msg::Twist>::SharedPtr vel_pub_;
  rclcpp::TimerBase::SharedPtr timer_;
  void cb(const sensor_msgs::msg::LaserScan::SharedPtr) {}
};
}  // namespace demo
"""

CPP_OUTLINE = r"""
#include <rclcpp/rclcpp.hpp>
#include <std_msgs/msg/string.hpp>
class Talker : public rclcpp::Node {
 public:
  Talker();
};
Talker::Talker() : rclcpp::Node("talker") {
  pub_ = this->create_publisher<std_msgs::msg::String>("/chatter", 10);
}
"""

LAUNCH_PY = r"""
from launch import LaunchDescription
from launch_ros.actions import Node, ComposableNode
from launch.actions import ExecuteProcess

def generate_launch_description():
    return LaunchDescription([
        Node(package="robot_state_publisher", executable="robot_state_publisher",
             name="rsp"),
        ComposableNode(package="my_comp", plugin="my_comp::SensorDriver", name="driver"),
        ExecuteProcess(cmd=["python3", "-m", "my_pkg.bridge_node"], name="bridge"),
    ])
"""

LAUNCH_XML = r"""
<launch>
  <arg name="gui" default="true"/>
  <node name="robot_state_publisher" pkg="robot_state_publisher" exec="robot_state_publisher"/>
  <node name="rviz" pkg="rviz2" exec="rviz2"/>
  <node name="fake" pkg="rostopic" type="rostopic" args="pub /x std_msgs/msg/Bool"/>
</launch>
"""


def _topics(nodes):
    out = []
    for n in nodes:
        out.append((n["name"], n.get("cls", ""), n.get("external", False),
                    [(i["topic"], i["type"], i["resolved"])
                     for i in n["publishes"]],
                    [(i["topic"], i["type"], i["resolved"])
                     for i in n["subscribes"]]))
    return out


def main():
    fails = []

    def ok(cond, msg):
        print(("  PASS  " if cond else "  FAIL  ") + msg)
        if not cond:
            fails.append(msg)

    # 1. C++ 类内构造
    r = parse_cpp("demo/scan_echo.cpp", CPP_INLINE)
    n = r["nodes"][0]
    ok(n["name"] == "scan_echo" and n["cls"] == "ScanEcho", "C++ 类内构造：节点名/类名")
    ok(n["has_timer"], "C++ create_wall_timer → has_timer")
    ok(any(t["topic"] == "/scan" and t["type"] == "sensor_msgs/msg/LaserScan"
           for t in n["subscribes"]), "C++ create_subscription 类型归一化")
    ok(any(t["topic"] == "/cmd_vel" and t["type"] == "geometry_msgs/msg/Twist"
           for t in n["publishes"]), "C++ create_publisher")
    ok(any(s["topic"] == "toggle" for s in n["service_servers"]), "C++ create_service")
    ok(any(p["name"] == "gain" for p in n["params"]), "C++ declare_parameter")

    # 2. C++ 类外构造
    r2 = parse_cpp("demo/talker.cpp", CPP_OUTLINE)
    ok(len(r2["nodes"]) == 1 and r2["nodes"][0]["name"] == "talker"
       and r2["nodes"][0]["cls"] == "Talker", "C++ 类外构造：Node(\"talker\")")
    ok(any(t["topic"] == "/chatter" for t in r2["nodes"][0]["publishes"]),
       "C++ 类外构造：create_publisher")

    # 3. .launch.py
    r3 = parse_launch_file("launch/x.launch.py", LAUNCH_PY)
    names = {n["name"]: n for n in r3["nodes"]}
    ok("rsp" in names and names["rsp"]["package"] == "robot_state_publisher",
       "launch.py Node(package, executable)")
    ok("driver" in names and names["driver"].get("composable"),
       "launch.py ComposableNode(plugin)")
    ok("bridge" in names and names["bridge"]["cls"] == "my_pkg.bridge_node",
       "launch.py ExecuteProcess -m 模块")

    # 4. .launch XML
    r4 = parse_launch_file("launch/x.launch", LAUNCH_XML)
    names4 = {n["name"]: n for n in r4["nodes"]}
    ok("robot_state_publisher" in names4 and "rviz" in names4
       and "fake" in names4, "launch XML <node> 提取（exec 与 type 都认）")
    ok(all(n["external"] for n in r4["nodes"]), "launch 节点都标 external")

    print("\n结果:", "PASS" if not fails else "FAIL（%d 项）" % len(fails))
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
