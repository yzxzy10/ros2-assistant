# ROS2 代码检查器（百炼脚本节点版）
# 契约：模块层执行 -> 平台调用 main() -> 返回值即节点输出
# 输入：code（必需） spec（可选）
# 输出：passed / result / summary / error_count / received
#

import ast
import json
import re
_LANGS = ("python3", "python", "py", "javascript", "js", "typescript", "ts",
          "bash", "shell", "sh", "json", "yaml", "yml", "xml", "html",
          "css", "cpp", "c\\+\\+", "c", "java", "go", "rust", "sql",
          "text", "plaintext")
_LANG = re.compile(r"[ \t]*(" + "|".join(_LANGS) + r")[ \t]*\r?\n?", re.I)
_SPECKEYS = ("node_name", "class_name", "publish_topics",
             "subscribe_topics", "code_type")
_ENVKEYS = ("result", "content", "text", "output", "data", "answer",
            "response", "value", "code", "generated_code", "fixed_code",
            "code_text")
def _strip(t):
    """剥离 markdown 围栏。兼容 '```pythonimport x'（围栏与代码粘在一起）。
    做法：剥掉围栏后生成两个候选（去掉语言标记 / 不去掉），
    取第一个能通过 ast.parse 的。这样既不会把 'pythonimport' 吃掉，
    也不会误伤真的以 'python' 开头的代码。
    """
    if not isinstance(t, str):
        return ""
    s = t.strip()
    if not s.startswith("```"):
        return s
    b = s[3:]
    e = b.rfind("```")
    if e != -1:
        b = b[:e]
    m = _LANG.match(b)
    c1 = (b[m.end():] if m else b).strip("\n")
    c2 = b.strip("\n")
    for c in (c1, c2):
        if c.strip():
            try:
                ast.parse(c)
                return c
            except Exception:
                pass
    return c1 if c1.strip() else c2
def _cs(n):
    return n.value if isinstance(n, ast.Constant) and isinstance(n.value, str) else None
def _es(n):
    if isinstance(n, ast.Name):
        return n.id
    if isinstance(n, ast.Attribute):
        b = _es(n.value)
        return (b + "." + n.attr) if b else n.attr
    return ""
def _mc(t, m):
    for n in ast.walk(t):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr == m:
            yield n
def _co(t, base, attr):
    for n in ast.walk(t):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                and n.func.attr == attr:
            if _es(n.func.value).split(".")[-1] == base:
                yield n
def _lj(v):
    if isinstance(v, (dict, list)):
        return v
    if not isinstance(v, str):
        return None
    t = _strip(v).strip()
    if not t:
        return None
    try:
        return json.loads(t)
    except Exception:
        pass
    i, j = t.find("{"), t.rfind("}")
    if i != -1 and j > i:
        try:
            return json.loads(t[i:j + 1])
        except Exception:
            return None
    return None
def _nt(n):
    if n is None:
        return ""
    s = str(n).strip().strip("\"'")
    return ("/" + s.lstrip("/")) if s else ""
def _nty(t):
    if not t:
        return ""
    s = str(t).strip().strip("\"'").replace(".msg.", "/").replace(".", "/")
    p = [x for x in s.split("/") if x and x != "msg"]
    return "/".join(p[-2:]) if len(p) >= 2 else (p[0] if p else "")
def _tm(a, b):
    if not a or not b:
        return True
    if "/" in a and "/" in b:
        return a == b
    return a.split("/")[-1] == b.split("/")[-1]
def _ct(raw):
    out = []
    if not raw:
        return out
    if isinstance(raw, dict):
        raw = [raw]
    if isinstance(raw, str):
        p = _lj(raw)
        raw = p if isinstance(p, (list, dict)) else \
            [x.strip() for x in re.split(r"[,，;；\n]", raw) if x.strip()]
    if not isinstance(raw, list):
        return out
    for it in raw:
        if isinstance(it, str):
            out.append({"name": _nt(it), "type": ""})
        elif isinstance(it, dict):
            out.append({"name": _nt(it.get("name") or it.get("topic") or ""),
                        "type": _nty(it.get("type") or it.get("msg_type") or "")})
    return [x for x in out if x["name"]]
def _spec(s):
    d = _lj(s)
    if not isinstance(d, dict):
        d = {}
    return {"n": str(d.get("node_name") or "").strip(),
            "c": str(d.get("class_name") or "").strip(),
            "p": _ct(d.get("publish_topics")),
            "s": _ct(d.get("subscribe_topics"))}
def _usable(sp):
    return bool(sp["n"] or sp["c"] or sp["p"] or sp["s"])
def check(code, spec=None):
    E, W = [], []
    def err(i, m, l=0):
        E.append({"id": i, "msg": m, "line": l})
    def warn(i, m, l=0):
        W.append({"id": i, "msg": m, "line": l})
    sp = _spec(spec)
    src = _strip(code)
    if not _usable(sp):
        warn("W201", "未接 spec 或解析为空，已跳过类名/节点名/话题一致性校验", 0)
    if not src.strip():
        err("E000", "代码为空：没收到 code，或收到的内容剥离后为空", 0)
        return E, W, sp
    try:
        tree = ast.parse(src)
    except SyntaxError as ex:
        err("E001", "语法错误：第 %s 行 —— %s" % (ex.lineno, ex.msg), ex.lineno or 0)
        return E, W, sp
    names, tmap = set(), {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for a in n.names:
                names.add(a.asname or a.name.split(".")[0])
        elif isinstance(n, ast.ImportFrom):
            mod = n.module or ""
            for a in n.names:
                names.add(a.asname or a.name)
                if mod:
                    tmap[a.asname or a.name] = _nty(mod + "." + a.name)
    if "rclpy" not in names:
        err("E107", "缺少 `import rclpy`（后面 rclpy.init/spin 会 NameError）", 0)
    cls, dotted_ok = [], False
    for c in ast.walk(tree):
        if not isinstance(c, ast.ClassDef):
            continue
        for b in c.bases:
            t = _es(b)
            if t == "Node" or t.endswith(".Node"):
                cls.append(c)
                if t.endswith(".Node"):
                    mod = t[:-5]
                    if mod == "rclpy.node":
                        dotted_ok = True
                    else:
                        err("E116", "基类 `%s` 不存在：Node 在 rclpy.node 里，"
                            "`rclpy` 本身没有 Node 属性（运行时会 AttributeError）。"
                            "正确写法是 `from rclpy.node import Node` 然后用 `Node`"
                            % t, c.lineno)
                break
    if "Node" not in names and not dotted_ok:
        err("E107", "缺少 `from rclpy.node import Node`", 0)
    if not cls:
        err("E101", "没有找到继承自 Node 的节点类", 0)
        mc = None
    else:
        mc = cls[0]
    if mc is not None:
        if sp["c"] and mc.name != sp["c"]:
            err("E108", "类名不一致：需求 `%s` / 代码 `%s`" % (sp["c"], mc.name), mc.lineno)
        im = None
        for it in mc.body:
            if isinstance(it, ast.FunctionDef) and it.name == "__init__":
                im = it
                break
        if im is None:
            err("E105", "节点类缺少 `__init__` 方法", mc.lineno)
        else:
            si = None
            for call in ast.walk(im):
                if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute) \
                        and call.func.attr == "__init__":
                    inner = call.func.value
                    if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name) \
                            and inner.func.id == "super":
                        si = call
                        break
            if si is None:
                err("E102", "`__init__` 中没有调用 `super().__init__(...)`", im.lineno)
            elif si.args:
                nn = _cs(si.args[0]) or ""
                if sp["n"] and nn and nn != sp["n"]:
                    err("E109", "节点名不一致：需求 `%s` / 代码 `%s`" % (sp["n"], nn), si.lineno)
    if not [n for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "main"]:
        err("E103", "缺少 `main` 函数", 0)
    if not any(True for _ in _co(tree, "rclpy", "init")):
        err("E104", "缺少 `rclpy.init()`", 0)
    if not (any(True for _ in _co(tree, "rclpy", "spin")) or
            any(True for _ in _co(tree, "rclpy", "spin_once"))):
        err("E104", "缺少 `rclpy.spin()`，节点不会进入事件循环", 0)
    if not (any(True for _ in _co(tree, "rclpy", "shutdown")) or
            any(True for _ in _mc(tree, "destroy_node"))):
        err("E104", "缺少 `rclpy.shutdown()` 或 `node.destroy_node()`", 0)
    pubs, subs = {}, {}
    for c in _mc(tree, "create_publisher"):
        if len(c.args) != 3:
            err("E112", "`create_publisher` 参数 %d 个，应为 3 个" % len(c.args), c.lineno)
        if len(c.args) > 1 and _cs(c.args[1]):
            raw = _es(c.args[0])
            r = tmap.get(raw) or (_nty(raw) if "." in raw else "")
            pubs[_nt(_cs(c.args[1]))] = {"line": c.lineno, "type": r,
                                         "res": bool(r), "raw": raw}
    for c in _mc(tree, "create_subscription"):
        if len(c.args) != 4:
            err("E113", "`create_subscription` 参数 %d 个，应为 4 个" % len(c.args), c.lineno)
        if len(c.args) > 1 and _cs(c.args[1]):
            raw = _es(c.args[0])
            r = tmap.get(raw) or (_nty(raw) if "." in raw else "")
            subs[_nt(_cs(c.args[1]))] = {"line": c.lineno, "type": r,
                                         "res": bool(r), "raw": raw}
    for tp, info in list(pubs.items()) + list(subs.items()):
        if not info["res"] and info["raw"]:
            err("E114", "话题 `%s` 的消息类型 `%s` 没有 import（运行期 NameError）"
                % (tp, info["raw"]), info["line"])
    for c in _mc(tree, "create_subscription"):
        if len(c.args) <= 2:
            continue
        cb = c.args[2]
        cbn = cb.attr if isinstance(cb, ast.Attribute) else \
            (cb.id if isinstance(cb, ast.Name) else None)
        if not cbn:
            continue
        tg = None
        for fn in ast.walk(tree):
            if isinstance(fn, ast.FunctionDef) and fn.name == cbn:
                tg = fn
                break
        if tg is None:
            err("E106", "回调函数 `%s` 未定义" % cbn, c.lineno)
        elif len(tg.args.args) != 2:
            err("E106", "回调 `%s` 参数 %d 个，应为 2 个 (self, msg)"
                % (cbn, len(tg.args.args)), tg.lineno)
    for w in sp["p"]:
        g = pubs.get(w["name"])
        if g is None:
            err("E110", "缺少发布者：需求要求发布 `%s`" % w["name"], 0)
        elif g["res"] and not _tm(w["type"], g["type"]):
            err("E115", "发布话题 `%s` 类型不一致：需求 `%s` / 代码 `%s`"
                % (w["name"], w["type"], g["type"]), g["line"])
    for w in sp["s"]:
        g = subs.get(w["name"])
        if g is None:
            err("E111", "缺少订阅者：需求要求订阅 `%s`" % w["name"], 0)
        elif g["res"] and not _tm(w["type"], g["type"]):
            err("E115", "订阅话题 `%s` 类型不一致：需求 `%s` / 代码 `%s`"
                % (w["name"], w["type"], g["type"]), g["line"])
    return E, W, sp
def _unwrap(v, d=0):
    if d > 8:
        return v
    if isinstance(v, dict):
        if any(k in v for k in _SPECKEYS):
            return v
        for k in _ENVKEYS:
            if k in v:
                r = _unwrap(v[k], d + 1)
                if r not in (None, "", {}, []):
                    return r
        if len(v) == 1:
            r = _unwrap(list(v.values())[0], d + 1)
            if r not in (None, "", {}, []):
                return r
        return v
    if isinstance(v, list):
        return _unwrap(v[0], d + 1) if len(v) == 1 else v
    if isinstance(v, str):
        s = v.strip()
        if s[:1] in ("{", "["):
            p = _lj(s)
            if isinstance(p, (dict, list)):
                return _unwrap(p, d + 1)
        return v
    return v
def _is_code(s):
    if not isinstance(s, str) or not s.strip():
        return False
    try:
        ast.parse(_strip(s))
    except Exception:
        return False
    return ("import " in s) or ("class " in s) or ("def " in s)
def _find_code(v, d=0):
    if d > 8:
        return ""
    if isinstance(v, str):
        return v if _is_code(v) else ""
    if isinstance(v, (dict, list)):
        best = ""
        for x in (v.values() if isinstance(v, dict) else v):
            s = _find_code(x, d + 1)
            if len(s) > len(best):
                best = s
        return best
    return ""
def _digs(o, d=0):
    """递归产出嵌套结构里的所有 dict（用于把 code+spec 从一个包里取出来）。"""
    if d > 6:
        return
    if isinstance(o, dict):
        yield o
        for v in o.values():
            for x in _digs(v, d + 1):
                yield x
    elif isinstance(o, list):
        for v in o:
            for x in _digs(v, d + 1):
                yield x
def main(*args, **kwargs):
    """百炼脚本节点入口。
    平台执行流程（实测）：
        模块层 exec 本文件  ->  查找 main  ->  调用  ->  返回值即节点输出
    因此这里用 *args, **kwargs 兼容平台可能的传参方式，
    并同时从 globals()/locals() 里兜底取输入变量。
    """
    import json as _json
    import traceback as _traceback
    src = {}
    for _k, _v in kwargs.items():
        src[_k] = _v
    for _a in args:
        if isinstance(_a, dict):
            for _k, _v in _a.items():
                src.setdefault(_k, _v)
    try:
        for _k, _v in globals().items():
            src.setdefault(_k, _v)
    except Exception:
        pass
    try:
        for _k, _v in locals().items():
            src.setdefault(_k, _v)
    except Exception:
        pass
    def _take(names):
        for _n in names:
            _v = src.get(_n)
            if _v is None or callable(_v):
                continue
            if isinstance(_v, (str, bytes, dict, list)) and _v:
                return _v
        return None
    rc = _take(("code", "fixed_code", "generated_code", "code_text", "input"))
    rs = _take(("spec", "requirement", "req_json", "input_json",
                "requirement_json"))
    if rc is None or rs is None:
        for _v in list(src.values()):
            if callable(_v):
                continue
            _p = None
            for _d in _digs(_v):
                if isinstance(_d, dict) and _d.get("code") and _d.get("spec"):
                    _p = _d
                    break
            if _p:
                if rc is None:
                    rc = _p.get("code")
                if rs is None:
                    rs = _p.get("spec")
                break
        if rc is None or rs is None:
            for _v in list(src.values()):
                if callable(_v):
                    continue
                if rc is None:
                    _fc = _find_code(_v)
                    if _fc:
                        rc = _fc
                if rs is None:
                    for _d in _digs(_v):
                        if isinstance(_d, dict) and _usable(_spec(_d)):
                            rs = _d
                            break
    c = _unwrap(rc) if rc is not None else ""
    s = _unwrap(rs) if rs is not None else {}
    if isinstance(c, bytes):
        try:
            c = c.decode("utf-8", "replace")
        except Exception:
            c = ""
    if not isinstance(c, str):
        c = _find_code(c)
    try:
        E, W, sp = check(c, s)
    except Exception as _fatal:
        return {
            "passed": False,
            "result": _json.dumps({
                "passed": False,
                "summary": "检查器内部异常：%r" % (_fatal,),
                "traceback": _traceback.format_exc()[-1200:],
            }, ensure_ascii=False),
            "summary": "检查器内部异常：%r" % (_fatal,),
            "error_count": 1,
            "received": "fatal during check",
        }
    passed = len(E) == 0
    if passed and not W:
        summary = "检查通过：结构、命名、话题一致性全部符合"
    elif passed:
        summary = "检查通过（%d 条建议）：%s" % (
            len(W), "；".join(w["msg"] for w in W[:3]))
    else:
        summary = "发现 %d 个错误：%s" % (
            len(E), "；".join(e["msg"] for e in E[:3]))
    try:
        received = _json.dumps({
            "code_type": type(rc).__name__,
            "code_len": len(c) if isinstance(c, str) else 0,
            "code_head": (c[:40] if isinstance(c, str) else ""),
            "spec_type": type(rs).__name__,
            "spec_ok": _usable(sp),
            "args_n": len(args),
            "kwargs_n": len(kwargs),
            "src_keys": sorted([k for k in src.keys()
                                if not k.startswith("_")])[:25],
        }, ensure_ascii=False)
    except Exception as _e:
        received = "dbg err: %r" % (_e,)
    return {
        "passed": passed,
        "result": _json.dumps({
            "passed": passed, "errors": E, "warnings": W,
            "summary": summary, "error_count": len(E), "spec_used": sp,
        }, ensure_ascii=False),
        "summary": summary,
        "error_count": len(E),
        "received": received,
    }