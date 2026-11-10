# -*- coding: utf-8 -*-
"""端到端逻辑验证（模拟 LLM 响应，不依赖真实 API Key）。"""
import sys, json
sys.path.insert(0, '.')
import app.llm as llm_mod
import app.orchestrator as orch
from app import wf_need, wf_plan, wf_code

NEED_JSON = {"schema_version": "1.0", "need_ask": False, "ask_question": "",
 "project": {"name_en": "obstacle_avoidance", "name_cn": "避障小车", "package_name": "obstacle_avoidance", "difficulty": "入门", "description": "激光雷达避障"},
 "modules": [
  {"module_name": "obstacle_detector", "function": "订阅 /scan 发布 /obstacle_distance", "inputs": [{"topic": "/scan", "message_type": "sensor_msgs/msg/LaserScan"}], "outputs": [{"topic": "/obstacle_distance", "message_type": "std_msgs/msg/Float32"}], "dependencies": ["rclpy"]},
  {"module_name": "motion_controller", "function": "订阅 /obstacle_distance 发布 /cmd_vel", "inputs": [{"topic": "/obstacle_distance", "message_type": "std_msgs/msg/Float32"}], "outputs": [{"topic": "/cmd_vel", "message_type": "geometry_msgs/msg/Twist"}], "dependencies": ["rclpy"]}]}

PLAN = {"schema_version": "1.0", "project": NEED_JSON["project"], "nodes": [
 {"node_name": "obstacle_detector_node", "class_name": "ObstacleDetectorNode", "file_name": "obstacle_detector_node.py", "function": "订阅 /scan 发布 /obstacle_distance",
  "publish_topics": [{"topic": "/obstacle_distance", "message_type": "std_msgs/msg/Float32"}],
  "subscribe_topics": [{"topic": "/scan", "message_type": "sensor_msgs/msg/LaserScan"}],
  "core_logic": "取最小距离", "search_keywords": ["LaserScan", "最小距离"]},
 {"node_name": "motion_controller_node", "class_name": "MotionControllerNode", "file_name": "motion_controller_node.py", "function": "订阅 /obstacle_distance 发布 /cmd_vel",
  "publish_topics": [{"topic": "/cmd_vel", "message_type": "geometry_msgs/msg/Twist"}],
  "subscribe_topics": [{"topic": "/obstacle_distance", "message_type": "std_msgs/msg/Float32"}],
  "core_logic": "低于阈值减速", "search_keywords": ["Twist", "cmd_vel"]}],
 "topic_overview": [{"topic": "/obstacle_distance", "message_type": "std_msgs/msg/Float32", "publisher": "obstacle_detector_node", "subscriber": "motion_controller_node", "purpose": "距离"}],
 "dependencies": ["rclpy"], "file_structure": ["obstacle_avoidance/package.xml"]}

GOOD_CODE = '''import rclpy
from rclpy.node import Node
from std_msgs.msg import Float32
from sensor_msgs.msg import LaserScan

class ObstacleDetectorNode(Node):
    def __init__(self):
        super().__init__("obstacle_detector_node")
        self.sub = self.create_subscription(LaserScan, "/scan", self.cb, 10)
        self.pub = self.create_publisher(Float32, "/obstacle_distance", 10)

    def cb(self, msg):
        d = min([r for r in msg.ranges if r > 0.1] or [999.0])
        self.pub.publish(Float32(data=d))
        self.get_logger().info("dist %.2f" % d)

def main(args=None):
    rclpy.init(args=args)
    node = ObstacleDetectorNode()
    rclpy.spin(node)
    rclpy.shutdown()
'''


def fake_chat(cfg, role, system, user, **kw):
    if role == "analysis":
        return json.dumps(NEED_JSON, ensure_ascii=False)
    if role == "plan_architect":
        return json.dumps(PLAN, ensure_ascii=False)
    if role == "plan_guide":
        return "## 学习指南（模拟）"
    if role == "node_spec":
        return json.dumps({"code_type": "node", "node_name": "obstacle_detector_node", "class_name": "ObstacleDetectorNode", "what_to_do": "检测", "publish_topics": [{"name": "/obstacle_distance", "msg_type": "std_msgs/msg/Float32"}], "subscribe_topics": [{"name": "/scan", "msg_type": "sensor_msgs/msg/LaserScan"}], "core_logic": "取最小距离", "extra_features": [], "search_keywords": {}}, ensure_ascii=False)
    if role in ("coder", "coder_fix"):
        return "```python\n" + GOOD_CODE + "\n```"
    if role == "teaching":
        return "【知识点解析】…"
    if role == "chat":
        return "（模拟直答）话题就像广播电台。"
    return "ok"


llm_mod.chat = fake_chat
orch.llm.chat = fake_chat
wf_need.llm.chat = fake_chat
wf_plan.llm.chat = fake_chat
wf_code.llm.chat = fake_chat

cfg = {"models": {}, "options": {}, "kb": {"top_k": 5, "max_context_chars": 4500}}
sid = "testsim001"

r = orch.handle(cfg, sid, "我想做一个激光雷达避障小车项目")
print("[1] stages:", r["stages"], "| options:", [o["key"] for o in r["options"]])
print("    规划书:", "项目规划书" in r["reply"], "| 学习指南:", "学习指南" in r["reply"], "| 节点数:", len(r.get("nodes", [])))

r = orch.handle(cfg, sid, "A")
print("[2] A -> options:", [o["key"] for o in r["options"]])

r = orch.handle(cfg, sid, "D")
print("[3] D -> 含代码:", "create_publisher" in r["reply"], "| 生成状态:", [n["generated"] for n in r["nodes"]], "| pending:", r["pending"])

r = orch.handle(cfg, sid, "继续")
print("[4] 继续 -> 全部生成:", all(n["generated"] for n in r["nodes"]), "| 文件数:", len(r["files"]))

r = orch.handle(cfg, sid, "什么是话题？")
print("[5] 直答:", r["reply"][:24], "| stages:", r["stages"])

r = orch.handle(cfg, sid, "帮我写一个发布hello world的节点")
print("[6] 单节点 含代码:", "create_publisher" in r["reply"], "| stages:", r["stages"])

s = orch.get_session(sid)
print("[7] 工作区文件:", s.file_tree())
print("ALL DONE")
