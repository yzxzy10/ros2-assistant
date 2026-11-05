# -*- coding: utf-8 -*-
"""版本口径 + 编译沙箱闭环的离线验证（不依赖 Docker / 真实 API Key）。

覆盖四件事：
  1. 版本单一事实来源：默认 Jazzy、可切 Humble、非法值回落、提示词注入；
  2. 沙箱后端的探测与降级：不可用时必须如实说「未执行」，绝不假通过；
  3. 编译失败 → 真实日志回灌 → 重新生成 → 再校验 的闭环；
  4. 界面/回复里的版本标注文案。

跑法：python -X utf8 tests/sandbox_version_test.py
"""
import io
import json
import os
import sys
import tempfile

sys.path.insert(0, ".")
os.environ["ROS2_VERIFY_BACKEND"] = "mock"      # 离线：走模拟后端

import app.llm as llm_mod                        # noqa: E402
from app import versions, sandbox, prompts       # noqa: E402
from app import wf_code                          # noqa: E402

FAIL = []


def check(name, ok, detail=""):
    print(("  [OK]   " if ok else "  [FAIL] ") + name + ("" if ok else "  → %s" % detail))
    if not ok:
        FAIL.append(name)


CFG = {"models": {}, "options": {}, "kb": {"top_k": 5, "max_context_chars": 4500},
       "ros": {"distro": "jazzy", "verify": {"enabled": True, "backend": "mock",
                                             "fix_rounds": 1}}}

print("\n=== 1. 版本号是单一事实来源 ===")
check("默认发行版是 jazzy", versions.distro_name({}) == "jazzy", versions.distro_name({}))
check("完整标注含 Ubuntu/Python/Gazebo",
      all(k in versions.label(CFG) for k in ("Jazzy", "24.04", "3.12", "Harmonic")),
      versions.label(CFG))
check("短标注是 ROS 2 Jazzy", versions.short(CFG) == "ROS 2 Jazzy", versions.short(CFG))
check("环境命令指向 jazzy", versions.setup_cmd(CFG) == "source /opt/ros/jazzy/setup.bash",
      versions.setup_cmd(CFG))
check("apt 包名带发行版", versions.apt_pkg("rclpy", CFG) == "ros-jazzy-rclpy",
      versions.apt_pkg("rclpy", CFG))
check("非法发行版回落默认", versions.distro_name({"ros": {"distro": "foxy"}}) == "jazzy")
h = {"ros": {"distro": "humble"}}
check("切 Humble 后标注同步", "22.04" in versions.label(h) and "humble" in versions.setup_cmd(h),
      versions.label(h))
check("镜像随发行版变化",
      versions.image(CFG) == "ros:jazzy-ros-base" and versions.image(h) == "ros:humble-ros-base",
      "%s / %s" % (versions.image(CFG), versions.image(h)))
check("配置可覆盖镜像", versions.image({"ros": {"distro": "jazzy",
                                               "verify": {"image": "my/ros:custom"}}})
      == "my/ros:custom")

print("\n=== 2. 提示词注入版本约束 ===")
block = versions.constraint_block(CFG)
check("约束块声明目标版本", "ROS 2 Jazzy Jalisco" in block)
check("约束块给出具体 source 命令", "/opt/ros/jazzy/setup.bash" in block)
check("约束块要求冲突时按目标版本", "按目标版本作答" in block)
injected = prompts.versioned("原始提示词", CFG)
check("versioned() 把约束拼在原文之前",
      injected.startswith("【目标版本（硬约束）】") and injected.endswith("原始提示词"))
check("人设里不再出现硬编码 Humble", "Humble" not in prompts.MASTER_PERSONA,
      prompts.MASTER_PERSONA[:40])
check("报错提示词不再硬编码 humble 路径", "/opt/ros/humble/" not in prompts.SYS_MASTER_ERROR)
check("报错提示词仍保留 pip 红线", "rclpy` 不能 pip 安装" in prompts.SYS_MASTER_ERROR)

print("\n=== 3. 沙箱后端探测与降级 ===")
check("pkg 名清洗（大写/空格）",
      sandbox._pkg_name({"node_name": "Camera Publisher"}) == "camera_publisher",
      sandbox._pkg_name({"node_name": "Camera Publisher"}))
check("pkg 名补前缀（数字开头）",
      sandbox._pkg_name({"node_name": "2d_lidar"}).startswith("pkg_"),
      sandbox._pkg_name({"node_name": "2d_lidar"}))
st = sandbox.status(CFG)
check("mock 后端被识别为可用", st["available"] and st["backend"] == "mock", str(st))

os.environ["ROS2_VERIFY_BACKEND"] = "none"
cfg_off = json.loads(json.dumps(CFG))
r_off = sandbox.verify(cfg_off, "import rclpy", {"node_name": "demo"})
check("后端不可用时 attempted=False", r_off["attempted"] is False, str(r_off))
check("后端不可用时不假装通过", r_off["ok"] is False)
check("降级原因写清楚", "关闭" in r_off["reason"], r_off["reason"])
line = sandbox.summary_line(r_off)
check("降级文案含「未执行」", "未执行" in line, line)
os.environ["ROS2_VERIFY_BACKEND"] = "mock"

print("\n=== 4. 模拟后端返回结构 ===")
fake_ok = {"ok": True, "stage": "run", "runtime_ok": True, "nodes": "/demo_node", "log": "ok"}
p = tempfile.mktemp(suffix=".json")
io.open(p, "w", encoding="utf-8").write(json.dumps(fake_ok, ensure_ascii=False))
os.environ["ROS2_SANDBOX_MOCK"] = p
r_ok = sandbox.verify(CFG, "import rclpy", {"node_name": "demo"})
check("读 mock 结果成功", r_ok["attempted"] and r_ok["ok"], str(r_ok))
check("结果带发行版与镜像", r_ok["distro"] == "jazzy" and r_ok["image"] == "ros:jazzy-ros-base")
check("文案标注模拟后端", "模拟" in sandbox.summary_line(r_ok), sandbox.summary_line(r_ok))

io.open(p, "w", encoding="utf-8").write(json.dumps(
    {"ok": False, "stage": "build", "reason": "colcon build 失败",
     "log": "error: package 'x' requires 'y'"}, ensure_ascii=False))
r_bad = sandbox.verify(CFG, "import rclpy", {"node_name": "demo"})
check("失败结果 ok=False", r_bad["ok"] is False)
check("失败文案含「未通过」", "未通过" in sandbox.summary_line(r_bad), sandbox.summary_line(r_bad))

print("\n=== 5. 编译失败 → 回灌 → 重生成 闭环 ===")
seen_prompts = []


def fake_chat(cfg, role, system, user, **kw):
    seen_prompts.append((role, system, user))
    if role == "node_spec":
        return json.dumps({"code_type": "node", "node_name": "talker_node",
                           "class_name": "TalkerNode", "what_to_do": "发消息",
                           "publish_topics": [], "subscribe_topics": [],
                           "core_logic": "定时发布", "extra_features": [],
                           "search_keywords": {}}, ensure_ascii=False)
    if role == "coder":
        return "```python\nimport rclpy\n```"
    if role == "coder_fix":
        return ("```python\nimport rclpy\nfrom rclpy.node import Node\n\n"
                "class TalkerNode(Node):\n"
                "    def __init__(self):\n"
                "        super().__init__('talker_node')\n\n"
                "def main(args=None):\n"
                "    rclpy.init(args=args)\n"
                "    node = TalkerNode()\n"
                "    rclpy.spin(node)\n"
                "    rclpy.shutdown()\n```")
    if role == "teaching":
        return "【知识点解析】模拟"
    return "ok"


llm_mod.chat = fake_chat
wf_code.llm.chat = fake_chat

calls = {"n": 0}
real_verify = sandbox.verify


def stub_verify(cfg, code, spec):
    calls["n"] += 1
    if calls["n"] == 1:
        return {"attempted": True, "ok": False, "backend": "docker", "distro": "jazzy",
                "image": "ros:jazzy-ros-base", "stage": "build", "elapsed": 3.0,
                "runtime_ok": False, "nodes": "",
                "log": "ModuleNotFoundError: No module named 'rclpy.node'", "reason": "colcon build 失败"}
    return {"attempted": True, "ok": True, "backend": "docker", "distro": "jazzy",
            "image": "ros:jazzy-ros-base", "stage": "run", "elapsed": 9.5,
            "runtime_ok": True, "nodes": "/talker_node", "log": "", "reason": ""}


sandbox.verify = stub_verify
wf_code.sandbox.verify = stub_verify

env = wf_code.run(CFG, json.dumps({"node_name": "talker_node"}, ensure_ascii=False))

check("编译尝试了两次（初次 + 修复后）", calls["n"] == 2, str(calls))
check("信封带 compile_rounds=1", env.get("compile_rounds") == 1, env.get("compile_rounds"))
check("信封 verify.ok 为真", (env.get("verify") or {}).get("ok") is True, env.get("verify"))
check("信封标注目标发行版", env.get("distro") == "jazzy", env.get("distro"))
check("信封 schema 升到 1.1", env.get("schema_version") == "1.1", env.get("schema_version"))

fix_users = [u for r, _s, u in seen_prompts if r == "coder_fix"]
compile_fix = [u for u in fix_users if "编译/启动校验" in u]
check("至少发生一次编译失败修复", bool(compile_fix), "coder_fix 次数=%d" % len(fix_users))
check("修复提示词带真实编译日志",
      bool(compile_fix) and "ModuleNotFoundError" in compile_fix[0],
      (compile_fix or [""])[0][:80])
check("修复提示词声明发行版",
      bool(compile_fix) and "ROS 2 jazzy" in compile_fix[0])
coder_sys = [sysp for r, sysp, _u in seen_prompts if r == "coder"]
check("代码生成提示词注入版本约束",
      any("【目标版本（硬约束）】" in (s or "") for s in coder_sys), "未注入")
check("教学提示词带目标版本", any("目标版本" in u for r, _s, u in seen_prompts if r == "teaching"))

print("\n=== 6. 降级路径不阻断主流程 ===")
sandbox.verify = real_verify
wf_code.sandbox.verify = real_verify
os.environ["ROS2_VERIFY_BACKEND"] = "none"
env2 = wf_code.run(CFG, json.dumps({"node_name": "talker_node"}, ensure_ascii=False))
check("无编译环境时仍产出代码", bool(env2.get("code")))
check("verify.attempted=False", (env2.get("verify") or {}).get("attempted") is False)
check("没有编译修复轮次", env2.get("compile_rounds") == 0)
check("教学说明未受影响", bool(env2.get("teaching")))
os.environ["ROS2_VERIFY_BACKEND"] = "mock"

print("\n=== 7. 结果 ===")
if FAIL:
    print("失败 %d 项：%s" % (len(FAIL), "、".join(FAIL)))
    sys.exit(1)
print("全部通过")
