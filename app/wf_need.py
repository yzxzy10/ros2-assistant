# -*- coding: utf-8 -*-
"""工作流①「需求分析」：学生需求 → 校验 → 失败走修复循环 → 信封 JSON。

流程与原 DSL 一致：
LLM(需求分析师) → 脚本校验(Script_rtRL, 原样复用) → Judge
  passed  → 直通信封
  failed  → Iterator(修复器 LLM → 再校验, 最多 1 轮) → 组装信封
"""
import json
import os
import io

from . import llm

_HERE = os.path.dirname(os.path.abspath(__file__))
_REQ_CHECKER_PATH = os.path.join(_HERE, "checker_requirement.py")


def _load_checker():
    """加载原样抽取的校验脚本，返回其 main(llm_output=...) 函数。"""
    src = io.open(_REQ_CHECKER_PATH, "r", encoding="utf-8").read()
    ns = {}
    exec(compile(src, "checker_requirement.py", "exec"), ns)  # noqa: S102
    return ns["main"]


_check = None


def checker(llm_output):
    global _check
    if _check is None:
        _check = _load_checker()
    return _check(llm_output=str(llm_output or ""))


def extract_json(text):
    """从可能带围栏/解释文字的输出中提取首个 JSON 对象。"""
    if not text:
        return None
    t = text.strip()
    if t.startswith("```"):
        parts = t.split("```")
        if len(parts) >= 3:
            t = parts[1]
        if t.lstrip()[:4].lower() == "json":
            t = t.lstrip()[4:]
    left, right = t.find("{"), t.rfind("}")
    if left == -1 or right == -1 or right <= left:
        return None
    try:
        return json.loads(t[left:right + 1])
    except Exception:
        return None


def run(cfg, query, leader_context="", history="", level_hint="beginner",
        profile=""):
    """执行需求分析流水线，返回信封 dict：{valid, need_ask, ask_question, data}。

    与原脚本 Ijod 输出一致（信封 JSON 字符串的解析态）。
    任何失败时 valid=false。
    profile：跨会话学习画像，让分析师知道学生已经做过什么、别重复问。
    """
    from . import prompts

    parts = ["【学生原始需求】%s" % query]
    if profile:
        parts.append(profile)
    parts.append("【Leader 初步拆解】%s" % (leader_context or ""))
    parts.append("【历史补充】%s" % (history or ""))
    user_msg = "\n\n".join(parts)
    sys_prompt = prompts.SYS_NEED_ANALYST.replace("{level_hint}", level_hint)

    try:
        raw = llm.chat(cfg, "analysis", sys_prompt, user_msg)
    except Exception:
        return {"valid": False, "need_ask": False, "ask_question": "", "data": None,
                "error": "需求分析模型调用失败"}

    res = checker(raw)
    if not res.get("passed"):
        # 修复循环（原 Iterator 1 轮）
        repair_user = (
            "【学生原始需求】\n%s\n\n【Leader 初步拆解】\n%s\n\n【历史补充】\n%s\n\n"
            "【上次的错误输出（原文）】\n%s\n\n【校验错误信息】\n%s"
            % (query, leader_context or "", history or "",
               res.get("raw", ""), res.get("error_msg", "")))
        try:
            fixed = llm.chat(cfg, "analysis_repair", prompts.SYS_NEED_REPAIR, repair_user)
        except Exception:
            fixed = ""
        if fixed:
            res = checker(fixed)

    if not res.get("passed"):
        return {"valid": False, "need_ask": False, "ask_question": "", "data": None,
                "error": res.get("error_msg", "校验未通过")}

    data = extract_json(res.get("raw", ""))
    if data is None:
        return {"valid": False, "need_ask": False, "ask_question": "", "data": None,
                "error": "信封数据解析失败"}

    return {
        "valid": True,
        "need_ask": bool(data.get("need_ask")),
        "ask_question": str(data.get("ask_question", "")),
        "data": data,
    }
