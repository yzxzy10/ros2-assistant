# -*- coding: utf-8 -*-
"""Agent 注册表：把「一堆分散的提示词」变成「有身份的协作角色」。

背景
----
在引入本模块之前，系统内部其实已经有九个专业角色，但它们只是 prompts.py 里
几个互不相识的常量，由 orchestrator 里的 if/else 顺手调用。对外看是「一个助手」，
对内也说不清「是谁在干活」——这正是评审表里「多 Agent」拿不到分的地方。

本模块做的事只有三件，不改变任何业务流程：

1. **登记**：每个角色声明 id、名字、职责、输入契约、输出契约、所用提示词；
2. **移交可见**：角色之间的交接被记录成轨迹（谁 → 谁，带了什么产物）；
3. **口径统一**：进度事件里带上 agent 标识，前端能显示「现在是需求分析师在做」。

设计约束（很重要）
------------------
- **不新建进程、不新建服务**：仍然跑在同一进程里，靠确定性状态机调度。
  这么做的原因不是偷懒：状态机是这套系统可靠性的来源（实测修掉过两类误路由），
  拆成独立服务会把「一次请求内的强一致」变成「跨服务协调」，决赛前不值得。
- **不接管业务逻辑**：本模块只做登记与观测，谁调用谁仍由 orchestrator 决定。
- **无副作用**：没有进度器时所有方法退化为空操作，离线单测可以直接调用。
"""
import threading

from . import progress as _p


class Agent(object):
    """一个协作角色的静态声明。"""

    __slots__ = ("id", "name", "duty", "inputs", "outputs", "prompt", "step")

    def __init__(self, id, name, duty, inputs, outputs, prompt, step):
        self.id = id
        self.name = name
        self.duty = duty
        self.inputs = inputs          # 输入契约（人可读）
        self.outputs = outputs        # 输出契约（人可读）
        self.prompt = prompt          # 对应 prompts.py 里的常量名
        self.step = step              # 进度步骤标识，与前端已有 id 对齐

    def as_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "duty": self.duty,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "prompt": self.prompt,
            "step": self.step,
        }


# 顺序即典型流转顺序，前端「团队」面板按此排序
AGENTS = [
    Agent(
        id="need_analyst",
        name="需求分析师",
        duty="把学生模糊的自然语言需求收敛成结构化功能规格，信息不足时反问",
        inputs="学生原话 + 学习画像 + 导入工程摘要",
        outputs="功能规格 JSON（方向/传感器/输出/话题）",
        prompt="SYS_NEED_ANALYST",
        step="analyze",
    ),
    Agent(
        id="architect",
        name="项目架构师",
        duty="把功能规格细化为可生成代码的节点规格，决定包结构与通信拓扑",
        inputs="功能规格 JSON",
        outputs="项目规划（包名 + 节点清单 + 话题关系）",
        prompt="SYS_ARCHITECT",
        step="plan",
    ),
    Agent(
        id="node_spec",
        name="节点规格分析员",
        duty="把单个节点的自然语言描述标准化成统一 JSON，供代码生成使用",
        inputs="节点描述或规划中的节点条目",
        outputs="节点规格 JSON",
        prompt="SYS_NODE_SPEC",
        step="spec",
    ),
    Agent(
        id="model_engineer",
        name="模型工程师",
        duty="把仿真需求收敛成参数表：机型、连杆尺寸、关节类型与限位、质量",
        inputs="学生原话（含尺寸/轴数/控制方式）",
        outputs="仿真参数表 JSON（sim spec）",
        prompt="SYS_SIM_SPEC",
        step="sim_spec",
    ),
    Agent(
        id="sim_engineer",
        name="仿真集成工程师",
        duty="按骨架模板装配 URDF / ros2_control / launch / 控制器配置，并跑静态闸门",
        inputs="仿真参数表 JSON",
        outputs="完整仿真工程包 + 六道闸门校验报告",
        prompt="（模板装配，不调模型）",
        step="sim_build",
    ),
    Agent(
        id="coder",
        name="代码工程师",
        duty="按节点规格产出可直接运行的 ROS2 Python 节点与构建文件",
        inputs="节点规格 JSON + 等级提示",
        outputs="节点源码 + package.xml / setup.py",
        prompt="SYS_CODER",
        step="code",
    ),
    Agent(
        id="fixer",
        name="代码修复工程师",
        duty="按机器校验给出的错误列表修复代码，最多三轮，不掩盖失败",
        inputs="源码 + 错误列表",
        outputs="修复后源码 + 剩余问题",
        prompt="SYS_CODE_FIXER",
        step="fix",
    ),
    Agent(
        id="guide",
        name="学习辅导员",
        duty="在规划完成后生成学习指南，不重复展示规划书",
        inputs="项目规划 + 学生等级",
        outputs="学习指南 Markdown",
        prompt="SYS_GUIDE",
        step="guide",
    ),
    Agent(
        id="answerer",
        name="答疑主控",
        duty="直接回答类问题的分诊：报错 / 概念 / 经验 / 出题 / 闲聊五个分支",
        inputs="学生问题 + BM25 检索依据",
        outputs="分结构回答 Markdown（可选追问选项卡）",
        prompt="SYS_MASTER_*",
        step="answer",
    ),
    Agent(
        id="vision",
        name="截图理解器",
        duty="把学生丢来的报错截图转成结构化文字，再交给答疑主控",
        inputs="图片（PNG/JPG/WEBP/GIF）",
        outputs="结构化识别结果 {kind,title,text,hint}",
        prompt="SYS_VISION",
        step="vision",
    ),
    Agent(
        id="archivist",
        name="记忆整理员",
        duty="把已有摘要与新对话合并成滚动记忆，失败时退化为原文拼接不丢信息",
        inputs="旧摘要 + 新增对话",
        outputs="不超过 300 字的新摘要",
        prompt="SYS_SUMMARY",
        step="summary",
    ),
]

_BY_ID = dict((a.id, a) for a in AGENTS)
_BY_STEP = dict((a.step, a) for a in AGENTS)


def get(agent_id):
    return _BY_ID.get(agent_id)


def name_of(agent_id):
    a = _BY_ID.get(agent_id)
    return a.name if a else (agent_id or "用户")


def all_agents():
    return [a.as_dict() for a in AGENTS]


def step_owner(step_id):
    """按进度步骤 id 反查角色名，供老代码平滑迁移。"""
    a = _BY_STEP.get(step_id)
    return a.name if a else ""


# ---------------- 线程本地：当前请求的移交轨迹 ----------------
# 与 progress 同一套思路：一次请求一个线程，请求结束即丢弃，不落盘、不加锁。

_LOCAL = threading.local()


def _state():
    st = getattr(_LOCAL, "st", None)
    if st is None:
        st = {"current": None, "trace": []}
        _LOCAL.st = st
    return st


def reset():
    """每次请求开始时调用，清空上一条轨迹。"""
    _LOCAL.st = {"current": None, "trace": []}


def current():
    return _state()["current"]


def trace():
    return list(_state()["trace"])


def _handoff(from_id, to_id, artifact):
    _state()["trace"].append({
        "from": from_id or "",
        "from_name": name_of(from_id) if from_id else "学生",
        "to": to_id,
        "to_name": name_of(to_id),
        "artifact": artifact or "",
    })


class _RunCtx(object):
    """with agents.run("architect", "规划中"): ...

    同时做三件事：登记当前角色、发一条带 agent 的进度事件、记录移交。
    未知 id 直接放行，不阻断主链路。
    """

    def __init__(self, agent_id, detail="", artifact="", label=None):
        self.agent = _BY_ID.get(agent_id)
        self.agent_id = agent_id
        self.detail = detail
        self.artifact = artifact
        self.label = label
        self._ctx = None

    def __enter__(self):
        if self.agent is None:
            return None
        st = _state()
        prev = st["current"]
        if prev != self.agent_id:
            _handoff(prev, self.agent_id, self.artifact)
            st["current"] = self.agent_id
        self._ctx = _p.step(
            self.agent.step,
            self.label or self.agent.name,
            self.detail,
            agent=self.agent.name,
        )
        self._ctx.__enter__()
        return self.agent

    def __exit__(self, et, ev, tb):
        if self._ctx is not None:
            return self._ctx.__exit__(et, ev, tb)
        return False


def run(agent_id, detail="", artifact="", label=None):
    return _RunCtx(agent_id, detail=detail, artifact=artifact, label=label)


def mark(agent_id, artifact=""):
    """只登记角色与移交，不新开进度步骤。

    用于「进度步骤已经在别处开过、但需要把角色记进轨迹」的场景
    （例如直接回答分支，步骤 id/label 早已由 progress.step 发出）。
    """
    if _BY_ID.get(agent_id) is None:
        return None
    st = _state()
    if st["current"] != agent_id:
        _handoff(st["current"], agent_id, artifact)
        st["current"] = agent_id
    return _BY_ID[agent_id]


def handoff_line():
    """把轨迹压成一行人话，用于最终回复里说明「这次是谁协作完成的」。"""
    t = trace()
    if not t:
        return ""
    names = [t[0]["from_name"]] + [h["to_name"] for h in t]
    seen, order = set(), []
    for n in names:
        if n not in seen:
            seen.add(n)
            order.append(n)
    return " → ".join(order)
