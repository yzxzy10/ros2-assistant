# -*- coding: utf-8 -*-
"""架构图的数据层：图纸草稿、合并视图、节点代码读写、提交回写。

设计口径（和产品约定一一对应）：
1. 图纸（草稿）和代码是两份东西。改话题名、拖线、加节点，全写进
   sessions/<sid>/graph.json，不动任何 .py；
2. 只有 commit() 才把图纸落到代码上；
3. 解析结果永远是「代码的镜子」，每次读取都重新解析，不缓存脏数据；
4. 代码编辑写的是云端工作区里的文件，写之前先做快照，可回退。
"""
import hashlib
import json
import os
import re
import shutil
import time

from . import graph, llm, paths

DRAFT_NAME = "graph.json"
SNAP_DIR = "snapshots"
_SNAP_KEEP = 20

TEXT_EXT = (".py", ".xml", ".yaml", ".yml", ".txt", ".md", ".launch",
            ".cfg", ".json", ".ini", ".toml", ".cpp", ".hpp", ".h", ".c",
            ".cc", ".msg", ".srv", ".action", ".urdf", ".xacro", ".rviz")

EDIT_EXT = {".py", ".xml", ".yaml", ".yml", ".txt", ".md", ".cfg", ".json",
            ".ini", ".toml", ".cpp", ".hpp", ".h", ".c", ".cc", ".launch",
            ".urdf", ".xacro", ".msg", ".srv", ".action", ".sh"}

# 画布上「新建连接」时的消息类型候选（常用子集，够 90% 的场景）
COMMON_TYPES = [
    "std_msgs/msg/String", "std_msgs/msg/Bool", "std_msgs/msg/Int32",
    "std_msgs/msg/Float32", "std_msgs/msg/Empty", "std_msgs/msg/Header",
    "geometry_msgs/msg/Twist", "geometry_msgs/msg/Pose",
    "geometry_msgs/msg/PoseStamped", "geometry_msgs/msg/PoseWithCovarianceStamped",
    "geometry_msgs/msg/Point", "geometry_msgs/msg/PointStamped",
    "geometry_msgs/msg/TransformStamped", "geometry_msgs/msg/Quaternion",
    "sensor_msgs/msg/LaserScan", "sensor_msgs/msg/Image",
    "sensor_msgs/msg/CompressedImage", "sensor_msgs/msg/PointCloud2",
    "sensor_msgs/msg/Imu", "sensor_msgs/msg/JointState", "sensor_msgs/msg/Range",
    "sensor_msgs/msg/BatteryState", "sensor_msgs/msg/CameraInfo",
    "nav_msgs/msg/Odometry", "nav_msgs/msg/OccupancyGrid", "nav_msgs/msg/Path",
    "nav_msgs/msg/MapMetaData", "tf2_msgs/msg/TFMessage",
    "visualization_msgs/msg/Marker", "visualization_msgs/msg/MarkerArray",
    "std_srvs/srv/Trigger", "std_srvs/srv/SetBool", "std_srvs/srv/Empty",
    "nav2_msgs/action/NavigateToPose", "nav2_msgs/action/FollowPath",
    "lifecycle_msgs/srv/ChangeState",
]


# ---------------- 路径 ----------------
def session_dir(sess):
    return os.path.join(paths.sessions(), sess.sid)


def draft_path(sess):
    return os.path.join(session_dir(sess), DRAFT_NAME)


def _empty_draft():
    return {
        "version": 1,
        "updated_at": "",
        "positions": {},
        "renames": {},
        "deleted": [],
        "draft_nodes": [],
        "draft_links": [],
        "intro": {},
    }


def read_draft(sess):
    p = draft_path(sess)
    if not os.path.isfile(p):
        return _empty_draft()
    try:
        with open(p, "r", encoding="utf-8") as f:
            d = json.load(f)
    except Exception:              # noqa: BLE001
        return _empty_draft()
    base = _empty_draft()
    base.update(d if isinstance(d, dict) else {})
    return base


def write_draft(sess, draft):
    d = _empty_draft()
    d.update(draft or {})
    d["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    p = draft_path(sess)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, p)
    return d


# ---------------- 合并视图 ----------------
def _apply_renames(node, renames):
    hit = []
    for field in ("publishes", "subscribes", "service_servers",
                  "service_clients", "actions"):
        for it in node.get(field) or []:
            old = it.get("topic") or ""
            if old in renames and renames[old] and renames[old] != old:
                it["topic_original"] = old
                it["topic"] = renames[old]
                it["pending"] = True
                hit.append(old)
    return hit


def _packages_with_build(packages, sess):
    """给建图结果里的包补上 build_type（ament_cmake / ament_python / inferred）。

    graph.analyze 只扫目录、不读 package.xml，所以它不知道一个包是 C++ 还是 Python。
    前端要靠这个字段解释「为什么图是空的」——纯 C++ 项目 vs 没有 rclpy 节点，
    是两种完全不同的原因，给人的建议也不一样。
    """
    known = {p.get("name"): p.get("build_type") or ""
             for p in ((getattr(sess, "imported_project", None) or {}).get("packages") or [])}
    out = []
    for p in packages or []:
        q = dict(p)
        q["build_type"] = known.get(q.get("name"), "") or ""
        out.append(q)
    # graph.analyze 只收「有 Python 节点」的包。纯 C++ 包会整个消失，
    # 前端就分不清「这项目是 C++」和「解析失败」了 —— 这里把导入时识别到的包补回来。
    seen = {p.get("name") for p in out}
    for p in ((getattr(sess, "imported_project", None) or {}).get("packages") or []):
        if p.get("name") in seen:
            continue
        out.append({"name": p.get("name"), "files": 0, "nodes": 0,
                    "build_type": p.get("build_type") or ""})
    return out


def load(sess):
    """解析工作区 + 叠加图纸草稿，返回前端直接可画的图。"""
    root = sess.ws_root()
    g = graph.analyze(root)
    draft = read_draft(sess)
    renames = {k: v for k, v in (draft.get("renames") or {}).items()
               if isinstance(v, str) and v and v != k}
    deleted = set(draft.get("deleted") or [])

    nodes = []
    for n in g["nodes"]:
        if n["id"] in deleted:
            continue
        _apply_renames(n, renames)
        pos = (draft.get("positions") or {}).get(n["id"]) or {}
        n["x"] = pos.get("x")
        n["y"] = pos.get("y")
        nodes.append(n)

    ids = {n["id"] for n in nodes}

    # 图纸里新增、还没生成代码的节点
    for dn in draft.get("draft_nodes") or []:
        if dn.get("id") in deleted:
            continue
        pos = (draft.get("positions") or {}).get(dn.get("id")) or {}
        nodes.append({
            "id": dn.get("id"),
            "name": dn.get("name") or "new_node",
            "cls": "", "file": "", "package": dn.get("package") or "",
            "line": 0, "loc": 0,
            "publishes": dn.get("publishes") or [],
            "subscribes": dn.get("subscribes") or [],
            "service_servers": [], "service_clients": [], "actions": [],
            "params": [], "has_timer": False,
            "status": "draft",
            "requirement": dn.get("requirement") or "",
            "orphan": True, "unresolved_count": 0,
            "x": pos.get("x"), "y": pos.get("y"),
        })
        ids.add(dn.get("id"))

    # 连线：解析出的真实连线（话题改名后重新按名字归并）
    edges, seen = [], set()
    by_topic = {}
    for n in nodes:
        if n.get("status") == "draft":
            continue
        for field, role in (("publishes", "pub"), ("subscribes", "sub")):
            for it in n.get(field) or []:
                if not it.get("resolved", True):
                    continue
                t = it.get("topic") or ""
                if not t:
                    continue
                rec = by_topic.setdefault(t, {"pubs": [], "subs": [], "types": []})
                (rec["pubs"] if role == "pub" else rec["subs"]).append(n["id"])
                if it.get("type") and it["type"] not in rec["types"]:
                    rec["types"].append(it["type"])

    for topic, rec in sorted(by_topic.items()):
        for p in rec["pubs"]:
            for s in rec["subs"]:
                if p == s:
                    continue
                key = (p, s, topic)
                if key in seen:
                    continue
                seen.add(key)
                edges.append({"from": p, "to": s, "topic": topic,
                              "type": rec["types"][0] if rec["types"] else "",
                              "draft": False})

    # 图纸上手工连的线（可能一端是还没生成的新节点）
    for ln in draft.get("draft_links") or []:
        a, b = ln.get("from"), ln.get("to")
        if not a or not b or a not in ids or b not in ids:
            continue
        topic = ln.get("topic") or ""
        if topic in renames and renames[topic] and renames[topic] != topic:
            topic = renames[topic]
        key = (a, b, topic)
        if key in seen:
            continue
        seen.add(key)
        edges.append({"from": a, "to": b, "topic": topic,
                      "type": ln.get("type") or "", "draft": True,
                      "tbd": not (ln.get("type") or ""),
                      "pending": True})

    for n in nodes:
        n["orphan"] = not any(e["from"] == n["id"] or e["to"] == n["id"]
                              for e in edges)

    topics = {}
    for t in g["topics"]:
        old = t["name"]
        new = renames.get(old, old)
        rec = topics.setdefault(new, {"name": new, "types": list(t["types"]),
                                      "pubs": list(t["pubs"]), "subs": list(t["subs"]),
                                      "original": old if new != old else ""})
        rec["pubs"] = [x for x in rec["pubs"] if x in ids]
        rec["subs"] = [x for x in rec["subs"] if x in ids]
    for topic, rec in by_topic.items():
        rec.setdefault("name", topic)

    pending = (len(renames) + len(draft.get("draft_nodes") or [])
               + len(draft.get("deleted") or [])
               + len(draft.get("draft_links") or []))

    conflicts = []
    for name, rec in by_topic.items():
        if len(rec["types"]) > 1:
            conflicts.append({"topic": name, "types": rec["types"]})

    return {
        "ok": True,
        "session": sess.sid,
        "root": "ros2_ws",
        "park": sess.package_name or "",
        "nodes": nodes,
        "edges": edges,
        "topics": sorted(topics.values(), key=lambda x: x["name"]),
        "packages": _packages_with_build(g["packages"], sess),
        "warnings": g["warnings"],
        "conflicts": conflicts,
        "msg_types": COMMON_TYPES,
        "stats": dict(g["stats"], pending=pending,
                      drafts=len(draft.get("draft_nodes") or [])),
        "draft": {"renames": renames,
                  "updated_at": draft.get("updated_at", "")},
        "generated_at": g["generated_at"],
    }


def save_draft(sess, incoming):
    """前端只提交增量字段，这里做白名单合并，防止把解析结果写进图纸。"""
    cur = read_draft(sess)
    if not isinstance(incoming, dict):
        return cur
    for key in ("positions", "renames", "deleted", "draft_nodes", "draft_links"):
        if key in incoming and isinstance(incoming[key], (dict, list)):
            cur[key] = incoming[key]
    write_draft(sess, cur)
    return cur


# ---------------- 节点代码 ----------------
def _abs_in_ws(sess, rel):
    root = os.path.abspath(sess.ws_root())
    p = os.path.abspath(os.path.join(root, (rel or "").replace("/", os.sep)))
    if not p.startswith(root + os.sep):
        return None
    return p


def read_node_source(sess, rel):
    p = _abs_in_ws(sess, rel)
    if not p or not os.path.isfile(p):
        return {"ok": False, "error": "文件不存在"}
    if os.path.splitext(p)[1].lower() not in EDIT_EXT:
        return {"ok": False, "error": "这类文件不支持在线编辑"}
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            src = f.read()
    except OSError as e:
        return {"ok": False, "error": "读取失败：%r" % (e,)}
    if len(src) > 400 * 1024:
        return {"ok": False, "error": "文件太大（>400KB），请下载后编辑"}
    return {"ok": True, "path": rel, "content": src,
            "sha": hashlib.sha1(src.encode("utf-8")).hexdigest(),
            "mtime": os.path.getmtime(p)}


def _snapshot(sess, rel, src):
    ts = time.strftime("%Y%m%d-%H%M%S")
    base = os.path.join(session_dir(sess), SNAP_DIR, ts)
    dst = os.path.join(base, rel.replace("/", os.sep))
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    with open(dst, "w", encoding="utf-8", newline="\n") as f:
        f.write(src)
    _prune_snapshots(sess)
    return os.path.relpath(dst, session_dir(sess)).replace("\\", "/")


def _prune_snapshots(sess):
    base = os.path.join(session_dir(sess), SNAP_DIR)
    if not os.path.isdir(base):
        return
    items = sorted(d for d in os.listdir(base)
                   if os.path.isdir(os.path.join(base, d)))
    for d in items[:-_SNAP_KEEP]:
        shutil.rmtree(os.path.join(base, d), ignore_errors=True)


def save_node_source(sess, rel, content, expect_sha=""):
    """保存代码：快照 → 写入 → 重新解析该文件。不动本地磁盘。"""
    p = _abs_in_ws(sess, rel)
    if not p:
        return {"ok": False, "error": "路径不合法"}
    ext = os.path.splitext(p)[1].lower()
    if ext not in EDIT_EXT:
        return {"ok": False, "error": "这类文件不支持在线编辑"}
    if not isinstance(content, str):
        return {"ok": False, "error": "内容格式不对"}
    if len(content.encode("utf-8")) > 400 * 1024:
        return {"ok": False, "error": "内容太大（>400KB）"}

    old = ""
    if os.path.isfile(p):
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                old = f.read()
        except OSError:
            old = ""
        if expect_sha and hashlib.sha1(old.encode("utf-8")).hexdigest() != expect_sha:
            return {"ok": False, "conflict": True,
                    "error": "文件已被其他人改动，请刷新后再编辑"}

    snap = _snapshot(sess, rel, old) if old else ""
    os.makedirs(os.path.dirname(p), exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    os.replace(tmp, p)

    # 只重解析这一个文件，把最新的节点结构回给前端
    parsed = graph.parse_python(rel, content)
    if parsed.get("error"):
        return {"ok": False, "error": "已保存，但有语法错误：" + parsed["error"],
                "saved": True, "snapshot": snap}
    return {"ok": True, "saved": True, "snapshot": snap,
            "sha": hashlib.sha1(content.encode("utf-8")).hexdigest(),
            "nodes": parsed["nodes"]}


# ---------------- 节点简介（按需生成 + 缓存） ----------------
_INTRO_SYS = (
    "你在读一个 ROS2 节点的源码。用一句中文说明它做什么，不超过 40 字。"
    "说功能，不要列举话题名，不要客套，不要以「该节点」开头。"
)


def node_intro(sess, cfg, node_id, force=False):
    g = load(sess)
    node = next((n for n in g["nodes"] if n["id"] == node_id), None)
    if not node:
        return {"ok": False, "error": "图上没有这个节点"}
    if node.get("status") == "draft":
        req = (node.get("requirement") or "").strip()
        return {"ok": True, "intro": (req[:40] + "…") if len(req) > 40 else req,
                "cached": False, "draft": True}

    rel = node.get("file") or ""
    p = _abs_in_ws(sess, rel)
    if not p or not os.path.isfile(p):
        return {"ok": False, "error": "节点文件不存在"}
    with open(p, "r", encoding="utf-8", errors="replace") as f:
        src = f.read()

    key = "%s#%s" % (rel, hashlib.sha1(src.encode("utf-8")).hexdigest()[:12])
    draft = read_draft(sess)
    cache = draft.get("intro") or {}
    if not force and cache.get(key):
        return {"ok": True, "intro": cache[key], "cached": True}

    facts = graph.node_facts(node)
    excerpt = "\n".join(src.splitlines()[:120])
    user = ("节点名：%s\n发布 %d / 订阅 %d / 参数 %d / 约 %d 行\n\n源码节选：\n%s"
            % (node.get("name"), facts["publishes"], facts["subscribes"],
               facts["params"], facts["loc"], excerpt))
    try:
        text = llm.chat(cfg, "teaching", _INTRO_SYS, user, timeout=40,
                        temperature=0.3).strip()
        text = re.sub(r"\s+", " ", text).strip().strip("。") + "。"
        if len(text) > 80:
            text = text[:78] + "…"
    except Exception as e:         # noqa: BLE001
        return {"ok": False, "error": "简介生成失败：%s" % e,
                "fallback": _fallback_intro(node)}

    cache[key] = text
    draft["intro"] = cache
    write_draft(sess, draft)
    return {"ok": True, "intro": text, "cached": False}


def _fallback_intro(node):
    """模型不可用时的兜底：只用硬信息拼一句，不编功能。"""
    parts = []
    if node.get("publishes"):
        parts.append("发布 " + "、".join(i["topic"] for i in node["publishes"][:2]))
    if node.get("subscribes"):
        parts.append("订阅 " + "、".join(i["topic"] for i in node["subscribes"][:2]))
    return "；".join(parts) or "未解析到接口"


# ---------------- 提交：把图纸落到代码 ----------------
_PKG_XML = """<?xml version="1.0"?>
<?xml-model href="http://download.ros.org/schema/package_format3.xsd" schematypens="http://www.w3.org/2001/XMLSchema"?>
<package format="3">
  <name>%(name)s</name>
  <version>0.1.0</version>
  <description>%(desc)s</description>
  <maintainer email="student@example.com">student</maintainer>
  <license>Apache-2.0</license>

  <depend>rclpy</depend>
  <depend>std_msgs</depend>

  <test_depend>ament_copyright</test_depend>
  <test_depend>ament_flake8</test_depend>
  <test_depend>ament_pep257</test_depend>
  <test_depend>python3-pytest</test_depend>

  <export>
    <build_type>ament_python</build_type>
  </export>
</package>
"""

_SETUP_PY = """from setuptools import find_packages, setup

package_name = '%(name)s'

setup(
    name=package_name,
    version='0.1.0',
    packages=find_packages(exclude=['test']),
    data_files=[
        ('share/ament_index/resource_index/packages',
            ['resource/' + package_name]),
        ('share/' + package_name, ['package.xml']),
    ],
    install_requires=['setuptools'],
    zip_safe=True,
    maintainer='student',
    maintainer_email='student@example.com',
    description='%(desc)s',
    license='Apache-2.0',
    tests_require=['pytest'],
    entry_points={
        'console_scripts': [
%(entries)s        ],
    },
)
"""


def _ensure_package(root, pkg, nodes=None):
    """保证 src/<pkg> 是一个能 colcon build 的合法包（缺什么补什么）。"""
    base = os.path.join(root, "src", pkg)
    inner = os.path.join(base, pkg)
    os.makedirs(inner, exist_ok=True)
    os.makedirs(os.path.join(base, "resource"), exist_ok=True)
    os.makedirs(os.path.join(base, "test"), exist_ok=True)

    px = os.path.join(base, "package.xml")
    if not os.path.isfile(px):
        with open(px, "w", encoding="utf-8", newline="\n") as f:
            f.write(_PKG_XML % {"name": pkg, "desc": "ROS2 nodes"})
    res = os.path.join(base, "resource", pkg)
    if not os.path.isfile(res):
        open(res, "w").close()
    sp = os.path.join(base, "setup.py")
    entries = "".join("            '%s = %s.%s:main',\n"
                      % (n, pkg, n) for n in (nodes or []))
    if not os.path.isfile(sp):
        with open(sp, "w", encoding="utf-8", newline="\n") as f:
            f.write(_SETUP_PY % {"name": pkg, "desc": "ROS2 nodes",
                                 "entries": entries})
    elif nodes:
        # 已有 setup.py：把新节点的入口补进 console_scripts
        with open(sp, "r", encoding="utf-8") as f:
            txt = f.read()
        add = ""
        for n in nodes:
            if ("'%s =" % n) not in txt and ('"%s =' % n) not in txt:
                add += "            '%s = %s.%s:main',\n" % (n, pkg, n)
        if add:
            txt = txt.replace("console_scripts': [", "console_scripts': [\n" + add, 1)
            with open(sp, "w", encoding="utf-8", newline="\n") as f:
                f.write(txt)
    init = os.path.join(inner, "__init__.py")
    if not os.path.isfile(init):
        open(init, "w").close()
    return base


_CODER_SYS = """你在为一个 ROS 2 Jazzy（Python 3.12，rclpy）项目写一个节点。
硬性要求：
1. 只输出一个 ```python 代码块，不要解释文字。
2. 节点名、话题名、消息类型必须与给定契约完全一致，一个字符都不能改。
3. 结构：import rclpy / from rclpy.node import Node / class X(Node) /
   __init__ 里 super().__init__('节点名') / main() + rclpy.init/shutdown。
4. 回调里写真实可运行的逻辑，不要 TODO、不要 pass 占位。
5. 只用标准库和 rclpy / ROS2 消息包，不引入第三方库。
6. 文件末尾必须有 main()，并在 if __name__ == '__main__' 里调用。
"""


def _build_node_prompt(spec):
    pub = "\n".join("  - %s : %s" % (i["topic"], i["type"] or "待定")
                    for i in spec.get("publishes") or []) or "  （无）"
    sub = "\n".join("  - %s : %s" % (i["topic"], i["type"] or "待定")
                    for i in spec.get("subscribes") or []) or "  （无）"
    return ("节点名：%s\n所属包：%s\n文件：%s\n\n需求：\n%s\n\n"
            "必须发布的接口：\n%s\n\n必须订阅的接口：\n%s\n\n"
            "参数：\n%s\n"
            % (spec["name"], spec["package"], spec["file"],
               spec.get("requirement") or "（未填写）", pub, sub,
               "\n".join("  - " + p for p in spec.get("params") or []) or "  （无）"))


def _extract_code(text):
    m = re.search(r"```(?:python|py)?\s*\n(.*?)```", text or "", re.S)
    code = m.group(1) if m else (text or "")
    return code.strip() + "\n" if code.strip() else ""


def _topic_file(name):
    """节点名 → 文件名（ROS2 习惯：小写下划线）。"""
    s = re.sub(r"[^0-9a-zA-Z_]+", "_", name or "node").strip("_").lower()
    return (s or "node") + ".py"


def _replace_topic(root, old, new):
    """把代码/配置里所有 "old" 字面量替换成 "new"，返回 (改动文件数, 替换数)。"""
    dq = re.compile(r'"%s"' % re.escape(old))
    sq = re.compile(r"'%s'" % re.escape(old))
    files, hits = 0, 0
    for dirpath, dirs, fnames in os.walk(root):
        dirs[:] = [d for d in dirs if d not in graph.SKIP_DIRS
                   and not d.startswith(".")]
        for fn in fnames:
            if not fn.endswith(TEXT_EXT):
                continue
            full = os.path.join(dirpath, fn)
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    txt = f.read()
            except OSError:
                continue
            if old not in txt:
                continue
            n1, n2 = len(dq.findall(txt)), len(sq.findall(txt))
            if not (n1 or n2):
                continue
            new_txt = sq.sub("'%s'" % new, dq.sub('"%s"' % new, txt))
            try:
                with open(full, "w", encoding="utf-8", newline="\n") as f:
                    f.write(new_txt)
            except OSError:
                continue
            files += 1
            hits += n1 + n2
    return files, hits


def commit(sess, cfg, body=None):
    """把图纸上的改动落到代码：改话题名 / 新建节点 / 删除节点。

    返回变更清单，前端据此展示「改了哪个文件、加了哪个节点」。
    """
    body = body or {}
    draft = read_draft(sess)
    root = sess.ws_root()
    if not os.path.isdir(root):
        return {"ok": False, "error": "还没有导入项目"}

    renames = {k: v for k, v in (draft.get("renames") or {}).items()
               if isinstance(v, str) and v and v != k}
    changes, errors = [], []

    # 1) 话题改名：纯字符串替换，不调模型，确定性强
    for old, new in sorted(renames.items()):
        if not body.get("allow_rename", True):
            break
        files, hits = _replace_topic(root, old, new)
        if files:
            changes.append({"kind": "rename", "topic": old, "to": new,
                            "files": files, "hits": hits,
                            "summary": "话题 %s → %s（%d 个文件，%d 处）"
                                       % (old, new, files, hits)})
        else:
            errors.append("话题 %s 在代码里没找到，可能只存在于图纸上" % old)

    # 2) 新建节点：按契约生成代码
    created, new_entries = [], {}
    pkg_hint = sess.package_name or ""
    for dn in draft.get("draft_nodes") or []:
        name = (dn.get("name") or "").strip()
        req = (dn.get("requirement") or "").strip()
        if not name:
            continue
        if not req:
            errors.append("节点 %s 还没填需求，已跳过" % name)
            continue
        pkg = (dn.get("package") or pkg_hint or "my_pkg").strip()
        rel = "src/%s/%s/%s" % (pkg, pkg, _topic_file(name))
        spec = {"name": name, "package": pkg, "file": rel, "requirement": req,
                "publishes": dn.get("publishes") or [],
                "subscribes": dn.get("subscribes") or []}
        tbd = [i["topic"] for i in (spec["publishes"] + spec["subscribes"])
               if not (i.get("type") or "").strip()]
        if tbd:
            errors.append("节点 %s 的连线 %s 还没定消息类型，先在图上线选类型"
                          % (name, "、".join(tbd)))
            continue
        try:
            text = llm.chat(cfg, "coder", _CODER_SYS, _build_node_prompt(spec),
                            timeout=180)
            code = _extract_code(text)
            if not code:
                raise RuntimeError("模型没有返回代码")
            parsed = graph.parse_python(rel, code)
            if parsed.get("error"):
                raise RuntimeError("生成的代码有语法错误：" + parsed["error"])
        except Exception as e:      # noqa: BLE001
            errors.append("节点 %s 生成失败：%s" % (name, e))
            continue

        base = _ensure_package(root, pkg, [name])
        full = os.path.join(root, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8", newline="\n") as f:
            f.write(code)
        created.append({"node": name, "package": pkg, "file": rel,
                        "summary": "新建节点 %s（%s）" % (name, rel)})
        for nd in parsed["nodes"]:
            new_entries[dn.get("id")] = nd["id"]
        sess.generated = list(set((sess.generated or []) + [name]))
    if created:
        changes.extend([dict(c, kind="create") for c in created])

    # 3) 删除节点：快照后删文件（一文件多类的保守跳过）
    for nid in draft.get("deleted") or []:
        got = next((n for n in graph.analyze(root)["nodes"] if n["id"] == nid), None)
        if not got:
            continue
        p = _abs_in_ws(sess, got["file"])
        if not p or not os.path.isfile(p):
            continue
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            src = f.read()
        classes = [c for c in graph.parse_python(got["file"], src)["nodes"]]
        if len(classes) > 1:
            errors.append("%s 里还有别的节点，未自动删除，请手工处理" % got["file"])
            continue
        _snapshot(sess, got["file"], src)
        try:
            os.remove(p)
            changes.append({"kind": "delete", "node": got["name"],
                            "file": got["file"],
                            "summary": "删除节点 %s（%s）" % (got["name"], got["file"])})
        except OSError as e:
            errors.append("删除 %s 失败：%r" % (got["file"], e))

    # 4) 清空已落地的图纸改动（位置和简介缓存保留）
    if not errors:
        write_draft(sess, {"positions": draft.get("positions") or {},
                           "intro": draft.get("intro") or {},
                           "renames": {}, "deleted": [],
                           "draft_nodes": [], "draft_links": []})
    else:
        keep = set(new_entries.keys())
        write_draft(sess, {
            "positions": draft.get("positions") or {},
            "intro": draft.get("intro") or {},
            "renames": {} if not renames else draft.get("renames"),
            "deleted": draft.get("deleted") if any(
                "未自动删除" in e for e in errors) else [],
            "draft_nodes": [d for d in (draft.get("draft_nodes") or [])
                            if d.get("id") not in keep],
            "draft_links": draft.get("draft_links") or [],
        })
    sess.save()

    return {"ok": True, "changes": changes, "errors": errors,
            "graph": load(sess)}
