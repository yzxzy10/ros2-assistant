# -*- coding: utf-8 -*-
"""主调度员状态机（总路由 + 项目流水线 + 单节点模式 + 直接回答）。

规则逐条对应主 Agent 提示词「总路由」「项目流水线」「状态追踪」「兜底与失败处理」。
与原提示词的差异（服务端工程化改造）：
1. 路由/状态由确定性代码执行（规则与提示词一致），直接回答类仍由 LLM 按原风格生成；
2. 原提示词的 write/read/edit 文件工具 → 服务端会话工作区（sessions/<id>/ros2_ws/...），
   网页端提供文件树与打包下载；
3. 知识库检索 → 内置 BM25 检索器（top_k=5 与原插件一致），注入报错排查/概念讲解/规划/代码生成。
"""
import json
import os
import queue
import re
import threading

from . import llm
from . import store
from . import paths
from . import progress
from . import vision
from . import versions
from . import sandbox
from .kb import get_retriever
from .prompts import (MASTER_PERSONA, SYS_MASTER_ERROR, SYS_MASTER_CONCEPT,
                      SYS_MASTER_EXERCISE, SYS_MASTER_CHAT, SYS_MASTER_FAQ,
                      DIRECT_GUARD, SYS_SUMMARY, VISION_FALLBACK_NOTE,
                      versioned)
from . import wf_need, wf_plan, wf_code

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def sess_dir():
    """会话目录（默认 项目根/sessions，可用 ROS2_DATA_DIR 指向挂载卷）。"""
    return paths.sessions()

# ---------- 记忆参数 ----------
HISTORY_MAX = 30        # 对话历史上限（消息条数，约 15 轮）
SUMMARY_KEEP = 20       # 触发摘要后保留的最近条数
SUMMARY_MIN = 8         # 摘要至少压多少条才值得调用模型

_LOCK = threading.Lock()

# ---------- 流式事件发射（线程本地，多会话互不干扰） ----------
_EMIT = threading.local()


def _set_emitter(fn):
    _EMIT.fn = fn


def _clear_emitter():
    _EMIT.fn = None


def _emitter():
    return getattr(_EMIT, "fn", None)


def _emit_stage(name):
    """兼容旧调用：把阶段名写进当前进度步骤的说明里。

    新代码请直接用 progress.step(...) —— 那样才有独立计时与完成状态。
    """
    progress.note(label=name)


def _emit_delta(text):
    """推送一段增量文本。"""
    fn = _emitter()
    if fn:
        fn(("delta", text))
_SESSIONS = {}
_SAFE_SID = re.compile(r"^[A-Za-z0-9_-]{6,64}$")


def list_user_sessions(uid, limit=20):
    """列出属于 uid 的会话（按最后活动时间倒序），供前端「历史会话」切换。

    只读 state.json 而不加载进内存，避免把一堆旧会话拉进 _SESSIONS。
    """
    root = sess_dir()
    out = []
    if not uid or not os.path.isdir(root):
        return out
    for name in os.listdir(root):
        if not _SAFE_SID.match(name):
            continue
        d = os.path.join(root, name)
        sf = os.path.join(d, "state.json")
        if not os.path.isfile(sf):
            continue
        try:
            with open(sf, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        if data.get("user_id") != uid:
            continue
        hist = (data.get("transcript") or data.get("history")) or []
        preview = ""
        for h in reversed(hist):
            if h.get("role") == "assistant" and (h.get("content") or "").strip():
                preview = h["content"].strip()[:60]
                break
        try:
            updated = os.path.getmtime(sf)
        except OSError:
            updated = 0
        out.append({
            "sessionId": name,
            "title": _title_from(data),
            "preview": preview,
            "nodes_total": len(data.get("node_list") or []),
            "updated": updated,
        })
    out.sort(key=lambda x: x.get("updated") or 0, reverse=True)
    return out[:limit]


def _title_from(data):
    """从 state.json（不加载 Session）里提取标题。"""
    p = (data.get("plan") or {}).get("project") or {}
    if p.get("name_cn"):
        return p["name_cn"]
    req = (data.get("requirement_desc") or "").strip()
    if req:
        line = req.split("\n")[0].strip()
        if line:
            return line[:24]
    for h in (data.get("history") or []):
        if h.get("role") == "user" and (h.get("content") or "").strip():
            return h["content"].strip()[:24]
    return "新对话"

RE_PROJECT = re.compile(
    r"做.{0,4}(一个|个|套|个完整的)?\s*(项目|系统)|"
    r"(项目|系统).{0,6}(规划|设计|搭建)|"
    r"整套|完整的项目|整个项目|多个节点|节点协作|项目流水线|"
    r"帮我设计|规划一个|小车|机器人|无人车|避障.{0,8}(项目|系统)|全流程|"
    r"(搭建|实现|开发|搞|弄).{0,6}(项目|系统)")
RE_SINGLE = re.compile(
    r"写一个|写个|单个节点|一段代码|发布者|订阅者|publisher|subscriber|"
    r"帮我写|帮我生成.{0,8}(节点|代码)|生成.{0,6}节点.{0,6}代码")
RE_BEGINNER = re.compile(r"什么是|是什么意思|新手|入门|第一次|听不懂|啥意思")
RE_ADVANCED = re.compile(r"QoS|生命周期|lifecycle|DDS|执行器|callback group|回调组|组合节点")

# 追问：明确在问「怎么做/为什么/报错」时，即使出现"项目"字样也不当成项目意图
RE_QUESTION_LIKE = re.compile(
    r"报错|错误|Error|error|Exception|Traceback|为什么|怎么|如何|什么是|是什么|"
    r"区别|讲解|解释|练习|题|坑|注意")


def _is_project_intent(msg):
    """是否在表达「我要做一个项目/一套系统」这类需要走项目流水线的意图。

    比单纯正则稳：先看显式模式，再看「项目/系统 + 动作动词」的组合，
    并排除「项目报错了怎么办」这类其实是提问的句子。
    """
    if RE_PROJECT.search(msg):
        return True
    if not re.search(r"项目|系统|套件|多节点", msg):
        return False
    if RE_QUESTION_LIKE.search(msg):
        return False
    return bool(re.search(r"做|搞|搭|建|实现|开发|设计|写|规划|要一个|来一个|想要|帮我", msg))

FALLBACK_MARK = "⚠️ 工作流暂时不可用，以下内容由我直接撰写，未经过规范检查流程。"
UNPASSED_MARK = "⚠️ 该代码未完全通过规范检查：{summary}。运行时可能需手动调整。"


class Session(object):
    def __init__(self, sid):
        self.sid = sid
        self.user_id = sid      # 学习画像主键（跨会话稳定，默认同 sid）
        self.pending = None       # None | 'ask' | 'options' | 'batch' | 'modify'
        self.ask_count = 0
        self.need_data = None     # 第1步产物
        self.requirement_desc = ""  # 原始需求 + 追问回答累积
        self.node_list = []       # [{spec..., generated: bool}]
        self.plan = None
        self.imported_project = None   # 用户导入的项目摘要（见 app/project_import.py）
        self.package_name = ""
        self.generated = []       # 已生成节点名
        self.level = "beginner"
        self.history = []         # 直接回答类对话（最近 HISTORY_MAX 条，给 LLM 当上下文）
        self.transcript = []      # 完整消息流（回放用，含项目流水线的需求/规划/追问）
        self.summary = ""         # 滚动摘要（更早对话压缩后留在这里）
        self.last_reply = ""
        self.ask_options = []     # 最近一次追问的选项（用于把孤立的 A/B/C/D 还原成完整语义）
        self.last_ask_question = ""  # 最近一次追问的题目（相同则不再重复追问）
        self.lock = threading.RLock()  # 每会话锁：串行化同会话请求，不阻塞其他会话

    # ---------- 持久化 ----------
    def to_dict(self):
        return {k: v for k, v in self.__dict__.items()
                if k not in ("last_reply", "lock")}

    def save(self):
        d = os.path.join(sess_dir(), self.sid)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, "state.json"), "w",
                  encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False)

    @classmethod
    def load(cls, sid):
        path = os.path.join(sess_dir(), sid, "state.json")
        if os.path.isfile(path):
            try:
                s = cls(sid)
                with open(path, "r", encoding="utf-8") as f:
                    d = json.load(f)
                for k, v in d.items():
                    if hasattr(s, k):
                        setattr(s, k, v)
                s.last_reply = ""
                return s
            except Exception:
                pass
        return cls(sid)

    # ---------- 会话工作区 ----------
    def ws_root(self):
        return os.path.join(sess_dir(), self.sid, "ros2_ws")

    def save_node_file(self, file_name, code):
        pkg = self.package_name or "my_package"
        d = os.path.join(self.ws_root(), "src", pkg, pkg)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, file_name or "node.py")
        first = "# file: %s" % (file_name or "node.py")
        body = code if code.lstrip().startswith("# file:") else first + "\n" + code
        with open(path, "w", encoding="utf-8") as f:
            f.write(body)
        return path

    def file_tree(self):
        root = self.ws_root()
        out = []
        if os.path.isdir(root):
            for dirpath, _dirs, files in os.walk(root):
                for fn in sorted(files):
                    rel = os.path.relpath(os.path.join(dirpath, fn), root)
                    out.append("ros2_ws/" + rel.replace("\\", "/"))
        return sorted(out)


def get_session(sid):
    with _LOCK:
        if sid not in _SESSIONS:
            _SESSIONS[sid] = Session.load(sid)
        return _SESSIONS[sid]


def active_sids():
    """当前内存里活跃的会话（归档时跳过，避免删掉正在用的目录）。"""
    with _LOCK:
        return list(_SESSIONS.keys())


def forget_session(sid):
    """从内存缓存移除会话（删除/归档后调用，避免残留对象又写回磁盘）。"""
    with _LOCK:
        _SESSIONS.pop(sid, None)


def _update_level(sess, msg):
    if RE_ADVANCED.search(msg):
        sess.level = "advanced"
    elif RE_BEGINNER.search(msg):
        sess.level = "beginner"
    try:
        store.set_level(sess.user_id, sess.level)
    except Exception:
        pass


# ============ 学习画像：跨会话记忆 ============

def _sync_profile(sess):
    """把数据库里的画像同步进会话（水平、长期摘要）。"""
    try:
        p = store.get(sess.user_id) or {}
        if p.get("level") == "advanced":
            sess.level = "advanced"
        if p.get("summary"):
            sess.summary = p["summary"]
    except Exception:
        pass


def _profile_context(sess):
    """渲染成可注入提示词的画像文本。"""
    try:
        return store.render(store.get(sess.user_id) or {})
    except Exception:
        return ""


def _imported_context(sess):
    """渲染「用户导入的项目」上下文，供后续开发注入提示词。"""
    ip = getattr(sess, "imported_project", None)
    if not ip:
        return ""
    try:
        from . import project_import
        return project_import.context_text(ip)
    except Exception:
        return ""


def _remember_message(sess, msg, kind=None):
    """把这句话里出现的概念/报错沉淀到画像。"""
    if not getattr(sess, "user_id", ""):
        return
    try:
        concepts = store.extract_concepts(msg)
        if concepts:
            store.add_concepts(sess.user_id, concepts)
        if kind == "error":
            store.add_error(sess.user_id, *store.extract_error(msg))
        store.record_turn(sess.user_id)
    except Exception:
        pass


def _remember_project(sess, status="规划完成"):
    """把当前项目进度写进画像。"""
    if not getattr(sess, "user_id", ""):
        return
    try:
        p = (sess.plan or {}).get("project") or {}
        pkg = sess.package_name or p.get("package_name") or ""
        if not pkg:
            return
        store.upsert_project(sess.user_id, {
            "package_name": pkg,
            "name_cn": p.get("name_cn") or pkg,
            "difficulty": p.get("difficulty") or "",
            "nodes_total": len(sess.node_list),
            "nodes_done": sum(1 for n in sess.node_list if n.get("generated")),
            "status": status,
        })
    except Exception:
        pass


def _mem_cfg(cfg):
    """读取记忆参数（config.memory），带默认值与容错。"""
    m = (cfg or {}).get("memory") or {}
    try:
        hmax = max(6, int(m.get("history_max") or HISTORY_MAX))
        keep = max(2, int(m.get("summary_keep") or SUMMARY_KEEP))
    except Exception:
        hmax, keep = HISTORY_MAX, SUMMARY_KEEP
    if keep >= hmax:
        keep = max(2, hmax // 2)
    return hmax, keep


def _maybe_summarize(cfg, sess):
    """历史超过上限时，用（便宜的）摘要模型把老对话压成一段长期记忆。

    压缩后只保留最近 SUMMARY_KEEP 条原文，更早的部分进 sess.summary 并落库。
    摘要失败不影响本次对话：退化为「原文拼接截断」。
    """
    hmax, keep = _mem_cfg(cfg)
    if len(sess.history) <= hmax:
        return
    cut = max(SUMMARY_MIN, len(sess.history) - keep)
    if cut >= len(sess.history):
        cut = len(sess.history) - keep
    old, sess.history = sess.history[:cut], sess.history[cut:]

    lines = []
    for h in old:
        role = "学生" if h.get("role") == "user" else "助手"
        text = (h.get("content") or "").replace("\n", " ").strip()[:240]
        if text:
            lines.append("%s: %s" % (role, text))
    if not lines:
        return

    prev = sess.summary or ""
    prompt = ("【已有摘要】\n%s\n\n【新对话】\n%s\n\n请合并成一份不超过 300 字的滚动记忆。"
              % (prev or "（无）", "\n".join(lines)))
    try:
        text = llm.chat(cfg, "summary", SYS_SUMMARY, prompt, timeout=90)
        text = (text or "").strip()
    except Exception:
        text = ""
    if not text:
        # 兜底：不丢信息，直接拼起来截断
        text = (prev + "\n" + "\n".join(lines)).strip()[-1200:]
    sess.summary = text[:1200]
    try:
        store.set_summary(sess.user_id, sess.summary,
                          covered_turns=max(1, len(old) // 2))
    except Exception:
        pass
    sess.save()


# ============ 选项定义 ============

OPT_ABCD = [
    {"key": "A", "label": "规划可以，就按这个来"},
    {"key": "B", "label": "我要修改规划（说下怎么改）"},
    {"key": "C", "label": "帮我生成某个节点的代码（说节点名）"},
    {"key": "D", "label": "帮我生成全部节点代码"},
]
OPT_CD = [
    {"key": "C", "label": "生成某个节点的代码（输入节点名）"},
    {"key": "D", "label": "生成全部节点代码"},
]

# 追问兜底选项卡：当模型追问没带选项、或把同一道题又问了一遍时，
# 用它兜底，确保学生始终有点选入口，而不是被空泛地反问。
FALLBACK_ASK_OPTS = [
    {"key": "A", "label": "机器人小车（移动 / 避障 / 导航）",
     "send": "我想做一个机器人小车项目"},
    {"key": "B", "label": "机械臂（抓取 / 轨迹控制）",
     "send": "我想做一个机械臂项目"},
    {"key": "C", "label": "传感器数据采集 / 图像视觉",
     "send": "我想做一个传感器数据采集项目"},
    {"key": "D", "label": "多节点协作系统 / 其他",
     "send": "我想做一个多节点协作系统"},
]


_OPT_TOKEN = re.compile(r"(?:^|[\s，,（(])([A-Z])[.．、)）]\s*")


def _parse_ask_options(q):
    """从追问文本中拆出题目正文与 A/B/C/D 选项；拆不出就原样返回。

    返回 (question, options)，options 中每项含 key/label/send，
    send 是点击后实际发给智能体的文本（保留选项内容，避免丢信息）。
    """
    text = (q or "").replace("**", "").strip()
    matches = list(_OPT_TOKEN.finditer(text))
    if len(matches) < 2:
        return text, []
    options = []
    for i, m in enumerate(matches):
        key = m.group(1)
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        label = re.sub(r"\s+", " ", text[start:end]).strip().strip("，,。;；")
        if not label:
            continue
        options.append({"key": key, "label": label, "send": "%s. %s" % (key, label)})
    if len(options) < 2:
        return text, []
    question = text[:matches[0].start()].strip().rstrip("：:，,")
    return (question or text), options


def _reply(md, options=None, options_mode=None, stages=None, sess=None):
    r = {"reply": md, "options": options or [], "stages": stages or []}
    if options_mode:
        r["options_mode"] = options_mode
    if sess is not None:
        r["nodes"] = [{"node_name": n["node_name"], "generated": n.get("generated", False),
                       "file_name": n.get("file_name", ""),
                       "what_to_do": n.get("what_to_do", "")} for n in sess.node_list]
        r["files"] = sess.file_tree()
        r["pending"] = sess.pending
    return r


# ============ 单节点生成（模式3 与 选项C/D 共用） ============

def _gen_single_node(cfg, sess, spec, label, expected_name=None):
    """调用 ros2代码生成，返回回复 dict。spec：规格 JSON 字符串。

    整个过程（生成 → AST 校验 → 定向修复）在同一步骤里，对外只报一个
    「代码生成」步骤，但用 detail 把 校验通过/修复轮次 透出去，学生能看见
    「它现在卡在检查」而不是一片空白。
    """
    spec_in = spec
    ctx = _imported_context(sess)
    if ctx:
        spec_in = ctx + "\n\n【节点规格】\n" + spec
    with progress.step("code", "代码生成", "正在生成 %s" % label):
        env = wf_code.run(cfg, spec_in)
        if not env.get("valid"):
            # 重试 1 次
            progress.note("%s · 首次失败，重试中" % label)
            env = wf_code.run(cfg, spec_in)
        if not env.get("valid"):
            return _reply("%s\n\n节点「%s」生成失败（%s），不阻塞其他节点。你可以稍后重试，"
                          "或换个说法描述该节点功能。" % (FALLBACK_MARK, label,
                          env.get("error", "未知原因")), sess=sess)

        code = env.get("code", "")
        node_name = env.get("node_name", "") or label
        file_name = env.get("file_name", "") or (node_name + ".py")
        sess.save_node_file(file_name, code)

        rounds = env.get("rounds", 0)
        verify = env.get("verify") or {}
        crounds = env.get("compile_rounds", 0)
        note = "%s · %s%s" % (
            node_name,
            "AST 校验通过" if env.get("passed") else "AST 未完全通过",
            "（修复 %d 轮）" % rounds if rounds else "")
        if verify.get("attempted"):
            note += " · 编译%s" % ("通过" if verify.get("ok") else "未通过")
        progress.note(note)

        # 标记已生成：优先按本次请求的节点（expected_name），再按返回的 node_name
        for n in sess.node_list:
            if n.get("node_name") == (expected_name or node_name) \
                    or n.get("node_name") == node_name:
                n["generated"] = True
        done_name = expected_name or node_name
        if done_name not in sess.generated:
            sess.generated.append(done_name)
        _remember_project(sess, status="代码生成中")

        md = ["## 节点：%s" % node_name, ""]
        md.append("```python\n%s\n```" % code)
        md.append("")
        if not env.get("passed"):
            md.append(UNPASSED_MARK.format(summary=env.get("summary", "存在未解决问题")))
            md.append("")
        teaching = env.get("teaching", "")
        if teaching:
            md.append("### 🧑‍🏫 教学说明")
            md.append("")
            md.append(teaching)
        if rounds:
            md.append("")
            md.append("> 本节点经过 %d 轮自动修复后通过检查。" % rounds
                      if env.get("passed") else
                      "> 本节点经过 %d 轮自动修复仍未完全通过检查。" % rounds)
        if crounds:
            md.append("> 编译/启动未通过，已按真实日志自动修复 %d 轮。" % crounds)
        # 版本与校验口径：明确这一版代码是按哪个 ROS 发行版生成、有没有真的编译过
        md.append("")
        md.append("> " + sandbox.summary_line(verify))
        if verify.get("attempted") and not verify.get("ok") and verify.get("log"):
            md.append("")
            md.append("<details><summary>查看校验日志</summary>\n")
            md.append("```\n%s\n```" % verify["log"])
            md.append("\n</details>")
        sess.save()
        return _reply("\n".join(md), sess=sess)


def _find_node(sess, name):
    name = name.strip().lower()
    for n in sess.node_list:
        if n.get("node_name", "").lower() == name:
            return n
    cand = name if name.endswith("_node") else name + "_node"
    for n in sess.node_list:
        if n.get("node_name", "").lower() == cand:
            return n
    return None


def _spec_json(node):
    return json.dumps({
        "node_name": node.get("node_name", ""),
        "class_name": node.get("class_name", ""),
        "file_name": node.get("file_name", ""),
        "what_to_do": node.get("what_to_do", ""),
        "publish_topics": node.get("publish_topics", []),
        "subscribe_topics": node.get("subscribe_topics", []),
    }, ensure_ascii=False)


# ============ 项目流水线 ============

def _pipeline_step1(cfg, sess, full_desc):
    """需求分析（重试 1 次 → 兜底）。"""
    stages = ["需求分析"]
    profile_text = _profile_context(sess)
    desc = full_desc
    ctx = _imported_context(sess)
    if ctx:
        desc = ctx + "\n\n" + full_desc
    with progress.step("analyze", "需求分析"):
        env = wf_need.run(cfg, desc, leader_context="", history="",
                          level_hint=sess.level, profile=profile_text)
        if not env.get("valid"):
            progress.note("规格校验未通过，走修复器")
            env = wf_need.run(cfg, desc, leader_context="", history="",
                              level_hint=sess.level, profile=profile_text)
        if not env.get("valid"):
            md = (FALLBACK_MARK + "\n\n需求分析流程暂时不可用（%s）。\n"
                  "请稍后重试，或先换一种说法描述你的项目需求。"
                  % env.get("error", "未知原因"))
            sess.pending = None
            sess.save()
            return _reply(md, stages=stages, sess=sess)
        if env.get("need_ask"):
            progress.note("信息不足，需要向学生追问")
        else:
            mods = (env.get("data") or {}).get("modules") or []
            progress.note("已拆出 %d 个功能模块" % len(mods) if mods else "需求已结构化")

    if env.get("need_ask"):
        q = env.get("ask_question", "你想做什么项目？")
        question, ask_opts = _parse_ask_options(q)
        already_asked = (sess.last_ask_question
                         and question.strip() == sess.last_ask_question.strip())
        if already_asked:
            # 模型把同一道题又问了一遍：换用通用选项卡，避免原地打转
            question = "我想再确认一下你要做的项目方向，选一个最接近的："
            ask_opts = list(FALLBACK_ASK_OPTS)
        if not ask_opts:
            # 追问没带出可点选选项：兜底成通用选项卡，确保学生始终有点选入口
            question = (question.strip() or "我想确认一下你的项目方向") + \
                       "\n\n（下面没有你要的，直接打字补充也行）"
            ask_opts = list(FALLBACK_ASK_OPTS)
        if sess.ask_count >= 3:
            # 连续追问 3 轮仍信息不足：才带着已有信息往下走
            return _pipeline_step2(cfg, sess, full_desc, stages)
        sess.pending = "ask"
        sess.ask_count += 1
        sess.last_ask_question = question
        sess.need_data = env.get("data")
        sess.ask_options = ask_opts
        sess.save()
        return _reply("%s\n\n（点下面的按钮，也可以直接说你的想法）" % question,
                      options=ask_opts, options_mode="ask", stages=stages, sess=sess)

    sess.need_data = env.get("data")
    return _pipeline_step2(cfg, sess, full_desc, stages)


def _pipeline_step2(cfg, sess, full_desc, stages=None):
    """项目规划。"""
    stages = (stages or []) + ["ros2项目规划"]
    req_json = json.dumps(sess.need_data, ensure_ascii=False) if sess.need_data else full_desc
    with progress.step("plan", "项目规划"):
        res = wf_plan.run(cfg, req_json)
        if not res.get("valid"):
            # 重试 1 次
            progress.note("规划校验未通过，重试中")
            res = wf_plan.run(cfg, req_json)
        if not res.get("valid"):
            sess.pending = None
            sess.save()
            return _reply(FALLBACK_MARK + "\n\n项目规划流程暂时不可用（%s）。请稍后重试。"
                          % res.get("error", "未知原因"), stages=stages, sess=sess)
        progress.note("已拆出 %d 个节点" % len(res.get("node_list") or []))

    sess.plan = res["plan"]
    sess.node_list = [dict(n, generated=False) for n in res["node_list"]]
    p = (sess.plan.get("project") or {})
    sess.package_name = p.get("package_name", "") or "my_package"
    _remember_project(sess, status="规划完成")

    plan_md = wf_plan.render_plan_md(sess.plan)
    guide = res.get("guide_md", "")
    md = [plan_md, ""]
    if guide:
        md.append("---")
        md.append("")
        md.append(guide)
    md.append("")
    md.append("接下来你可以：")
    sess.pending = "options"
    sess.need_data = sess.need_data or {"original_request": full_desc}
    sess.save()
    return _reply("\n".join(md), options=OPT_ABCD, options_mode="abcd",
                  stages=stages, sess=sess)


# ============ 直接回答 ============

def _direct_answer(cfg, sess, msg, kind, stages):
    kbcfg = cfg.get("kb", {})
    with progress.step("retrieve", "检索依据"):
        context, source_count = get_retriever().context(
            msg, top_k=kbcfg.get("top_k", 5),
            max_chars=kbcfg.get("max_context_chars", 4500))
        progress.note("命中 %d 条知识库条目" % source_count if source_count
                      else "知识库无命中，按模型知识作答")

    if kind == "error":
        sys_p = SYS_MASTER_ERROR
    elif kind == "faq":
        sys_p = SYS_MASTER_FAQ
    elif kind == "concept":
        sys_p = SYS_MASTER_CONCEPT
    elif kind == "exercise":
        sys_p = SYS_MASTER_EXERCISE
    else:
        sys_p = SYS_MASTER_CHAT.replace("{level_hint}",
            "advanced" if sess.level == "advanced" else "beginner")
    sys_p = MASTER_PERSONA + "\n\n" + DIRECT_GUARD + "\n\n" + sys_p
    # 目标发行版硬约束（Humble/Jazzy 命令与包名不同，必须显式告知模型）
    sys_p = versioned(sys_p, cfg)

    project_info = ""
    if sess.plan:
        p = sess.plan.get("project") or {}
        project_info = "学生当前项目：%s（%s），节点：%s" % (
            p.get("name_cn", ""), p.get("difficulty", ""),
            "、".join(n.get("node_name", "") for n in sess.node_list))
    imported_text = _imported_context(sess)

    user = msg
    if context:
        user += "\n\n【知识库检索结果（%d 条）】\n%s" % (source_count, context)
    if imported_text:
        user = imported_text + "\n\n" + user
    if project_info:
        user = "【当前项目上下文】%s\n\n%s" % (project_info, user)
    profile_text = _profile_context(sess)
    if profile_text:
        user = profile_text + "\n\n" + user
    elif sess.summary:
        user = "【往期对话摘要】%s\n\n%s" % (sess.summary, user)

    try:
        if _emitter() is not None:
            with progress.step("answer", "生成回答") as _p:
                if _p is not None:
                    _p.note("流式输出中")
                parts = []
                try:
                    for chunk in llm.chat_stream(cfg, "chat", sys_p, user, history=sess.history):
                        parts.append(chunk)
                        _emit_delta(chunk)
                except Exception:
                    if not parts:
                        raise
                answer = "".join(parts)
        else:
            with progress.step("answer", "生成回答"):
                answer = llm.chat(cfg, "chat", sys_p, user, history=sess.history)
    except Exception as e:
        return _reply("抱歉，我这边暂时连不上大脑（%s）。请稍后再试，或检查服务端模型配置。"
                      % e, stages=stages, sess=sess)

    # 兜底防线：聊天分支不许擅自产出「整套项目 + 多节点代码」
    if _looks_like_project_dump(answer):
        answer = ("看起来你是想做一个完整的项目～那我先问清楚再动手，别让 AI 替你"
                  "拍脑袋定项目 😄\n\n想知道三件事：\n"
                  "1. 想做什么？（比如避障小车 / 机械臂抓取 / 室内导航 / 传感器数据采集）\n"
                  "2. 手上有哪些传感器或设备？（激光雷达 / 摄像头 / 超声波 / 只有电脑）\n"
                  "3. 目标难度：能跑起来就行，还是想深入理解每个节点？\n\n"
                  "你说完，我就按「需求分析 → 项目规划 → 逐节点生成代码」带你走一遍。")

    sess.history.append({"role": "user", "content": msg})
    sess.history.append({"role": "assistant", "content": answer})
    _remember_message(sess, msg, kind)
    _maybe_summarize(cfg, sess)          # 超阈值时先压缩老对话
    hmax = _mem_cfg(cfg)[0]
    if len(sess.history) > hmax:         # 摘要失败时的兜底裁剪
        sess.history = sess.history[-hmax:]
    sess.save()
    return _reply(answer, stages=stages, sess=sess)


def _looks_like_project_dump(text):
    """判断模型回复是否越界成了「整套项目 + 多个节点代码」。

    只在同时出现「工程量证据」时才判定，避免把讲解话题时给的
    发布者+订阅者小例子误判为项目代码。
    """
    if not text:
        return False
    node_classes = len(re.findall(r"class\s+\w+\s*\(.*Node.*\)\s*:", text))
    engineering = bool(re.search(r"package\.xml|setup\.py|CMakeLists\.txt|"
                                 r"colcon\s+build|项目结构|目录结构|ros2_ws/src", text))
    if not engineering:
        return False
    if node_classes >= 2:
        return True
    if node_classes >= 1 and len(re.findall(r"def\s+main\s*\(", text)) >= 2:
        return True
    return bool(re.search(r"colcon\s+build", text) and node_classes >= 1)


def _classify_direct(msg):
    """报错 > 练习 > 概念 > 闲聊。"""
    if re.search(r"报错|错误|Error|Exception|Traceback|跑不了|运行不了|失败|不通|"
                 r"没反应|起不来|不能用|为什么.{0,8}(不行|失败)", msg, re.I):
        return "error"
    # 注意：不能用「出.{0,4}题」，否则「出现什么问题」会被误判成出练习题
    if re.search(r"出(?!现|错|问).{0,3}题|练习|习题|考考我|测验|测试我|出题", msg):
        return "exercise"
    # 经验类：常见问题/容易踩的坑 → 走清单式回答，而不是「结论+类比」模板
    if re.search(r"常见.{0,6}(问题|错误|坑)|容易.{0,6}(问题|出错|踩坑|犯)|"
                 r"踩.{0,4}坑|避坑|注意事项|经验", msg):
        return "faq"
    if re.search(r"什么是|是什么|什么意思|区别|为什么|怎么理解|讲讲|介绍一下|解释|建议", msg):
        return "concept"
    return "chat"


# ============ 选项流转 ============

def _is_option_reply(msg):
    """判断学生这句话是否真的在回应「规划完成后的 A/B/C/D 选项流转」。

    用于避免：会话停在 options 待办态时，学生问了一个全新问题
    （如「什么是话题」）却被死板地兜底成「请回复选项字母」。
    只有明确的选项意图才继续走选项流转，否则退出待办态按普通问题处理。
    """
    t = (msg or "").strip()
    if not t:
        return False
    upper = t.upper()[:1]
    if upper in ("A", "B", "C", "D") and len(t) <= 2:
        return True
    # 明确动作词（这些词即使出现在长句里也基本指向选项动作，相对安全）
    if re.search(r"修改|改一下|要改|调整|生成.{0,20}代码|节点.{0,10}代码|"
                 r"全部生成|所有.{0,6}生成", t):
        return True
    # 确认类：必须整句匹配，避免「可以讲讲话题吗」被误判为「确认规划」
    return _is_confirm(t)


_CONFIRM_FULL = re.compile(
    r"^(按这个来|就这样|可以|可以的|可以呀|好|好的|没问题|确认|行|嗯|OK|ok)[！!。.，, ]*$")


def _is_confirm(msg):
    """整句就是「确认/同意」的短回复。"""
    return bool(_CONFIRM_FULL.match((msg or "").strip()))


def _is_continue_reply(msg):
    """学生是否明确表示「继续生成下一批节点」。"""
    t = (msg or "").strip()
    if not t:
        return False
    if re.fullmatch(r"(继续|下一批|接着|接着生成|继续生成|好|好的|是|是的|嗯|OK|ok|next)[！!。.，, ]*",
                    t):
        return True
    return bool(re.search(r"继续生成|下一批|接着生成|剩下的.{0,4}生成|全部生成", t))


def _looks_like_off_topic_question(msg):
    """学生在项目待办态（ask/modify）里突然问了一个独立问题（概念/报错/练习），
    而不是在补充需求或说修改要求。命中则退出项目流水线，按普通问题回答。
    """
    t = (msg or "").strip()
    if not t or len(t) <= 3:
        return False
    # 明显在回应追问的短回复（选项字母/短确认）不算偏离
    if _is_option_reply(t):
        return False
    return bool(re.search(
        r"什么是|是什么|什么意思|区别|为什么|怎么理解|讲讲|介绍一下|解释|"
        r"报错|错误|Error|Traceback|Exception|出题|练习|考考我", t))


def _handle_options(cfg, sess, msg, stages):
    t = msg.strip()
    upper = t.upper()[:1] if t else ""
    if upper == "A" or _is_confirm(t):
        sess.pending = "options_cd"
        sess.save()
        return _reply("好，规划就定下来了✅ 你可以随时让我生成某个节点的代码。",
                      options=OPT_CD, options_mode="cd", stages=stages, sess=sess)
    if upper == "B" or re.search(r"修改|改一下|要改|调整", t):
        if len(t) > 12 or re.search(r"[：:]|，|。", t):
            # 说了具体改法 → 重走第 1 步
            sess.requirement_desc = sess.requirement_desc + "\n【修改要求】" + t
            sess.ask_count = 0
            sess.last_ask_question = ""
            sess.ask_options = []
            return _pipeline_step1(cfg, sess, sess.requirement_desc)
        sess.pending = "modify"
        sess.save()
        return _reply("具体要改哪里？比如节点增减、话题改名、难度调整",
                      stages=stages, sess=sess)
    if upper == "C" or re.search(r"生成.{0,20}代码|节点.{0,10}代码", t):
        name = ""
        m = re.search(r"[「『\"]?([A-Za-z_][A-Za-z0-9_]*)[」』\"]?的?代码", t)
        if m:
            name = m.group(1)
        node = _find_node(sess, name) if name else None
        if node is None:
            names = "、".join(n.get("node_name", "") for n in sess.node_list)
            return _reply("未找到节点「%s」，清单里有：%s。请输入清单中的节点名。"
                          % (name or "（未提供）", names),
                          options=OPT_CD, options_mode="cd", stages=stages, sess=sess)
        return _gen_single_node(cfg, sess, _spec_json(node), node["node_name"], expected_name=node.get("node_name"))
    if upper == "D" or re.search(r"全部生成|所有.{0,6}生成|全都生成", t):
        return _gen_batch(cfg, sess, stages)
    # 不认识的回复 → 重新给选项
    return _reply("请回复选项字母（A/B/C/D），或直接说你的想法～",
                  options=OPT_ABCD, options_mode="abcd", stages=stages, sess=sess)


def _pending_nodes(sess):
    return [n for n in sess.node_list if not n.get("generated")]


def _gen_batch(cfg, sess, stages=None):
    stages = (stages or []) + ["ros2代码生成"]
    pend = _pending_nodes(sess)
    if not pend:
        sess.pending = None
        sess.save()
        return _reply("所有节点的代码都已经生成过啦🎉 可以到右侧「项目文件」下载，"
                      "也可以继续问我问题。", stages=stages, sess=sess)
    batch = pend if len(pend) <= 2 else pend[:2]
    parts = []
    with progress.step("code", "代码生成", "本批 %d 个节点，共 %d 个待生成"
                       % (len(batch), len(pend))):
        for node in batch:
            r = _gen_single_node(cfg, sess, _spec_json(node), node["node_name"],
                                 expected_name=node.get("node_name"))
            parts.append(r["reply"])
            progress.note("已完成 %d/%d 个节点" % (len(parts), len(batch)))
    remain = len(_pending_nodes(sess))
    total = len(sess.node_list)
    if remain > 0:
        sess.pending = "batch"
        sess.save()
        tail = ("\n\n已生成 %d/%d，继续吗？（回复「继续」生成下一批）"
                % (total - remain, total))
        return _reply("\n\n---\n\n".join(parts) + tail, stages=stages, sess=sess)
    sess.pending = None
    sess.save()
    return _reply("\n\n---\n\n".join(parts) +
                  "\n\n全部节点生成完毕🎉 可到右侧「项目文件」下载整个工作区。",
                  stages=stages, sess=sess)


def _expand_choice(sess, msg):
    """学生只回了一个字母（A/B/C/D）时，用上一轮追问的选项补全语义。

    兼容前端旧缓存、以及学生直接手打字母的情况，避免模型拿到孤立字母后重复追问。
    """
    t = (msg or "").strip().strip("。.！!").upper()
    if not re.fullmatch(r"[A-Z]", t):
        return msg
    for o in (sess.ask_options or []):
        if o.get("key") == t:
            return "%s. %s" % (t, o.get("label", ""))
    return msg


# ============ 总路由 ============

def handle(cfg, sid, message, user_id=None, images=None):
    sess = get_session(sid)
    with sess.lock:
        if user_id and user_id != sess.user_id:
            sess.user_id = user_id
        _sync_profile(sess)
        _record_transcript(sess, message, "user")
        res = _handle_locked(cfg, sess, message, images)
        if isinstance(res, dict) and res.get("reply"):
            _record_transcript(sess, res.get("reply"), "assistant")
        return res


def _record_transcript(sess, content, role):
    """把一句话追加进回放消息流（transcript），并控制长度。"""
    text = (content or "").strip()
    if not text:
        return
    try:
        sess.transcript.append({"role": role, "content": text})
        if len(sess.transcript) > 200:
            sess.transcript = sess.transcript[-200:]
        sess.save()
    except Exception:
        pass


_DONE = ("__done__", None)


def handle_stream(cfg, sid, message, user_id=None, images=None):
    """流式处理，逐条 yield ("progress"|"tick"|"delta"|"final"|"error", payload)。

    工作流在线程里跑，通过线程本地 emitter 把进度/增量文本推进队列，
    主协程边收边吐；另有一个心跳线程每 2 秒补一个 tick，
    保证任何时刻前端都不会超过 2 秒收不到信号。
    """
    q = queue.Queue()

    def emit(event):
        q.put(event)

    p = progress.Progress(emit).start()

    def worker():
        _set_emitter(emit)
        progress.set_current(p)
        try:
            res = handle(cfg, sid, message, user_id, images)
            if isinstance(res, dict):
                snap = p.snapshot()
                res["elapsed"] = snap["elapsed"]
                res["steps"] = snap["steps"]
            p.stop()                     # 先把心跳停掉，再发 final
            q.put(("final", res))
        except Exception as e:  # noqa: BLE001
            p.stop()
            q.put(("error", "处理请求时出现内部错误：%r" % e))
        finally:
            _clear_emitter()
            progress.clear()
            q.put(_DONE)

    threading.Thread(target=worker, daemon=True).start()
    try:
        while True:
            item = q.get()
            if item is _DONE:
                break
            yield item
    finally:
        p.stop()


def _handle_locked(cfg, sess, message, images=None):
    msg = (message or "").strip()
    orig_text = msg
    img_paths = vision.resolve_many(sess.sid, images)

    if not msg and not img_paths:
        return _reply("想做什么 ROS2 项目？或者随便问点什么～", sess=sess)

    # ---------- 多模态：先把图看懂，再决定走哪条链路 ----------
    v = None
    if img_paths:
        v = vision.describe(cfg, sess.sid, img_paths, msg)
        if v.get("ok"):
            msg = vision.attach_to_message(msg, v)
        else:
            msg = (msg + "\n\n" + VISION_FALLBACK_NOTE).strip()
        if not msg.strip():
            msg = "帮我看看这张图"

    _update_level(sess, msg)

    # 报错截图 / 代码截图：直接走诊断链路，不进项目流水线
    if v and v.get("ok") and (v.get("kind") in ("error", "code")
                              or vision.looks_like_error(v.get("text"))):
        r = _direct_answer(cfg, sess, msg, "error", ["直接回答"])
        r["reply"] = "> 📷 识别为：**%s**\n\n%s" % (
            v.get("title") or "截图", r["reply"])
        r["vision"] = {"kind": v.get("kind"), "title": v.get("title", "")}
        return r

    # 只发了图、没写字：按图片类型讲解，绝不擅自开一条项目流水线
    # （学生贴张课件截图却被生成一整套代码，是最伤信任的越界行为）
    if img_paths and not orig_text:
        kind = "concept" if (v or {}).get("kind") in ("slide", "diagram",
                                                      "hardware") \
            else _classify_direct(msg)
        r = _direct_answer(cfg, sess, msg, kind, ["直接回答"])
        if (v or {}).get("ok"):
            r["reply"] = "> 📷 识别为：**%s**\n\n%s" % (
                v.get("title") or "截图", r["reply"])
            r["vision"] = {"kind": v.get("kind"), "title": v.get("title", "")}
        return r

    # 1. 上一轮留了待办
    if sess.pending == "ask":
        if _looks_like_off_topic_question(msg):
            # 学生没在补充需求，而是在问新问题：退出项目流水线，按普通消息路由
            sess.pending = None
            sess.save()
        else:
            answer = _expand_choice(sess, msg)
            sess.requirement_desc = (sess.requirement_desc or "") + "\n【补充】" + answer
            sess.pending = None
            r = _pipeline_step1(cfg, sess, sess.requirement_desc)
            return r
    if sess.pending == "modify":
        if _looks_like_off_topic_question(msg):
            sess.pending = None
            sess.save()
        else:
            sess.requirement_desc = (sess.requirement_desc or "") + "\n【修改要求】" + msg
            sess.ask_count = 0
            sess.last_ask_question = ""
            sess.ask_options = []
            sess.pending = None
            return _pipeline_step1(cfg, sess, sess.requirement_desc)
    if sess.pending == "options":
        if _is_option_reply(msg):
            return _handle_options(cfg, sess, msg, ["选项流转"])
        # 学生没在回应选项，而是在问新问题：退出待办态，按普通消息继续往下路由
        sess.pending = None
        sess.save()
    if sess.pending == "options_cd":
        t = msg.strip().upper()[:1]
        if t == "C" or re.search(r"生成.{0,20}代码|节点.{0,10}代码", msg):
            return _handle_options(cfg, sess, msg, [])
        if t == "D" or re.search(r"全部生成|所有.{0,6}生成|全都生成", msg):
            return _gen_batch(cfg, sess, [])
        # 其他回复当普通问题直接答
        sess.pending = None
    if sess.pending == "batch":
        if _is_continue_reply(msg):
            sess.pending = None
            return _gen_batch(cfg, sess, [])
        sess.pending = None
        # 不是"继续"就当普通消息处理

    # 2. 项目意图（带图时只看学生自己打的字，别被图里抄来的文字带跑）
    route_text = orig_text if (img_paths and orig_text) else msg
    with progress.step("route", "意图识别"):
        is_project = _is_project_intent(route_text) and len(route_text) >= 4
        progress.note("判定为%s · 讲解深度 %s" % (
            "项目需求" if is_project else "单点提问",
            "进阶" if sess.level == "advanced" else "入门"))
    if is_project:
        sess.requirement_desc = msg
        sess.ask_count = 0
        sess.last_ask_question = ""
        sess.ask_options = []
        sess.pending = None
        return _pipeline_step1(cfg, sess, msg)

    # 3. 单节点意图
    if RE_SINGLE.search(msg):
        stages = ["ros2代码生成"]
        r = _gen_single_node(cfg, sess, msg, "（你描述的节点）")
        r["stages"] = stages
        return r

    # 4. 直接回答
    kind = _classify_direct(msg)
    return _direct_answer(cfg, sess, msg, kind, ["直接回答"])
