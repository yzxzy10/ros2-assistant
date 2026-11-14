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
这里没有 STL 可读，只能标成 wire（前端画一个虚线小方块），并在 warnings 里
说明「这个 link 是 mesh，尺寸未知」。**不假装知道它长什么样**。
"""
import os
import re
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
        return {"kind": "mesh", "file": (mesh.get("filename") or "").split("/")[-1]}
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
            warnings.append("「%s」用 mesh 绘制（%s），尺寸未知，预览里显示为虚线框"
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
    """挑一个最能说明问题的模型，返回场景描述 + 候选清单。

    pkg 用来锁定「刚生成的那个包」：同一会话里可能同时存在机械臂和小车，
    不带 pkg 就会按 link 数挑到别人家去（真实踩过：回复是 my_car，预览画的是 my_arm）。
    """
    cands = _candidates(sess)
    if pkg:
        key = "/%s/" % pkg
        hit = [(r, f) for r, f in cands if key in ("/" + r)]
        if hit:
            cands = hit
    if not cands:
        return {"ok": False, "error": "会话里还没有模型文件"}

    best, best_score, tried = None, -1, []
    for rel, full in cands[:12]:
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            continue
        scene = parse_model(text)
        if not scene.get("ok"):
            tried.append({"path": rel, "error": scene.get("error")})
            continue
        score = sum(len(l["visuals"]) for l in scene["links"])
        if score > best_score:
            best, best_score = scene, score
            best["source"] = rel
    if not best:
        return {"ok": False, "error": "没找到可解析的模型",
                "tried": tried[:5]}
    best["candidates"] = [rel for rel, _f in cands[:12]]
    return best
