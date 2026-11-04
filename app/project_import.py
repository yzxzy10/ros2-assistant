# -*- coding: utf-8 -*-
"""项目导入：识别用户导入的 ROS2 项目结构，并落到会话工作区。

职责：
1. 接收文件列表 [{path, content}]（相对路径 + 文本内容）；
2. 识别 ROS2 包 / 节点 / launch / msg·srv·action / 模型 / 配置文件；
3. 把文件按原目录结构写进 sessions/<sid>/ros2_ws/；
4. 生成「项目上下文摘要」，供后续「基于该项目继续开发」注入提示词。

安全边界：只接收白名单文本扩展名；单文件、总量、文件数均有上限；
路径做规范化防目录穿越。
"""
import base64
import json
import os
import re
import shutil
import time

# 二进制扩展名（压缩包 + 可执行文件）：用 base64 传输、二进制落盘，
# 不做内容解析（结构识别只看文本源码）。
_BINARY_EXT = {
    # 压缩包
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".zst", ".tgz", ".tbz2", ".txz",
    ".7z", ".rar",
    # 可执行 / 二进制产物
    ".so", ".a", ".o", ".bin", ".run", ".exe", ".appimage",
}

# 允许落盘的文件扩展名（文本/源码 + 二进制）
_ALLOWED_EXT = {
    ".py", ".xml", ".yaml", ".yml", ".txt", ".md", ".launch", ".msg",
    ".srv", ".action", ".cfg", ".json", ".cpp", ".hpp", ".h", ".c", ".cc",
    ".urdf", ".xacro", ".sdf", ".world", ".rviz", ".cmake", ".ini", ".toml",
    ".sh", ".bash", ".csv",
} | _BINARY_EXT

_MAX_FILES = 200
_MAX_FILE_BYTES = 1 * 1024 * 1024          # 单文件上限 1MB
_MAX_TOTAL_BYTES = 100 * 1024 * 1024       # 总量上限 100MB

_NODE_RE = re.compile(
    r"(?:rclpy|rclcpp|class\s+\w+\s*\([^)]*Node[^)]*\)|super\(\s*\w+\s*,\s*self\s*\)\s*\.__init__)",
    re.I)
_PKG_NAME_RE = re.compile(r"<name>\s*([^<\s]+)\s*</name>", re.I)
_SETUP_PKG_RE = re.compile(r"package_name\s*=\s*['\"]([^'\"]+)['\"]")
_TOPIC_RE = re.compile(
    r"create_(publisher|subscription|service|client|action_client|action_server)"
    r"\s*\(\s*[^,)]+\s*,\s*['\"]([^'\"]+)['\"]")


def sanitize_rel(rel):
    """把前端给的文件路径规范化，防目录穿越；非法返回 None。"""
    rel = (rel or "").replace("\\", "/").strip()
    rel = rel.lstrip("/")
    parts = []
    for p in rel.split("/"):
        if p in ("", "."):
            continue
        if p == "..":
            return None
        parts.append(p)
    if not parts:
        return None
    return "/".join(parts)


def _ext(name):
    return os.path.splitext(name)[1].lower()


def _collect_topics(content):
    out = []
    for m in _TOPIC_RE.finditer(content or ""):
        name = m.group(2)
        if name and name not in out:
            out.append(name)
    return out


def recognize(files):
    """识别项目结构，返回结构化摘要。files: [{path, content}]。"""
    packages = {}    # name -> {dir, build_type, package_xml}
    nodes = []       # {path, name}
    launch = []      # {path}
    interfaces = []  # {path, kind}
    models = []      # {path}
    configs = []     # {path}
    other = []       # {path}
    topics = []      # 话题/服务名

    for f in files:
        rel = sanitize_rel(f.get("path"))
        content = f.get("content") or ""
        if not rel:
            continue
        base = os.path.basename(rel)
        d = os.path.dirname(rel)
        ext = _ext(base)

        if base == "package.xml":
            m = _PKG_NAME_RE.search(content)
            pkg = m.group(1) if m else os.path.basename(d) or "package"
            rec = packages.setdefault(pkg, {"dir": d, "build_type": "ament",
                                            "package_xml": True})
            rec["package_xml"] = True
            continue
        if base == "setup.py":
            m = _SETUP_PKG_RE.search(content)
            pkg = m.group(1) if m else os.path.basename(d) or "package"
            packages.setdefault(pkg, {"dir": d, "build_type": "ament_python",
                                      "package_xml": False})
            continue
        if base == "CMakeLists.txt":
            pkg = os.path.basename(d) or "package"
            packages.setdefault(pkg, {"dir": d, "build_type": "ament_cmake",
                                      "package_xml": False})
            continue

        if ext in (".msg", ".srv", ".action"):
            interfaces.append({"path": rel, "kind": ext[1:]})
        elif base.endswith(".launch.py") or base.endswith(".launch.xml") \
                or base.endswith(".launch"):
            launch.append({"path": rel})
        elif ext in (".urdf", ".xacro", ".sdf", ".world", ".rviz"):
            models.append({"path": rel})
        elif ext in (".yaml", ".yml", ".xml", ".cfg", ".json", ".ini", ".toml"):
            configs.append({"path": rel})
        elif ext == ".py" and _NODE_RE.search(content):
            nodes.append({"path": rel, "name": base[:-3]})
            for t in _collect_topics(content):
                if t not in topics:
                    topics.append(t)
        elif ext in (".py", ".cpp", ".hpp", ".h", ".c", ".cc",
                     ".sh", ".cmake", ".txt", ".md"):
            other.append({"path": rel})
        elif ext in _BINARY_EXT:
            other.append({"path": rel})

    return {
        "packages": [{"name": k, **v} for k, v in sorted(packages.items())],
        "nodes": nodes,
        "launch": launch,
        "interfaces": interfaces,
        "models": models,
        "configs": configs,
        "topics": topics,
        "other": other,
    }


def _project_name(struct):
    pkgs = struct.get("packages") or []
    if pkgs:
        return pkgs[0].get("name", "")
    return ""


def import_to_session(sess, files):
    """把文件写入会话工作区并设置项目上下文。

    返回 (ok, message, struct)。切换项目 = 用新导入的项目替换工作区内容。
    """
    files = list(files or [])
    if not files:
        return False, "没有收到任何文件", {}

    # 校验：数量、扩展名、大小（二进制文件按解码后字节数计）
    if len(files) > _MAX_FILES:
        return False, "文件太多（上限 %d 个），请精简后重试" % _MAX_FILES, {}
    total = 0
    cleaned = []
    for f in files:
        rel = sanitize_rel(f.get("path"))
        if not rel:
            return False, "包含非法路径，已拒绝导入", {}
        ext = _ext(rel)
        if ext not in _ALLOWED_EXT:
            continue          # 跳过不支持的文件类型
        content = f.get("content")
        if not isinstance(content, str):
            continue
        if ext in _BINARY_EXT:
            try:
                data = base64.b64decode(content, validate=False)
            except Exception:
                continue
            n = len(data)
            if n > _MAX_FILE_BYTES:
                continue
            total += n
            if total > _MAX_TOTAL_BYTES:
                return False, "项目总大小超过 100MB，请排除 build/install/log 等大目录", {}
            cleaned.append({"path": rel, "data": data, "binary": True})
        else:
            n = len(content.encode("utf-8"))
            if n > _MAX_FILE_BYTES:
                continue
            total += n
            if total > _MAX_TOTAL_BYTES:
                return False, "项目总大小超过 100MB，请排除 build/install/log 等大目录", {}
            cleaned.append({"path": rel, "content": content, "binary": False})

    if not cleaned:
        return False, "没有可导入的文件（支持 .py/.xml/.yaml/.msg/.cpp 及 .zip/.so 等）", {}

    struct = recognize(cleaned)
    pkg = _project_name(struct)
    if not pkg and not struct["nodes"] and not struct["launch"]:
        return False, "未识别到 ROS2 项目结构（未发现 package.xml/setup.py/CMakeLists.txt 或节点）", struct

    # 切换项目：清空旧工作区，写入新项目
    root = sess.ws_root()
    if os.path.isdir(root):
        shutil.rmtree(root, ignore_errors=True)

    written = 0
    for f in cleaned:
        full = os.path.join(root, f["path"].replace("/", os.sep))
        os.makedirs(os.path.dirname(full), exist_ok=True)
        try:
            if f.get("binary"):
                with open(full, "wb") as fh:
                    fh.write(f["data"])
            else:
                with open(full, "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(f["content"])
            written += 1
        except OSError:
            continue

    sess.imported_project = {
        "name": pkg or "导入的项目",
        "packages": struct["packages"],
        "nodes": struct["nodes"],
        "launch": struct["launch"],
        "interfaces": struct["interfaces"],
        "models": struct["models"],
        "configs": struct["configs"],
        "topics": struct["topics"],
        "file_count": written,
        "files": [f["path"] for f in cleaned],
        "imported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    # 用导入的第一个包名作为后续「继续开发」的落盘包名
    if pkg:
        sess.package_name = pkg
    # 切换项目后，旧的规划/待办不再适用
    sess.node_list = []
    sess.plan = None
    sess.pending = None
    sess.save()

    summary = summarize(sess.imported_project)
    return True, summary, struct


def summarize(ip):
    """把导入摘要渲染成给前端/回复的一段 markdown。"""
    if not ip:
        return ""
    lines = ["**已导入项目**：%s" % ip.get("name", "导入的项目"), ""]
    pkgs = ip.get("packages") or []
    if pkgs:
        lines.append("- 包（%d 个）：%s" % (
            len(pkgs), "、".join(p.get("name", "") for p in pkgs)))
    nodes = ip.get("nodes") or []
    if nodes:
        lines.append("- 节点（%d 个）：%s" % (
            len(nodes), "、".join(n.get("name", "") for n in nodes)))
    topics = ip.get("topics") or []
    if topics:
        lines.append("- 话题/服务：%s" % "、".join(topics[:20]))
    launch = ip.get("launch") or []
    if launch:
        lines.append("- launch 文件（%d 个）" % len(launch))
    interfaces = ip.get("interfaces") or []
    if interfaces:
        lines.append("- 自定义接口（%d 个）" % len(interfaces))
    lines.append("")
    lines.append("已就绪，接下来你可以说：**「给这个项目加一个 XXX 节点」**，"
                 "或 **「这个项目里的订阅者怎么改」**，我会基于现有结构继续开发。")
    return "\n".join(lines)


def context_text(ip):
    """把导入摘要渲染成注入提示词的项目上下文文本（空则返回空串）。"""
    if not ip:
        return ""
    lines = ["【学生导入的现有项目（在此基础上继续开发，尽量复用已有包/话题/依赖，"
             "不要推翻重来）】"]
    pkgs = ip.get("packages") or []
    if pkgs:
        lines.append("- 包：%s" % "、".join(p.get("name", "") for p in pkgs))
    nodes = ip.get("nodes") or []
    if nodes:
        lines.append("- 已有节点：%s" % "、".join(n.get("name", "") for n in nodes))
    topics = ip.get("topics") or []
    if topics:
        lines.append("- 已有话题/服务：%s" % "、".join(topics[:20]))
    launch = ip.get("launch") or []
    if launch:
        lines.append("- launch：%s" % "、".join(l.get("path", "") for l in launch[:10]))
    interfaces = ip.get("interfaces") or []
    if interfaces:
        lines.append("- 自定义接口：%s" % "、".join(i.get("path", "") for i in interfaces[:10]))
    return "\n".join(lines)


def to_json(ip):
    """给 /api/session 的轻量摘要（不泄漏文件内容）。"""
    if not ip:
        return None
    return {
        "name": ip.get("name", ""),
        "package_count": len(ip.get("packages") or []),
        "node_count": len(ip.get("nodes") or []),
        "launch_count": len(ip.get("launch") or []),
        "interface_count": len(ip.get("interfaces") or []),
        "topics": ip.get("topics") or [],
        "file_count": ip.get("file_count", 0),
        "imported_at": ip.get("imported_at", ""),
    }
