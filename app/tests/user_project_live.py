# -*- coding: utf-8 -*-
"""线上验收：模拟「用户上传自己的项目」，走完整导入 → 出图 → 真 Docker 验证。

为什么要有这个脚本：demo 项目是我们自己写的，天然绕开了用户项目的坑
（多包依赖、自定义 msg 包、package.xml 校验）。这个用例更接近真实提交：
  - 3 个包：自定义 msg 包（CMake）+ 两个 Python 节点包
  - robot_brain 订阅 /scan、发布 /cmd_vel
  - robot_driver 订阅 /cmd_vel、发布 /motor_state（自定义类型，应触发下位机模拟）

期望：3 个包全部编译通过，2 个节点起来，/cmd_vel 与 /motor_state 两条跳都真实收到。

用法：
    python tests/user_project_live.py                 # 默认线上地址 + 分享码
    python tests/user_project_live.py --base http://127.0.0.1:8131 --share ""
"""
import argparse
import http.cookiejar
import json
import time
import urllib.request

MAIL = "student@example.com"

MSGS_XML = """<?xml version="1.0"?>
<package format="3">
  <name>robot_msgs</name>
  <version>0.0.1</version>
  <description>messages</description>
  <maintainer email="%s">student</maintainer>
  <license>MIT</license>
  <buildtool_depend>ament_cmake</buildtool_depend>
  <buildtool_depend>rosidl_default_generators</buildtool_depend>
  <depend>std_msgs</depend>
  <member_of_group>rosidl_interface_packages</member_of_group>
  <export><build_type>ament_cmake</build_type></export>
</package>""" % MAIL

DRIVER_XML = """<?xml version="1.0"?>
<package format="3">
  <name>robot_driver</name>
  <version>0.0.1</version>
  <description>driver</description>
  <maintainer email="%s">student</maintainer>
  <license>MIT</license>
  <depend>rclpy</depend>
  <depend>robot_msgs</depend>
  <export><build_type>ament_python</build_type></export>
</package>""" % MAIL

BRAIN_XML = """<?xml version="1.0"?>
<package format="3">
  <name>robot_brain</name>
  <version>0.0.1</version>
  <description>brain</description>
  <maintainer email="%s">student</maintainer>
  <license>MIT</license>
  <depend>rclpy</depend>
  <depend>sensor_msgs</depend>
  <depend>geometry_msgs</depend>
  <export><build_type>ament_python</build_type></export>
</package>""" % MAIL

BRAIN_PY = '''import rclpy
from rclpy.node import Node
from sensor_msgs.msg import LaserScan
from geometry_msgs.msg import Twist


class Brain(Node):
    def __init__(self):
        super().__init__("brain")
        self.pub = self.create_publisher(Twist, "/cmd_vel", 10)
        self.sub = self.create_subscription(LaserScan, "/scan", self.cb, 10)
        self.create_timer(0.1, self.tick)

    def cb(self, msg):
        self.front = min(msg.ranges) if msg.ranges else 9.9

    def tick(self):
        t = Twist()
        t.linear.x = 0.1
        self.pub.publish(t)


def main():
    rclpy.init()
    n = Brain()
    rclpy.spin(n)
    n.destroy_node()
    rclpy.shutdown()
'''

DRIVER_PY = '''import rclpy
from rclpy.node import Node
from geometry_msgs.msg import Twist
from robot_msgs.msg import MotorState


class Driver(Node):
    def __init__(self):
        super().__init__("driver")
        self.pub = self.create_publisher(MotorState, "/motor_state", 10)
        self.sub = self.create_subscription(Twist, "/cmd_vel", self.cb, 10)

    def cb(self, msg):
        st = MotorState()
        st.rpm = int(msg.linear.x * 100)
        self.pub.publish(st)


def main():
    rclpy.init()
    n = Driver()
    rclpy.spin(n)
    n.destroy_node()
    rclpy.shutdown()
'''

CMAKELISTS = """cmake_minimum_required(VERSION 3.8)
project(robot_msgs)
find_package(ament_cmake REQUIRED)
find_package(std_msgs REQUIRED)
find_package(rosidl_default_generators REQUIRED)
rosidl_generate_interfaces(${PROJECT_NAME} "msg/MotorState.msg" DEPENDENCIES std_msgs)
ament_export_dependencies(rosidl_default_runtime)
ament_package()
"""


def setup_py(name, entry):
    return ("from setuptools import setup\n"
            "setup(name='%s', version='0.0.1', packages=['%s'],\n"
            "      data_files=[('share/ament_index/resource_index/packages', ['resource/%s']),"
            " ('share/%s', ['package.xml'])],\n"
            "      install_requires=['setuptools'], zip_safe=True, maintainer='student',"
            " maintainer_email='%s',\n"
            "      description='x', license='MIT',\n"
            "      entry_points={'console_scripts': ['%s = %s.%s:main']})\n"
            % (name, name, name, name, MAIL, entry, name, entry))


SETUP_CFG = ("[develop]\nscript_dir=$base/lib/%s\n[install]\n"
             "install_scripts=$base/lib/%s\n")


def files():
    out = [
        {"path": "src/robot_msgs/package.xml", "content": MSGS_XML},
        {"path": "src/robot_msgs/CMakeLists.txt", "content": CMAKELISTS},
        {"path": "src/robot_msgs/msg/MotorState.msg", "content": "int32 rpm\n"},
    ]
    for name, xml, code, entry in (
            ("robot_driver", DRIVER_XML, DRIVER_PY, "driver"),
            ("robot_brain", BRAIN_XML, BRAIN_PY, "brain")):
        out += [
            {"path": "src/%s/package.xml" % name, "content": xml},
            {"path": "src/%s/setup.py" % name, "content": setup_py(name, entry)},
            {"path": "src/%s/setup.cfg" % name, "content": SETUP_CFG % (name, name)},
            {"path": "src/%s/resource/%s" % (name, name), "content": ""},
            {"path": "src/%s/%s/__init__.py" % (name, name), "content": ""},
            {"path": "src/%s/%s/%s.py" % (name, name, entry), "content": code},
        ]
    return out


class Api(object):
    def __init__(self, base, share):
        self.base = base.rstrip("/")
        self.share = share
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def _url(self, path):
        if not self.share:
            return self.base + path
        sep = "&" if "?" in path else "?"
        return self.base + path + sep + "sharecode=" + self.share

    def get(self, path, timeout=120):
        with self.opener.open(self._url(path), timeout=timeout) as r:
            return json.loads(r.read().decode())

    def post(self, path, body, timeout=120):
        req = urllib.request.Request(self._url(path), data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with self.opener.open(req, timeout=timeout) as r:
            return json.loads(r.read().decode())


def files_from_dir(root):
    """把本地目录读成 {path, content}；路径相对 root，跳过构建产物。"""
    import os
    skip = {"build", "install", "log", ".git", "__pycache__", ".venv"}
    out = []
    for dp, dn, fn in os.walk(root):
        dn[:] = [d for d in dn if d not in skip]
        for f in fn:
            p = os.path.join(dp, f)
            rel = os.path.relpath(p, root).replace("\\", "/")
            try:
                if os.path.getsize(p) > 1024 * 1024:
                    continue
                with open(p, encoding="utf-8", errors="replace") as fh:
                    out.append({"path": rel, "content": fh.read()})
            except OSError:
                continue
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="https://ros2-tutor-34167.app.workbuddy.host")
    ap.add_argument("--share", default="OGI6JJJSMW")
    ap.add_argument("--sid", default="")
    ap.add_argument("--dir", default="", help="上传这个目录里的项目，不用内置示例")
    a = ap.parse_args()

    sid = a.sid or ("uplive%d" % (int(time.time()) % 100000))
    api = Api(a.base, a.share)
    api.get("/api/identity")

    payload = files_from_dir(a.dir) if a.dir else files()
    r = api.post("/api/project/import", {"sessionId": sid, "files": payload})
    print("导入 ok=%s" % r.get("ok"))
    if not r.get("ok"):
        print(r.get("message"))
        return 2

    g = api.get("/api/graph?sid=" + sid)
    print("图: %d 节点 / %d 连线" % (len(g.get("nodes") or []), len(g.get("edges") or [])))

    print("开始验证（真 Docker，含 colcon build）…")
    api.post("/api/graph/verify", {"sessionId": sid})
    for _ in range(150):
        time.sleep(2)
        s = api.get("/api/graph/verify/status?sid=" + sid)
        if s.get("finished"):
            print("\nbackend = %s | 耗时 %.1fs" % (s.get("backend"), s.get("elapsed") or 0))
            print("ok = %s" % s.get("ok"))
            print("结论 = %s" % s.get("reason"))
            if s.get("hint"):
                print("诊断 = %s" % s["hint"])
            print("依赖 = %s" % s.get("pkgs_needed"))
            print("跳 = %s" % [(h["topic"], h["result"]) for h in s.get("hops") or []])
            print("节点 = %s" % [(n["name"], n.get("result")) for n in s.get("nodes") or []])
            # 日志条数直接反映「有没有真抓到输出」：全 0 就是采集断了
            print("日志 = %s" % [(n["name"], len((n.get("log") or "").strip().splitlines()))
                                for n in s.get("nodes") or []])
            bl = s.get("build_log") or ""
            print("--- 编译输出末尾 ---")
            print((bl if isinstance(bl, str) else "\n".join(bl))[-600:])
            return 0 if s.get("ok") else 1
    print("超时：150 次轮询仍未结束")
    return 3


if __name__ == "__main__":
    raise SystemExit(main())
