# -*- coding: utf-8 -*-
"""回归：缺依赖的提示必须分清「镜像本该有」和「得找管理员装」。

    python -X utf8 tests/base_pkgs_test.py

背景：用户导入 navigation2，结论条列了 35 个缺包，最后一句是「到有网机器上跑
fetch_pkgs」。可**用户手上根本没有有网的 ROS 机器**——他只会照着这句话原地卡住。
真正该说的是：这些包不在 ROS 基础镜像里、你自己装不了，**请联系管理员**。

判据来自 app/base_pkgs.py（ros_base 变体递归展开，201 个包）。这里钉住三件事：
1. 清单本身没算错：tf2_ros / rclcpp 在，backward_ros / rviz2 / xacro 不在；
2. 提示文案按两类分岔，混为一谈就红；
3. 基础包缺失时的措辞是「包名写错」，不能也是「找管理员」——那是两个方向。
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import base_pkgs, chain_diag, chain_flow       # noqa: E402

_OK = [0]
_BAD = [0]


def check(name, cond, detail=""):
    if cond:
        _OK[0] += 1
        print("  ✓ %s" % name)
    else:
        _BAD[0] += 1
        print("  ✗ %s  %s" % (name, detail))


def test_manifest():
    print("\n[1] 基础包清单本身")
    check("清单有内容（>100 个包）", base_pkgs.count("jazzy") > 100,
          base_pkgs.count("jazzy"))
    for name in ("tf2_ros", "rclcpp", "rclpy", "nav_msgs", "geometry_msgs",
                 "std_msgs", "launch_ros", "rclcpp_lifecycle"):
        check("%s 是基础包" % name, base_pkgs.is_base(name, "jazzy") is True)
    for name in ("backward_ros", "rviz2", "nav2_msgs", "slam_toolbox",
                 "xacro", "controller_manager", "ompl"):
        check("%s 不是基础包" % name, base_pkgs.is_base(name, "jazzy") is False)
    check("apt 名也认（ros-jazzy-tf2-ros）",
          base_pkgs.is_base("ros-jazzy-tf2-ros", "jazzy") is True)
    check("apt 名也认（ros-jazzy-backward-ros）",
          base_pkgs.is_base("ros-jazzy-backward-ros", "jazzy") is False)
    check("没有清单的发行版返回 None（不瞎猜）",
          base_pkgs.is_base("tf2_ros", "kilted") is None)


def test_classify():
    print("\n[2] 分批：基础包 / 额外装 / 不确定")
    base, extra, unknown = base_pkgs.classify(
        ["ros-jazzy-tf2-ros", "ros-jazzy-backward-ros", "libqt6-core",
         "python3-matplotlib"], "jazzy")
    check("tf2-ros 归基础包", base == ["ros-jazzy-tf2-ros"], base)
    check("backward-ros 归额外装", "ros-jazzy-backward-ros" in extra, extra)
    check("系统包 libqt6-core 归额外装", "libqt6-core" in extra, extra)
    check("python3-matplotlib 归额外装", "python3-matplotlib" in extra, extra)
    check("没有不确定项", not unknown, unknown)
    _b, _e, unk = base_pkgs.classify(["tf2_ros"], "kilted")
    check("未知发行版全进 uncertain", unk == ["tf2_ros"], unk)


def test_dep_note():
    print("\n[3] 结论条那句话")
    note = chain_flow.dep_note({"distro": "jazzy", "pkgs_missing": [
        "ros-jazzy-backward-ros", "ros-jazzy-rviz-common", "ros-jazzy-xacro",
        "libqt6-core"]})
    check("说清「需要额外安装」", "需要额外安装" in note, note)
    check("点名找管理员", "联系管理员" in note, note)
    check("不再让用户自己上 apt/fetch（只留管理员那一句）",
          "自己装不了" in note, note)
    check("数量对得上（4 个）", "缺 4 个" in note, note)
    check("包名还原成下划线（backward_ros）", "backward_ros" in note, note)
    check("系统包不改写（libqt6-core）", "libqt6-core" in note, note)
    check("没有多余的顿号切碎（不该出现 'b、a、c'）",
          "b、a、c" not in note, note)

    note2 = chain_flow.dep_note({"distro": "jazzy",
                                 "pkgs_missing": ["ros-jazzy-tf2-ros"]})
    check("基础包缺失 → 提示包名写错", "包名写错" in note2, note2)
    check("基础包缺失 → 不喊管理员", "联系管理员" not in note2, note2)

    note3 = chain_flow.dep_note({"distro": "jazzy", "pkgs_missing": [
        "ros-jazzy-tf2-ros", "ros-jazzy-backward-ros"]})
    check("混合场景两句都在",
          "联系管理员" in note3 and "包名写错" in note3, note3)

    check("没有缺包就不说话", chain_flow.dep_note({"distro": "jazzy"}) == "")


def test_hint():
    print("\n[4] 红字诊断")
    h = chain_diag.hint('Could not find a package configuration file provided '
                        'by "backward_ros" with any of the following names:')
    check("backward_ros → 点出不在基础镜像", "不在 ROS 基础镜像里" in h, h)
    check("backward_ros → 给出 apt 名", "ros-jazzy-backward-ros" in h, h)
    check("backward_ros → 找管理员", "联系管理员" in h, h)

    h2 = chain_diag.hint('Could not find a package configuration file provided '
                         'by "tf2_ros" with any of the following names:')
    check("tf2_ros → 基础包、指向拼写", "基础包" in h2 and "拼错" in h2, h2)
    check("tf2_ros → 不喊管理员", "联系管理员" not in h2, h2)

    h3 = chain_diag.hint("ModuleNotFoundError: No module named 'nav2_simple_commander'")
    check("import 缺失也走同一套判定", "联系管理员" in h3, h3)

    h4 = chain_diag.hint("ModuleNotFoundError: No module named 'rclpy'")
    check("import 基础包缺失 → 不走管理员", "联系管理员" not in h4, h4)


def main():
    test_manifest()
    test_classify()
    test_dep_note()
    test_hint()
    print("\n通过 %d 项，失败 %d 项" % (_OK[0], _BAD[0]))
    return 1 if _BAD[0] else 0


if __name__ == "__main__":
    sys.exit(main())
