# -*- coding: utf-8 -*-
"""生成一个用于验收的 ROS2 示例工作空间 zip（4 节点 / 5 条接口 / 1 个服务）。"""
import os
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "demo_ros2_ws.zip")

PKG_XML = """<?xml version="1.0"?>
<?xml-model href="http://download.ros.org/schema/package_format3.xsd" schematypens="http://www.w3.org/2001/XMLSchema"?>
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
    """模拟相机：按固定帧率往外发图像。"""

    def __init__(self):
        super().__init__('camera_node')
        self.declare_parameter('fps', 30)
        self.declare_parameter('frame_id', 'camera_link')
        self.pub = self.create_publisher(Image, '/image_raw', 10)
        self.timer = self.create_timer(1.0 / 30.0, self.tick)

    def tick(self):
        msg = Image()
        msg.height = 480
        msg.width = 640
        msg.encoding = 'rgb8'
        self.pub.publish(msg)


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
from sensor_msgs.msg import Image
from vision_msgs.msg import Detection2DArray
from std_srvs.srv import Trigger


class DetectorNode(Node):
    """订阅原图，跑一遍检测，把框发出去；提供重置计数服务。"""

    def __init__(self):
        super().__init__('detector_node')
        self.declare_parameter('confidence', 0.5)
        self.sub = self.create_subscription(Image, '/image_raw', self.on_image, 10)
        self.pub = self.create_publisher(Detection2DArray, '/detections', 10)
        self.srv = self.create_service(Trigger, '/reset_detector', self.on_reset)
        self.frames = 0

    def on_image(self, msg):
        self.frames += 1
        out = Detection2DArray()
        self.pub.publish(out)

    def on_reset(self, request, response):
        self.frames = 0
        response.success = True
        response.message = 'counter reset'
        return response


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

PLANNER = '''import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from vision_msgs.msg import Detection2DArray


class PlannerNode(Node):
    """按检测结果决定往哪走，输出速度指令。"""

    def __init__(self):
        super().__init__('planner_node')
        self.declare_parameter('max_speed', 0.4)
        self.declare_parameter('safe_distance', 0.8)
        self.sub = self.create_subscription(
            Detection2DArray, '/detections', self.on_detections, 10)
        self.pub = self.create_publisher(Twist, '/cmd_vel', 10)

    def on_detections(self, msg):
        cmd = Twist()
        cmd.linear.x = 0.0
        if len(msg.detections) == 0:
            cmd.linear.x = 0.4
        self.pub.publish(cmd)


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

MOTOR = '''import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from nav_msgs.msg import Odometry


class MotorNode(Node):
    """把速度指令打成轮速，同时回推里程计。"""

    def __init__(self):
        super().__init__('motor_node')
        self.declare_parameter('wheel_base', 0.35)
        self.declare_parameter('wheel_radius', 0.05)
        self.sub = self.create_subscription(Twist, '/cmd_vel', self.on_cmd, 10)
        self.pub = self.create_publisher(Odometry, '/odom', 10)
        self.x = 0.0

    def on_cmd(self, msg):
        self.x += msg.linear.x * 0.05
        odom = Odometry()
        odom.pose.pose.position.x = self.x
        self.pub.publish(odom)


def main():
    rclpy.init()
    node = MotorNode()
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
        "name": "perception_pkg",
        "desc": "感知：相机采集与目标检测",
        "deps": "  <depend>sensor_msgs</depend>\n  <depend>vision_msgs</depend>\n  <depend>std_srvs</depend>",
        "files": [("camera_node.py", CAMERA), ("detector_node.py", DETECTOR)],
        "entries": [("camera_node", "camera_node:main"),
                    ("detector_node", "detector_node:main")],
    },
    {
        "name": "control_pkg",
        "desc": "控制：路径决策与底盘驱动",
        "deps": "  <depend>geometry_msgs</depend>\n  <depend>nav_msgs</depend>\n  <depend>vision_msgs</depend>",
        "files": [("planner_node.py", PLANNER), ("motor_node.py", MOTOR)],
        "entries": [("planner_node", "planner_node:main"),
                    ("motor_node", "motor_node:main")],
    },
]

if os.path.exists(OUT):
    os.remove(OUT)

with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
    for pkg in PACKAGES:
        n = pkg["name"]
        base = "src/%s/" % n
        z.writestr(base + "package.xml", PKG_XML.format(
            name=n, desc=pkg["desc"], deps=pkg["deps"]))
        entries = ",\n".join(
            "            '%s = %s.%s'" % (e, n, t) for e, t in pkg["entries"])
        z.writestr(base + "setup.cfg", "[develop]\nscript_dir=$base/lib/%s\n\n"
                                     "[install]\ninstall_scripts=$base/lib/%s\n" % (n, n))
        z.writestr(base + "setup.py", SETUP_PY.format(
            name=n, desc=pkg["desc"], entries=entries))
        z.writestr(base + "resource/" + n, "")
        z.writestr(base + n + "/__init__.py", "")
        for fn, src in pkg["files"]:
            z.writestr(base + n + "/" + fn, src)
    z.writestr("README.md",
               "# demo_ros2_ws\n\n验收用示例：2 个包 / 4 个节点 / 5 条接口。\n")

print("生成:", OUT)
with zipfile.ZipFile(OUT) as z:
    for i in z.namelist():
        print("  ", i)
