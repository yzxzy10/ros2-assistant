# -*- coding: utf-8 -*-
"""ROS2 项目通信图谱解析（纯标准库，AST 驱动）。

为什么不用正则：节点的名字、话题名、消息类型都写在调用参数里，正则只能
抓个大概；一旦出现 f-string、变量传参、多个 Node 子类挤在一个文件里，
正则就会张冠李戴。AST 能准确回答「哪个类、哪一行、发布了什么类型」。

解析口径（宁可标"未解析"，也不猜）：
- 节点：继承 rclpy Node 的类；文件名兜底（模块级 rclpy 调用）。
- 节点名：super().__init__("名字") 的第一个字符串字面量。
- 接口：create_publisher / create_subscription / create_service /
        create_client / ActionClient / ActionServer。
- 消息类型：能顺着 import 解析成 std_msgs/msg/String 就解析，否则保留原文。
- 话题名：字面量才认；f-string、变量、拼接一律 resolved=False。
- 连线：同名话题下，每个 publisher 指向每个 subscriber（有向边）。
  动态话题名连不上，单独记在 unresolved 里，由图上的虚线提示用户补。
"""
import ast
import os
import re
import time

# 这些目录里的东西不是源码（colcon 产物、缓存、编辑器配置），一律跳过。
SKIP_DIRS = {
    "build", "install", "log", ".git", "__pycache__", "node_modules",
    ".venv", "venv", "env", ".mypy_cache", ".pytest_cache", ".idea",
    ".vscode", "dist", "egg-info",
}

_INTERFACE_KINDS = {
    "create_publisher": ("publishes", "topic"),
    "create_subscription": ("subscribes", "topic"),
    "create_service": ("service_servers", "service"),
    "create_client": ("service_clients", "service"),
}

# 常见消息包，用于把 `from std_msgs.msg import String` 反推成标准写法
_KNOWN_PKGS = {
    "std_msgs", "sensor_msgs", "geometry_msgs", "nav_msgs", "nav2_msgs",
    "action_msgs", "builtin_interfaces", "tf2_msgs", "visualization_msgs",
    "lifecycle_msgs", "rcl_interfaces", "trajectory_msgs", "shape_msgs",
    "diagnostic_msgs", "std_srvs", "example_interfaces", "unique_identifier_msgs",
}


def _unparse(node):
    try:
        return ast.unparse(node)
    except Exception:            # noqa: BLE001
        return ""


def _const_str(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def infer_package(rel):
    """从相对路径推断包名：src/<pkg>/... 或 <pkg>/...。"""
    parts = [p for p in (rel or "").replace("\\", "/").split("/") if p]
    if len(parts) >= 3 and parts[0] == "src":
        return parts[1]
    if len(parts) >= 2 and parts[0] != "src":
        return parts[0]
    return ""


class _Module(object):
    """一个 .py 文件的解析结果。"""

    def __init__(self, rel, src, package):
        self.rel = rel
        self.src = src
        self.package = package
        self.imports = {}        # 本地名 -> 全名（std_msgs.msg.String）
        self.error = ""
        self.tree = None

    def collect_imports(self):
        for node in ast.walk(self.tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                for a in node.names:
                    if a.name == "*":
                        continue
                    self.imports[a.asname or a.name] = node.module + "." + a.name
            elif isinstance(node, ast.Import):
                for a in node.names:
                    local = a.asname or a.name.split(".")[0]
                    self.imports[local] = a.name

    def resolve_type(self, node):
        """把类型参数解析成 `std_msgs/msg/String` 这种标准写法。"""
        raw = _unparse(node).strip()
        if not raw:
            return "", ""
        if isinstance(node, ast.Name):
            full = self.imports.get(node.id, "")
            if full:
                return _normalize_type(full), raw
        if isinstance(node, ast.Attribute):
            chain = _attr_chain(node)
            if chain:
                head = chain.split(".")[0]
                if head in self.imports:        # std_msgs -> std_msgs (Import)
                    pass
                return _normalize_type(chain), raw
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return _normalize_type(node.value), raw
        return raw, raw


def _attr_chain(node):
    parts = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return ""


def _normalize_type(name):
    """std_msgs.msg.String -> std_msgs/msg/String；String -> String。"""
    name = (name or "").strip().strip("'\"")
    if not name:
        return ""
    if "/" in name and "msg" not in name:
        return name
    parts = name.split(".")
    if len(parts) == 3 and parts[1] in ("msg", "srv", "action"):
        return "%s/%s/%s" % (parts[0], parts[1], parts[2])
    if len(parts) == 2 and parts[0] in _KNOWN_PKGS:
        return "%s/msg/%s" % (parts[0], parts[1])
    return parts[-1]


def _call_name(call):
    """取被调用函数的名字：self.create_publisher(...) -> create_publisher。"""
    f = call.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


def _arg(call, idx, keyword):
    if len(call.args) > idx:
        return call.args[idx]
    for kw in call.keywords:
        if kw.arg == keyword:
            return kw.value
    return None


def _node_name_from_class(cls):
    """从 __init__ 里的 super().__init__("名字") 取节点名。"""
    for item in cls.body:
        if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if item.name != "__init__":
            continue
        for node in ast.walk(item):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            is_super = (isinstance(fn, ast.Attribute) and fn.attr == "__init__"
                        and (isinstance(fn.value, ast.Call)
                             and getattr(fn.value.func, "id", "") == "super"))
            is_base = (isinstance(fn, ast.Attribute) and fn.attr == "__init__"
                       and getattr(fn.value, "id", "") in ("Node", "LifecycleNode"))
            if not (is_super or is_base):
                continue
            for kw in node.keywords:
                if kw.arg in ("node_name", "name"):
                    v = _const_str(kw.value)
                    if v:
                        return v
            if node.args:
                start = 0
                for a in node.args:
                    if isinstance(a, ast.Name) and a.id == "self":
                        continue
                    v = _const_str(node.args[start])
                    if v:
                        return v
                    start += 1
    return ""


def _interface_entry(mod, call, kind_field, name_field):
    """把一次 create_* 调用拆成 {topic,type,raw,resolved,line}。"""
    if kind_field == "publishes" or kind_field == "subscribes":
        type_node = _arg(call, 0, "msg_type")
        name_node = _arg(call, 1, name_field)
    elif kind_field == "service_servers":
        type_node = _arg(call, 0, "srv_type")
        name_node = _arg(call, 1, "srv_name")
    else:
        type_node = _arg(call, 0, "srv_type")
        name_node = _arg(call, 1, "srv_name")

    mtype, raw_type = ("", "")
    if type_node is not None:
        mtype, raw_type = mod.resolve_type(type_node)

    literal = _const_str(name_node) if name_node is not None else None
    if literal is not None:
        name, resolved = literal, True
    else:
        name, resolved = _unparse(name_node).strip(), False
    if not name:
        return None
    return {
        "topic": name,
        "type": mtype,
        "raw_type": raw_type,
        "resolved": resolved,
        "line": getattr(call, "lineno", 0),
    }


def parse_python(rel, src, package=""):
    """解析单个 .py，返回 {nodes, error, package, rel}。"""
    mod = _Module(rel, src, package or infer_package(rel))
    try:
        mod.tree = ast.parse(src)
    except SyntaxError as e:
        return {"path": rel, "package": mod.package, "nodes": [],
                "error": "第 %s 行语法错误：%s" % (e.lineno or "?", e.msg)}
    except Exception as e:       # noqa: BLE001
        return {"path": rel, "package": mod.package, "nodes": [],
                "error": "无法解析：%r" % (e,)}

    mod.collect_imports()
    nodes = []
    classes = [n for n in ast.walk(mod.tree) if isinstance(n, ast.ClassDef)
               and _is_node_class(n)]

    for cls in classes:
        rec = _node_from_class(mod, cls)
        if rec:
            nodes.append(rec)

    if not nodes and _has_rclpy(mod):
        rec = _procedural_node(mod)
        if rec:
            nodes.append(rec)

    return {"path": rel, "package": mod.package, "nodes": nodes,
            "error": "", "lines": src.count("\n") + 1}


def _is_node_class(cls):
    for base in cls.bases:
        name = ""
        if isinstance(base, ast.Name):
            name = base.id
        elif isinstance(base, ast.Attribute):
            name = base.attr
        if name.endswith("Node"):
            return True
    return False


def _has_rclpy(mod):
    text = mod.src
    return "rclpy" in text


def _node_from_class(mod, cls):
    name = _node_name_from_class(cls) or _snake(cls.name)
    rec = {
        "id": "%s::%s" % (mod.rel, cls.name),
        "name": name,
        "cls": cls.name,
        "file": mod.rel,
        "package": mod.package,
        "line": cls.lineno,
        "loc": 0,
        "publishes": [],
        "subscribes": [],
        "service_servers": [],
        "service_clients": [],
        "actions": [],
        "params": [],
        "has_timer": False,
        "status": "ok",
    }
    for node in ast.walk(cls):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            rec["loc"] += getattr(node, "end_lineno", node.lineno) - node.lineno or 0
        if not isinstance(node, ast.Call):
            continue
        fn = _call_name(node)
        if fn in _INTERFACE_KINDS:
            field, name_kw = _INTERFACE_KINDS[fn]
            entry = _interface_entry(mod, node, field, name_kw)
            if entry:
                rec[field].append(entry)
        elif fn in ("ActionClient", "ActionServer"):
            type_node = _arg(node, 1, "action_type")
            name_node = _arg(node, 2, "action_name")
            mtype, raw_type = mod.resolve_type(type_node) if type_node is not None else ("", "")
            literal = _const_str(name_node) if name_node is not None else None
            rec["actions"].append({
                "topic": literal if literal is not None else _unparse(name_node),
                "type": mtype, "raw_type": raw_type,
                "resolved": literal is not None,
                "role": "server" if fn == "ActionServer" else "client",
                "line": getattr(node, "lineno", 0),
            })
        elif fn == "declare_parameter":
            pname = _const_str(_arg(node, 0, "name"))
            default = _arg(node, 1, "value")
            if pname:
                rec["params"].append({"name": pname,
                                      "default": _unparse(default) if default is not None else ""})
        elif fn == "create_timer":
            rec["has_timer"] = True
    return rec


def _procedural_node(mod):
    """模块级写法：rclpy.init(); node = rclpy.create_node('x') 或 Node('x')。"""
    name = ""
    for node in ast.walk(mod.tree):
        if isinstance(node, ast.Call) and _call_name(node) == "create_node":
            name = _const_str(_arg(node, 0, "name")) or ""
            break
    if not name:
        return None
    rec = {
        "id": "%s::__module__" % mod.rel,
        "name": name, "cls": "",
        "file": mod.rel, "package": mod.package,
        "line": 1, "loc": mod.src.count("\n") + 1,
        "publishes": [], "subscribes": [],
        "service_servers": [], "service_clients": [],
        "actions": [], "params": [], "has_timer": False, "status": "ok",
    }
    for node in ast.walk(mod.tree):
        if isinstance(node, ast.Call) and _call_name(node) in _INTERFACE_KINDS:
            fn = _call_name(node)
            field, name_kw = _INTERFACE_KINDS[fn]
            entry = _interface_entry(mod, node, field, name_kw)
            if entry:
                rec[field].append(entry)
    return rec


def _snake(name):
    s = re.sub(r"(?<!^)(?=[A-Z])", "_", name or "").lower()
    return s or "node"


def iter_py_files(root):
    """遍历工作区里的 .py（跳过 build/install/log 等），返回相对路径。"""
    out = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in files:
            if fn.endswith(".py"):
                rel = os.path.relpath(os.path.join(dirpath, fn), root)
                out.append(rel.replace("\\", "/"))
    return sorted(out)


def analyze(root):
    """解析整个工作区，产出节点表 / 连线 / 话题表。"""
    nodes, packages, warnings, files = [], {}, [], []
    for rel in iter_py_files(root):
        try:
            with open(os.path.join(root, rel), "r", encoding="utf-8",
                      errors="replace") as f:
                src = f.read()
        except OSError:
            continue
        res = parse_python(rel, src)
        files.append({"path": rel, "nodes": len(res["nodes"]),
                      "error": res.get("error", "")})
        if res.get("error"):
            warnings.append("%s %s" % (rel, res["error"]))
        if res["nodes"]:
            pkg = res["package"] or ""
            packages.setdefault(pkg, {"name": pkg, "files": 0, "nodes": 0})
            packages[pkg]["files"] += 1
            packages[pkg]["nodes"] += len(res["nodes"])
            nodes.extend(res["nodes"])

    topics = {}
    for n in nodes:
        for field, role in (("publishes", "pub"), ("subscribes", "sub")):
            for it in n[field]:
                if not it["resolved"]:
                    continue
                rec = topics.setdefault(it["topic"], {
                    "name": it["topic"], "types": [], "pubs": [], "subs": []})
                if it["type"] and it["type"] not in rec["types"]:
                    rec["types"].append(it["type"])
                bucket = rec["pubs"] if role == "pub" else rec["subs"]
                if n["id"] not in bucket:
                    bucket.append(n["id"])

    edges, seen = [], set()
    for name, t in sorted(topics.items()):
        if len(t["types"]) > 1:
            warnings.append("话题 %s 被赋了多种类型（%s），真实 ROS 会报类型冲突"
                            % (name, " / ".join(t["types"])))
        for p in t["pubs"]:
            for s in t["subs"]:
                if p == s:
                    continue
                key = (p, s, name)
                if key in seen:
                    continue
                seen.add(key)
                edges.append({"from": p, "to": s, "topic": name,
                              "type": t["types"][0] if t["types"] else ""})

    connected = set()
    for e in edges:
        connected.add(e["from"])
        connected.add(e["to"])
    for n in nodes:
        n["orphan"] = n["id"] not in connected
        n["unresolved_count"] = sum(
            1 for f in ("publishes", "subscribes", "service_servers",
                        "service_clients", "actions")
            for it in n[f] if not it.get("resolved", True))

    unresolved = sum(n["unresolved_count"] for n in nodes)
    if unresolved:
        warnings.append("有 %d 处接口名不是字面量（f-string/变量），图上无法连线，"
                        "请在节点详情里手工补话题名" % unresolved)

    return {
        "ok": True,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "nodes": nodes,
        "edges": edges,
        "topics": sorted(topics.values(), key=lambda t: t["name"]),
        "packages": sorted(packages.values(), key=lambda p: p["name"]),
        "files": files,
        "warnings": warnings,
        "stats": {
            "nodes": len(nodes),
            "edges": len(edges),
            "topics": len(topics),
            "orphans": sum(1 for n in nodes if n["orphan"]),
            "unresolved": unresolved,
            "files": len(files),
        },
    }


def scan_files(files):
    """给 project_import 用：不解磁盘，直接扫 [{path, content}] 列表。"""
    nodes = []
    for f in files:
        rel = f.get("path") or ""
        content = f.get("content") or ""
        if not rel.endswith(".py"):
            continue
        res = parse_python(rel, content)
        nodes.extend(res["nodes"])
    return nodes


def node_facts(node):
    """给详情面板的"硬信息"（不花钱、瞬时出）。"""
    return {
        "publishes": len(node.get("publishes") or []),
        "subscribes": len(node.get("subscribes") or []),
        "services": len(node.get("service_servers") or []) + len(node.get("service_clients") or []),
        "actions": len(node.get("actions") or []),
        "params": len(node.get("params") or []),
        "loc": node.get("loc") or 0,
    }
