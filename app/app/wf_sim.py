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
from .kb import get_retriever
from .prompts import SYS_SIM_SPEC, SYS_SIM_TEACHING, versioned
from .wf_need import extract_json


def run(cfg, message, fallback=False):
    """返回 {spec, files, gate, degraded, teaching}。不写盘，落盘由 orchestrator 负责。

    需求信息不够（看不出机型）时返回 {"need_more": {...}}，由上层去问学生，
    不生成任何文件——猜一个机型出来的模型，学生还得自己发现不对。

    fallback=True 表示「已经问过一次了」：这次不再追问，按最接近的机型
    （差速底盘）生成，并在回复里标注，避免把人卡在来回追问里。
    """
    spec, degraded, unknown = _analyze(cfg, message, allow_unknown=not fallback)
    if unknown:
        return {"need_more": {"question": ARCH_ASK, "options": ARCH_OPTIONS},
                "spec": None, "files": {}, "gate": None,
                "degraded": "", "teaching": ""}
    if fallback and not degraded:
        degraded = ("需求里仍没有明确的机型，已按最接近的差速底盘生成；"
                    "要机械臂或阿克曼车，直接说一句我重做一版。")
    files = _assemble(spec)
    gate = _gates(spec, files)
    # 闸门没过就先谈问题，别谈教学——学生此刻要的是能跑起来的包
    teaching = _teach(cfg, spec, files) if gate["passed"] else ""
    return {"spec": spec, "files": files, "gate": gate,
            "degraded": degraded, "teaching": teaching}


# 机型缺失时的追问：说清「为什么问」，并给出可直接点的选项。
# 选项的 send 一定含机型关键词，学生点一下就能命中 _is_sim_intent。
ARCH_ASK = (
    "动手前要先确认一件事：这次的模型是哪一类机器人？\n\n"
    "差速底盘、阿克曼车、机械臂三者的骨架、控制器和驱动方式完全不同，"
    "选错了会得到一个「看着像但动不起来」的模型，所以这里不猜。\n\n"
    "点下面的选项，或者直接描述也行——比如「像 turtlebot3 那样的两轮底盘」"
    "「六轴机械臂，四轴转动两轴平移」。"
)
ARCH_OPTIONS = [
    {"key": "1", "label": "差速底盘（两轮/四轮，能原地转）", "send": "生成一个差速底盘"},
    {"key": "2", "label": "阿克曼转向车（前轮打方向，像真汽车）", "send": "生成一个四轮阿克曼转向车"},
    {"key": "3", "label": "固定基座机械臂（几轴可以再说）", "send": "生成一个六轴机械臂"},
]


# ---------- 第一段：参数表 ----------

def _analyze(cfg, message, allow_unknown=True):
    """模型 → 参数表。返回 (spec, degraded, unknown)。

    unknown=True 表示机型判不出来：**不要兜底成差速底盘**，交给上层追问。
    模型调用失败/JSON 不合法时，还会用学生的原话再试一次文本匹配——那是
    最后的机会，仍判不出就追问。

    allow_unknown=False 用于「已经问过一轮还没说清」的兜底：这次直接落到
    差速底盘，不再让用户空转。
    """
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
            spec = sim_spec.normalize(data, allow_unknown=allow_unknown)
            progress.note(sim_spec.summary_lines(spec))
            return spec, "", False
        except sim_spec.UnknownArchetype:
            progress.note("需求里没看出机型，先问清楚再生成")
            return None, "", True
        except Exception as e:
            progress.note("参数表不合法：%s" % e)

    # 降级：模型没给出可用参数表时，至少保证「说机械臂不会给小车」
    progress.note("没拿到参数表，按需求文本判定机型并用默认尺寸")
    try:
        spec = sim_spec.normalize({"brief": message, "requirement": message},
                                  allow_unknown=allow_unknown)
    except sim_spec.UnknownArchetype:
        progress.note("需求里没看出机型，先问清楚再生成")
        return None, "", True
    return spec, "参数表没解析出来，已按需求文本选用默认尺寸生成", False


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


# ---------- 第四段：教学说明 ----------
# 与单节点流程（wf_code）保持同一套输出格式：代码/结构 + 🧑‍🏫 教学说明三段。
# 学生拿到小车或机械臂时，最先想知道的是「这几个数字为什么这么填、
# 为什么它在 Gazebo 里能走」，而不是文件清单。

_TEACH_QUERY = ("URDF link joint 惯量 inertial ros2_control 硬件接口 "
                "Gazebo 插件 差速控制器 关节限位")


def _teaching_brief(spec, files):
    """把工程压成一段模型看得懂的概要，避免把整包源码塞进提示词。"""
    lines = ["机型：%s" % sim_spec.summary_lines(spec),
             "包名：%s" % spec["package"],
             "控制器：%s" % spec.get("controller_name", "")]
    joints = spec.get("joints") or []
    if joints:
        desc = []
        for j in joints:
            unit = "m" if j["type"] == "prismatic" else "rad"
            desc.append("%s（%s，%.2f ~ %.2f %s）" % (
                j["name"], "平移" if j["type"] == "prismatic" else "转动",
                j["lower"], j["upper"], unit))
        lines.append("关节：%s" % "；".join(desc))
    if spec.get("archetype") in ("diff_drive", "ackermann"):
        lines.append("底盘参数：轮径 %.0f mm、轮距 %.0f mm、车体 %.0f×%.0f×%.0f mm" % (
            spec["wheel_radius"] * 1000, spec["wheel_separation"] * 1000,
            spec["base_length"] * 1000, spec["base_width"] * 1000,
            spec["base_height"] * 1000))
    # 控制器配置原文交给模型当事实依据：不给它看，它就爱按「差速 + 轮径」硬套机械臂
    cy = files.get("config/controllers.yaml")
    if isinstance(cy, str) and cy.strip():
        lines.append("控制器配置 config/controllers.yaml：")
        lines.append("```yaml")
        lines.append(cy.strip()[:1200])
        lines.append("```")
    # 配套控制节点：让教学能讲「代码怎么把指令送到控制器」
    for p in ("scripts/drive_demo.py", "scripts/arm_demo.py"):
        t = files.get(p)
        if isinstance(t, str) and t.strip():
            lines.append("配套控制节点 %s（随包生成，负责发指令）：" % p)
            lines.append("```python")
            lines.append(t.strip()[:900])
            lines.append("```")
            break
    lines.append("文件：")
    for p, n in sim_build.describe_files(files):
        lines.append("- %s（%s）" % (p, "网格" if n is None else "%d 行" % n))
    return "\n".join(lines)


def _teach(cfg, spec, files):
    """调教学模型写三段说明。失败不阻断出包——模型挂了也照样交付工程。"""
    with agents.run("sim_engineer", "写仿真教学说明", artifact="教学说明"):
        try:
            kbcfg = cfg.get("kb") or {}
            context, _n = get_retriever().context(
                _TEACH_QUERY, top_k=kbcfg.get("top_k", 5),
                max_chars=min(int(kbcfg.get("max_context_chars", 4500)), 3500))
        except Exception:
            context = ""
        user = "【工程概要】\n%s\n\n【知识库参考资料】\n%s" % (
            _teaching_brief(spec, files), context or "（空）")
        try:
            text = llm.chat(cfg, "teaching", versioned(SYS_SIM_TEACHING, cfg), user)
            text = (text or "").strip()
            if text:
                progress.note("教学说明已生成（%d 字）" % len(text))
            return text
        except Exception as e:
            progress.note("教学说明生成失败：%s" % e)
            return ""


# ---------- 渲染 ----------

_EXCERPT_LINES = 46          # 主模型文件展示多少行：够看清结构，又不至于刷屏


def _excerpt(files):
    """挑主模型文件（urdf/<pkg>.urdf.xacro）作为「代码」展示。

    单节点回复展示的是完整 .py；仿真包有十来个文件，全贴没法看，
    这里给主描述文件的开头——机器人名、材质、底盘 link，正好是最该看的头部。
    """
    path = ""
    for p in sorted(files):
        if p.startswith("urdf/") and p.endswith(".urdf.xacro"):
            path = p
            break
    if not path:
        return None
    text = files.get(path) or ""
    if not isinstance(text, str) or not text.strip():
        return None
    lines = text.split("\n")
    cut = len(lines) > _EXCERPT_LINES
    body = "\n".join(lines[:_EXCERPT_LINES]).rstrip()
    return {"path": path, "body": body, "total": len(lines), "cut": cut}


def _demo_script(spec, files):
    """取出配套控制节点，供回复里展示。

    与模型同一批生成（模板填参数，不是让模型写）：话题名和数组长度必须
    和控制器严格对齐，模型写这类代码最容易在长度上出错。
    """
    if spec["archetype"] == "arm":
        path = "scripts/arm_demo.py"
        note = ("机械臂只有结构不会自己动：这个节点往 `/%s/commands` 发位置指令，"
                "先到目标位再回零，数组长度等于该控制器的关节数。写法是"
                "「一下到位」，不做插值——原因见下面的教学说明。"
                % spec.get("controller_name", "arm_controller"))
    else:
        path = "scripts/drive_demo.py"
        note = ("模型本身不会动：这个节点按方形路线发 `/cmd_vel`，直行与圆弧转弯交替，"
                "走完四条边自动发零速停车。转弯段同时给线速度和角速度，"
                "所以差速底盘和阿克曼车都能直接跑。")
    body = files.get(path)
    if not isinstance(body, str) or not body.strip():
        return None
    return {"path": path, "name": path.split("/")[-1],
            "note": note, "body": body.strip()}


def render_markdown(res):
    spec = res["spec"]
    gate = res["gate"]
    files = res["files"]
    pkg = spec["package"]

    # 骨架与单节点回复一致：标题 → 结构/代码 → 🧑‍🏫 教学说明 → 操作命令 → 版本行
    out = []
    out.append("## 仿真包：`%s`" % pkg)
    out.append("")
    out.append("%s · 共 **%d 个文件**" % (spec["archetype_name"], len(files)))
    if res.get("degraded"):
        out.append("")
        out.append("> ⚠️ %s" % res["degraded"])

    ex = _excerpt(files)
    if ex:
        out.append("")
        if ex["cut"]:
            out.append("`%s`（前 %d 行，共 %d 行；完整内容在右侧文件树）："
                       % (ex["path"], _EXCERPT_LINES, ex["total"]))
        else:
            out.append("`%s`：" % ex["path"])
        out.append("")
        out.append("```xml")
        out.append(ex["body"])
        out.append("```")

    # 配套控制节点：模型只是结构，不懂怎么让它动——所以一次请求里连代码一起给
    demo = _demo_script(spec, files)
    if demo:
        out.append("")
        out.append("### 配套控制节点 `%s`" % demo["name"])
        out.append("")
        out.append(demo["note"])
        out.append("")
        out.append("```python")
        out.append(demo["body"])
        out.append("```")

    teaching = (res.get("teaching") or "").strip()
    out.append("")
    out.append("### 🧑‍🏫 教学说明")
    out.append("")
    out.append(teaching or "暂无教学内容")

    out.append("")
    out.append("### 工程文件")
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
    if demo:
        out.append("")
        out.append("# 另一个终端：让模型真的动起来")
        out.append("ros2 run %s %s" % (pkg, demo["name"][:-3]))
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
    out.append("")
    out.append("> %s" % sim_spec.summary_lines(spec))
    return "\n".join(out)
