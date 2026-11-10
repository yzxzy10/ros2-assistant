# -*- coding: utf-8 -*-
"""工作流②「ros2项目规划」：需求 JSON → 节点规格 + 学习指南。

流程与原 DSL 一致：
LLM(架构师, 产出规划 JSON) → Script_NdeX(提取 node_list 标记块 + 检索关键词)
  → Plugin(知识库检索, top_k=5) → LLM(学习辅导员, 产出学习指南 Markdown)
输出：{valid, plan, node_list, guide_md, error}
"""
import json
import re

from . import llm
from .kb import get_retriever
from .prompts import SYS_ARCHITECT, SYS_GUIDE
from .wf_need import extract_json


def _slim(n):
    """与原 Script_NdeX._slim 一致：节点规格瘦身。"""
    if not isinstance(n, dict):
        return None
    return {
        "node_name": n.get("node_name", ""),
        "class_name": n.get("class_name", ""),
        "file_name": n.get("file_name", ""),
        "what_to_do": n.get("function", ""),
        "publish_topics": n.get("publish_topics", []),
        "subscribe_topics": n.get("subscribe_topics", []),
    }


def run(cfg, requirement_json, leader_hint=""):
    user_msg = "%s\n%s" % (leader_hint or "", requirement_json)
    try:
        plan_raw = llm.chat(cfg, "plan_architect", SYS_ARCHITECT, user_msg)
    except Exception as e:
        return {"valid": False, "error": "项目规划模型调用失败: %s" % e}

    plan = extract_json(plan_raw)
    if plan is None or not isinstance(plan.get("nodes"), list) or not plan["nodes"]:
        # 重试 1 次（原工作流 LLM 有 retryEnabled）
        try:
            plan_raw = llm.chat(cfg, "plan_architect", SYS_ARCHITECT, user_msg)
            plan = extract_json(plan_raw)
        except Exception:
            plan = None
    if plan is None or not isinstance(plan, dict):
        return {"valid": False, "error": "规划 JSON 解析失败"}

    nodes = plan.get("nodes", []) or []
    slim = [s for s in (_slim(n) for n in nodes) if s]

    # 检索关键词（原 Script_NdeX 逻辑）
    kws = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        for k in (n.get("search_keywords") or []):
            k = str(k).strip()
            if k and k not in kws:
                kws.append(k)
    if not kws:
        for s in slim:
            if s["node_name"]:
                kws.append(s["node_name"])
            for t in list(s["publish_topics"]) + list(s["subscribe_topics"]):
                if isinstance(t, dict) and t.get("topic"):
                    kws.append(str(t["topic"]).lstrip("/"))
    keywords = " ".join(kws[:40])

    # 知识库检索（原 Plugin_pp04：question=search_keywords, top_k=5）
    kbcfg = cfg.get("kb", {})
    context, source_count = get_retriever().context(
        keywords, top_k=kbcfg.get("top_k", 5),
        max_chars=kbcfg.get("max_context_chars", 4500))

    # 学习指南（原 LLM_0czX）
    guide_user = "【项目规划 JSON】\n%s\n\n【知识库检索结果（教学相关，共%d条）】\n%s" % (
        json.dumps(plan, ensure_ascii=False), source_count, context or "（空）")
    try:
        guide_md = llm.chat(cfg, "plan_guide", SYS_GUIDE, guide_user)
    except Exception:
        guide_md = ""

    return {
        "valid": True,
        "plan": plan,
        "node_list": slim,
        "guide_md": guide_md,
    }


# ============ 规划书渲染（主调度员「规划书渲染模板」） ============

def render_plan_md(plan):
    """从 plan 字段渲染 Markdown 规划书，不自编内容。"""
    p = plan.get("project", {}) or {}
    lines = []
    lines.append("## 📋 项目规划书")
    lines.append("")
    lines.append("### 项目信息")
    lines.append("- 项目英文名：%s" % p.get("name_en", "-"))
    lines.append("- 项目中文名：%s" % p.get("name_cn", "-"))
    lines.append("- 包名：%s" % p.get("package_name", "-"))
    lines.append("- 一句话描述：%s" % p.get("description", "-"))
    lines.append("- 难度：%s" % p.get("difficulty", "-"))
    lines.append("")
    lines.append("### 节点列表")
    for i, n in enumerate(plan.get("nodes", []) or [], 1):
        lines.append("")
        lines.append("#### 节点%d：%s ← 类名 %s / 文件 %s / 功能 %s" % (
            i, n.get("node_name", "-"), n.get("class_name", "-"),
            n.get("file_name", "-"), n.get("function", "-")))
        pubs = n.get("publish_topics", []) or []
        subs = n.get("subscribe_topics", []) or []
        pub_s = "、".join("%s（%s）" % (t.get("topic", "?"), t.get("message_type", "?"))
                          for t in pubs) if pubs else "无"
        sub_s = "、".join("%s（%s）" % (t.get("topic", "?"), t.get("message_type", "?"))
                          for t in subs) if subs else "无"
        lines.append("- 发布话题：%s" % pub_s)
        lines.append("- 订阅话题：%s" % sub_s)
        lines.append("- 核心逻辑：%s" % n.get("core_logic", "-"))
    lines.append("")
    lines.append("### 话题总览")
    lines.append("")
    lines.append("| 话题名 | 消息类型 | 发布者 | 订阅者 | 作用 |")
    lines.append("|---|---|---|---|---|")
    for t in plan.get("topic_overview", []) or []:
        lines.append("| %s | %s | %s | %s | %s |" % (
            t.get("topic", "-"), t.get("message_type", "-"),
            t.get("publisher", "-"), t.get("subscriber", "-"), t.get("purpose", "-")))
    lines.append("")
    lines.append("### 依赖包")
    lines.append("、".join(plan.get("dependencies", []) or []) or "-")
    lines.append("")
    lines.append("### 项目文件清单")
    lines.append("")
    lines.append("```")
    for f in plan.get("file_structure", []) or []:
        lines.append(f)
    lines.append("```")
    return "\n".join(lines)
