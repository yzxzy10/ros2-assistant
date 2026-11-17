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
- 话题名：字面量优先；变量顺着赋值找一层；`os.environ['K']` 去项目里的
  .env / *.env 配置表里查真实值。查不到才 resolved=False。
- 连线：同名话题下，每个 publisher 指向每个 subscriber（有向边）。
  动态话题名连不上，单独记在 unresolved 里，由图上的虚线提示用户补。
- 节点来源：ROS2 的 rclpy（AST）与 rclcpp（正则，见下），以及 launch 文件里
  显式声明的「外部节点」（从已安装 ROS 包拉起的，标 external）。ROS1/catkin 的
  rospy 写法不解析 —— 本环境不能编译也不能运行它，画出来只会让人以为能跑。
- C++ 用正则是因为标准库没有 C++ AST；好在 rclcpp 节点写法高度规律
  （继承 rclcpp::Node + 构造初始化列表 `: Node("名")` + create_publisher<类型>）。
"""
import ast
import os
import re
import time

# 这些目录里的东西不是源码（colcon 产物、缓存、编辑器配置），一律跳过。
# 我们自己往工作区根目录放的辅助脚本，不是用户项目的一部分。
# 不排掉的话它们会被当成节点：hop_recorder.py 曾在图上长出一个名叫
# hop_recorder 的假节点，跑验证时 ros2 run '' 报 "Package '' not found"。
SKIP_FILES = {"hop_recorder.py", "hop_probe.py", "run_chain_verify.sh"}

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


def infer_package(rel, index=None):
    """从相对路径推断包名。

    优先按 package.xml 找最近祖先目录：GitHub 上直接 clone 的仓库，很多是
    「仓库根目录就是一个包、节点放在 scripts/ 下」，按目录名推只会得到
    `scripts` 这种假包名。找不到 package.xml 才退回老规矩（src/<pkg>/…）。
    """
    rel = (rel or "").replace("\\", "/")
    if index:
        d = os.path.dirname(rel)
        while True:
            if d in index:
                return index[d]
            if not d:
                break
            d = os.path.dirname(d)
    parts = [p for p in rel.split("/") if p]
    if len(parts) >= 3 and parts[0] == "src":
        return parts[1]
    if len(parts) >= 2 and parts[0] != "src":
        return parts[0]
    return ""


class _Module(object):
    """一个 .py 文件的解析结果。"""

    def __init__(self, rel, src, package, env=None, env_src=None):
        self.rel = rel
        self.src = src
        self.package = package
        self.imports = {}        # 本地名 -> 全名（std_msgs.msg.String）
        self.env = env or {}     # KEY -> 值（来自项目的 .env）
        self.env_src = env_src or {}
        self.values = {}         # 变量名 / self.属性 -> 赋值右值（列表，多值算歧义）
        self.params = {}         # 参数名 -> declare_parameter 的默认值表达式
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

    def collect_values(self):
        """记下「名字 = 右值」，好顺着变量找回话题名。

        `self.topic_x = os.environ['X']` 然后 `create_publisher(T, self.topic_x)`
        是这类项目最常见的写法，不跟进去就永远是「未解析」。
        一个名字可能被赋多次（`TOPIC_X = None` 占位、之后在函数里再赋值），
        所以存成列表，取值时要求所有能解析出来的结果一致，否则按歧义处理。
        """
        for node in ast.walk(self.tree):
            # declare_parameter("topic", "scan") —— 后面 get_parameter 读到的就是它
            if isinstance(node, ast.Call):
                chain = _attr_chain(node.func)
                if chain.split(".")[-1] == "declare_parameter":
                    pname = _const_str(_arg(node, 0, "name"))
                    default = _arg(node, 1, "value")
                    if pname and default is not None and pname not in self.params:
                        self.params[pname] = default
            pairs = []
            if isinstance(node, ast.Assign):
                pairs = [(t, node.value) for t in node.targets]
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                pairs = [(node.target, node.value)]
            for target, value in pairs:
                key = _target_key(target)
                if not key:
                    continue
                bucket = self.values.setdefault(key, [])
                if all(_unparse(v) != _unparse(value) for v in bucket):
                    bucket.append(value)

    def resolve_str(self, node, depth=0):
        """把字符串参数还原成真实值，返回 (值, 依据)。

        依据：literal（代码里写死）/ env（查 .env）/ var（顺着变量找的）/
        param（declare_parameter 的默认值）。
        """
        if node is None:
            return "", ""
        s = _const_str(node)
        if s is not None:
            return s, "literal"
        if depth >= 5:
            return "", ""
        # str(x) / str(self.get_parameter("topic").value) 这类包装先拆一层
        if isinstance(node, ast.Call) and _call_name(node) in ("str", "repr"):
            inner = _arg(node, 0, "object")
            if inner is not None:
                return self.resolve_str(inner, depth + 1)
        pname = _param_ref(node)
        if pname and pname in self.params:
            v, via = self.resolve_str(self.params[pname], depth + 1)
            if v:
                return v, via if via == "env" else "param"
        key = _env_key(node)
        if key:
            if key in self.env:
                return self.env[key], "env"
            d = _env_default(node)      # 有默认值就先用默认值
            if d is not None:
                return d, "literal"
            return "", ""
        tkey = _target_key(node)
        if tkey and tkey in self.values:
            got = {}
            for val in self.values[tkey]:
                v, via = self.resolve_str(val, depth + 1)
                if v:
                    got.setdefault(v, via or "var")
            if len(got) == 1:           # 多处赋值且结果不一致就宁可不算
                v = list(got)[0]
                return v, got[v]
        return "", ""

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


# ---------------- 环境变量表：话题名不写在代码里的那种项目 ----------------
# 真实项目（尤其实验室交付件）常把话题名做成配置：
#   TOPIC_ARM_CMD = os.environ['ROS_TOPIC_ARM_CMD_VEL']
# 值放在 deploy/config/*.env。不读这张表，图上就只剩一堆没线的方块
# ——实测某机械臂项目 20 个节点、23 处接口，全部解析失败、0 条边。
_ENV_NAME_RE = re.compile(r"^(\.env(\..+)?|[^/]+\.env)$", re.I)
_ENV_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


def is_env_file(rel):
    name = (rel or "").replace("\\", "/").rsplit("/", 1)[-1]
    return bool(_ENV_NAME_RE.match(name))


def parse_env_text(text):
    """KEY=VALUE 文本 → {KEY: VALUE}。去引号、去行尾注释，解析不了的跳过。"""
    out = {}
    for line in (text or "").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        m = _ENV_LINE_RE.match(s)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if val[:1] in ("'", '"'):
            q = val[0]
            end = val.find(q, 1)
            val = val[1:end] if end > 0 else val[1:]
        else:
            val = re.sub(r"\s+#.*$", "", val).strip()
        out[key] = val
    return out


def env_map_from_entries(files):
    """从导入的文件列表收集环境变量（导入流程只有内容、没有磁盘路径）。"""
    env, src = {}, {}
    for f in files or []:
        rel = f.get("path") or ""
        if not is_env_file(rel):
            continue
        for k, v in parse_env_text(f.get("content") or "").items():
            if k not in env:            # 先到先得：deploy/config 排在前面时以它为准
                env[k], src[k] = v, rel
    return env, src


def load_env_map(root):
    """扫工作区里的 .env / *.env，返回 ({KEY: 值}, {KEY: 来源文件})。"""
    env, src = {}, {}
    hits = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in files:
            if not is_env_file(fn):
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), root).replace("\\", "/")
            hits.append(rel)
    for rel in sorted(hits):
        try:
            with open(os.path.join(root, rel), "r", encoding="utf-8",
                      errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        for k, v in parse_env_text(text).items():
            if k not in env:
                env[k], src[k] = v, rel
    return env, src


def _env_key(node):
    """`os.environ['K']` / `os.environ.get('K')` / `os.getenv('K')` → K。"""
    if isinstance(node, ast.Subscript):
        if _attr_chain(node.value) in ("os.environ", "environ"):
            return _const_str(node.slice)
    elif isinstance(node, ast.Call):
        if _attr_chain(node.func) in ("os.environ.get", "environ.get",
                                      "os.getenv", "getenv"):
            return _const_str(_arg(node, 0, "key"))
    return None


def _env_default(node):
    """`.get('K', '默认值')` 的默认值；没有默认值返回 None。"""
    if isinstance(node, ast.Call):
        d = _arg(node, 1, "default")
        if d is not None:
            return _const_str(d)
    return None


def _param_ref(node):
    """`self.get_parameter("topic").value` / `get_parameter("topic")` → "topic"。

    这是「话题名由启动参数决定」的写法。只看声明处 `declare_parameter` 的默认值：
    用户没传参时，节点实际就跑在默认值上，比标成「未解析」有用得多。
    """
    if isinstance(node, ast.Attribute) and node.attr == "value":
        node = node.value
    if not isinstance(node, ast.Call):
        return ""
    if _attr_chain(node.func).split(".")[-1] != "get_parameter":
        return ""
    return _const_str(_arg(node, 0, "name")) or ""


def _target_key(node):
    """赋值左值 → 查表用的键：X 或 self.X。"""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) \
            and node.value.id in ("self", "cls"):
        return node.value.id + "." + node.attr
    return ""


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


def _entry(mod, call, field, name_node, type_node):
    """把一次接口声明拆成 {topic,type,raw,resolved,via,line}。"""
    mtype, raw_type = ("", "")
    if type_node is not None:
        mtype, raw_type = mod.resolve_type(type_node)

    name, via = mod.resolve_str(name_node)
    raw = _unparse(name_node).strip() if name_node is not None else ""
    if not name:
        if not raw:
            return None
        # 解析不出来就把原样表达式当名字，标记未解析（图上画虚线提示）
        return {"topic": raw, "type": mtype, "raw_type": raw_type,
                "resolved": False, "via": "", "raw": raw,
                "line": getattr(call, "lineno", 0)}
    return {
        "topic": name,
        "type": mtype,
        "raw_type": raw_type,
        "resolved": True,
        "via": via,
        "raw": raw,
        "line": getattr(call, "lineno", 0),
    }


def _interface_entry(mod, call, kind_field, name_field):
    """rclpy 的 create_publisher / create_subscription / create_service / create_client。"""
    if kind_field in ("publishes", "subscribes"):
        type_node = _arg(call, 0, "msg_type")
        name_node = _arg(call, 1, name_field)
    else:
        type_node = _arg(call, 0, "srv_type")
        name_node = _arg(call, 1, "srv_name")
    return _entry(mod, call, kind_field, name_node, type_node)


_TEST_DIRS = {"test", "tests", "testing"}


def is_test_path(rel):
    """测试文件里的 `*Node` 一般是替身（fake），不是能 `ros2 run` 的节点。

    典型写法：`class Gone(BareNode): super().__init__("gone")` —— 名字、接口
    都像真节点，画进架构图就是一堆 `gone` / `bare` 的方框，把真节点淹掉。
    """
    parts = (rel or "").replace("\\", "/").split("/")
    if any(p in _TEST_DIRS for p in parts[:-1]):
        return True
    fn = parts[-1] if parts else ""
    if fn.startswith("test_"):
        return True
    return "_test." in fn      # foo_test.py / foo_test.cpp / foo_test.hpp


def parse_python(rel, src, package="", env=None, env_src=None, pkg_index=None):
    """解析单个 .py，返回 {nodes, error, package, rel}。"""
    mod = _Module(rel, src, package or infer_package(rel, pkg_index),
                  env=env, env_src=env_src)
    try:
        mod.tree = ast.parse(src)
    except SyntaxError as e:
        return {"path": rel, "package": mod.package, "nodes": [],
                "error": "第 %s 行语法错误：%s" % (e.lineno or "?", e.msg)}
    except Exception as e:       # noqa: BLE001
        return {"path": rel, "package": mod.package, "nodes": [],
                "error": "无法解析：%r" % (e,)}

    if is_test_path(rel):
        return {"path": rel, "package": mod.package, "nodes": [],
                "error": "", "lines": src.count("\n") + 1, "test": True}

    mod.collect_imports()
    mod.collect_values()
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


# ---------------- C++ (rclcpp) 节点解析 ----------------
# C++ 没有标准库 AST，用正则抓 rclcpp 那几个高度规律的写法。口径和 Python
# 解析保持一致：宁可标 unresolved 也不猜。

_CPP_EXTS = (".cpp", ".hpp", ".h", ".cc", ".cxx", ".c")

# 构造函数初始化列表里的节点名：`: rclcpp::Node("名字")`、`: Node("名字")`、
# 以及 nav2 等自定义基类 `: nav2::LifecycleNode("名字", ...)`。命名空间前缀任意。
_NODE_ANCHOR_RE = re.compile(
    r":\s*(?:(?:\w+)::)*?(?:LifecycleNode|Node)\s*"
    r"\(\s*\"([^\"]+)\"")

# 类外构造函数 `X::X(...) : Node(...)` 的类名；类内则是 `class X : ... Node`
# 注意：构造函数签名和 `: Node(...)` 常常跨行，所以只锚 `X::X(`，不再要求行尾。
_OUTLINE_CTOR_RE = re.compile(r"\b(\w+)::\1\s*\(")
_NODE_CLASS_RE = re.compile(
    r"\bclass\s+(\w+)\s*(?:final\s*)?:\s*(?:public|protected|private)\s+"
    r"(?:(?:\w+)::)*?(?:LifecycleNode|Node)\b")

# 接口调用：模板类型 + 话题/服务名（字面量或变量名）
_PUB_RE = re.compile(r"create_publisher\s*<\s*([^>]+?)\s*>\s*\(\s*([^,)\s][^,)]*)")
_SUB_RE = re.compile(r"create_subscription\s*<\s*([^>]+?)\s*>\s*\(\s*([^,)\s][^,)]*)")
_SRV_RE = re.compile(r"create_service\s*<\s*([^>]+?)\s*>\s*\(\s*([^,)\s][^,)]*)")
_CLIENT_RE = re.compile(r"create_client\s*<\s*([^>]+?)\s*>\s*\(\s*([^,)\s][^,)]*)")
_ACTION_RE = re.compile(
    r"rclcpp_action::create_(server|client)\s*<\s*([^>]+?)\s*>\s*\(\s*"
    r"[^,]+?,\s*\"([^\"]+)\"")
_DECLARE_PARAM_RE = re.compile(
    r"declare_parameter(?:<[^>]+>)?\s*\(\s*\"([^\"]+)\""
    r"(?:\s*,\s*\"([^\"]*)\")?")
_TIMER_RE = re.compile(r"create_(?:wall_)?timer\b")
# `const std::string TOPIC = "/x";` / `auto TOPIC = "/x";` —— 把话题名变量还原成字面量
_STR_ASSIGN_RE = re.compile(r"\b([A-Za-z_]\w*)\s*=\s*\"([^\"]+)\"\s*;")


def _normalize_cpp_type(name):
    """std_msgs::msg::Bool -> std_msgs/msg/Bool；std_srvs::srv::Trigger -> std_srvs/srv/Trigger。"""
    name = (name or "").strip().strip("'\"")
    if not name:
        return ""
    parts = [p for p in name.split("::") if p]
    if len(parts) == 3 and parts[1] in ("msg", "srv", "action"):
        return "%s/%s/%s" % (parts[0], parts[1], parts[2])
    if len(parts) == 2 and parts[0] in _KNOWN_PKGS:
        return "%s/msg/%s" % (parts[0], parts[1])
    return parts[-1] if parts else name


def _match_brace(src, open_idx):
    """open_idx 指向 '{'，返回与之匹配的 '}' 下标（找不到返回 len(src)）。

    跳过字符串、行注释、块注释里的花括号，避免话题名里带 { } 或注释干扰配对。
    """
    depth = 0
    i = open_idx
    n = len(src)
    while i < n:
        c = src[i]
        if c == "/" and i + 1 < n and src[i + 1] == "/":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if c == "/" and i + 1 < n and src[i + 1] == "*":
            i += 2
            while i + 1 < n and not (src[i] == "*" and src[i + 1] == "/"):
                i += 1
            i += 2
            continue
        if c in "\"'":
            q = c
            i += 1
            while i < n and src[i] != q:
                if src[i] == "\\":
                    i += 1
                i += 1
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return n


def _cpp_class_for_anchor(src, anchor):
    """从 `: Node("name")` 锚点反推它属于哪个类。"""
    head = src[max(0, anchor - 200):anchor]
    m = _OUTLINE_CTOR_RE.search(head)
    if m:
        return m.group(1)
    cls = ""
    for m in _NODE_CLASS_RE.finditer(src[:anchor]):
        cls = m.group(1)
    return cls


def _cpp_topic(raw, values):
    """把 create_* 的话题参数还原成 (名字, 是否解析, 依据)。"""
    raw = (raw or "").strip()
    if not raw:
        return "", False, ""
    if raw[:1] == '"' and raw[-1:] == '"':
        return raw[1:-1], True, "literal"
    m = re.match(r"^([A-Za-z_]\w*)$", raw)
    if m and m.group(1) in values:
        got = set(values[m.group(1)])
        if len(got) == 1:
            return list(got)[0], True, "var"
    return raw, False, ""


def _cpp_iface(m, typ_raw, topic_raw, values, line):
    topic, ok, via = _cpp_topic(topic_raw, values)
    return {
        "topic": topic,
        "type": _normalize_cpp_type(typ_raw),
        "raw_type": typ_raw,
        "resolved": ok,
        "via": via,
        "raw": topic_raw,
        "line": line,
    }


def _cpp_scan_interfaces(rec, body, values, body_offset, src):
    def line_of(pos):
        return src.count("\n", 0, body_offset + pos) + 1

    for m in _PUB_RE.finditer(body):
        rec["publishes"].append(_cpp_iface(m, m.group(1), m.group(2), values, line_of(m.start())))
    for m in _SUB_RE.finditer(body):
        rec["subscribes"].append(_cpp_iface(m, m.group(1), m.group(2), values, line_of(m.start())))
    for m in _SRV_RE.finditer(body):
        rec["service_servers"].append(_cpp_iface(m, m.group(1), m.group(2), values, line_of(m.start())))
    for m in _CLIENT_RE.finditer(body):
        rec["service_clients"].append(_cpp_iface(m, m.group(1), m.group(2), values, line_of(m.start())))
    for m in _ACTION_RE.finditer(body):
        rec["actions"].append({
            "topic": m.group(3),
            "type": _normalize_cpp_type(m.group(2)),
            "raw_type": m.group(2),
            "resolved": True,
            "role": "server" if m.group(1) == "server" else "client",
            "line": line_of(m.start()),
        })
    for m in _DECLARE_PARAM_RE.finditer(body):
        rec["params"].append({"name": m.group(1), "default": m.group(2) or ""})
    if _TIMER_RE.search(body):
        rec["has_timer"] = True


def parse_cpp(rel, src, package="", env=None, env_src=None, pkg_index=None):
    """解析单个 .cpp/.hpp/.h，返回 {nodes, error, package, rel}。"""
    pkg = package or infer_package(rel, pkg_index)
    if is_test_path(rel):
        return {"path": rel, "package": pkg, "nodes": [], "error": "", "test": True}

    values = {}
    for m in _STR_ASSIGN_RE.finditer(src):
        values.setdefault(m.group(1), []).append(m.group(2))

    nodes = []
    for m in _NODE_ANCHOR_RE.finditer(src):
        node_name = m.group(1)
        cls = _cpp_class_for_anchor(src, m.start())
        open_brace = src.find("{", m.end())
        if open_brace == -1:
            open_brace = m.end()
        close_brace = _match_brace(src, open_brace)
        body = src[open_brace:close_brace + 1]
        rec = {
            "id": "%s::%s" % (rel, cls or ("_cpp_node_%d" % len(nodes))),
            "name": node_name,
            "cls": cls,
            "file": rel,
            "package": pkg,
            "line": src.count("\n", 0, m.start()) + 1,
            "loc": src.count("\n", m.end(), close_brace) + 1,
            "publishes": [], "subscribes": [],
            "service_servers": [], "service_clients": [],
            "actions": [], "params": [], "has_timer": False,
            "status": "ok",
            "lang": "cpp",
        }
        _cpp_scan_interfaces(rec, body, values, open_brace, src)
        nodes.append(rec)

    return {"path": rel, "package": pkg, "nodes": nodes, "error": "",
            "lines": src.count("\n") + 1}


# ---------------- launch 文件解析：外部节点 ----------------
# MoveIt / 配置类仓库常「本体没有节点源码」，节点都是从已安装的 ROS 包里拉起的。
# 把这些显式声明的节点画成「外部节点」，至少能看出「跑起来有哪些节点」。

_LAUNCH_NODE_TAG_RE = re.compile(r"<node\b([^>]*)>", re.I)
_XML_ATTR_RE = re.compile(r"(\w+)\s*=\s*[\"']([^\"']*)[\"']")


def _external_node(rel, name, pkg, exe, line=0):
    return {
        "id": "%s::launch:%s" % (rel, name or exe or pkg or "node"),
        "name": name or exe or pkg or "node",
        "cls": exe or "",
        "file": rel,
        "package": pkg or "",
        "line": line, "loc": 0,
        "publishes": [], "subscribes": [],
        "service_servers": [], "service_clients": [],
        "actions": [], "params": [], "has_timer": False,
        "status": "ok",
        "external": True,
    }


def parse_launch_file(rel, src, package="", env=None, env_src=None, pkg_index=None):
    """解析 launch 文件，产出「外部节点」（从已安装 ROS 包拉起的节点）。"""
    pkg = package or infer_package(rel, pkg_index)
    # `.py` 都是 Python launch（含 xxx_launch.py 这种 .py 后缀的），只有 .launch/.xml 走 XML
    if rel.endswith(".py"):
        nodes = _launch_py_nodes(rel, src)
    else:
        nodes = _launch_xml_nodes(rel, src)
    return {"path": rel, "package": pkg, "nodes": nodes, "error": ""}


# IncludeLaunchDescription(get_package_share_directory('pkg')/...) —— 引用了外部包的 launch。
# 那些 launch 文件不在仓库里、读不到里面启动的节点，这里至少标出「引用了哪个包」。
_INCLUDE_PKG_RE = re.compile(
    r"get_package_share_(?:directory|path)\s*\(\s*['\"]([^'\"]+)['\"]")


def _launch_py_nodes(rel, src):
    try:
        tree = ast.parse(src)
    except Exception:            # noqa: BLE001
        return []
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = _call_name(node)
        if fn == "Node":
            e = _launch_node_action(node, rel)
        elif fn == "ComposableNode":
            e = _launch_composable_action(node, rel)
        elif fn == "ExecuteProcess":
            e = _launch_exec_action(node, rel)
        else:
            continue
        if e:
            out.append(e)
    # include 的外部包：get_package_share_directory('slam_toolbox') 这类。
    seen_pkgs = {e.get("package") for e in out if e.get("package")}
    for m in _INCLUDE_PKG_RE.finditer(src):
        pkg = m.group(1)
        if not pkg or pkg in seen_pkgs:
            continue
        e = _external_node(rel, pkg, pkg, "")
        e["include"] = True
        out.append(e)
        seen_pkgs.add(pkg)
    return out


def _launch_node_action(call, rel):
    pkg = _const_str(_arg(call, 0, "package")) or ""
    exe = (_const_str(_arg(call, 1, "executable"))
           or _const_str(_arg(call, 1, "node_executable")) or "")
    name = _const_str(_arg(call, 2, "name")) or exe or pkg
    if not pkg and not exe:
        return None
    return _external_node(rel, name, pkg, exe, getattr(call, "lineno", 0))


def _launch_composable_action(call, rel):
    pkg = _const_str(_arg(call, 0, "package")) or ""
    plugin = _const_str(_arg(call, 1, "plugin")) or ""
    name = _const_str(_arg(call, 2, "name")) or plugin or pkg
    if not pkg and not plugin:
        return None
    e = _external_node(rel, name, pkg,
                       plugin.split("::")[-1] if plugin else "",
                       getattr(call, "lineno", 0))
    e["composable"] = True
    return e


# ExecuteProcess 里这些是命令行工具，不是 ROS 节点，不该画成节点方块
_NON_NODE_EXEC = {
    "xacro", "gz", "gzserver", "gzclient", "ign", "ignition",
    "ros2", "python", "python3", "colcon", "catkin", "cmake", "make",
    "echo", "mkdir", "rm", "cp", "mv", "ls", "cat", "gzip", "tar",
    "sed", "awk", "grep", "bash", "sh", "source",
}


def _launch_exec_action(call, rel):
    cmd = _arg(call, 0, "cmd")
    exe = ""
    if isinstance(cmd, (ast.List, ast.Tuple)) and cmd.elts:
        parts = [p for p in (_const_str(el) for el in cmd.elts) if p is not None]
        head = parts[0] if parts else ""
        if len(parts) >= 4 and parts[0] == "ros2" and parts[1] == "run":
            # `ros2 run <pkg> <node>`：真正被拉起的是 <node>
            exe = parts[3] or ""
        elif "-m" in parts:
            # `python3 -m <pkg>.<module>`：真正被拉起的是 <module>
            i = parts.index("-m")
            if i + 1 < len(parts):
                exe = parts[i + 1] or ""
        elif _const_str(_arg(call, 1, "name")):
            # 显式起了 ROS 节点名，才当节点；否则是 xacro / gz / python3 这类工具命令
            exe = head
        else:
            return None
    if not exe or exe in _NON_NODE_EXEC or exe.startswith("-"):
        return None
    name = _const_str(_arg(call, 1, "name")) or exe
    return _external_node(rel, name or exe, "", exe, getattr(call, "lineno", 0))


def _launch_xml_nodes(rel, src):
    out = []
    for m in _LAUNCH_NODE_TAG_RE.finditer(src):
        attrs = {}
        for a, v in _XML_ATTR_RE.findall(m.group(1)):
            attrs[a] = v
        pkg = attrs.get("pkg", "")
        exe = attrs.get("exec", "") or attrs.get("type", "")
        name = attrs.get("name", "") or exe or pkg
        if not pkg and not exe:
            continue
        out.append(_external_node(rel, name, pkg, exe))
    return out


# launch 文件不一定叫 `.launch.py`：nav2、moveit 等大量包直接叫 `xxx_launch.py`
# （.py 后缀），甚至不叫 _launch.py 但 import 了 launch。靠后缀判断会漏掉整张启动网。
_LAUNCH_IMPORT_RE = re.compile(
    r"^\s*(?:import\s+launch(?:\s|$)|from\s+launch(?:_ros)?(?:\s+import|\s|$))",
    re.M)


def _is_launch_py(fn, head):
    """一个 .py 文件是否是 launch 文件：文件名以 `_launch.py` 结尾，或 import 了 launch。"""
    if fn.endswith("_launch.py"):
        return True
    return bool(_LAUNCH_IMPORT_RE.search(head or ""))


def _read_head(root, rel, size=4096):
    """读文件头若干字节（足够判断 import），读不到返回空串。"""
    try:
        with open(os.path.join(root, rel), "rb") as f:
            return f.read(size).decode("utf-8", errors="replace")
    except OSError:
        return ""


def iter_py_files(root):
    """遍历工作区里的节点源码 .py（跳过 build/install/log 等），返回相对路径。

    launch 文件不只看后缀：`xxx_launch.py`（.py 后缀）或 import 了 launch 的 .py
    都交给 launch 解析，这里排除，避免被当成 rclpy 源码扫一遍。
    """
    out = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in files:
            if not fn.endswith(".py") or fn.endswith(".launch.py"):
                continue
            if fn in SKIP_FILES:
                continue
            rel = os.path.relpath(os.path.join(dirpath, fn), root).replace("\\", "/")
            if _is_launch_py(fn, _read_head(root, rel)):
                continue
            out.append(rel)
    return sorted(out)


def iter_cpp_files(root):
    """遍历工作区里的 C++ 源文件（.cpp/.hpp/.h/.cc/.cxx/.c）。"""
    out = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in files:
            if fn.endswith(_CPP_EXTS):
                rel = os.path.relpath(os.path.join(dirpath, fn), root)
                out.append(rel.replace("\\", "/"))
    return sorted(out)


def iter_launch_files(root):
    """遍历工作区里的 launch 文件：.launch / .launch.py / .launch.xml，以及
    内容 import 了 launch 的 .py（nav2 等包的启动文件直接叫 xxx_launch.py）。"""
    out = []
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in files:
            rel = os.path.relpath(os.path.join(dirpath, fn), root).replace("\\", "/")
            if fn.endswith(".launch") or fn.endswith(".launch.py") \
                    or fn.endswith(".launch.xml"):
                out.append(rel)
            elif fn.endswith(".py") and _is_launch_py(fn, _read_head(root, rel)):
                out.append(rel)
    return sorted(out)


_PKG_NAME_RE = re.compile(r"<name>\s*([^<\s]+)\s*</name>", re.I)
_AMENT_RE = re.compile(r"<build_type>\s*(ament_\w+|cmake)\s*</build_type>", re.I)


def load_package_dirs(root):
    """包目录（相对 root，根目录是空串）→ 包名。

    为什么要按目录记：GitHub 上 clone 下来的仓库经常「根目录就是包」，
    只按包名记的话，`infer_package` 没法从文件路径反推它属于哪个包。
    """
    out = {}
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        if "package.xml" not in files:
            continue
        path = os.path.join(dirpath, "package.xml")
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                txt = f.read()
        except OSError:
            continue
        m = _PKG_NAME_RE.search(txt)
        name = m.group(1) if m else os.path.basename(dirpath)
        rel = os.path.relpath(path, root).replace("\\", "/")
        out[os.path.dirname(rel)] = name
    return out


def load_package_kinds(root):
    """包名 → {name, dir}。用来核对「代码里引用的包真的能被 colcon 找到」。"""
    return {name: {"name": name, "dir": d}
            for d, name in load_package_dirs(root).items()}


def norm_topic(name):
    """话题名归一化：`/odom//` 与 `odom` 视作同一个。"""
    n = re.sub(r"/+", "/", (name or "").strip())
    return n.strip("/")


def _strip_one_suffix(name):
    """去掉名字尾部一个 `_word` 后缀，返回 (前缀, 被去掉的词)。"""
    name = (name or "").strip()
    i = name.rfind("_")
    if i <= 0:
        return name, ""
    return name[:i], name[i + 1:]


def _merge_external_into_source(nodes, warnings):
    """把 launch 外部节点归并到「同包、名字对得上」的源码节点，消除「有源码却被标外部」。

    ROS2 里 launch 的 Node(package, executable, name) 启动的就是该包编译出的可执行
    文件，节点默认名写在源码里，launch 的 name/executable 都是它的别名。据此，同包下
    满足任一即判同一节点：
    - executable == 源码节点名（精确）
    - launch name == 源码节点名（精确）
    - launch name 是「源码节点名 + _后缀」（lifecycle_manager_navigation → lifecycle_manager）
    - executable 去掉尾部一个 _word 后缀后 == 源码节点名（map_saver_server → map_saver）

    只在「匹配结果唯一」时归并；有歧义或对不上就保留外部（宁标外部，不误归）。
    """
    src_by_pkg = {}
    for n in nodes:
        if not n.get("external"):
            src_by_pkg.setdefault(n.get("package") or "", []).append(n)
    if not src_by_pkg:
        return nodes

    drop = set()
    for n in nodes:
        if not n.get("external"):
            continue
        pkg = n.get("package") or ""
        exe = (n.get("cls") or "").strip()
        name = (n.get("name") or "").strip()
        cands = src_by_pkg.get(pkg, [])
        if not cands or (not exe and not name):
            continue

        hit = None
        for s in cands:
            sname = (s.get("name") or "").strip()
            scls = (s.get("cls") or "").strip()
            if exe == sname or name == sname or name.startswith(sname + "_"):
                hit = s
                break
            # ComposableNode 的 cls 是 plugin 类名（如 MapServer），源码节点的 cls 也是类名
            if n.get("composable") and scls and exe == scls:
                hit = s
                break
        if hit is None:
            # 去尾部一个后缀再比（map_saver_server → map_saver），唯一匹配才认
            pre, tail = _strip_one_suffix(exe or name)
            if tail:
                for s in cands:
                    sname = (s.get("name") or "").strip()
                    if sname and pre == sname:
                        if hit is not None:      # 歧义：不归
                            hit = None
                            break
                        hit = s
        if hit is None:
            continue
        hit.setdefault("launch_names", [])
        if name and name not in hit["launch_names"]:
            hit["launch_names"].append(name)
        drop.add(id(n))

    if drop:
        return [n for n in nodes if id(n) not in drop]
    return nodes


def _dedupe(nodes, warnings):
    """同名节点只留一个，避免图上出现两个同名方块。

    留接口多的那个（信息更全），被合并的记进 dup，详情里还能看到。
    """
    groups = {}
    for n in nodes:
        groups.setdefault(n.get("name") or n.get("id"), []).append(n)
    keep = []
    for name, group in groups.items():
        if len(group) == 1:
            keep.append(group[0])
            continue
        group.sort(key=lambda n: (
            1 if n.get("external") else 0,
            -(len(n.get("publishes") or []) + len(n.get("subscribes") or [])),
            n.get("file") or "",
        ))
        win = group[0]
        win["dup"] = [{"file": g.get("file"), "package": g.get("package")}
                      for g in group[1:]]
        keep.append(win)
        warnings.append("节点名 %s 在 %d 个文件里重复（%s），图上只画了 %s"
                        % (name, len(group),
                           " / ".join(sorted({g.get("file") or "" for g in group})),
                           win.get("file") or ""))
    keep.sort(key=lambda n: (n.get("file") or "", n.get("line") or 0))
    return keep


def _absorb(res, nodes, packages, files, warnings, used_env):
    """把一个文件的解析结果并进 analyze 的累积器。

    外部节点（launch 拉起的）不算进工作区包的「节点数」——它们属于
    controller_manager、rviz 这类已安装包，硬算会让 packages 里冒出一堆
    不存在的包。
    """
    files.append({"path": res.get("path"), "nodes": len(res.get("nodes") or []),
                  "error": res.get("error", "")})
    if res.get("error"):
        warnings.append("%s %s" % (res.get("path"), res["error"]))
    if not res.get("nodes"):
        return
    pkg = res.get("package") or ""
    own = [nd for nd in res["nodes"] if not nd.get("external")]
    if pkg:
        packages.setdefault(pkg, {"name": pkg, "files": 0, "nodes": 0})
        packages[pkg]["files"] += 1
        packages[pkg]["nodes"] += len(own)
    for nd in res["nodes"]:
        nodes.append(nd)
        if nd.get("external"):
            continue
        for f in ("publishes", "subscribes", "service_servers", "service_clients"):
            for it in nd.get(f) or []:
                if it.get("via") == "env" and it.get("raw"):
                    used_env.add(it["raw"])


def analyze(root):
    """解析整个工作区，产出节点表 / 连线 / 话题表。"""
    nodes, packages, warnings, files = [], {}, [], []
    env, env_src = load_env_map(root)
    kinds = load_package_kinds(root)
    pkg_index = load_package_dirs(root)
    used_env = set()

    def scan(iter_fn, parser):
        for rel in iter_fn(root):
            try:
                with open(os.path.join(root, rel), "r", encoding="utf-8",
                          errors="replace") as f:
                    src = f.read()
            except OSError:
                continue
            res = parser(rel, src, env=env, env_src=env_src, pkg_index=pkg_index)
            _absorb(res, nodes, packages, files, warnings, used_env)

    scan(iter_py_files, parse_python)
    scan(iter_cpp_files, parse_cpp)
    scan(iter_launch_files, parse_launch_file)

    nodes = _merge_external_into_source(nodes, warnings)
    nodes = _dedupe(nodes, warnings)

    # include 引用的包若在工作区内（有源码），就不是「外部」，去掉这个占位节点
    ws_pkgs = set(kinds.keys())
    nodes = [n for n in nodes
             if not (n.get("include") and n.get("package") in ws_pkgs)]

    no_pkg = sorted({n.get("package") for n in nodes
                     if n.get("package") and not n.get("external")
                     and n["package"] not in kinds})
    for p in no_pkg:
        if p:
            warnings.append("目录 %s 没有 package.xml，colcon 不会把它当包编译" % p)

    topics = {}
    for n in nodes:
        for field, role in (("publishes", "pub"), ("subscribes", "sub")):
            for it in n[field]:
                if not it["resolved"]:
                    continue
                # 按「去掉前导斜杠」的规范化名字归类：ROS2 里 `odom` 在根命名空间下
                # 就是 `/odom`。仓库里两种写法混着出现太常见了（launch 里写死的绝对名
                # 配代码里的相对名），按原样比字符串会得到「一堆节点、零条线」的假图。
                key = norm_topic(it["topic"])
                if not key:
                    continue
                rec = topics.setdefault(key, {
                    "name": it["topic"], "types": [], "pubs": [], "subs": []})
                if it["topic"].startswith("/") and not rec["name"].startswith("/"):
                    rec["name"] = it["topic"]      # 绝对名更能说明问题，优先显示
                if it["type"] and it["type"] not in rec["types"]:
                    rec["types"].append(it["type"])
                bucket = rec["pubs"] if role == "pub" else rec["subs"]
                if n["id"] not in bucket:
                    bucket.append(n["id"])

    edges, seen = [], set()
    for key, t in sorted(topics.items()):
        if len(t["types"]) > 1:
            warnings.append("话题 %s 被赋了多种类型（%s），真实 ROS 会报类型冲突"
                            % (t["name"], " / ".join(t["types"])))
        for p in t["pubs"]:
            for s in t["subs"]:
                if p == s:
                    continue
                sig = (p, s, key)
                if sig in seen:
                    continue
                seen.add(sig)
                edges.append({"from": p, "to": s, "topic": t["name"],
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

    # 解析成功但也别装作是「代码里写死的」：告诉用户这些名字是从配置文件里查出来的，
    # 换机器/改 .env 时要一起改，否则图上和图下对不上。
    via_env = sum(1 for n in nodes
                  for f in ("publishes", "subscribes", "service_servers", "service_clients")
                  for it in (n.get(f) or []) if it.get("via") == "env")
    if via_env:
        files_txt = "、".join(sorted(set(env_src.values()))[:2])
        warnings.append("有 %d 处话题名取自项目里的 .env 配置（%s），不是代码里写死的；"
                        "改配置后图会跟着变" % (via_env, files_txt))

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
            "external": sum(1 for n in nodes if n.get("external")),
            "unresolved": unresolved,
            "via_env": via_env,
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
