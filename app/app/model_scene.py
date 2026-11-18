# -*- coding: utf-8 -*-
"""把 URDF / xacro 解析成前端可直接绘制的场景描述。

为什么不让前端自己解析 URDF
--------------------------
URDF 里有三件事前端做起来很别扭：xacro 表达式求值、`<material>` 名字到颜色的
间接引用、以及 rpy（固定轴 XYZ）到四元数的换算。这三件在 Python 里都是十几行，
在前端要重写一遍还得处理表达式安全。所以服务端算好「关节树 + 每个 link 的
primitive 参数 + 颜色」，前端只做一件事：按树摆位置、拉滑块。

mesh 的处理
-----------
生成的模型全是 primitive，能精确画出来。用户上传的模型如果用了 mesh，
服务端把网格字节 base64 内联进场景，前端解析后按实体渲染：
- 二进制 STL：前端直接解析（stlVertexData）；
- DAE（Collada）：真实 ROS 模型的 visual 常用它（panda/ur 等），前端不认，
  这里服务端解析出三角网格、转成二进制 STL 再内联，前端零改动；
- 其它格式（obj/gltf/ascii-stl）暂不支持，缺数据时前端画占位小方块并在
  warnings 里说明，**不假装知道它长什么样**。
"""
import os
import re
import struct
import xml.etree.ElementTree as ET

from . import sim_gate

_PALETTE = ["#4a90d9", "#e8833a", "#5cb85c", "#9b59b6",
            "#16a085", "#d9534f", "#7f8c8d", "#c9a227"]


def _vec(text, props, default=(0.0, 0.0, 0.0)):
    # 不能按空格直接 split：`${-base_height/2 + 0.010}` 里有空格，
    # 切开就变成 5 段、解析失败、退回 (0,0,0)，表现是「所有部件叠在原点」。
    # 用正则把 ${...} 整体当作一个 token。
    parts = re.findall(r"\$\{[^}]*\}|\S+", str(text or ""))
    if len(parts) != 3:
        return list(default)
    out = []
    for p in parts:
        v = sim_gate._safe_eval(p, props)
        if v is None:
            return list(default)
        out.append(round(v, 6))
    return out


def _num(text, props, default=None):
    v = sim_gate._safe_eval(text, props)
    return default if v is None else round(v, 6)


def _hex_of(rgba):
    try:
        nums = [float(x) for x in str(rgba).split()[:3]]
    except (TypeError, ValueError):
        return None
    if len(nums) < 3:
        return None
    return "#%02x%02x%02x" % tuple(max(0, min(255, int(n * 255))) for n in nums)


def _materials(root):
    """collect name → hex，供 <material name="x"/> 这种引用反查颜色。"""
    out = {}
    for m in root.iter("material"):
        name = m.get("name")
        c = m.find("color")
        if name and c is not None:
            h = _hex_of(c.get("rgba"))
            if h:
                out[name] = h
    return out


def _geometry(el, props):
    """取 visual/collision 下的几何，返回 dict 或 None。"""
    if el is None:
        return None
    for tag in ("box", "cylinder", "sphere"):
        # 几何在 <visual><geometry><box/></geometry></visual> 里，必须递归找
        g = el.find(".//" + tag)
        if g is None:
            continue
        if tag == "box":
            size = _vec(g.get("size"), props)
            if all(v > 0 for v in size):
                return {"kind": "box", "size": size}
        elif tag == "cylinder":
            r = _num(g.get("radius"), props)
            L = _num(g.get("length"), props)
            if r and L and r > 0 and L > 0:
                return {"kind": "cylinder", "radius": r, "length": L}
        else:
            r = _num(g.get("radius"), props)
            if r and r > 0:
                return {"kind": "sphere", "radius": r}
    mesh = el.find(".//mesh")
    if mesh is not None:
        fn = mesh.get("filename") or ""
        # 路径要分三种写法处理：只有 package:// 才需要剥掉包名；
        # 相对路径（meshes/a.dae、../meshes/a.dae）原样保留 —— 早先统一剥掉首段，
        # 会把相对路径的目录也吃掉，导致 mesh 找不到、退化成占位方块。
        if "package://" in fn:
            rest = fn.split("package://", 1)[1]
            rel = rest.split("/", 1)[1] if "/" in rest else rest
        elif fn.startswith("file://"):
            rel = fn[len("file://"):].lstrip("/")
        else:
            rel = fn
        sc = (mesh.get("scale") or "1 1 1").split()
        try:
            scale = [float(v) for v in (sc + ["1", "1", "1"])[:3]]
        except ValueError:
            scale = [1.0, 1.0, 1.0]
        return {"kind": "mesh", "file": fn.split("/")[-1], "rel": rel,
                "scale": scale}
    return None


def _visuals_of(link, props, mats, warnings):
    out = []
    for vis in link.findall("visual"):
        geo = _geometry(vis, props)
        if not geo:
            continue
        origin = vis.find("origin")
        xyz = _vec(origin.get("xyz"), props) if origin is not None else [0, 0, 0]
        rpy = _vec(origin.get("rpy"), props) if origin is not None else [0, 0, 0]
        color = None
        mat = vis.find("material")
        if mat is not None:
            c = mat.find("color")
            if c is not None:
                color = _hex_of(c.get("rgba"))
            if not color and mat.get("name"):
                color = mats.get(mat.get("name"))
        if geo["kind"] == "mesh":
            warnings.append("「%s」用 mesh 绘制（%s），尺寸未知时预览显示为虚线框"
                            % (link.get("name"), geo.get("file") or "mesh"))
        out.append({"geometry": geo, "xyz": xyz, "rpy": rpy, "color": color})
    return out


def parse_model(text):
    """解析一份 URDF/xacro 文本。返回 None 表示解析失败。"""
    try:
        root = ET.fromstring(text.encode("utf-8"))
    except ET.ParseError as e:
        return {"ok": False, "error": "XML 不合法：%s" % e}

    props = sim_gate._props_of(text)
    mats = _materials(root)
    warnings = []

    links = []
    for i, link in enumerate(root.iter("link")):
        name = link.get("name")
        if not name:
            continue
        visuals = _visuals_of(link, props, mats, warnings)
        for v in visuals:
            if not v["color"]:
                v["color"] = _PALETTE[i % len(_PALETTE)]
        links.append({"name": name, "visuals": visuals})

    joints = []
    for j in root.iter("joint"):
        name = j.get("name")
        p, c = j.find("parent"), j.find("child")
        if not name or p is None or c is None:
            continue
        origin = j.find("origin")
        axis = j.find("axis")
        lim = j.find("limit")
        jtype = (j.get("type") or "fixed").lower()
        item = {
            "name": name,
            "parent": p.get("link"),
            "child": c.get("link"),
            "type": jtype,
            "xyz": _vec(origin.get("xyz"), props) if origin is not None else [0, 0, 0],
            "rpy": _vec(origin.get("rpy"), props) if origin is not None else [0, 0, 0],
            "axis": _vec(axis.get("xyz"), props, (1, 0, 0)) if axis is not None else [1, 0, 0],
        }
        if jtype in ("revolute", "prismatic"):
            lo = _num(lim.get("lower"), props, -3.1416) if lim is not None else -3.1416
            hi = _num(lim.get("upper"), props, 3.1416) if lim is not None else 3.1416
            item["limit"] = [lo, hi]
            item["value"] = 0.0
        elif jtype == "continuous":
            item["limit"] = [-3.1416, 3.1416]
            item["value"] = 0.0
        joints.append(item)

    if not links:
        return {"ok": False, "error": "没有解析到 <link>（可能是 xacro 宏或 include 拼出来的）"}

    # 根 = 没有被任何关节当作 child 的 link
    children = {j["child"] for j in joints}
    roots = [l["name"] for l in links if l["name"] not in children]
    return {
        "ok": True,
        "robot": root.get("name") or "",
        "links": links,
        "joints": joints,
        "root": roots[0] if roots else links[0]["name"],
        "warnings": sorted(set(warnings)),
    }


def _pkg_root_of(path):
    """从文件路径往上找含 package.xml 的目录（找不到就退回文件所在目录）。"""
    d = os.path.dirname(os.path.abspath(path))
    cur = d
    for _ in range(6):
        if os.path.isfile(os.path.join(cur, "package.xml")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        cur = parent
    return d


def _resolve_include(fn, base_dir, pkg_root):
    """把 xacro:include 的 filename 解析成实际文件路径（支持 package://、$(find)、相对路径）。"""
    fn = (fn or "").strip()
    if not fn:
        return None
    m = re.match(r"package://[^/]+/(.+)$", fn)
    if m:
        p = os.path.join(pkg_root, m.group(1))
        return p if os.path.isfile(p) else None
    m = re.match(r"\$\(find\s+[^)]+\)/(.+)$", fn)
    if m:
        p = os.path.join(pkg_root, m.group(1))
        return p if os.path.isfile(p) else None
    p = os.path.join(base_dir, fn)
    return p if os.path.isfile(p) else None


_INCLUDE_RE = re.compile(r'<xacro:include\s+[^>]*filename="([^"]+)"[^>]*/?>', re.I)


def _strip_robot_wrapper(text):
    """去掉 XML 声明和 <robot> 根标签，只留内部内容。

    include 拼接时如果把被包含文件的 `<?xml?>` 和 `<robot>` 一起塞进来，
    就会得到「一个文档里多个根标签」的非法 XML，ET 直接解析失败、整个文件被跳过。
    """
    t = re.sub(r"<\?xml[^>]*\?>", "", text, flags=re.I)
    m = re.search(r"<robot\b[^>]*>", t, re.I)
    if m:
        t = t[m.end():]
        idx = t.rfind("</robot>")
        if idx >= 0:
            t = t[:idx]
    return t


def _expand_includes(text, base_dir, pkg_root, seen=None, depth=0):
    """递归展开 xacro:include，把被包含文件的内容拼进来。

    一个 description 包常把模型拆成 base/arm/gripper 多个 xacro 再 include 拼起来。
    不展开 include、只解析单个文件，就会只画出一个部件（用户看到的「修了一半」）。
    这里只做 include 展开（文件拼接），不做宏展开 —— parse_model 的 `root.iter("link")`
    能穿过 `<xacro:macro>` 找到里面的 link 标签，所以宏里定义的部件本来就能看到。
    """
    if depth > 8:
        return text
    if seen is None:
        seen = set()

    def repl(m):
        p = _resolve_include(m.group(1), base_dir, pkg_root)
        if not p or p in seen:
            return ""
        seen.add(p)
        try:
            sub = open(p, encoding="utf-8", errors="replace").read()
        except OSError:
            return ""
        sub = _strip_robot_wrapper(sub)     # 只拼内部内容，保住单一根标签
        return _expand_includes(sub, os.path.dirname(p), pkg_root, seen, depth + 1)

    return _INCLUDE_RE.sub(repl, text)


_MACRO_DEF_RE = re.compile(
    r"<xacro:macro\s+[^>]*name=\"([^\"]+)\"[^>]*params=\"([^\"]*)\"[^>]*>(.*?)</xacro:macro>",
    re.I | re.S)
_MACRO_CALL_RE = re.compile(
    r"<xacro:([A-Za-z_][\w.-]*)((?:\s+[\w.-]+=\"[^\"]*\")*)\s*(?:/>|>\s*</xacro:\1>)")
_MACRO_ATTR_RE = re.compile(r"([\w.-]+)=\"([^\"]*)\"")


def _expand_macros(text):
    """展开最基础的 xacro:macro（参数替换），让 `${prefix}1_link` 变成实名字。

    只处理「无嵌套条件、无块参数」的常见宏；展开失败就原样保留 —— parse_model
    的 `root.iter("link")` 本来就能穿过 `<xacro:macro>` 看到里面的 link，
    所以最差情况也只是名字带 `${...}`，不会少画部件。
    """
    defs = {}
    for m in _MACRO_DEF_RE.finditer(text):
        name, params, body = m.group(1), m.group(2), m.group(3)
        pnames, defaults = [], {}
        for p in params.split():
            if ":=" in p:
                k, v = p.split(":=", 1)
                k, v = k.strip(), v.strip().strip("'\"")
                pnames.append(k)
                defaults[k] = v
            elif p.strip():
                pnames.append(p.strip())
        defs[name] = (pnames, defaults, body)
    if not defs:
        return text
    text = _MACRO_DEF_RE.sub("", text)      # 先删宏定义，避免里面的模板 link 被重复解析

    def repl(m):
        name = m.group(1)
        if name not in defs:
            return m.group(0)
        pnames, defaults, body = defs[name]
        args = dict(defaults)
        for am in _MACRO_ATTR_RE.finditer(m.group(2) or ""):
            args[am.group(1)] = am.group(2)
        out = body
        for p in pnames:
            val = args.get(p, "")
            out = out.replace("${%s}" % p, val)
            out = out.replace("$(arg %s)" % p, val)
        return out

    for _ in range(6):                      # 宏里再调宏的情况，多展开几轮
        new = _MACRO_CALL_RE.sub(repl, text)
        if new == text:
            break
        text = new
    return text


def _is_template(name):
    """名字里还残留未展开的 xacro 表达式（${...} / $(...)）→ 是模板，不是真实部件。"""
    n = name or ""
    return "${" in n or "$(" in n


def _candidates(sess):
    """优先给「刚生成的仿真包」出图，其次才是导入的模型。"""
    root = sess.ws_root()
    out = []
    if not os.path.isdir(root):
        return out
    for dirpath, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in ("build", "install", "log", ".git")]
        for fn in files:
            low = fn.lower()
            if not low.endswith((".urdf", ".xacro")):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            # 生成的模型一定在 <pkg>/urdf/ 下，优先
            pri = 0 if "/urdf/" in rel else 1
            if "/mesh/" in rel:
                continue
            out.append((pri, rel, full))
    out.sort()
    return [(rel, full) for _p, rel, full in out]


def build_scene(sess, pkg=None):
    """解析并合并会话里所有模型文件，返回场景描述 + 候选清单。

    pkg 用来锁定「刚生成的那个包」：同一会话里可能同时存在机械臂和小车，
    不带 pkg 就会按 link 数挑到别人家去（真实踩过：回复是 my_car，预览画的是 my_arm）。

    为什么不是「挑一个文件」：一个 description 包常把模型拆成 base/arm/gripper
    多个 xacro 用 include 拼起来。只解析单个文件就会只画出一个部件 —— 这正是
    「预览只修了一半、另一半还是方块」的原因。这里对每个候选文件展开 include 后
    解析，再按名字合并，无论拆成几个文件都能拼全。
    """
    cands = _candidates(sess)
    if pkg:
        key = "/%s/" % pkg
        hit = [(r, f) for r, f in cands if key in ("/" + r)]
        if hit:
            cands = hit
    if not cands:
        return {"ok": False, "error": "会话里还没有模型文件"}

    merged_links, merged_joints = {}, {}
    _tmpl_links, _tmpl_joints = {}, {}      # 未展开的模板（名字里还有 ${...}）
    warnings = set()
    robot_name = ""
    tried = []
    best_source, best_visuals, best_pkg_root = "", -1, None
    for rel, full in cands[:20]:
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        pkg_root = _pkg_root_of(full)
        expanded = _expand_includes(text, os.path.dirname(full), pkg_root)
        expanded = _expand_macros(expanded)
        scene = parse_model(expanded)
        if not scene.get("ok"):
            tried.append({"path": rel, "error": scene.get("error")})
            continue
        vcount = sum(len(l["visuals"]) for l in scene["links"])
        if vcount > best_visuals:
            best_visuals, best_source, best_pkg_root = vcount, rel, pkg_root
        if not robot_name:
            robot_name = scene.get("robot") or ""
        for l in scene["links"]:
            nm = l["name"]
            # 未展开的模板 link（名字里还有 ${...}/$(...)）不是真实部件：panda 这类包
            # 同时存在「已展开的 panda.urdf」和「宏定义 panda_arm.xacro」，两者都解析会把
            # 同一批 link 收两遍（一份正常名、一份 ${prefix}... 模板名），预览里会多出一堆幽灵部件。
            (_tmpl_links if _is_template(nm) else merged_links).setdefault(nm, l)
        for j in scene["joints"]:
            nm = j["name"]
            (_tmpl_joints if _is_template(nm) else merged_joints).setdefault(nm, j)
        warnings.update(scene.get("warnings") or [])

    # 优先用真实展开的 link；只有「整个模型都是模板」（宏展开彻底失败）时才回退用模板名，
    # 保证最差情况下也能看到部件，而不是一片空白。
    if not merged_links and _tmpl_links:
        merged_links = _tmpl_links
    if not merged_joints and _tmpl_joints:
        merged_joints = _tmpl_joints
    links = list(merged_links.values())
    if not links:
        return {"ok": False, "error": "没找到可解析的模型", "tried": tried[:5]}

    # 合并后可能有 joint 指向没解析出来的 link（跨文件但那个文件解析失败），
    # 这类 joint 留着会让前端画断线，直接丢掉。
    link_names = {l["name"] for l in links}
    joints = [j for j in merged_joints.values()
              if j.get("parent") in link_names and j.get("child") in link_names]

    children = {j["child"] for j in joints}
    roots = [l["name"] for l in links if l["name"] not in children]
    result = {
        "ok": True,
        "robot": robot_name,
        "links": links,
        "joints": joints,
        "root": roots[0] if roots else links[0]["name"],
        "warnings": sorted(warnings),
        "source": best_source,
        "candidates": [rel for rel, _f in cands[:12]],
    }
    if best_pkg_root:
        _attach_mesh_data(result, best_pkg_root)
    return result


def _pkg_of_rel(rel):
    """从相对路径猜模型所属包名：src/my_car/urdf/my_car.urdf → my_car。

    猜不出来就返回空串 —— 前端据此只做「全部模型」预览，不会锁错包。
    """
    segs = (rel or "").split("/")
    if "urdf" in segs:
        i = segs.index("urdf")
        if i >= 1:
            return segs[i - 1]
    if len(segs) >= 3 and segs[0] in ("src", "install"):
        return segs[1]
    return ""


def detect(sess, pkg=None):
    """轻量探测会话里有没有可预览的模型（只扫文件名，不解析 xacro）。

    给前端「输入框末尾是否弹预览入口」用。刻意与 build_scene 分开：
    每次打开会话都要问一次，不能为了弹个按钮去解析整套 URDF。
    """
    try:
        cands = _candidates(sess)
    except Exception:            # noqa: BLE001 — 探测失败就当没有，不影响主流程
        cands = []
    if pkg:
        key = "/%s/" % pkg
        hit = [(r, f) for r, f in cands if key in ("/" + r)]
        if hit:
            cands = hit
    files = [rel for rel, _full in cands[:20]]
    return {
        "available": bool(files),
        "count": len(files),
        "files": files[:8],
        "package": _pkg_of_rel(files[0]) if files else "",
    }


MESH_TOTAL_LIMIT = 24_000_000     # 一场预览最多带 24MB 网格（与离线演示页的 24MB 对齐）
                                  # 真实 CAD 机械臂/底盘整包网格常见 5–15MB，8MB 会把稍大的
                                  # 模型卡掉一半零件；24MB 在本地/局域网预览下仍可接受。


# ---------------------------------------------------------------------------
# DAE(Collada) → 二进制 STL
#
# 真实 ROS 模型的 visual 大概率用 .dae（panda、ur 都是），前端只会解二进制 STL。
# 这里在服务端把 DAE 的三角网格抽出来，拼成标准 STL 字节，前端一行都不用改。
# 只处理「静态外观」需要的部分：positions + triangles/polylist，法线按面重新算。
# ---------------------------------------------------------------------------

def _dae_local(tag):
    return tag.split("}")[-1] if "}" in tag else tag


def _dae_to_stl(raw):
    """解析 Collada(.dae) 的三角网格 → 二进制 STL 字节；失败返回 None。"""
    try:
        root = ET.fromstring(raw)
    except ET.ParseError:
        return None
    tris = []
    for mesh in root.iter():
        if _dae_local(mesh.tag) != "mesh":
            continue
        # 收集本 mesh 下所有 float_array（按 source id 索引）
        sources = {}
        for src in mesh:
            if _dae_local(src.tag) != "source":
                continue
            sid = src.get("id")
            for child in src:
                if _dae_local(child.tag) == "float_array":
                    try:
                        sources[sid] = [float(x) for x in (child.text or "").split()]
                    except ValueError:
                        pass
        # <vertices> 把 POSITION 语义映射到某个 source
        vtx_pos = {}
        for vtx in mesh:
            if _dae_local(vtx.tag) != "vertices":
                continue
            vid = vtx.get("id")
            for inp in vtx:
                if _dae_local(inp.tag) == "input" and inp.get("semantic") == "POSITION":
                    vtx_pos[vid] = sources.get((inp.get("source") or "").lstrip("#"), [])
        # <triangles>/<polylist> 的索引
        for tri in mesh:
            if _dae_local(tri.tag) not in ("triangles", "polylist"):
                continue
            ins = []
            for inp in tri:
                if _dae_local(inp.tag) == "input":
                    ins.append((inp.get("semantic"),
                                (inp.get("source") or "").lstrip("#"),
                                int(inp.get("offset") or 0)))
            p = next((c for c in tri if _dae_local(c.tag) == "p"), None)
            if p is None:
                continue
            try:
                idx = [int(x) for x in (p.text or "").split()]
            except ValueError:
                continue
            if not idx:
                continue
            stride = max(o for _s, _src, o in ins) + 1
            vtx_off = next((o for s, _src, o in ins if s == "VERTEX"), 0)
            vtx_src = next((src for s, src, _o in ins if s == "VERTEX"), None)
            pos = vtx_pos.get(vtx_src, [])
            nvert = len(idx) // stride
            for k in range(0, nvert - 2, 3):
                pts = []
                for j in range(3):
                    vi = idx[(k + j) * stride + vtx_off]
                    if vi * 3 + 2 >= len(pos):
                        pts = []
                        break
                    pts.append((pos[vi * 3], pos[vi * 3 + 1], pos[vi * 3 + 2]))
                if len(pts) == 3:
                    tris.append(pts)
    if not tris:
        return None
    out = bytearray(80)                       # STL 头（留空）
    out += struct.pack("<I", len(tris))
    for a, b, c in tris:
        ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        ln = (nx * nx + ny * ny + nz * nz) ** 0.5 or 1.0
        nx, ny, nz = nx / ln, ny / ln, nz / ln
        out += struct.pack("<12fH", nx, ny, nz,
                           a[0], a[1], a[2], b[0], b[1], b[2], c[0], c[1], c[2], 0)
    return bytes(out)


def _load_mesh_bytes(path, rel):
    """读一个 mesh 文件，返回前端可直接解析的二进制 STL 字节（DAE 先转换）。"""
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return None
    if rel.lower().endswith(".dae"):
        return _dae_to_stl(raw)
    return raw      # .stl 原样返回；其它格式前端解析失败会自己降级


_SKIP_MESH_DIRS = {"build", "install", "log", ".git", "__pycache__"}


def _attach_mesh_data(scene, pkg_root, limit=None):
    """把 mesh 文件的原始字节以 base64 塞进场景，前端解析后直接画。

    为什么内联而不是给个 URL：预览要能在独立演示页（无后端）里跑，
    多一个取文件的接口就多一条只能在服务器上用的路径。

    路径匹配要宽容：URDF 写 `package://pkg/meshes/visual/x.dae`，真实包里
    却可能是 `pkg/panda/meshes/visual/x.dae`（panda 源码布局就这样，install 时
    symlink 才抹平差异）。只按标准路径拼、找不到就跳过，会让整包 mesh 一个都
    加载不出来 —— 预览全是占位方块。这里先试标准路径，不行再按文件名兜底。
    """
    import base64
    cap = MESH_TOTAL_LIMIT if limit is None else limit
    total = 0
    _index = {"map": None}

    def _mesh_files():
        if _index["map"] is None:
            m = {}
            for dirpath, dirs, files in os.walk(pkg_root):
                dirs[:] = [d for d in dirs if d not in _SKIP_MESH_DIRS]
                for fn in files:
                    m.setdefault(fn.lower(), []).append(os.path.join(dirpath, fn))
            _index["map"] = m
        return _index["map"]

    def find_mesh(rel):
        rel = (rel or "").replace("\\", "/").lstrip("/")
        if not rel:
            return None
        p = os.path.join(pkg_root, rel.replace("/", os.sep))
        if os.path.isfile(p):
            return p
        cands = _mesh_files().get(os.path.basename(rel).lower()) or []
        for c in cands:                     # 优先「路径后缀一致」的，避免同名文件选错
            if c.replace(os.sep, "/").lower().endswith("/" + rel.lower()):
                return c
        return cands[0] if cands else None

    missing = []                     # (link, file, 原因) —— 缺失的网格必须能解释
    for link in scene["links"]:
        for v in link["visuals"]:
            g = v["geometry"]
            if g.get("kind") != "mesh" or not g.get("rel"):
                continue
            path = find_mesh(g["rel"])
            if not path:
                missing.append((link.get("name"), g.get("file") or g.get("rel"),
                                "没有随包导入（文件缺失或超过单文件导入上限）"))
                continue
            data = _load_mesh_bytes(path, g["rel"])
            if data is None:
                missing.append((link.get("name"), g.get("file") or g.get("rel"), "读取失败"))
                continue
            if total + len(data) > cap:
                missing.append((link.get("name"), g.get("file") or g.get("rel"),
                                "超出单次预览的网格总量上限"))
                continue
            total += len(data)
            g["data"] = base64.b64encode(data).decode("ascii")
            g["bytes"] = len(data)
    if total:
        scene["mesh_bytes"] = total
        # 网格已经内联进来了，就一定会按实体渲染 —— 去掉「尺寸未知、显示为虚线框」的提示，
        # 否则界面会挂一条与实际不符的警告（用户会以为模型没画出来）
        scene["warnings"] = [w for w in scene.get("warnings", [])
                             if "虚线框" not in w]
    # 找不到 / 读不出 / 超预算的网格：前端会退回 3cm 占位方块。这里必须明说哪个 link
    # 的哪个文件没了，否则用户只看到「模型长得不对」，完全无从排查。
    if missing:
        ws = list(scene.get("warnings") or [])
        for lname, fname, why in missing:
            ws.append("「%s」的网格 %s %s，预览以占位方块代替（真实外观缺失）"
                      % (lname, fname, why))
        scene["warnings"] = sorted(set(ws))
    return scene
