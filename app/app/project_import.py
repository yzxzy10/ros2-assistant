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
import io
import json
import os
import re
import shutil
import time
import zipfile

from . import graph

# 二进制扩展名（压缩包 + 可执行文件）：用 base64 传输、二进制落盘，
# 不做内容解析（结构识别只看文本源码）。
_BINARY_EXT = {
    # 压缩包
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".zst", ".tgz", ".tbz2", ".txz",
    ".7z", ".rar",
    # 可执行 / 二进制产物
    ".so", ".a", ".o", ".bin", ".run", ".exe", ".appimage",
    # 3D 网格：STL 基本是二进制（80 字节头 + 三角面），必须按二进制落盘，
    # 否则预览解析错位、整个模型画不出来。
    ".stl",
}

# 允许落盘的文件扩展名（文本/源码 + 二进制）
_ALLOWED_EXT = {
    ".py", ".xml", ".yaml", ".yml", ".txt", ".md", ".launch", ".msg",
    ".srv", ".action", ".cfg", ".json", ".cpp", ".hpp", ".h", ".c", ".cc",
    ".urdf", ".xacro", ".sdf", ".world", ".rviz", ".cmake", ".ini", ".toml",
    ".sh", ".bash", ".csv",
    # 3D 网格：真实机器人包的 visual 外观几乎都是 .dae/.stl，漏掉它们，
    # 导入后工作区里没有网格，预览只能画占位小方块（用户会以为模型坏了）。
    # .dae/.obj 是 XML/文本，按文本收；.stl 已在 _BINARY_EXT 里按二进制收。
    ".dae", ".obj",
    # 话题名/设备参数放在 .env 里的项目很常见（os.environ['ROS_TOPIC_X']）。
    # 不落盘的话，图上一个边都连不出来，运行时也会 KeyError。
    ".env",
} | _BINARY_EXT

_MAX_FILES = 6000                         # 按「过滤后」的可导入文件数计（见 import_to_session）
# 单文件上限 8MB：真实 CAD 导出的 STL/DAE 单个零件常见 2–6MB。旧值 2MB 会把
# 「2_Link.STL 4.18MB」这类正常网格直接丢掉 —— 落盘少了网格，预览只能退回 3cm
# 占位方块，用户看到的就是「模型预览画错了」。总量仍有 300MB 兜底，不必卡这么死。
_MAX_FILE_BYTES = 8 * 1024 * 1024
_MAX_TOTAL_BYTES = 300 * 1024 * 1024       # 总量上限 300MB

# 没有扩展名但必须导入的文件。ament_python 的 `resource/<包名>` 是个空文件，
# 它就是包的「身份证」：漏掉它，colcon 打包阶段会报
#   can't copy '/ws/build/<包名>/resource/<包名>': doesn't exist
# 然后整包编译失败 —— 看起来像用户代码有问题，实际是导入时被过滤了。
def _keep(rel):
    """是否允许这个路径落盘。"""
    if _ext(rel) in _ALLOWED_EXT:
        return True
    name = rel.rsplit("/", 1)[-1]
    if name.upper() in ("COLCON_IGNORE", "AMENT_IGNORE"):
        return True
    # resource/ 下的标记文件、scripts/ 下的无后缀脚本都按文本收
    return bool(_ext(rel)) is False and (
        "resource" in rel.split("/")[:-1] or "scripts" in rel.split("/")[:-1])


# 这些目录不是源码：colcon 产物、虚拟环境、编辑器配置。
# 不排除它们的话，build/ 里的 .py 也会占名额，一个成熟项目必然超限。
_SKIP_DIRS = {
    "build", "install", "log", ".git", "__pycache__", "node_modules",
    ".venv", "venv", "env", ".idea", ".vscode", ".mypy_cache",
    ".pytest_cache", "dist", "egg-info", ".ros", ".colcon",
}


def _skip_dir_in(rel):
    """路径中是否含有应当排除的目录（按段判断，避免误伤 build_utils.py）。"""
    parts = rel.replace("\\", "/").split("/")[:-1]
    return any(p in _SKIP_DIRS for p in parts)

_NODE_RE = re.compile(
    r"(?:rclpy|rclcpp|class\s+\w+\s*\([^)]*Node[^)]*\)|super\(\s*\w+\s*,\s*self\s*\)\s*\.__init__)",
    re.I)
_PKG_NAME_RE = re.compile(r"<name>\s*([^<\s]+)\s*</name>", re.I)
_SETUP_PKG_RE = re.compile(r"package_name\s*=\s*['\"]([^'\"]+)['\"]")
# 包的构建类型只写在 package.xml 的 <build_type> 里；不读它，纯 C++ 包会被当成
# 普通 ament 包，用户看到空图也不知道是「这是 C++ 项目」还是「解析失败了」。
_BUILD_TYPE_RE = re.compile(r"<build_type>\s*([A-Za-z_]+)\s*</build_type>", re.I)
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


# ---------------- zip 展开 ----------------
# 用户拿到的 ROS2 项目八成是个 zip；把 zip 原样存进工作区等于什么都没导入，
# 所以要在这里解开，让里面的 package.xml / 节点源码真正进到识别流程。

_ZIP_MAX_DEPTH = 2          # 嵌套 zip 只展开两层，防止恶意深递归
_ZIP_MAX_ENTRIES = 10000    # 解压后保留条目上限，防 zip 炸弹（大 monorepo 源码常超 2000 个）


def _zip_entries(data, depth=0, stats=None):
    """展开 zip 成 [(相对路径, 原始字节)]，失败返回空列表。

    stats：可选的跳过计数表（dir/type/size/read）。zip 里的文件在**解压阶段**就按
    大小过滤，如果只在这里 `continue` 而不记账，导入摘要会只字不提「哪个网格没了」，
    用户只能看到预览里一个莫名其妙的小方块 —— 必须让它可被解释。
    """
    if depth > _ZIP_MAX_DEPTH:
        return []
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except Exception:            # noqa: BLE001 —— 坏包/加密包，当普通二进制处理
        return []
    out = []
    total = 0
    for info in zf.infolist():
        if info.is_dir() or len(out) >= _ZIP_MAX_ENTRIES:
            continue
        rel = sanitize_rel(info.filename)
        if not rel or _skip_dir_in(rel):
            continue
        ext = _ext(rel)
        if not _keep(rel):
            continue
        # 按未压缩大小预判，避免解压炸弹把内存吃光
        if info.file_size > _MAX_FILE_BYTES:
            if stats is not None:
                stats["size"] = stats.get("size", 0) + 1
            continue
        total += info.file_size
        if total > _MAX_TOTAL_BYTES:
            break
        try:
            raw = zf.read(info)
        except Exception:        # noqa: BLE001 —— 单个条目 CRC 错误，跳过即可
            continue
        if ext == ".zip":
            out.extend(_zip_entries(raw, depth + 1, stats))
        else:
            out.append((rel, raw))
    return out


def _strip_single_root(items):
    """my_ws/src/... + my_ws/README.md → src/... + README.md。

    只在"所有条目都在同一个顶层目录下"时剥掉这一层，避免误删真实的包目录。
    """
    if not items:
        return items
    tops = set()
    for rel, _raw in items:
        if "/" not in rel:
            return items            # 顶层有散落文件，说明没有统一的包裹目录
        tops.add(rel.split("/")[0])
        if len(tops) > 1:
            return items
    top = tops.pop()
    n = len(top) + 1
    return [(rel[n:], raw) for rel, raw in items]


def _entry_from_bytes(rel, raw):
    """解压出来的字节 → 统一的 cleaned 条目。"""
    if _ext(rel) in _BINARY_EXT:
        return {"path": rel, "data": raw, "binary": True}
    try:
        return {"path": rel, "content": raw.decode("utf-8"), "binary": False}
    except UnicodeDecodeError:
        return None                 # 非 UTF-8 文本（GBK 等），跳过不猜


def _collect_topics(content):
    out = []
    for m in _TOPIC_RE.finditer(content or ""):
        name = m.group(2)
        if name and name not in out:
            out.append(name)
    return out


def _absorb_recognized_nodes(parsed, rel, nodes, topics, seen):
    """把 parse_python / parse_cpp 的结果并进 recognize 的 nodes/topics。"""
    for nd in parsed.get("nodes") or []:
        rec = {
            "path": rel,
            "name": nd["name"],
            "cls": nd.get("cls", ""),
            "package": nd.get("package") or "",
            "id": nd.get("id", ""),
        }
        if nd.get("lang"):
            rec["lang"] = nd["lang"]
        nodes.append(rec)
        for field in ("publishes", "subscribes",
                      "service_servers", "service_clients"):
            for it in nd.get(field) or []:
                t = it.get("topic") or ""
                # 只收解析出来的名字。`f'/ros_abi_check_{i}'` 这种是代码
                # 表达式，不是话题名，列在导入摘要里纯属噪音。
                # 另外 `/battery` 和 `battery` 是同一个话题，按归一化去重。
                if t and it.get("resolved", True) \
                        and graph.norm_topic(t) not in seen:
                    seen.add(graph.norm_topic(t))
                    topics.append(t)


def recognize(files):
    """识别项目结构，返回结构化摘要。files: [{path, content}]。"""
    packages = {}    # name -> {dir, build_type, package_xml}
    nodes = []       # {path, name}  源码节点（Python + C++）
    external = []    # {path, name}  launch 里拉起的「外部节点」
    launch = []      # {path}
    interfaces = []  # {path, kind}
    models = []      # {path}
    configs = []     # {path}
    other = []       # {path}
    topics = []      # 话题/服务名
    has_cpp = False
    _seen_topics = set()   # 归一化后的名字，防止 /battery 和 battery 各列一遍
    _seen_ext = set()      # 外部节点名去重（多个 launch 拉起同一个节点很常见）
    # 先扫一遍配置表：话题名常常不在代码里，而在 .env 里，
    # 不先建这张表，识别出来的话题全是一堆 os.environ[...]
    env, _env_src = graph.env_map_from_entries(files)

    # 先建「目录 → 包名」表。解析 .py 时要靠它判断节点属于哪个包：
    # GitHub 上 clone 下来的仓库经常「仓库根目录就是包、节点在 scripts/ 下」，
    # 只按目录名推会把 10 个节点的包名全写成 scripts。
    dir_pkg = {}
    for f in files:
        rel = sanitize_rel(f.get("path"))
        if not rel:
            continue
        base = os.path.basename(rel)
        content = f.get("content") or ""
        if base == "package.xml":
            m = _PKG_NAME_RE.search(content)
            if m:
                dir_pkg[os.path.dirname(rel)] = m.group(1)
        elif base == "setup.py":
            m = _SETUP_PKG_RE.search(content)
            if m:
                dir_pkg.setdefault(os.path.dirname(rel), m.group(1))

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
            mb = _BUILD_TYPE_RE.search(content)
            rec = packages.setdefault(pkg, {"dir": d,
                                            "build_type": mb.group(1) if mb else "ament",
                                            "package_xml": True})
            rec["package_xml"] = True
            if mb:
                rec["build_type"] = mb.group(1)
            continue
        if base == "setup.py":
            m = _SETUP_PKG_RE.search(content)
            pkg = m.group(1) if m else os.path.basename(d) or "package"
            packages.setdefault(pkg, {"dir": d, "build_type": "ament_python",
                                      "package_xml": False})
            continue
        if base == "CMakeLists.txt":
            # 同目录已经有 package.xml 的话，包名以 package.xml 为准，
            # 别再按目录名造一个「同名不同名」的条目（仓库根目录的 CMakeLists
            # 更会造出一个叫 package 的假包）。
            if d in dir_pkg or not d:
                continue
            # 只认 ament 的 CMakeLists。仓库里 CMakeLists 满地都是——Zephyr 固件、
            # 原生 C 库都有——按「有 CMakeLists 就是包」会凭空多出 fw/base/app 这些
            # 根本不能 colcon 编译的假包。
            if "ament" not in content:
                continue
            pkg = os.path.basename(d)
            packages.setdefault(pkg, {"dir": d, "build_type": "ament_cmake",
                                      "package_xml": False})
            continue

        if ext in (".msg", ".srv", ".action"):
            interfaces.append({"path": rel, "kind": ext[1:]})
        elif base.endswith(".launch.py") or base.endswith(".launch.xml") \
                or base.endswith(".launch"):
            launch.append({"path": rel})
            # launch 里显式声明的「外部节点」（从已安装 ROS 包拉起的）也收进摘要。
            # PAROL6 / MoveIt 这类仓库本体没有节点源码，全靠这一项才不空图。
            for nd in graph.parse_launch_file(rel, content, pkg_index=dir_pkg)["nodes"]:
                if nd["name"] in _seen_ext:
                    continue
                _seen_ext.add(nd["name"])
                external.append({
                    "path": rel, "name": nd["name"], "cls": nd.get("cls", ""),
                    "package": nd.get("package") or "", "id": nd.get("id", ""),
                })
        elif ext in (".urdf", ".xacro", ".sdf", ".world", ".rviz"):
            models.append({"path": rel})
        elif ext in (".yaml", ".yml", ".xml", ".cfg", ".json", ".ini", ".toml"):
            configs.append({"path": rel})
        elif ext == ".py":
            parsed = graph.parse_python(rel, content, env=env, pkg_index=dir_pkg)
            _absorb_recognized_nodes(parsed, rel, nodes, topics, _seen_topics)
            if not parsed["nodes"]:
                other.append({"path": rel})
        elif ext in (".cpp", ".hpp", ".h", ".c", ".cc", ".cxx"):
            has_cpp = True
            parsed = graph.parse_cpp(rel, content, pkg_index=dir_pkg)
            _absorb_recognized_nodes(parsed, rel, nodes, topics, _seen_topics)
            if not parsed["nodes"]:
                other.append({"path": rel})
        elif ext in (".sh", ".cmake", ".txt", ".md"):
            other.append({"path": rel})
        elif ext in _BINARY_EXT:
            other.append({"path": rel})

    # 每个包名下有几个节点：决定「主包」（新节点落到哪个目录）用
    for nd in nodes:
        rec = packages.get(nd.get("package") or "")
        if rec is not None:
            rec["node_count"] = (rec.get("node_count") or 0) + 1

    # 没找到 package.xml / setup.py / CMakeLists.txt 时，按目录推断包。
    # 「一个大文件夹里全是节点」是最常见的导入形态，缺包清单不该让结构全丢。
    if not packages and nodes:
        for nd in nodes:
            pkg = nd.get("package") or graph.infer_package(nd["path"])
            if not pkg:
                continue
            nd["package"] = pkg
            rec = packages.setdefault(pkg, {"dir": os.path.dirname(nd["path"]),
                                            "build_type": "inferred",
                                            "package_xml": False,
                                            "inferred": True})
            rec.setdefault("node_count", 0)
            rec["node_count"] = rec.get("node_count", 0) + 1

    return {
        "packages": [{"name": k, **v} for k, v in sorted(packages.items())],
        "nodes": nodes,
        "external": external,
        "launch": launch,
        "interfaces": interfaces,
        "models": models,
        "configs": configs,
        "topics": topics,
        "other": other,
        "has_cpp": has_cpp,
    }


def _main_package(struct):
    """节点最多的包 = 用户心里的主包（决定新节点落到哪个目录）。"""
    pkgs = struct.get("packages") or []
    if not pkgs:
        return ""
    best = max(pkgs, key=lambda p: (p.get("node_count") or 0))
    return best.get("name", "")


def _project_name(struct):
    name = _main_package(struct)
    if name:
        return name
    pkgs = struct.get("packages") or []
    if pkgs:
        return pkgs[0].get("name", "")
    return ""


def ensure_markers(ws_root):
    """把工作区里 ament_python 包缺的 `resource/<包名>` 补上，返回补了哪些。

    两条路都要走：导入时补一次（新项目），验证前再补一次（导入逻辑修好之前
    留下的老会话、用户手工删过、或者从别的渠道搬进来的工作区）。
    文件是空的，ROS 的约定就是「有这个文件 = 我注册了」，补它不改任何用户代码。
    """
    out = []
    if not ws_root or not os.path.isdir(ws_root):
        return out
    seen = set()
    for dirpath, dirnames, filenames in os.walk(ws_root):
        dirnames[:] = [d for d in dirnames
                       if d not in _SKIP_DIRS and not d.startswith(".")]
        depth = os.path.relpath(dirpath, ws_root).replace("\\", "/").count("/")
        if depth > 3:                 # src/a/b/c，再深就不像包目录了
            dirnames[:] = []
            continue
        if "package.xml" not in filenames:
            continue
        try:
            with open(os.path.join(dirpath, "package.xml"),
                      encoding="utf-8", errors="replace") as fh:
                xml = fh.read()
        except OSError:
            continue
        m = _PKG_NAME_RE.search(xml)
        name = (m.group(1) if m else "").strip() or os.path.basename(dirpath)
        if not name or name in seen:
            continue
        mb = _BUILD_TYPE_RE.search(xml)
        bt = (mb.group(1) if mb else "").lower()
        if bt.startswith("ament_cmake"):
            continue                  # C++ 包不需要这张身份证
        if bt != "ament_python" and "setup.py" not in filenames:
            continue                  # 既没声明也没 setup.py 的，别凭空造
        marker = os.path.join(dirpath, "resource", name)
        if os.path.exists(marker):
            continue
        try:
            os.makedirs(os.path.dirname(marker), exist_ok=True)
            open(marker, "wb").close()
        except OSError:
            continue
        seen.add(name)
        out.append(os.path.relpath(marker, ws_root).replace("\\", "/"))
    return out


def import_to_session(sess, files):
    """把文件写入会话工作区并设置项目上下文。

    返回 (ok, message, struct)。切换项目 = 用新导入的项目替换工作区内容。
    """
    files = list(files or [])
    if not files:
        return False, "没有收到任何文件", {}

    # 校验：扩展名、大小（二进制文件按解码后字节数计）。
    # 数量上限放在**过滤之后**判断：GitHub 上直接 clone 的仓库动辄上千个文件，
    # 其中一大半是 docs/、web/、插图，按原始个数卡会把正常仓库挡在门外。
    total = 0
    cleaned = []
    skipped = {"dir": 0, "type": 0, "size": 0, "read": 0}
    zipped = 0          # 成功展开的 zip 个数
    for f in files:
        rel = sanitize_rel(f.get("path"))
        if not rel:
            return False, "包含非法路径，已拒绝导入", {}
        if _skip_dir_in(rel):
            skipped["dir"] += 1
            continue          # build/install/log/.git 等产物目录，不占名额
        ext = _ext(rel)
        if not _keep(rel):
            skipped["type"] += 1
            continue          # 跳过不支持的文件类型
        content = f.get("content")
        data = f.get("data")
        if ext in _BINARY_EXT:
            if isinstance(data, (bytes, bytearray)):
                data = bytes(data)        # 二进制直传路径：已是原始字节
            elif isinstance(content, str):
                try:
                    data = base64.b64decode(content, validate=False)
                except Exception:
                    skipped["read"] += 1
                    continue
            else:
                skipped["read"] += 1
                continue
            # 压缩包先展开：用户拖进来一个 zip 时，真正要导入的是里面的源码
            if ext == ".zip":
                inner = _strip_single_root(_zip_entries(data, stats=skipped))
                got = 0
                for irel, item_bytes in inner:
                    if _skip_dir_in(irel) or not _keep(irel):
                        continue
                    item = _entry_from_bytes(irel, item_bytes)
                    if not item:
                        continue
                    cleaned.append(item)
                    got += 1
                if got:
                    zipped += 1
                    continue        # 展开成功：zip 本身不再落盘，省空间
                skipped["type"] += 1   # 解不开（加密/改名的 7z），退回按二进制存
                continue
            n = len(data)
            if n > _MAX_FILE_BYTES:
                skipped["size"] += 1
                continue
            total += n
            if total > _MAX_TOTAL_BYTES:
                return False, "项目总大小超过 300MB，请排除 build/install/log 等大目录", {}
            cleaned.append({"path": rel, "data": data, "binary": True})
        else:
            if not isinstance(content, str):
                skipped["read"] += 1
                continue
            n = len(content.encode("utf-8"))
            if n > _MAX_FILE_BYTES:
                skipped["size"] += 1
                continue
            total += n
            if total > _MAX_TOTAL_BYTES:
                return False, "项目总大小超过 300MB，请排除 build/install/log 等大目录", {}
            cleaned.append({"path": rel, "content": content, "binary": False})

    if not cleaned:
        return False, "没有可导入的文件（支持 .py/.xml/.yaml/.msg/.cpp 及 .zip/.so 等）", {}

    if len(cleaned) > _MAX_FILES:
        return False, ("文件太多（可导入的就有 %d 个，上限 %d）。"
                       "建议只导入含 package.xml 的目录，或打包成 zip 再导入"
                       % (len(cleaned), _MAX_FILES)), {}

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

    # ament_python 包必须有 `resource/<包名>`（空文件）。它没有扩展名，
    # 上传/解压/手工建包任何一环漏掉，colcon 都会在打包阶段报
    #   can't copy '.../resource/<包名>': doesn't exist or not a regular file
    # 整包编不过，用户看到的是「我的包编译不通过」，其实是少了一张身份证。
    healed = ensure_markers(root)

    sess.imported_project = {
        "name": pkg or "导入的项目",
        "packages": struct["packages"],
        "nodes": struct["nodes"],
        "external": struct.get("external", []),
        "launch": struct["launch"],
        "interfaces": struct["interfaces"],
        "models": struct["models"],
        "configs": struct["configs"],
        "topics": struct["topics"],
        "skipped": skipped,
        "zipped": zipped,
        "healed": healed,
        "has_cpp": struct.get("has_cpp", False),
        "file_count": written,
        "files": [f["path"] for f in cleaned],
        "imported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    # 落盘包名的选择：节点最多的那个包，比"按字母序第一个"更接近用户心里的主包
    main_pkg = _main_package(struct)
    if main_pkg:
        sess.package_name = main_pkg
    # 切换项目后，旧的规划/待办不再适用
    sess.node_list = []
    sess.plan = None
    sess.pending = None
    sess.save()

    summary = summarize(sess.imported_project)
    return True, summary, struct


def remove_from_session(sess):
    """移除当前项目，回到「没有项目、只有一个想法」的状态。

    和「再导入一次」的区别：再导入是**覆盖**，这里是**清空**。
    聊天记录、学习画像都保留 —— 用户只是不想要这个项目了，
    不该顺手把聊到一半的对话也删掉（那是删会话，另一个动作）。
    """
    root = sess.ws_root()
    removed = 0
    if os.path.isdir(root):
        for _dirpath, _dirs, files in os.walk(root):
            removed += len(files)
        shutil.rmtree(root, ignore_errors=True)

    sess.imported_project = None
    sess.node_list = []
    sess.plan = None
    sess.pending = None
    sess.ask_count = 0
    sess.need_data = None
    sess.requirement_desc = ""
    sess.generated = []
    sess.package_name = ""
    sess.ask_options = []
    sess.last_ask_question = ""
    sess.save()

    # 图纸草稿一起清掉，否则下次打开还是一张指向不存在文件的图
    try:
        from . import graph_api        # 延迟导入，避免模块级循环依赖
        p = graph_api.draft_path(sess)
        if os.path.isfile(p):
            os.remove(p)
    except Exception:                  # noqa: BLE001  清不掉草稿不该挡着移除项目
        pass

    return True, "已移除项目（清理 %d 个文件），可以重新导入或直接说想做什么" % removed


def _names(items, limit):
    """把 [{name}] 串成「a、b、c」，超长就截断。"""
    names = [it.get("name", "") for it in items if it.get("name")]
    if len(names) <= limit:
        return "、".join(names)
    return "、".join(names[:limit]) + " 等 %d 个" % len(names)


def summarize(ip):
    """把导入摘要渲染成给前端/回复的一段 markdown。"""
    if not ip:
        return ""
    lines = ["**已导入项目**：%s" % ip.get("name", "导入的项目"), ""]
    if ip.get("zipped"):
        lines.append("- 已从压缩包（%d 个）中展开源码，压缩包本身不再保留"
                     % ip["zipped"])
    if ip.get("healed"):
        lines.append("- 自动补了 %d 个 `resource/<包名>` 标记文件（ament_python 包必需，"
                     "缺了 colcon 一定编不过）：%s"
                     % (len(ip["healed"]), "、".join(ip["healed"][:6])))
    pkgs = ip.get("packages") or []
    if pkgs:
        lines.append("- 包（%d 个）：%s" % (
            len(pkgs), _names(pkgs, 20)))
    nodes = ip.get("nodes") or []
    if nodes:
        lines.append("- 节点（%d 个）：%s" % (
            len(nodes), _names(nodes, 15)))
    ext = ip.get("external") or []
    if ext:
        lines.append("- 外部节点（%d 个，launch 里拉起）：%s" % (
            len(ext), _names(ext, 15)))
    # 有包但源码节点和外部节点一个都没有：分情形说明，别让用户对着空图猜。
    if pkgs and not nodes and not ext:
        if ip.get("has_cpp"):
            lines.append("- 有 C++ 文件但没识别出 rclcpp 节点（没有继承 Node / "
                         "create_publisher 之类），架构图会是空的")
        elif ip.get("launch") or ip.get("configs") or ip.get("models"):
            lines.append("- 这个项目里没有节点源码（.py/.cpp 都没有），节点都是从已安装的 "
                         "ROS 包里拉起的（写在 launch/配置里），所以架构图是空的；"
                         "文件树、看代码、下载 zip 都正常")
        else:
            lines.append("- 没在 .py 里找到 rclpy 节点（没有 create_publisher/"
                         "create_subscription 之类），架构图会是空的")
    topics = ip.get("topics") or []
    if topics:
        lines.append("- 话题/服务：%s" % "、".join(topics[:20]))
    launch = ip.get("launch") or []
    if launch:
        lines.append("- launch 文件（%d 个）" % len(launch))
    interfaces = ip.get("interfaces") or []
    if interfaces:
        lines.append("- 自定义接口（%d 个）" % len(interfaces))
    inferred = [p.get("name") for p in pkgs if p.get("inferred")]
    if inferred:
        lines.append("- 注意：这个项目没有 package.xml，包名是按目录推断的（%s），"
                     "新建节点时我会补齐标准包结构" % "、".join(inferred[:6]))
    sk = ip.get("skipped") or {}
    if any(sk.values()):
        bits = []
        if sk.get("dir"):
            bits.append("%d 个产物目录里的文件" % sk["dir"])
        if sk.get("size"):
            bits.append("%d 个超过单文件上限 %dMB（如网格/权重文件）"
                        % (sk["size"], _MAX_FILE_BYTES // (1024 * 1024)))
        if sk.get("type"):
            bits.append("%d 个不在支持范围" % sk["type"])
        if bits:
            lines.append("- 已跳过：%s" % "、".join(bits))
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
        "zipped": ip.get("zipped", 0),
        "file_count": ip.get("file_count", 0),
        "imported_at": ip.get("imported_at", ""),
    }
