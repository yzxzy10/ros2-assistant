# -*- coding: utf-8 -*-
"""Agent 层离线自测：不调模型、不起服务，只验注册表与移交轨迹。"""
import sys

from app import agents, progress

fails = []


def check(name, cond, extra=""):
    if cond:
        print("  ok   %s" % name)
    else:
        print("  FAIL %s %s" % (name, extra))
        fails.append(name)


print("[1] 注册表")
check("十一个角色", len(agents.all_agents()) == 11, len(agents.all_agents()))
ids = [a["id"] for a in agents.all_agents()]
check("id 唯一", len(ids) == len(set(ids)))
steps = [a["step"] for a in agents.all_agents()]
check("step 唯一", len(steps) == len(set(steps)))
for a in agents.all_agents():
    check("字段完整 %s" % a["id"], all(a.get(k) for k in ("name", "duty", "inputs", "outputs", "prompt")))
check("未知 id 返回 None", agents.get("nope") is None)
check("name_of 未知回退原文", agents.name_of("nope") == "nope")

print("[2] 移交轨迹")
events = []


def emit(ev):
    events.append(ev)


p = progress.Progress(emit).start()
progress.set_current(p)
agents.reset()
with agents.run("need_analyst", "收敛需求") as a1:
    check("进入返回 Agent 对象", a1 is not None and a1.id == "need_analyst")
    check("current 已设置", agents.current() == "need_analyst")
with agents.run("architect", "细化规格"):
    pass
with agents.run("coder", "生成代码"):
    pass
with agents.run("fixer", "按错误修"):
    pass
p.stop()
progress.clear()

t = agents.trace()
check("轨迹 4 步", len(t) == 4, len(t))
check("首跳来自学生", t[0]["from_name"] == "学生")
check("首跳目标需求分析师", t[0]["to_name"] == "需求分析师")
check("末跳目标修复工程师", t[-1]["to_name"] == "代码修复工程师")
check("链路串起来", agents.handoff_line() ==
      "学生 → 需求分析师 → 项目架构师 → 代码工程师 → 代码修复工程师", agents.handoff_line())

print("[3] 进度事件带 agent")
prog = [ev for ev in events if ev[0] == "progress"]
with_agent = [e for e in prog if e[1].get("agent")]
check("有进度事件", len(prog) > 0, len(prog))
check("事件带 agent 字段", len(with_agent) == 8, len(with_agent))
check("start 事件 agent=需求分析师", prog[0][1]["agent"] == "需求分析师", prog[0][1].get("agent"))
check("label 用角色名", prog[0][1]["label"] == "需求分析师", prog[0][1].get("label"))

print("[4] 重复进入同一角色不重复记移交")
events.clear()
p2 = progress.Progress(emit).start()
progress.set_current(p2)
agents.reset()
with agents.run("coder"):
    pass
with agents.run("coder"):
    pass
p2.stop()
progress.clear()
check("同一角色连续两次只记一跳", len(agents.trace()) == 1, len(agents.trace()))

print("[5] 无进度器时不崩")
agents.reset()
with agents.run("answerer") as a:
    check("无进度器仍返回 Agent", a is not None)
check("无进度器仍留轨迹", len(agents.trace()) == 1)

print("")
if fails:
    print("FAILED: %s" % ", ".join(fails))
    sys.exit(1)
print("ALL PASS")
