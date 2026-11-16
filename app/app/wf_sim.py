# -*- coding: utf-8 -*-
"""工作流④「仿真建模」：一句话需求 → 可 colcon build 的 Gazebo 工程包。

与既有两条流水线的分工
----------------------
- 单节点流程（wf_code）：一次生成一个 .py 节点，重点是代码正确性；
- 项目流程（wf_need → wf_plan → wf_code）：多个节点 + 话题拓扑；
- 本流程：生成「描述 + 接线 + 配置」，产出的是一个能直接在 Gazebo 里
  看得到、动得起来的包，没有业务节点。

三段式，只有第一段用模型：

    参数表（模型） → 骨架装配（代码） → 静态闸门（代码）

这样切分的理由是可靠性：URDF 这类结构化文件，模型每次都会写出不一样的结构，
而结构一漂移，URDF / ros2_control / controllers.yaml 三处就对不齐，
在 Gazebo 里的表现是「模型出来了但不动」，最难查。
把结构交给模板，模型只填数字，出错面就从几百行塌缩到十几个字段。
"""
from . import agents
from . import llm
from . import progress
from . import sim_build
from . import sim_profiles
from . import sim_gate
from . import sim_spec
from .prompts import SYS_SIM_SPEC, versioned
from .wf_need import extract_json


def run(cfg, message):
    """返回 {spec, files, gate, degraded}。不写盘，落盘由 orchestrator 负责。"""
    spec, degraded = _analyze(cfg, message)
    files = _assemble(spec)
    gate = _gates(spec, files)
    return {"spec": spec, "files": files, "gate": gate, "degraded": degraded}


# ---------- 第一段：参数表 ----------

def _analyze(cfg, message):
    """模型 → 参数表。模型抽风时按文本判机型 + 默认尺寸，不阻断主流程。"""
    raw = ""
    with agents.run("model_engineer", "把需求收敛成仿真参数表",
                    artifact="仿真参数表"):
        try:
            sys_p = versioned(SYS_SIM_SPEC, cfg) + (
                "\n\n可用机型档案（学生提到真机型号时，把 profile 填成对应的键，"
                "其余尺寸不用写，系统会按真机参数填）：\n"
                + sim_profiles.list_for_prompt())
            raw = llm.chat(cfg, "sim_spec", sys_p, message)
        except Exception as e:
            progress.note("参数表模型调用失败：%s" % e)
            raw = ""

    data = extract_json(raw) if raw else None
    if isinstance(data, dict):
        try:
            # 学生原话也带上：模型可能只回「两轮差速小车」，但学生说的是
            # 「像 turtlebot3 那样」，型号关键词只在原话里，靠它命中机型档案
            data = dict(data)
            data.setdefault("requirement", message)
            spec = sim_spec.normalize(data)
            progress.note(sim_spec.summary_lines(spec))
            return spec, ""
        except Exception as e:
            progress.note("参数表不合法：%s" % e)

    # 降级路径：至少保证「用户说机械臂不会给小车」
    progress.note("没拿到参数表，按需求文本判定机型并用默认尺寸")
    spec = sim_spec.normalize({"brief": message})
    return spec, "参数表没解析出来，已按需求文本选用默认尺寸生成"


# ---------- 第二段：装配 ----------

def _assemble(spec):
    with agents.run("sim_engineer", "按骨架模板装配工程",
                    artifact="工程文件"):
        files = sim_build.build(spec)
    return files


# ---------- 第三段：闸门 ----------

def _gates(spec, files):
    with agents.run("sim_engineer", "静态闸门：关节链 / 命名 / 物理量",
                    artifact="校验报告"):
        gate = sim_gate.check(spec, files)
        if gate["passed"]:
            progress.note("六道闸门通过（%d 项）" % gate["stats"]["checks"])
        else:
            progress.note("闸门拦下 %d 个问题" % len(gate["errors"]))
        return gate


# ---------- 渲染 ----------

def render_markdown(res):
    spec = res["spec"]
    gate = res["gate"]
    files = res["files"]
    pkg = spec["package"]

    out = []
    out.append("已生成 `%s`，共 **%d 个文件**。" % (pkg, len(files)))
    out.append("")
    out.append("> %s" % sim_spec.summary_lines(spec))
    if res.get("degraded"):
        out.append(">")
        out.append("> ⚠️ %s" % res["degraded"])

    out.append("")
    out.append("| 文件 | 行数 |")
    out.append("|---|---|")
    for p, n in sim_build.describe_files(files)[:10]:
        if n is None:
            kb = len(files[p]) / 1024.0
            out.append("| `%s` | 网格 %.0f KB |" % (p, kb))
        else:
            out.append("| `%s` | %d |" % (p, n))
    if len(files) > 10:
        out.append("| …共 %d 个 | |" % len(files))

    out.append("")
    if gate["passed"]:
        out.append("**静态校验通过** · 六道闸门 %d 项断言" % gate["stats"]["checks"])
    else:
        out.append("**静态校验拦下 %d 个问题**" % len(gate["errors"]))
        out.append("")
        for e in gate["errors"][:6]:
            out.append("- `%s` %s" % (e["code"], e["msg"]))
        out.append("")
        out.append("这些问题我这边没解决，先别急着 build——把上面几条发我，我改完再给一版。")
        return "\n".join(out)

    out.append("")
    out.append("```bash")
    out.append("cd ~/ros2_ws && colcon build --symlink-install --packages-select %s" % pkg)
    out.append("source install/setup.bash")
    out.append("python3 install/%s/share/%s/scripts/selfcheck.py   # 先自检" % (pkg, pkg))
    out.append("ros2 launch %s sim.launch.py" % pkg)
    out.append("```")
    out.append("")

    if spec["archetype"] == "arm":
        demo = []
        for j in spec["joints"]:
            demo.append("%.2f" % (j["upper"] * 0.5 if j["type"] == "prismatic" else 0.4))
        out.append("控制器 `%s` 起来后，发一条目标就能动：" % spec["controller_name"])
        out.append("")
        out.append("```bash")
        out.append("ros2 topic pub --once /%s/commands std_msgs/msg/Float64MultiArray \\"
                   % spec["controller_name"])
        out.append('  "{data: [%s]}"' % ", ".join(demo))
        out.append("```")
    else:
        out.append("差速插件直接吃 `/cmd_vel`，键盘遥控即可：")
        out.append("")
        out.append("```bash")
        out.append("ros2 run teleop_twist_keyboard teleop_twist_keyboard")
        out.append("```")

    out.append("")
    out.append("文件已写进工作区，右侧文件树能直接看，也可以整包下载。"
               "要改尺寸就改 xacro 顶部的 `xacro:property`，惯量会跟着重算。")
    return "\n".join(out)
