import json
import re

TOPIC_RE = re.compile(r"(?<![\w/])/[A-Za-z_][A-Za-z0-9_]*")

# 外部话题白名单：传感器/执行器等不由本项目模块生产的话题
EXTERNAL_TOPICS = {
    "/scan", "/cmd_vel", "/odom", "/map", "/tf", "/tf_static", "/clock",
    "/image_raw", "/camera/image_raw", "/imu", "/joint_states"
}


def extract_json(text):
    """从可能带 markdown 围栏或解释文字的文本中提取首个 JSON 对象"""
    if not text:
        return None, "模型输出为空"
    t = text.strip()
    # 去除 ```json ... ``` 围栏
    if t.startswith("```"):
        parts = t.split("```")
        if len(parts) >= 3:
            t = parts[1]
        if t.lstrip()[:4].lower() == "json":
            t = t.lstrip()[4:]
    left, right = t.find("{"), t.rfind("}")
    if left == -1 or right == -1 or right <= left:
        return None, "输出中未找到 JSON 对象"
    try:
        return json.loads(t[left:right + 1]), ""
    except Exception as e:
        return None, "JSON 解析失败: %s" % str(e)


def validate(data):
    """结构校验 + 话题闭环检查，返回错误信息列表"""
    errors = []
    if not isinstance(data, dict):
        return ["根节点必须是 JSON 对象"]

    # need_ask 必须是布尔值
    if not isinstance(data.get("need_ask"), bool):
        errors.append("need_ask 必须为布尔值 true/false")

    # 追问场景：ask_question 必填，且必须带至少 2 个可点选选项（A/B/C/D 开头）
    if data.get("need_ask") is True:
        q = str(data.get("ask_question", "")).strip()
        if not q:
            errors.append("need_ask=true 时 ask_question 必填")
            return errors
        opts = re.findall(r"(?:^|[\s，,（(])([A-D])[.．、)）]", q)
        if len(opts) < 2:
            errors.append("need_ask=true 时 ask_question 必须含至少 2 个选项"
                          "（A/B/C/D 开头，如 \"A. xxx\\nB. yyy\"）")
        return errors

    # 正常场景：project 校验
    project = data.get("project")
    if not isinstance(project, dict):
        errors.append("project 缺失或不是对象")
    else:
        for field in ("name_en", "name_cn", "package_name", "difficulty", "description"):
            if not str(project.get(field, "")).strip():
                errors.append("project.%s 缺失" % field)

    # modules 校验
    modules = data.get("modules")
    if not isinstance(modules, list) or len(modules) == 0:
        errors.append("modules 必须为非空数组")
        return errors

    produced = set()
    for i, module in enumerate(modules):
        prefix = "modules[%d]" % i
        if not isinstance(module, dict):
            errors.append(prefix + " 必须是对象")
            continue
        if not str(module.get("module_name", "")).strip():
            errors.append(prefix + ".module_name 缺失")
        if not str(module.get("function", "")).strip():
            errors.append(prefix + ".function 缺失")
        if not isinstance(module.get("inputs"), list):
            errors.append(prefix + ".inputs 必须是数组")
        if not isinstance(module.get("outputs"), list):
            errors.append(prefix + ".outputs 必须是数组")
        for out in module.get("outputs", []):
            if isinstance(out, dict) and out.get("topic"):
                produced.add(out["topic"])

    # 话题闭环：内部输入话题必须有生产模块
    for i, module in enumerate(modules):
        if not isinstance(module, dict):
            continue
        prefix = "modules[%d]" % i
        name = str(module.get("module_name", "")).strip() or prefix
        inputs = module.get("inputs", []) or []
        outputs = module.get("outputs", []) or []

        for inp in inputs:
            if not isinstance(inp, dict):
                continue
            topic = inp.get("topic", "")
            if topic and topic not in produced and topic not in EXTERNAL_TOPICS:
                errors.append("话题 %s 没有生产模块（且不在外部话题白名单）" % topic)

        # 交叉校验：禁止通过删除字段来通过校验
        declared = set()
        for item in list(inputs) + list(outputs):
            if isinstance(item, dict) and item.get("topic"):
                declared.add(item["topic"])
        desc = "%s %s" % (module.get("function", ""), module.get("description", ""))
        for topic in sorted(set(TOPIC_RE.findall(desc))):
            if topic not in declared:
                errors.append(
                    "模块 %s 的 function 提到话题 %s，但 inputs/outputs 里没有声明（禁止用删除字段的方式通过校验；请补齐该话题的声明，并为模块间话题补齐生产者模块）"
                    % (name, topic)
                )
        if "订阅" in str(module.get("function", "")) and not inputs:
            errors.append(
                "模块 %s 的 function 写明「订阅」但 inputs 为空（禁止删除订阅声明来通过校验；请把被订阅话题补回 inputs，并为模块间话题补一个生产者模块）"
                % name
            )

    return errors


def _pick(name, args, kwargs):
    """从多来源取入参：kwargs > 位置参数字典 > 模块全局变量（百炼把入参注入为模块级变量）"""
    if name in kwargs and kwargs[name] is not None:
        return kwargs[name]
    for a in args:
        if isinstance(a, dict):
            if name in a and not isinstance(a[name], dict):
                return a[name]
            for holder in ("params", "inputs", "variables"):
                inner = a.get(holder)
                if isinstance(inner, dict) and name in inner:
                    return inner[name]
    g = globals()
    if name in g:
        return g[name]
    if isinstance(g.get("params"), dict) and name in g["params"]:
        return g["params"][name]
    return None


def main(*args, **kwargs):
    # 百炼脚本节点入口：平台以「无参」调用 main()，入参以模块全局变量注入
    raw = _pick("llm_output", args, kwargs) or ""
    raw = raw if isinstance(raw, str) else str(raw)
    raw = raw.strip()

    data, parse_err = extract_json(raw)
    error_msg = parse_err
    if data is not None:
        error_list = validate(data)
        error_msg = "；".join(error_list)

    # 注意：passed 是 Python 布尔值 True/False，下游按布尔比较
    passed = data is not None and error_msg == ""

    if passed:
        envelope = json.dumps({
            "valid": True,
            "need_ask": data.get("need_ask", False),
            "ask_question": data.get("ask_question", ""),
            "data": data
        }, ensure_ascii=False)
    else:
        # 脚本1 失败时不产出信封，交由修复循环处理
        envelope = ""

    return {
        "passed": passed,
        "envelope": envelope,
        "raw": raw,
        "error_msg": error_msg
    }