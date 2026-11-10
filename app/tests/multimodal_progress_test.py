# -*- coding: utf-8 -*-
"""离线验证：进度事件（含心跳）与多模态截图链路。

不依赖真实 API Key：所有模型调用都被替换成固定的假响应。
运行： python tests/multimodal_progress_test.py
"""
import base64
import json
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 会话/画像写到临时目录，别污染项目的 sessions/
_TMP = tempfile.mkdtemp(prefix="ros2test_")
os.environ["ROS2_DATA_DIR"] = _TMP

import app.llm as llm_mod          # noqa: E402
import app.orchestrator as orch     # noqa: E402
import app.vision as vision         # noqa: E402
from app import wf_need, wf_plan, wf_code   # noqa: E402

NEED_JSON = {
    "schema_version": "1.0", "need_ask": False, "ask_question": "",
    "project": {"name_en": "obstacle_avoidance", "name_cn": "避障小车",
                "package_name": "obstacle_avoidance", "difficulty": "入门",
                "description": "激光雷达避障"},
    "modules": [
        {"module_name": "obstacle_detector", "function": "订阅 /scan 发布 /obstacle_distance",
         "inputs": [{"topic": "/scan", "message_type": "sensor_msgs/msg/LaserScan"}],
         "outputs": [{"topic": "/obstacle_distance", "message_type": "std_msgs/msg/Float32"}],
         "dependencies": ["rclpy"]},
        {"module_name": "motion_controller", "function": "订阅 /obstacle_distance 发布 /cmd_vel",
         "inputs": [{"topic": "/obstacle_distance", "message_type": "std_msgs/msg/Float32"}],
         "outputs": [{"topic": "/cmd_vel", "message_type": "geometry_msgs/msg/Twist"}],
         "dependencies": ["rclpy"]}]}

PLAN = {"schema_version": "1.0", "project": NEED_JSON["project"], "nodes": [
    {"node_name": "obstacle_detector_node", "class_name": "ObstacleDetectorNode",
     "file_name": "obstacle_detector_node.py", "function": "订阅 /scan 发布 /obstacle_distance",
     "publish_topics": [{"topic": "/obstacle_distance", "message_type": "std_msgs/msg/Float32"}],
     "subscribe_topics": [{"topic": "/scan", "message_type": "sensor_msgs/msg/LaserScan"}],
     "core_logic": "取最小距离", "search_keywords": ["LaserScan"]}],
    "topic_overview": [{"topic": "/obstacle_distance", "message_type": "std_msgs/msg/Float32",
                        "publisher": "obstacle_detector_node",
                        "subscriber": "motion_controller_node", "purpose": "距离"}],
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

def main(args=None):
    rclpy.init(args=args)
    node = ObstacleDetectorNode()
    rclpy.spin(node)
    rclpy.shutdown()
'''

VISION_JSON = {
    "kind": "error",
    "title": "终端里的 Python 导入报错",
    "text": 'Traceback (most recent call last):\n  File "detector.py", line 3, in <module>\n'
            '    from rclpy.node import Node\nImportError: No module named rclpy',
    "hint": "看起来没有 source ROS 环境",
}


def fake_chat(cfg, role, system, user, **kw):
    if role == "analysis":
        return json.dumps(NEED_JSON, ensure_ascii=False)
    if role == "plan_architect":
        return json.dumps(PLAN, ensure_ascii=False)
    if role == "plan_guide":
        return "## 学习指南（模拟）"
    if role == "node_spec":
        return json.dumps({"code_type": "node", "node_name": "obstacle_detector_node",
                           "class_name": "ObstacleDetectorNode", "what_to_do": "检测",
                           "publish_topics": [{"name": "/obstacle_distance",
                                               "msg_type": "std_msgs/msg/Float32"}],
                           "subscribe_topics": [{"name": "/scan",
                                                 "msg_type": "sensor_msgs/msg/LaserScan"}],
                           "core_logic": "取最小距离", "extra_features": [],
                           "search_keywords": {}}, ensure_ascii=False)
    if role in ("coder", "coder_fix"):
        return "```python\n" + GOOD_CODE + "\n```"
    if role == "teaching":
        return "【知识点解析】…"
    if role == "chat":
        return "（模拟直答）报错原因：缺少 ROS 环境；修复：source /opt/ros/<distro>/setup.bash。"
    return "ok"


def fake_stream(cfg, role, system, user, **kw):
    for piece in ("（流式）", "原因是没 source 环境。"):
        yield piece


def fake_vision(cfg, role, system, user, images, **kw):
    assert images and images[0].startswith("data:image/"), "图片必须以 data URL 传入"
    return json.dumps(VISION_JSON, ensure_ascii=False)


llm_mod.chat = fake_chat
llm_mod.chat_stream = fake_stream
llm_mod.chat_vision = fake_vision
for m in (orch, wf_need, wf_plan, wf_code):
    m.llm.chat = fake_chat

# 让需求分析慢一点，确保能收到心跳（HEARTBEAT=2s）
_real_need_run = wf_need.run


def slow_need_run(*a, **kw):
    time.sleep(2.4)
    return _real_need_run(*a, **kw)


orch.wf_need.run = slow_need_run

CFG = {"baseUrl": "http://mock", "apiKey": "x",
       "models": {}, "options": {},
       "kb": {"top_k": 5, "max_context_chars": 4500},
       "multimodal": {"enabled": True, "max_bytes": 4194304}}

FAIL = []


def check(name, cond, extra=""):
    print(("  [OK]   " if cond else "  [FAIL] ") + name + (("  → " + str(extra)) if extra else ""))
    if not cond:
        FAIL.append(name)


print("=== 1. 进度事件（项目流水线）===")
events = list(orch.handle_stream(CFG, "progtest001", "我想做一个激光雷达避障小车项目"))
kinds = [k for k, _ in events]
steps = [p for k, p in events if k == "progress" and p.get("state") == "start"]
ids = [s["id"] for s in steps]
finals = [p for k, p in events if k == "final"]
final = finals[-1] if finals else {}

check("有 final 事件", bool(finals))
check("步骤顺序为 意图识别→需求分析→项目规划",
      ids and ids[0] == "route" and "analyze" in ids and "plan" in ids, ids)
check("每步都带耗时字段", all("elapsed" in s for s in steps))
check("final 带总耗时与步骤快照",
      isinstance(final.get("elapsed"), float) and len(final.get("steps") or []) >= 3,
      final.get("elapsed"))
check("收到心跳 tick（模型慢时也不静默）",
      "tick" in kinds, "%d 个 tick" % kinds.count("tick"))
check("tick 带 elapsed", any(isinstance(p.get("elapsed"), float)
                            for k, p in events if k == "tick"))
print("  总事件数 %d，步骤 %s，耗时 %.1fs" % (len(events), ids, final.get("elapsed", 0)))

print("\n=== 2. 进度事件（代码生成 + 校验）===")
events2 = list(orch.handle_stream(CFG, "progtest001", "D"))
ids2 = [p["id"] for k, p in events2 if k == "progress" and p.get("state") == "start"]
det2 = [p.get("detail") for k, p in events2 if k == "progress" and p.get("detail")]
check("出现 code 步骤", "code" in ids2, ids2)
check("detail 里能看到节点名/AST 结论",
      any("obstacle_detector_node" in (d or "") for d in det2)
      or any("AST" in (d or "") for d in det2), det2[:3])

print("\n=== 3. 图片校验与落盘 ===")
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
check("魔数识别为 png", vision.sniff(PNG) == ("png", "image/png"), vision.sniff(PNG))
check("拒绝非图片", vision.sniff(b"hello world") == (None, None))
check("拒绝伪装扩展名（内容是文本）", vision.sniff(b"GIF89a-but-not-really"[:6]) is not None)
check("data URL 解析", vision.decode_data_url(
    "data:image/png;base64," + base64.b64encode(PNG).decode()) == PNG)
check("非法 data URL 返回 None", vision.decode_data_url("http://x/a.png") is None)

saved = vision.save("progtest001", PNG)
check("落盘成功", saved.get("ok") and len(saved.get("id", "")) == 20, saved.get("id"))
check("内容寻址：重复上传不落新文件",
      vision.save("progtest001", PNG)["id"] == saved["id"])
check("按 id 能找回", vision.resolve("progtest001", saved["id"]) == saved["path"])
check("跨会话取不到别人的图", vision.resolve("other_sid", saved["id"]) is None)
check("超大图被拒", vision.save("progtest001", b"\x89PNG\r\n\x1a\n" + b"0" * 5000,
                                max_bytes=1024).get("ok") is False)

print("\n=== 4. 视觉识别 + 路由到报错分支 ===")
IMG_SID = "vis001"
img1 = vision.save(IMG_SID, PNG)
v = vision.describe(CFG, IMG_SID, [img1["path"]], "这个怎么办")
check("识别成功", v.get("ok"), v.get("error"))
check("kind=error", v.get("kind") == "error", v.get("kind"))
check("抄到了 ImportError", "ImportError" in (v.get("text") or ""))
check("结果被缓存（第二次直接命中）",
      vision.describe(CFG, IMG_SID, [img1["path"]]).get("cached") is True)

r = orch.handle(CFG, IMG_SID, "这个报错怎么修", None, [img1["id"]])
check("回复带识别标注", r["reply"].startswith("> 📷 识别为"), r["reply"][:40])
check("回复走报错三段式", "模拟直答" in r["reply"])
check("会话状态回填 vision 字段", (r.get("vision") or {}).get("kind") == "error")

# 无效 id 不应该炸
r2 = orch.handle(CFG, IMG_SID, "随便问问", None, ["deadbeefdeadbeefdead"])
check("无效图片 id 被静默忽略", isinstance(r2.get("reply"), str) and r2["reply"] != "")

print("\n=== 5. 视觉不可用时的降级 ===")


def boom(*a, **kw):
    raise llm_mod.LLMError("视觉模型 404")


IMG_SID2 = "vis002"                     # 独立会话 → 独立路径，绕开上一轮的缓存
img2 = vision.save(IMG_SID2, PNG)
llm_mod.chat_vision = boom
d2 = vision.describe(CFG, IMG_SID2, [img2["path"]])
check("识别失败时 ok=False 而不是抛异常", d2.get("ok") is False, d2.get("error"))
r3 = orch.handle(CFG, IMG_SID2, "这是什么", None, [img2["id"]])
check("图片识别失败仍能正常回答", "模拟直答" in r3.get("reply", ""), r3.get("reply", "")[:30])

print("\n=== 6. 非报错类截图（课件）走普通讲解 ===")
IMG_SID3 = "vis003"
img3 = vision.save(IMG_SID3, PNG)
llm_mod.chat_vision = lambda cfg, role, system, user, images, **kw: json.dumps(
    {"kind": "slide", "title": "课件：话题与服务", "text": "话题是异步，服务是同步。",
     "hint": "第 3 页"}, ensure_ascii=False)
d3 = vision.describe(CFG, IMG_SID3, [img3["path"]])
check("kind=slide 时不进报错分支", d3.get("kind") == "slide")
check("slide 不触发报错三段式", not vision.looks_like_error(d3.get("text") or ""))

print("\n" + ("全部通过 ✅" if not FAIL else "失败 %d 项：%s" % (len(FAIL), FAIL)))
sys.exit(1 if FAIL else 0)
