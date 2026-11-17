# -*- coding: utf-8 -*-
"""生成「多链路 + 下位机双向回传」示例工作空间（5 节点 / 2 包 / 6 条链路）。

和 demo_ros2_ws（单链 camera→detector→planner→motor）不同，这个样例刻意做成网状：
  · 感知链   camera →(image_raw) detector
  · 分支     detector →(detections) planner 与 display 两个订阅者
  · 双向     detector ↔ planner：/detections、/detector_status 上行，
             /detector_enable 反向下行（一对节点之间三条线）
  · 反馈链   battery_monitor →(speed_limit) planner，battery_monitor 又吃 /battery_state
  · 执行链   planner →(motor_cmd) 下位机，下位机 →(motor_state) planner

/robot/motor_cmd 没有订阅者 → 验证时配一台下位机接住；/motor_state、/battery_state
没有发布者 → 同一台下位机（或电池板）回传灌进去，planner / battery_monitor 真的会
收到并改变决策。图上就是「一条线下去、一条线上来」。

每个节点都有日志：启动一条 + 关键动作时一条（都做了节流，不刷屏）。

用法：
    python samples/_make_multi_demo.py                  # 输出到桌面（目录 + zip）
    python samples/_make_multi_demo.py -o D:/tmp/ws     # 指定输出目录
"""
import argparse
import os
import shutil
import zipfile

DEFAULT_OUT = os.path.join(os.path.expanduser("~"), "Desktop", "demo_multi_chain_ws")

README = """# demo_multi_chain_ws

多链路示例：2 个包 / 5 个节点 / 6 条节点间链路 + 2 台下位机（下行 + 回传）。

## 链路

```
                    ┌──/detections─────┐
camera ─/image_raw─> detector ──────────┼──> planner ─/motor_cmd──> 下位机(motor)
                       ↑  │            └──> display        ↑              │
        /detector_enable  │                                 │              │
                       └──┼──/detector_status──────────────┘              │
                          │                            /motor_state────────┘
                          │                                 ↑
battery_monitor ─/speed_limit──────────────────────────────┘
       ↑
  /battery_state  ← 下位机(battery)
```

- **detector ↔ planner 之间有三条线**：检测结果 `/detections` 和状态
  `/detector_status` 往上走，`/detector_enable` 反向走 —— 一个节点既能发也能收。
- **下位机是双向的**：planner 把 `/motor_cmd` 下发下去，下位机把实测速度
  `/motor_state` 回传给 planner，回传真的参与决策（跟不上就降速）。
  电池板同理：`/battery_state` 回传给 battery_monitor，换算成 `/speed_limit`。
- `/motor_cmd`、`/motor_state`、`/battery_state` 在示例里都没有对端 ——
  验证时由「下位机模拟」补上：指令被接住，回传被灌进去。

## 跑起来

```bash
colcon build --symlink-install && source install/setup.bash
ros2 run robot_sense camera_node
ros2 run robot_sense detector_node
ros2 run robot_sense display_node
ros2 run robot_control battery_monitor
ros2 run robot_control planner_node
```
"""

PKG_XML = """<?xml version="1.0"?>
<package format="3">
  <name>{name}</name>
  <version>0.1.0</version>
  <description>{desc}</description>
  <maintainer email="demo@example.com">demo</maintainer>
  <license>Apache-2.0</license>
  <depend>rclpy</depend>
{deps}
  <export>
    <build_type>ament_python</build_type>
  </export>
</package>
"""

SETUP_PY = """from setuptools import setup

package_name = '{name}'

setup(
    name=package_name,
    version='0.1.0',
    packages=[package_name],
    data_files=[
        ('share/ament_index/resource_index/packages', ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='demo',
    maintainer_email='demo@example.com',
    description='{desc}',
    license='Apache-2.0',
    entry_points={{
        'console_scripts': [
{entries}
        ],
    }},
)
"""

CAMERA = '''import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image


class CameraNode(Node):
    """模拟相机：固定帧率往 /image_raw 推图。"""

    def __init__(self):
        super().__init__('camera_node')
        self.declare_parameter('fps', 30)
        self.pub = self.create_publisher(Image, '/image_raw', 10)
        self.timer = self.create_timer(1.0 / 30.0, self.tick)
        self.frames = 0
        self.get_logger().info('相机启动：640x480 rgb8 @30Hz → /image_raw')

    def tick(self):
        msg = Image()
        msg.height = 480
        msg.width = 640
        msg.encoding = 'rgb8'
        msg.header.frame_id = 'camera_link'
        self.pub.publish(msg)
        self.frames += 1
        # 每 150 帧（约 5 秒）报一次，日志里能看出它在持续采
        if self.frames % 150 == 0:
            self.get_logger().info('已采集 %d 帧' % self.frames)


def main():
    rclpy.init()
    node = CameraNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
'''

DETECTOR = '''import rclpy
from rclpy.node import Node
from sensor_msgs.msg import Image, RegionOfInterest
from std_msgs.msg import Bool, Int32


class DetectorNode(Node):
    """订阅原图输出目标框，同时接受规划器的「降频」指令。

    和 planner 之间有三条线：
      /detections       → planner   检测结果（30Hz）
      /detector_status  → planner   运行状态：已处理帧数 / 是否降频（2Hz）
      /detector_enable  ← planner   要不要降频（目标贴近、要停车时省算力）
    """

    def __init__(self):
        super().__init__('detector_node')
        self.sub = self.create_subscription(Image, '/image_raw', self.on_image, 10)
        self.sub_en = self.create_subscription(Bool, '/detector_enable', self.on_enable, 10)
        self.pub = self.create_publisher(RegionOfInterest, '/detections', 10)
        self.pub_st = self.create_publisher(Int32, '/detector_status', 10)
        self.frames = 0
        self.sent = 0
        self.throttle = False
        self.get_logger().info(
            '检测器启动：/image_raw → /detections（扇出给 planner 和 display），'
            '状态回报 /detector_status，接受 /detector_enable 降频')

    def on_enable(self, msg):
        if bool(msg.data) == self.throttle:
            return
        self.throttle = bool(msg.data)
        self.get_logger().info('收到降频指令：%s'
                               % ('每 3 帧出一帧' if self.throttle else '恢复全速'))

    def on_image(self, msg):
        self.frames += 1
        # 降频时每 3 帧出一帧，但话题不能断流 —— 下游和验证都在等这条线
        if self.throttle and self.frames % 3:
            return
        box = RegionOfInterest()
        box.x_offset = 120
        box.y_offset = 80
        box.width = 200
        box.height = 160
        box.do_rectify = True
        self.pub.publish(box)
        self.sent += 1
        if self.sent % 15 == 0:
            self.pub_st.publish(Int32(data=self.sent))
            self.get_logger().info('已输出 %d 个目标框（收到 %d 帧）'
                                   % (self.sent, self.frames))


def main():
    rclpy.init()
    node = DetectorNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
'''

DISPLAY = '''import rclpy
from rclpy.node import Node
from sensor_msgs.msg import RegionOfInterest


class DisplayNode(Node):
    """可视化节点：只订阅检测框（示例里统计一下，真实项目里画到界面）。"""

    def __init__(self):
        super().__init__('display_node')
        self.sub = self.create_subscription(RegionOfInterest, '/detections', self.on_box, 10)
        self.n = 0
        self.get_logger().info('显示器启动：订阅 /detections，准备画框')

    def on_box(self, msg):
        self.n += 1
        if self.n % 150 == 0:
            self.get_logger().info('已绘制 %d 个目标框（当前 %dx%d）'
                                   % (self.n, msg.width, msg.height))


def main():
    rclpy.init()
    node = DisplayNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
'''

BATTERY = '''import rclpy
from rclpy.node import Node
from sensor_msgs.msg import BatteryState
from std_msgs.msg import Float32


class BatteryMonitor(Node):
    """吃下位机回传的电池状态，算出速度上限发给规划器。"""

    def __init__(self):
        super().__init__('battery_monitor')
        self.sub = self.create_subscription(BatteryState, '/battery_state', self.on_batt, 10)
        self.pub = self.create_publisher(Float32, '/speed_limit', 10)
        self.limit = None
        self.get_logger().info('电量监控启动：/battery_state → /speed_limit')

    def on_batt(self, msg):
        pct = float(msg.percentage)
        if pct > 0.5:
            limit = 0.4
        elif pct > 0.2:
            limit = 0.25
        else:
            limit = 0.15
        self.pub.publish(Float32(data=limit))
        # 只在档位变化时打，5Hz 的电池消息不能每次都刷
        if limit != self.limit:
            self.limit = limit
            self.get_logger().info('电量 %.0f%%（%.1fV）→ 速度上限 %.2f m/s'
                                   % (pct * 100, msg.voltage, limit))


def main():
    rclpy.init()
    node = BatteryMonitor()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
'''

PLANNER = '''import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry
from sensor_msgs.msg import RegionOfInterest
from std_msgs.msg import Bool, Float32, Int32


class PlannerNode(Node):
    """汇合四路输入下发速度，同时反向控制检测器要不要降频。

    /detections       目标框（越大越近）
    /detector_status  检测器回报的已输出帧数
    /speed_limit      电量给的限速
    /motor_state      下位机回传的实测速度 —— 指令和实测差太多说明打滑/堵转
    → /motor_cmd      速度指令（下发给下位机）
    → /detector_enable 反向通道：停车时让检测器降频省算力
    """

    def __init__(self):
        super().__init__('planner_node')
        self.sub_box = self.create_subscription(
            RegionOfInterest, '/detections', self.on_box, 10)
        self.sub_fb = self.create_subscription(
            Odometry, '/motor_state', self.on_feedback, 10)
        self.sub_lim = self.create_subscription(
            Float32, '/speed_limit', self.on_limit, 10)
        self.sub_st = self.create_subscription(
            Int32, '/detector_status', self.on_status, 10)
        self.pub = self.create_publisher(Twist, '/motor_cmd', 10)
        self.pub_en = self.create_publisher(Bool, '/detector_enable', 10)
        self.near = None
        self.limit = 0.4
        self.fb = None
        self.state = None
        self.det_frames = 0
        self.get_logger().info(
            '规划器启动：/detections + /speed_limit + /motor_state → /motor_cmd，'
            '反向控制 /detector_enable')

    def on_limit(self, msg):
        self.limit = float(msg.data)

    def on_status(self, msg):
        self.det_frames = int(msg.data)

    def on_feedback(self, msg):
        # 下位机回传的实测线速度
        self.fb = float(msg.twist.twist.linear.x)

    def on_box(self, msg):
        near = (msg.width * msg.height) > (200 * 160)
        v = 0.0 if near else 0.4
        v = min(v, self.limit)
        # 反馈闭环：要它走却几乎不动，先按实测的三倍给，别硬顶
        slow = self.fb is not None and v > 0.1 and self.fb < v * 0.3
        if slow:
            v = max(0.1, round(self.fb * 3.0, 2))
        cmd = Twist()
        cmd.linear.x = v
        self.pub.publish(cmd)
        # 反向通道：要停车了就让检测器降频，省算力；恢复前进再放开
        self.pub_en.publish(Bool(data=not near))
        # 只在决策/限速/打滑状态变化时打，30Hz 不能刷屏
        state = (near, round(self.limit, 2), slow)
        if state != self.state:
            self.state = state
            why = '目标%s' % ('贴近，停车' if near else '较远，前进')
            if self.limit < 0.4:
                why += ' · 电量限速 %.2f' % self.limit
            if slow:
                why += ' · 实测 %.2f 跟不上，降速' % self.fb
            if self.det_frames:
                why += ' · 检测器已出 %d 帧' % self.det_frames
            why += ' · 检测器%s' % ('降频' if near else '全速')
            self.get_logger().info('%s → 下发 %.2f m/s' % (why, v))


def main():
    rclpy.init()
    node = PlannerNode()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == '__main__':
    main()
'''

PACKAGES = [
    {
        "name": "robot_sense",
        "desc": "感知：相机采集、目标检测、可视化",
        "deps": "  <depend>sensor_msgs</depend>\n  <depend>std_msgs</depend>",
        "files": [("camera_node.py", CAMERA),
                  ("detector_node.py", DETECTOR),
                  ("display_node.py", DISPLAY)],
        "entries": [("camera_node", "camera_node:main"),
                    ("detector_node", "detector_node:main"),
                    ("display_node", "display_node:main")],
    },
    {
        "name": "robot_control",
        "desc": "控制：电量限速与速度规划",
        "deps": ("  <depend>sensor_msgs</depend>\n  <depend>geometry_msgs</depend>\n"
                 "  <depend>nav_msgs</depend>\n  <depend>std_msgs</depend>"),
        "files": [("battery_monitor.py", BATTERY),
                  ("planner_node.py", PLANNER)],
        "entries": [("battery_monitor", "battery_monitor:main"),
                    ("planner_node", "planner_node:main")],
    },
]


def write_tree(root):
    if os.path.isdir(root):
        shutil.rmtree(root)
    for pkg in PACKAGES:
        n = pkg["name"]
        base = os.path.join(root, "src", n)
        os.makedirs(os.path.join(base, n), exist_ok=True)
        os.makedirs(os.path.join(base, "resource"), exist_ok=True)
        files = {
            "package.xml": PKG_XML.format(name=n, desc=pkg["desc"], deps=pkg["deps"]),
            "setup.cfg": "[develop]\nscript_dir=$base/lib/%s\n\n[install]\n"
                         "install_scripts=$base/lib/%s\n" % (n, n),
            "setup.py": SETUP_PY.format(
                name=n, desc=pkg["desc"],
                entries=",\n".join("            '%s = %s.%s'" % (e, n, t)
                                   for e, t in pkg["entries"])),
            "resource/" + n: "",
            n + "/__init__.py": "",
        }
        for fn, src in pkg["files"]:
            files[n + "/" + fn] = src
        for rel, src in files.items():
            p = os.path.join(base, rel)
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(src)
    with open(os.path.join(root, "README.md"), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(README)


def make_zip(root):
    out = root.rstrip("/\\") + ".zip"
    if os.path.exists(out):
        os.remove(out)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for dp, _, fns in os.walk(root):
            for fn in fns:
                p = os.path.join(dp, fn)
                z.write(p, os.path.relpath(p, root).replace("\\", "/"))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default=DEFAULT_OUT)
    a = ap.parse_args()
    write_tree(a.out)
    zp = make_zip(a.out)
    print("目录:", a.out)
    print("压缩包:", zp)
    for dp, _, fns in os.walk(a.out):
        for fn in sorted(fns):
            print("  ", os.path.relpath(os.path.join(dp, fn), a.out).replace("\\", "/"))


if __name__ == "__main__":
    main()
