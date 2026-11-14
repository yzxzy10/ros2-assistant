# -*- coding: utf-8 -*-
"""静态三闸门：不跑容器、不联网、毫秒级。

为什么不用 Docker 编译验证
--------------------------
编译验证要等容器（colcon 上限 300 秒），Gazebo 镜像更大，同步执行会让进度条
长时间停在同一行——这个问题在 1.3.1 上真实发生过，用户直接要求关掉。
所以这里只做「不依赖运行时就能判定」的检查，真正确认交给生成包里的
selfcheck.py，在学生的虚拟机里跑。

六道检查的取舍：
  G1 关节链     —— 树断了 check_urdf 必报，属于「跑不起来」，必须拦
  G2 命名一致   —— 三处名字对不上，表现是「模型出来了但不动」，最难查，必须拦
  G3 物理量     —— 质量/惯量离谱会发散或穿透，属于「跑起来乱飞」，必须拦
  G4 接线一致   —— 插件参数与几何必须同源，防止改了尺寸忘了改插件
  G5 依赖声明   —— 少声明 exec_depend，换台机器就装不上
  G6 模板残留   —— {{...}} 没替换干净说明装配有 bug，属于内部错误
另外顺带做 XML 可解析性、Python 语法两项基础检查。
"""
import math
import re
import xml.etree.ElementTree as ET

_XACRO_NS = "{http://www.ros.org/wiki/xacro}"
_MOVABLE = ("revolute", "prismatic", "continuous")
_TOL = 1e-6


class Gate(object):
    def __init__(self):
        self.errors = []   # [(code, msg)]
        self.warns = []    # [(code, msg)]
        self.checks = 0

    def err(self, code, msg):
        self.checks += 1
        self.errors.append((code, msg))

    def warn(self, code, msg):
        self.checks += 1
        self.warns.append((code, msg))

    def ok(self, _code):
        self.checks += 1


# ---------- 表达式求值（只用于我们自己生成的 xacro 表达式） ----------

def _eval_value(text, props):
    """把 '0.05' 或 '${wheel_mass*(3*r**2)/12}' 求成 float，失败返回 None。"""
    if text is None:
        return None
    s = str(text).strip()
    m = re.fullmatch(r"\$\{(.+)\}", s, re.S)
    if m:
        s = m.group(1).strip()
        # 标识符替换成数值，未知标识符原样保留，使 eval 失败而不是乱算
        def sub(mo):
            name = mo.group(0)
            if name in props:
                return repr(props[name])
            return name
        s = re.sub(r"[A-Za-z_][A-Za-z_0-9]*", sub, s)
        if not re.fullmatch(r"[0-9eE+\-*/(). ]+", s):
            return None
    try:
        return float(s)
    except (TypeError, ValueError):
        return None
    except Exception:
        return None


def _safe_eval(text, props):
    """表达式形态下走受限 eval；字面量直接 float。"""
    s = str(text or "").strip()
    if not s:
        return None
    # `-${expr}` / `+${expr}` 是合法写法（xacro 会把负号留在前面），
    # 这里先剥掉一元符号，否则整串既不匹配字面量也不是纯 ${}，会被当成非法值退化成 0。
    if len(s) > 3 and s[0] in "+-" and s[1:].startswith("${"):
        v = _safe_eval(s[1:], props)
        return None if v is None else (-v if s[0] == "-" else v)
    if not s.startswith("${"):
        try:
            return float(s)
        except ValueError:
            return None
    inner = s[2:-1] if s.endswith("}") else s[2:]
    expr = inner
    for name, val in props.items():
        expr = re.sub(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(name),
                      "(%r)" % val, expr)
    if not re.fullmatch(r"[0-9eE+\-*/().\[\] ]+", expr):
        return None
    try:
        return float(eval(expr, {"__builtins__": {}}, {}))  # noqa: S307
    except Exception:
        return None


def _props_of(text):
    """从 xacro 文本里取所有 property 的数值（含常量 pi）。"""
    props = {"pi": math.pi}
    for name, val in re.findall(
            r'<xacro:property\s+name="([^"]+)"\s+value="([^"]*)"', text):
        v = _safe_eval(val, props)
        if v is not None:
            props[name] = v
    return props


# ---------- 解析 ----------

def _links_joints(root):
    links = [el.get("name") for el in root.iter("link") if el.get("name")]
    joints = []
    for el in root.iter("joint"):
        p, c = el.find("parent"), el.find("child")
        joints.append({
            "name": el.get("name"),
            "type": el.get("type"),
            "parent": p.get("link") if p is not None else None,
            "child": c.get("link") if c is not None else None,
            "el": el,
        })
    return links, joints


def _yaml_joints(text):
    """解析我们生成的 controllers.yaml：取 joints: 下的 - name 列表。"""
    out = []
    in_j = False
    for line in (text or "").splitlines():
        s = line.strip()
        if s.startswith("joints:"):
            in_j = True
            continue
        if in_j:
            if s.startswith("- "):
                out.append(s[2:].strip().strip('"\''))
            elif s and not s.startswith("#"):
                if not s.startswith("-"):
                    in_j = False
    return out


# ---------- 主入口 ----------

def check(spec, files):
    """返回 {passed, errors, warns, stats}。errors 为空即通过。"""
    g = Gate()
    arch = spec["archetype"]
    pkg = spec["package"]

    _check_basics(g, files)
    _check_deps(g, pkg, files)

    urdf_key = "urdf/%s.urdf.xacro" % pkg
    if urdf_key not in files:
        g.err("G1", "缺少主描述文件 %s" % urdf_key)
        return _result(g)

    props = _props_of(files[urdf_key])
    try:
        root = ET.fromstring(files[urdf_key].encode("utf-8"))
    except ET.ParseError as e:
        g.err("G1", "%s 不是合法 XML：%s" % (urdf_key, e))
        return _result(g)

    links, joints = _links_joints(root)
    _check_tree(g, links, joints)
    _check_physics(g, root, props)

    if arch == "arm":
        _check_arm_names(g, spec, files, joints, props)
    else:
        _check_diff_wiring(g, spec, root, joints, props)

    return _result(g)


def _result(g):
    return {
        "passed": not g.errors,
        "errors": [{"code": c, "msg": m} for c, m in g.errors],
        "warns": [{"code": c, "msg": m} for c, m in g.warns],
        "stats": {"checks": g.checks},
    }


def _check_basics(g, files):
    """XML 可解析 + Python 可编译 + 占位符无残留。"""
    for path, text in files.items():
        if "{{" in text:
            g.err("G6", "%s 里还有未替换的占位符" % path)
        if path.endswith((".xacro", ".xml", ".sdf")):
            try:
                ET.fromstring(text.encode("utf-8"))
            except ET.ParseError as e:
                g.err("G1", "%s XML 不合法：%s" % (path, e))
        elif path.endswith(".py"):
            try:
                compile(text, path, "exec")
            except SyntaxError as e:
                g.err("G1", "%s Python 语法错误：第 %s 行 %s" % (path, e.lineno, e.msg))


def _check_tree(g, links, joints):
    """G1：父子都在、根唯一、无环、无不可达 link。"""
    link_set = set(links)
    children = {}
    for j in joints:
        if not j["parent"] or not j["child"]:
            g.err("G1", "关节 %s 缺少 parent 或 child" % j["name"])
            continue
        if j["parent"] not in link_set:
            g.err("G1", "关节 %s 的 parent「%s」没有定义" % (j["name"], j["parent"]))
        if j["child"] not in link_set:
            g.err("G1", "关节 %s 的 child「%s」没有定义" % (j["name"], j["child"]))
        if j["child"] in children:
            g.err("G1", "link「%s」被两个关节同时作为 child：%s / %s"
                  % (j["child"], children[j["child"]], j["name"]))
        children[j["child"]] = j["name"]

    if len(set(links)) != len(links):
        g.err("G1", "存在重名 link")

    roots = [l for l in link_set if l not in children]
    if len(roots) != 1:
        g.err("G1", "根 link 不唯一（%d 个）：%s" % (len(roots), "、".join(roots[:4])))
        return

    adj = {}
    for j in joints:
        if j["parent"] and j["child"]:
            adj.setdefault(j["parent"], []).append(j["child"])
    seen, stack = set(), [roots[0]]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(adj.get(cur, []))
    missing = link_set - seen
    if missing:
        g.err("G1", "这些 link 从根不可达（链断了或成环）：%s"
              % "、".join(sorted(missing)[:5]))
    g.ok("G1")


def _check_physics(g, root, props):
    """G3：质量 > 0、惯量主对角 > 0 且满足三角不等式、关节限位合法。"""
    for link in root.iter("link"):
        name = link.get("name")
        inert = link.find("inertial")
        if inert is None:
            if name not in ("world", "base_footprint", "tool0"):
                g.err("G3", "link「%s」缺少 <inertial>" % name)
            continue
        mass = _safe_eval((inert.find("mass").get("value")
                           if inert.find("mass") is not None else ""), props)
        if mass is None or mass <= 0:
            g.err("G3", "link「%s」质量无效：%s" % (name, mass))
            continue
        it = inert.find("inertia")
        if it is None:
            g.err("G3", "link「%s」缺少 <inertia>" % name)
            continue
        vals = {}
        for k in ("ixx", "iyy", "izz"):
            vals[k] = _safe_eval(it.get(k), props)
        if any(v is None or v <= 0 for v in vals.values()):
            g.err("G3", "link「%s」主惯量无效：%s" % (name, vals))
            continue
        # 三角不等式：任何两个主惯量之和必须不小于第三个
        tol = 1e-9 * max(vals.values())
        if (vals["ixx"] + vals["iyy"] < vals["izz"] - tol
                or vals["iyy"] + vals["izz"] < vals["ixx"] - tol
                or vals["ixx"] + vals["izz"] < vals["iyy"] - tol):
            g.err("G3", "link「%s」惯量不满足三角不等式：ixx=%.3e iyy=%.3e izz=%.3e"
                  % (name, vals["ixx"], vals["iyy"], vals["izz"]))
        g.ok("G3")

    for j in root.iter("joint"):
        if j.get("type") not in ("revolute", "prismatic"):
            continue
        lim = j.find("limit")
        if lim is None:
            g.err("G3", "关节 %s 缺少 <limit>" % j.get("name"))
            continue
        lo = _safe_eval(lim.get("lower"), props)
        hi = _safe_eval(lim.get("upper"), props)
        eff = _safe_eval(lim.get("effort"), props)
        vel = _safe_eval(lim.get("velocity"), props)
        if lo is None or hi is None or hi <= lo:
            g.err("G3", "关节 %s 限位不合法：[%s, %s]" % (j.get("name"), lo, hi))
        if eff is None or eff <= 0:
            g.err("G3", "关节 %s 的 effort 必须为正：%s" % (j.get("name"), eff))
        if vel is None or vel <= 0:
            g.err("G3", "关节 %s 的 velocity 必须为正：%s" % (j.get("name"), vel))


def _check_arm_names(g, spec, files, joints, props):
    """G2：URDF / ros2_control / controllers.yaml 三处关节名必须一致。"""
    pkg = spec["package"]
    urdf_names = set(j["name"] for j in joints if j["type"] in _MOVABLE)

    rc_key = "urdf/%s.ros2_control.xacro" % pkg
    rc_names = set()
    if rc_key not in files:
        g.err("G2", "缺少 %s" % rc_key)
    else:
        try:
            rc_root = ET.fromstring(files[rc_key].encode("utf-8"))
            for el in rc_root.iter("joint"):
                if el.get("name"):
                    rc_names.add(el.get("name"))
        except ET.ParseError as e:
            g.err("G2", "%s XML 不合法：%s" % (rc_key, e))

    yml_key = "config/controllers.yaml"
    yml_names = set(_yaml_joints(files.get(yml_key, ""))) if yml_key in files else set()
    if not yml_names:
        g.err("G2", "controllers.yaml 里没有解析到 joints 列表")

    for label, a, b in (("URDF 与 ros2_control", urdf_names, rc_names),
                        ("ros2_control 与 controllers.yaml", rc_names, yml_names)):
        only_a = sorted(a - b)
        only_b = sorted(b - a)
        if only_a:
            g.err("G2", "%s 不一致：只在左边有 %s" % (label, "、".join(only_a)))
        if only_b:
            g.err("G2", "%s 不一致：只在右边有 %s" % (label, "、".join(only_b)))
        if not only_a and not only_b:
            g.ok("G2")

    # 每个 ros2_control 关节都必须声明 position 命令接口，否则控制器加载不上
    if rc_key in files:
        try:
            rc_root = ET.fromstring(files[rc_key].encode("utf-8"))
            for el in rc_root.iter("joint"):
                names = [ci.get("name") for ci in el.findall("command_interface")]
                if "position" not in names:
                    g.err("G2", "关节 %s 没有声明 position 命令接口（当前：%s）"
                          % (el.get("name"), names))
                if not el.findall("state_interface"):
                    g.err("G2", "关节 %s 没有声明 state 接口" % el.get("name"))
        except ET.ParseError:
            pass

    # controllers.yaml 的 interface_name 必须是 position
    yml = files.get(yml_key, "")
    if "interface_name: position" not in yml:
        g.err("G2", "controllers.yaml 的 interface_name 不是 position")


def _check_diff_wiring(g, spec, root, joints, props):
    """G2/G4：驱动插件的关节名与几何必须和 URDF 同源（差速 / 阿克曼）。"""
    plugin, ack = None, False
    for el in root.iter("plugin"):
        name = el.get("name", "")
        if name.endswith("DiffDrive"):
            plugin = el
            break
        if name.endswith("AckermannSteering"):
            plugin, ack = el, True
            break
    if plugin is None:
        g.err("G2", "URDF 里没有驱动插件（DiffDrive / AckermannSteering）")
        return
    if ack:
        jn = set(j["name"] for j in joints)
        for tag in ("left_steering_joint", "right_steering_joint"):
            nm = (plugin.findtext(tag) or "").strip()
            if nm not in jn:
                g.err("G2", "阿克曼插件 %s 指向的关节「%s」在 URDF 里不存在" % (tag, nm))
            else:
                g.ok("G2")
        wb = _safe_eval(plugin.findtext("wheel_base"), props)
        if wb is None or abs(wb - spec["wheelbase"]) > _TOL:
            g.err("G4", "插件轴距 %s 与参数表 %s 不一致" % (wb, spec["wheelbase"]))
        else:
            g.ok("G4")

    jn = set(j["name"] for j in joints)
    left = (plugin.findtext("left_joint") or "").strip()
    right = (plugin.findtext("right_joint") or "").strip()
    for label, name in (("left_joint", left), ("right_joint", right)):
        if name not in jn:
            g.err("G2", "差速插件 %s 指向的关节「%s」在 URDF 里不存在" % (label, name))
        else:
            g.ok("G2")

    sep = _safe_eval(plugin.findtext("wheel_separation"), props)
    rad = _safe_eval(plugin.findtext("wheel_radius"), props)
    if sep is None or abs(sep - spec["wheel_separation"]) > _TOL:
        g.err("G4", "插件轮距 %s 与参数表 %s 不一致" % (sep, spec["wheel_separation"]))
    else:
        g.ok("G4")
    if rad is None or abs(rad - spec["wheel_radius"]) > _TOL:
        g.err("G4", "插件轮径 %s 与参数表 %s 不一致" % (rad, spec["wheel_radius"]))
    else:
        g.ok("G4")

    # 车轮必须是 continuous，否则车动不了
    for j in joints:
        if j["name"].endswith("_wheel_joint") and j["type"] != "continuous":
            g.err("G2", "车轮关节 %s 类型应为 continuous，实际 %s"
                  % (j["name"], j["type"]))


def _check_deps(g, pkg, files):
    """G5：launch 里用到的包必须在 package.xml 声明。"""
    pkgxml = files.get("package.xml", "")
    declared = set(re.findall(r"<exec_depend>([^<]+)</exec_depend>", pkgxml))
    used = set()
    for path, text in files.items():
        if not path.endswith(".py"):
            continue
        used |= set(re.findall(r'get_package_share_directory\(\s*"([^"]+)"', text))
        used |= set(re.findall(r'package\s*=\s*"([^"]+)"', text))
    used.discard(pkg)          # 自身不需要声明
    missing = sorted(used - declared)
    if missing:
        g.err("G5", "launch 用到但 package.xml 未声明：%s" % "、".join(missing))
    else:
        g.ok("G5")


def render_lines(gate):
    """给对话用的短句列表。"""
    out = []
    for e in gate["errors"][:6]:
        out.append("❌ [%s] %s" % (e["code"], e["msg"]))
    for w in gate["warns"][:3]:
        out.append("⚠️ [%s] %s" % (w["code"], w["msg"]))
    return out


# ============ 对外体检接口（供 model_inspect 复用） ============

def _geometry_of(link):
    """取 link 的第一个 primitive 几何，用于估算质量与惯量。"""
    for tag in ("box", "cylinder", "sphere"):
        el = link.find(".//" + tag)
        if el is None:
            continue
        if tag == "box":
            parts = [float(x) for x in (el.get("size") or "").split()[:3]]
            if len(parts) == 3 and all(p > 0 for p in parts):
                return {"kind": "box", "size": parts}
        elif tag == "cylinder":
            try:
                r = float(el.get("radius") or 0)
                L = float(el.get("length") or 0)
            except ValueError:
                continue
            if r > 0 and L > 0:
                return {"kind": "cylinder", "radius": r, "length": L}
        else:
            try:
                r = float(el.get("radius") or 0)
            except ValueError:
                continue
            if r > 0:
                return {"kind": "sphere", "radius": r}
    return None


def geometry_mass_inertia(geo, density=800.0):
    """按 primitive 几何 + 密度估一组物理量，返回 (mass, ixx, iyy, izz)。"""
    import math as _m
    if not geo:
        return None
    if geo["kind"] == "box":
        a, b, c = geo["size"]
        m = density * a * b * c
        return (m, m * (b * b + c * c) / 12.0,
                m * (a * a + c * c) / 12.0, m * (a * a + b * b) / 12.0)
    if geo["kind"] == "cylinder":
        r, L = geo["radius"], geo["length"]
        m = density * _m.pi * r * r * L
        i_axis = m * r * r / 2.0
        i_side = m * (3 * r * r + L * L) / 12.0
        return (m, i_side, i_side, i_axis)
    r = geo["radius"]
    m = density * 4.0 / 3.0 * _m.pi * r ** 3
    i = 2.0 / 5.0 * m * r * r
    return (m, i, i, i)


def inspect_source(text):
    """对一份 URDF / xacro 文本做结构与控制配置体检。

    与 check() 的区别：check() 是「校验我们生成的包」，要求 error 为零；
    本函数面向「用户上传的模型」，只做事实呈现 + 分级提示，不阻断流程。
    issues 里 fixable=True 的，model_inspect 能自动修。
    """
    out = {
        "ok": False, "links": [], "joints": [], "movable": [], "limited": [],
        "control": {"ros2_control": False, "gz_plugin": False, "diff_drive": False},
        "issues": [], "props": {}, "geometry": {},
    }
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError as e:
        out["issues"].append({"level": "error", "code": "X1", "target": "",
                              "fixable": False, "msg": "XML 不合法：%s" % e})
        return out
    out["ok"] = True
    props = _props_of(text)
    out["props"] = props

    links, joints = _links_joints(root)
    out["links"] = links
    out["joints"] = [{"name": j["name"], "type": j["type"],
                      "parent": j["parent"], "child": j["child"]} for j in joints]

    # 关节链
    link_set = set(links)
    children = {}
    for j in joints:
        if not j["parent"] or not j["child"]:
            out["issues"].append({"level": "error", "code": "G1", "target": j["name"],
                                  "fixable": False, "msg": "关节 %s 缺少 parent 或 child" % j["name"]})
            continue
        if j["parent"] not in link_set:
            out["issues"].append({"level": "error", "code": "G1", "target": j["name"],
                                  "fixable": False,
                                  "msg": "关节 %s 的 parent「%s」没有定义" % (j["name"], j["parent"])})
        if j["child"] not in link_set:
            out["issues"].append({"level": "error", "code": "G1", "target": j["name"],
                                  "fixable": False,
                                  "msg": "关节 %s 的 child「%s」没有定义" % (j["name"], j["child"])})
        if j["child"] in children:
            out["issues"].append({"level": "error", "code": "G1", "target": j["child"],
                                  "fixable": False,
                                  "msg": "link「%s」被两个关节同时作为 child（%s / %s）"
                                         % (j["child"], children[j["child"]], j["name"])})
        children[j["child"]] = j["name"]

    movable, limited = [], []
    for j in joints:
        if j["type"] in ("revolute", "prismatic", "continuous"):
            movable.append(j["name"])
        if j["type"] in ("revolute", "prismatic"):
            limited.append(j["name"])
            lim = j["el"].find("limit")
            if lim is None:
                out["issues"].append({"level": "error", "code": "G3", "target": j["name"],
                                      "fixable": True,
                                      "msg": "关节 %s（%s）缺少 <limit>，URDF 不合法"
                                             % (j["name"], j["type"])})
            else:
                lo = _safe_eval(lim.get("lower"), props)
                hi = _safe_eval(lim.get("upper"), props)
                if lo is None or hi is None or hi <= lo:
                    out["issues"].append({"level": "error", "code": "G3", "target": j["name"],
                                          "fixable": True,
                                          "msg": "关节 %s 限位不合法：[%s, %s]"
                                                 % (j["name"], lim.get("lower"), lim.get("upper"))})
        elif j["type"] == "continuous":
            lim = j["el"].find("limit")
            if lim is not None:
                eff = _safe_eval(lim.get("effort"), props)
                if eff is not None and eff <= 0:
                    out["issues"].append({"level": "warn", "code": "G3", "target": j["name"],
                                          "fixable": True,
                                          "msg": "关节 %s 的 effort 非正：%s" % (j["name"], eff)})
    out["movable"] = movable
    out["limited"] = limited

    # 物理量
    for link in root.iter("link"):
        name = link.get("name")
        geo = _geometry_of(link)
        if geo:
            out["geometry"][name] = geo
        inert = link.find("inertial")
        if inert is None:
            if name in ("world", "base_footprint", "tool0") or geo is None:
                continue
            out["issues"].append({"level": "error", "code": "G3", "target": name,
                                  "fixable": bool(geo),
                                  "msg": "link「%s」缺少 <inertial>（有 primitive 几何，可按密度估算）" % name})
            continue
        mass = _safe_eval(inert.find("mass").get("value")
                          if inert.find("mass") is not None else "", props)
        if mass is None or mass <= 0:
            out["issues"].append({"level": "error", "code": "G3", "target": name,
                                  "fixable": bool(geo),
                                  "msg": "link「%s」质量无效（%s）" % (name, mass)})
            continue
        it = inert.find("inertia")
        vals = {}
        if it is not None:
            for k in ("ixx", "iyy", "izz"):
                vals[k] = _safe_eval(it.get(k), props)
        if not vals or any(v is None or v <= 0 for v in vals.values()):
            out["issues"].append({"level": "error", "code": "G3", "target": name,
                                  "fixable": bool(geo),
                                  "msg": "link「%s」惯量无效：%s" % (name, vals)})
            continue
        tol = 1e-9 * max(vals.values())
        if (vals["ixx"] + vals["iyy"] < vals["izz"] - tol
                or vals["iyy"] + vals["izz"] < vals["ixx"] - tol
                or vals["ixx"] + vals["izz"] < vals["iyy"] - tol):
            out["issues"].append({"level": "error", "code": "G3", "target": name,
                                  "fixable": bool(geo),
                                  "msg": "link「%s」惯量不满足三角不等式" % name})

    # 控制层
    for el in root.iter("ros2_control"):
        out["control"]["ros2_control"] = True
        break
    for el in root.iter("plugin"):
        fn = (el.get("filename") or "") + " " + (el.get("name") or "")
        if "gz_ros2_control" in fn:
            out["control"]["gz_plugin"] = True
        if "DiffDrive" in fn or "diff_drive" in fn:
            out["control"]["diff_drive"] = True

    return out
