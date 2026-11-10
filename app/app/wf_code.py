# -*- coding: utf-8 -*-
"""工作流③「ros2代码生成」：单节点规格 → 代码 + AST 检查 + 修复循环 + 教学。

流程与原 DSL 一致：
LLM(节点需求分析员) → Plugin(知识库检索, top_k=5) → LLM(代码生成)
  → Script_B9sf(AST 检查, 原样复用) → Judge
    passed → LLM(教学) → 信封 A
    failed → Iterator(修复 LLM → 复检, 最多 3 轮) → LLM(教学) → 信封 B
信封字段与原脚本 EnvA/EnvB/9tSI 完全一致。
"""
import io
import json
import os
import re

from . import llm
from . import sandbox
from . import progress
from . import versions
from .kb import get_retriever
from .prompts import (SYS_NODE_SPEC, SYS_CODER, SYS_CODE_FIXER, SYS_TEACHING,
                      versioned)
from .wf_need import extract_json

_HERE = os.path.dirname(os.path.abspath(__file__))
_CODE_CHECKER_PATH = os.path.join(_HERE, "checker_code.py")

_check_main = None


def _load_code_checker():
    global _check_main
    if _check_main is None:
        src = io.open(_CODE_CHECKER_PATH, "r", encoding="utf-8").read()
        ns = {}
        exec(compile(src, "checker_code.py", "exec"), ns)  # noqa: S102
        _check_main = ns["main"]
    return _check_main


def check_code(code, spec):
    return _load_code_checker()(code=str(code or ""), spec=spec)


def strip_fence(code):
    if not isinstance(code, str):
        return ""
    t = code.strip()
    if t.startswith("```"):
        parts = t.split("```")
        if len(parts) >= 3:
            body = parts[1]
            for lang in ("python", "Python", "py"):
                if body.lstrip().lower().startswith(lang):
                    body = body.lstrip()[len(lang):]
                    break
            return body.strip()
    return t


def spec_search_text(spec):
    """把节点规格展开成检索问句（对应原 Plugin 的 question 入参）。"""
    parts = [spec.get("what_to_do", ""), spec.get("node_name", ""),
             spec.get("class_name", "")]
    sk = spec.get("search_keywords") or {}
    if isinstance(sk, dict):
        for v in sk.values():
            if isinstance(v, list):
                parts.extend(str(x) for x in v)
            else:
                parts.append(str(v))
    for t in list(spec.get("publish_topics") or []) + list(spec.get("subscribe_topics") or []):
        if isinstance(t, dict):
            parts.append("%s %s %s" % (t.get("name") or t.get("topic", ""),
                                       t.get("msg_type") or t.get("message_type", ""),
                                       t.get("description", "")))
    return " ".join(p for p in parts if p)


def run(cfg, node_spec_input):
    """node_spec_input：节点规格 JSON 字符串（情况A）或自然语言（情况B）。"""
    kbcfg = cfg.get("kb", {})

    # 1. 节点需求分析员 → 标准化规格 JSON
    try:
        spec_raw = llm.chat(cfg, "node_spec", versioned(SYS_NODE_SPEC, cfg),
                            str(node_spec_input or ""))
    except Exception as e:
        return {"valid": False, "error": "节点规格模型调用失败: %s" % e}
    spec = extract_json(spec_raw)
    if spec is None:
        try:
            spec_raw = llm.chat(cfg, "node_spec", versioned(SYS_NODE_SPEC, cfg),
                                str(node_spec_input or ""))
            spec = extract_json(spec_raw)
        except Exception:
            spec = None
    if spec is None:
        return {"valid": False, "error": "节点规格 JSON 解析失败"}
    spec.setdefault("node_name", "")
    spec.setdefault("class_name", "")
    spec.setdefault("file_name", (spec.get("node_name") or "node") + ".py")
    if not spec.get("file_name"):
        spec["file_name"] = (spec.get("node_name") or "node") + ".py"

    # 2. 知识库检索
    context, source_count = get_retriever().context(
        spec_search_text(spec), top_k=kbcfg.get("top_k", 5),
        max_chars=kbcfg.get("max_context_chars", 4500))

    # 3. 代码生成
    code_user = "【知识库参考资料】\n%s\n\n【结构化需求】\n%s" % (
        context or "（空）", json.dumps(spec, ensure_ascii=False))
    try:
        code_raw = llm.chat(cfg, "coder", versioned(SYS_CODER, cfg), code_user)
    except Exception as e:
        return {"valid": False, "error": "代码生成模型调用失败: %s" % e}
    code = strip_fence(code_raw)

    # 4. AST 检查（原样复用 Script_B9sf/Y7kF）
    res = check_code(code, json.dumps(spec, ensure_ascii=False))
    passed = bool(res.get("passed"))
    rounds = 0

    # 5. 修复循环（原 Iterator countLimit=3）
    while not passed and rounds < 3:
        rounds += 1
        try:
            result_json = res.get("result", "{}")
            fix_user = ("%s\n\n【原始代码】\n\n%s\n\n\n【需要修复的错误】\n\n%s\n\n"
                        "请输出修正后的完整代码。"
                        % ("请根据错误列表修复以下 ROS2 代码。",
                           code, result_json))
            fixed_raw = llm.chat(cfg, "coder_fix", versioned(SYS_CODE_FIXER, cfg), fix_user)
        except Exception:
            break
        fixed = strip_fence(fixed_raw)
        if not fixed:
            break
        res2 = check_code(fixed, json.dumps(spec, ensure_ascii=False))
        code = fixed
        res = res2
        passed = bool(res.get("passed"))

    # 6. 编译验证（真实 colcon build + 节点启停）
    #    AST 只查得到写法错误，查不到「msg 类型不存在」「setup.py 入口写错」
    #    「节点一起来就崩」。这一步把真实编译器/运行时的报错拿回来，
    #    有预算就回灌给修复器再生成一次。
    def _run_verify(src):
        with progress.step("verify", "编译验证",
                           "在 ROS 2 %s 环境执行 colcon build" % versions.distro_name(cfg)):
            v = sandbox.verify(cfg, src, spec)
            if v.get("attempted"):
                note = "编译%s" % ("通过" if v.get("build_ok", v.get("ok")) else "未通过")
                if v.get("ok"):
                    note = "编译 + 启动均通过 · %s" % (v.get("elapsed") or 0)
                progress.note(note)
            else:
                progress.note("未执行：%s" % (v.get("reason") or "环境不支持"))
            return v

    verify = _run_verify(code) if code else {
        "attempted": False, "ok": False, "backend": "none", "stage": "",
        "distro": versions.distro_name(cfg), "elapsed": 0.0,
        "reason": "代码为空，跳过编译验证"}
    compile_rounds = 0
    fix_budget = int(((cfg.get("ros") or {}).get("verify") or {}).get("fix_rounds") or 0)
    while (verify.get("attempted") and not verify.get("ok")
           and compile_rounds < fix_budget):
        compile_rounds += 1
        try:
            fix_user = (
                "以下 ROS2 代码在 ROS 2 %s 环境里没有通过编译/启动校验，请按真实日志修复。\n\n"
                "【阶段】%s\n【原始代码】\n%s\n\n【编译器/运行时日志】\n%s\n\n"
                "只输出修正后的完整代码，不要解释。"
                % (versions.distro_name(cfg), verify.get("stage") or "build",
                   code, (verify.get("log") or "（空）")[-1800:]))
            fixed_raw = llm.chat(cfg, "coder_fix", versioned(SYS_CODE_FIXER, cfg), fix_user)
        except Exception:
            break
        fixed = strip_fence(fixed_raw)
        if not fixed:
            break
        res = check_code(fixed, json.dumps(spec, ensure_ascii=False))
        passed = bool(res.get("passed"))
        code = fixed
        verify = _run_verify(code)

    # 7. 教学内容
    teach_user = ("【节点规格】\n%s\n\n【待讲解ROS2代码】\n%s\n\n【知识库参考资料】\n%s\n\n"
                  "【目标版本】ROS 2 %s"
                  % (json.dumps(spec, ensure_ascii=False), code, context or "（空）",
                     versions.distro_name(cfg)))
    try:
        teaching = llm.chat(cfg, "teaching", versioned(SYS_TEACHING, cfg), teach_user)
    except Exception:
        teaching = ""

    check_obj = extract_json(res.get("result", "{}")) or {}
    envelope = {
        "schema_version": "1.1",
        "valid": bool(code),
        "node_name": spec.get("node_name", ""),
        "class_name": spec.get("class_name", ""),
        "file_name": spec.get("file_name", ""),
        "code": code,
        "passed": passed,
        "errors": check_obj.get("errors", []),
        "warnings": check_obj.get("warnings", []),
        "summary": res.get("summary", check_obj.get("summary", "")),
        "rounds": rounds,
        "compile_rounds": compile_rounds,
        "verify": verify,
        "distro": versions.distro_name(cfg),
        "teaching": teaching,
    }
    return envelope
