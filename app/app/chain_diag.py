# -*- coding: utf-8 -*-
"""把 colcon / 节点启动的原始报错翻译成一句能照着做的话。

为什么单独一个模块：原始输出动辄几十行，用户看到的是「编译没通过」四个字，
真正有用的往往只有一行（缺 resource 标记、缺依赖、语法错在第 12 行）。
这里只做「模式 → 人话」，不参与判定，判定仍然只看标记行。
"""
import re

# 兜底用的「基础镜像里没有、学生项目又爱用」的包名。
# 正常情况下由 app/base_pkgs.py（从软件源算出的 201 个基础包）判定，
# 只有那个发行版没有清单时才退回这张手写表 —— 宁可少说，也别把基础包说成要装。
_EXTRA_ROS_PKGS = {
    "vision_msgs", "cv_bridge", "image_transport", "image_geometry",
    "nav2_msgs", "nav2_simple_commander", "moveit_msgs", "moveit",
    "tf_transformations", "ros2_numpy", "control_msgs", "action_msgs",
    "camera_info_manager", "depth_image_proc", "pcl_ros", "message_filters",
}

_GENERIC = "容器里没有 Python 模块 `{0}`：在 package.xml 里用 <depend> 声明并装上，" \
           "或者这行 import 本来就不该有"

# (正则, 模板)；按顺序命中第一条。{0}/{1} 是捕获组，{pkg} 是 resource/ 后面的包名。
_RULES = [
    # 内存被杀必须排在第一位。
    # 容器内存不够时，编译输出里只有一句 `c++: fatal error: Killed signal
    # terminated program cc1plus`，后面跟一串 make Error 1/2。它跟「代码写错」
    # 长得几乎一样，用户会去翻自己的第 150 行，实际上一个字都不用改 ——
    # 是容器太小被 OOM killer 掐了。实测 45 个包、17 个 C++ 包的工作区，
    # 4G 内存 + 3 并行时 7 个包全部这样死掉。
    (r"Killed signal terminated program (?:cc1plus|cc1|ld|gmake)|"
     r"virtual memory exhausted|Cannot allocate memory",
     "容器内存不足：编译器进程被系统直接杀掉了，这不是你代码的问题。"
     "这个工作区里的 C++ 包并行编译时每个要吃 1~2GB，容器给的内存不够。"
     "系统已自动降低并行度重试；如果仍然出现，需要管理员调大 "
     "config.json 里的 ros.verify.memory"),
    # package.xml 校验类要排在「File ... line N」之前：catkin_pkg 的报错一定带
    # 一串 Python 堆栈，先匹配堆栈只会告诉用户「第 150 行」，等于什么也没说。
    (r'Invalid email "([^"]*)" for person',
     "package.xml 里 maintainer 的邮箱 `{0}` 不合法（catkin_pkg 要求 name@domain.tld 形式），"
     "改成 student@example.com 这类就能过"),
    (r"InvalidPackage: Error\(s\) in package '([^']+)'",
     "`{0}` 没通过 package.xml 校验（缺 name/version/description/maintainer/"
     "license 之一，或字段格式不对）—— 上面的报错行就是具体哪一项"),
    (r"rosidl_interface_packages",
     "这个包定义了自定义 msg/srv/action：package.xml 里要加 "
     "<member_of_group>rosidl_interface_packages</member_of_group>，"
     "同时 <buildtool_depend>rosidl_default_generators</buildtool_depend>，"
     "否则 rosidl 不认它"),
    (r"can't copy '([^']*resource/[\w.\-]+)'",
     "包 {pkg} 缺 `resource/{pkg}` 标记文件（内容为空的普通文件即可）——"
     "ament_python 包必须有它，缺了 colcon 会在打包阶段直接失败"),
    (r"error: package directory '([^']+)' does not exist",
     "setup.py 里 packages 写的目录 `{0}` 不存在：包名与目录名对不上"),
    (r"Multiple top-level packages discovered",
     "setup.py 的 packages 没写死，setuptools 发现了多个顶层目录；写 packages=[包名]"),
    (r"ImportError: cannot import name '([\w]+)' from '([\w.]+)'",
     "`{1}` 里没有 `{0}`：接口类型名或版本对不上，确认消息包已装且名字拼对"),
    (r"Could not find a package configuration file provided by \"([\w\-]+)\"",
     "找不到依赖包 `{0}`。{dep_hint}"),
    (r"Package '([\w\-]+)' specified but not found",
     "colcon 没找到包 `{0}`：目录里少了 package.xml，或它不在 src/ 下"),
    # ros2 run 的原话是大写 P：「Package 'xxx' not found」
    (r"Package '([\w\-]+)' not found",
     "镜像里没有包 `{0}`。{dep_hint}"),
    (r"package '([\w\-]+)' not found",
     "镜像里没有包 `{0}`。{dep_hint}"),
    (r"No executable found",
     "ros2 run 找不到可执行：setup.py 的 entry_points 名字与代码里的入口函数不一致"),
]

# 用户自己代码里的 traceback 才有意义；ROS 安装目录 / 系统库的行号只会误导人
_TRACE = re.compile(r'File "([^"]+)", line (\d+)')
_SYS_PATH = ("/opt/ros/", "/usr/lib/python", "/usr/local/lib", "site-packages",
             "dist-packages", "/usr/share/")
# colcon 生成出来的树：install/ 是装出来的入口壳，build/ 是中间产物。
# 里面的行号指向自动生成的 main() 包装，用户手上根本没有那行可改 ——
# 实测报过「报错定位在 /ws/install/.../custom_executor 第 33 行」，
# 用户只会问「这是什么意思」。这种位置一律不报行号，改报异常类型。
_GEN_PATH = ("/ws/install/", "/ws/build/", "/ws/log/", "/ws/src/../install/")

_MOD_RE = re.compile(r"ModuleNotFoundError: No module named '([\w.]+)'")

# traceback 最后一行就是异常本身（`NotImplementedError: ...`），它比行号有用得多。
# 注意分隔符只吃空格/制表符：写成 `\s*` 会跨过换行，把下一行粘成异常消息
# （实测就把 `[ros2run]: Process exited with failure 1` 当成了 NotImplementedError 的内容）。
_EXC_LINE = re.compile(
    r"^([A-Za-z_][\w.]*(?:Error|Exception|Interrupt|Exit|Warning))\b[: \t]*(.*)$",
    re.M)

# `ros2 run` 这个壳自己加的噪音：节点崩了它补一句 `[ros2run]: Process exited...`，
# 这不是异常说的话，摆在诊断里等于没说
_WRAPPER = re.compile(r"\[ros2run\]:\s*Process exited with \w+ \d+\.?", re.I)


def _exc_msg(msg):
    """异常行后面真正有用的是异常带的话；壳补的、黏上来的噪音一律去掉。"""
    msg = _WRAPPER.sub("", msg or "")
    msg = re.sub(r"\s*\[ros2run\]:.*$", "", msg)
    msg = re.sub(r"^\[[\w.\-]+\]:\s*", "", msg.strip())
    # 对象地址每次都变，抄进诊断只是噪音
    msg = re.sub(r" at 0x[0-9a-fA-F]+", "", msg)
    if re.match(r"Process exited with (failure|code|signal)", msg, re.I):
        return ""
    return msg.strip(" ：:")

def _to_src(path):
    """把 colcon 生成树里的路径折回 src/。

    python 包的 traceback 多半落在 `/ws/build/<pkg>/<pkg>/x.py`：那是装出来的副本，
    和 `/ws/src/<pkg>/<pkg>/x.py` 逐行一致，但用户打开的是 src 里那一份 ——
    报 build 里的路径，等于让他去找一个自己没建过的文件。
    """
    p = (path or "").replace("\\", "/")
    if any(s in p for s in _SYS_PATH):
        return ""
    i = p.find("/ws/src/")
    if i >= 0:
        return p[i:]
    m = re.match(r".*/ws/build/([^/]+)/(.+)$", p)
    if m:
        pkg, rest = m.group(1), m.group(2)
        if rest.startswith(pkg + "/"):
            return "/ws/src/" + rest
        return "/ws/src/%s/%s" % (pkg, rest)
    return ""


def _exact_reason(name, text):
    """认得出是哪个坑就直说 —— 比 `_EXC_HINT` 里的通用解释准得多。"""
    if name == "NotImplementedError" and "rclpy/executors.py" in text:
        return ("rclpy 的 Executor 基类要求子类实现 `_spin_once_impl()`（Jazzy 起），"
                "只实现老的 `spin_once()` 不够 —— 自己写的 Executor、还有示例里那个"
                "自定义 Executor，都是栽在这一行")
    if name == "RuntimeError" and "bad yield" in text:
        return ("asyncio 和 rclpy 的 Future 不兼容：Python 3.12 的 asyncio 不再接受 "
                "`yield` 一个自定义 Future 对象，`await rclpy_future` 这种老写法会直接报错。"
                "改成 `rclpy.spin_until_future_complete(node, future)`，"
                "或者让 executor 驱动回调，别把 rclpy 的 Future 丢进 asyncio 的事件循环")
    return ""


# 只有这两类「坑」是发行版升级带来的、改不动一行代码也修不好的：
# 认得出就单独归类，别和「你的代码写错了」混在同一行诊断里。
_UPSTREAM_PKG = ("examples_", "demo_nodes_", "turtlesim", "composition_")


def upstream(text, pkg=""):
    """这次崩，是「官方示例没跟上发行版」而不是用户代码写错了吗？

    返回一句很短的原因；不是这一类就返回空串（不猜）。

    判据是两条同时成立：异常来自 rclpy 内部（`_spin_once_impl` / `bad yield`
    这种发行版行为），且包是官方示例。用户自己写的 Executor 子类会带着
    `/ws/src/<自己的包>` 的帧，pkg 也对不上，所以不会被误判成「示例的问题」。
    """
    text = text or ""
    known = ""
    if "NotImplementedError" in text and "rclpy/executors.py" in text:
        known = ("Jazzy 的 Executor 要求子类实现 `_spin_once_impl()`，"
                 "示例只实现了老的 `spin_once()`")
    elif "bad yield" in text:
        known = "asyncio 写法与 Python 3.12 不兼容（`Task got bad yield`）"
    if not known:
        return ""
    if (pkg or "").startswith(_UPSTREAM_PKG):
        return known
    # 包名认不出来时退一步看堆栈：没有任何用户 src 帧，才敢说是上游的事
    for m in _TRACE.finditer(text):
        if _to_src(m.group(1)):
            return ""
    return known


# `ros2 run` 找不到包时原话是大写 P。命中这条却要分两种人：包是镜像缺的，
# 还是用户自己 src/ 里的 —— 处置动作完全不同（一个找管理员，一个补 package.xml）。
_WS_MISS = re.compile(r"Package '([\w\-]+)' not found")


def _own_pkg_advice(name):
    """包本来就在工作区 src/ 里：这不是「镜像缺包」，叫管理员装 apt 包是错的方向。

    colcon 只认 package.xml。src/<pkg>/ 里光有几个 .py，colcon 连包都认不出来
    （build 日志里 `_identify(src/<pkg>) … python_setup_py` 一路全空，最后 0 个包），
    install/ 里自然没有它，`ros2 run <pkg> xxx` 就报 Package not found。
    实测用户拿着这种工作区来问「怎么跑不通」，上一版却回「请联系管理员补进依赖包池」。
    """
    return ("`%s` 是你自己工作区的包（就在 src/%s/ 下），不是镜像缺包 —— "
            "colcon 这次没把它认成包、也没编译它，所以 ros2 run 找不到。"
            "colcon 只认 package.xml：src/%s/ 下要同时有 package.xml、setup.py"
            "（entry_points 里注册节点可执行名）、setup.cfg，以及空的 resource/%s "
            "标记文件，少一样就当不成包（「查看编译输出」里连一行 Starting 都没有，"
            "就是这个原因）。补齐后重新验证" % (name, name, name, name))


# 常见异常给一句「大概是哪儿的事」，认不出来就只报类型，不编原因
_EXC_HINT = {
    "NotImplementedError":
        "某个抽象方法没实现就调用了：自己写了 Executor/Node 子类，就检查有没有漏实现"
        "必须覆盖的方法；ROS 自带示例这样，多半是示例没跟上这个发行版",
    "AttributeError":
        "属性/接口名对不上。发行版升级后 API 改名很常见，按报错里的名字对照当前版本的接口",
    "TypeError":
        "调用时参数个数或类型对不上：先看报错里给的是哪个函数",
    "KeyError":
        "从字典/参数里取了一个不存在的键（参数没声明、或名字拼错）",
}


def _dep_advice(name, distro="jazzy"):
    """缺的这个包，是「镜像本该有」还是「这台机器压根没有」—— 处置动作完全不同。

    基础包清单来自 tools/dump_base_pkgs.py（ros_base 变体递归展开），不靠印象猜。
    用户那头没有外网、也没有装包权限，所以「要额外装」必须落到「找管理员」上，
    并且给他一条能照着执行的命令，而不是一句「到有网机器上装一下」。
    """
    from . import base_pkgs
    apt = name.replace("_", "-")
    verdict = base_pkgs.is_base(name, distro)
    if verdict is False:
        return ("它不在 ROS 基础镜像里（这台机器目前没有这个包），要额外装 `ros-%s-%s`。"
                "容器没有外网、你自己装不了 —— 请联系管理员补进依赖包池："
                "`python tools/fetch_pkgs.py ros-%s-%s`；"
                "管理员在 ROS 源里也查不到的话，说明是第三方/私有包，"
                "得把它的源码放进工作区的 src/ 一起编译"
                % (distro, apt, distro, apt))
    if verdict:
        return ("它属于 ROS 基础包、镜像本该自带，缺了多半是这行 find_package 拼错了，"
                "或 package.xml 里写的是另一个名字")
    return ("这台机器没有 %s 的基础包清单，判断不了它是不是自带 —— "
            "请联系管理员确认支不支持这个包（`python tools/fetch_pkgs.py --check "
            "ros-%s-%s`）" % (distro, distro, apt))


def summary(text, limit=100):
    """多节点一起失败时的第二行：只要「抛了什么 + 大概是哪一类问题」。

    完整诊断留给第一个节点；后面的节点一句话就够，否则诊断条会长成一篇文章。
    异常消息本身通常比解释短（`Task got bad yield`），但真正能照做的是解释，
    所以优先给解释。
    """
    excs = _EXC_LINE.findall(text or "")
    if not excs:
        return ""
    name, msg = excs[-1]
    name = name.split(".")[-1]
    if name == "KeyboardInterrupt":
        return ""
    body = _exact_reason(name, text or "") or _EXC_HINT.get(name, "") or _exc_msg(msg)
    if not body:
        return "抛了 `%s`" % name
    # 只留第一句：第二句以后是「怎么改」的细节，第一个节点那里已经给全了。
    # 硬截字会切出「自定义 Fu…」这种半句，读起来比不解释还差。
    dot = body.find("。")
    if dot > 0:
        body = body[:dot + 1]
    if len(body) > limit:
        head = body[:limit]
        cut = max(head.rfind(p) for p in "。；，：")
        if cut >= limit - 30:
            body = head[:cut + 1] + ("" if head[cut] == "。" else "…")
        else:
            # 实在没有可断的标点，至少别把英文单词切成「Fu…」
            body = re.sub(r"[A-Za-z`/（]+$", "", head).rstrip("，、 ") + "…"
    return "抛了 `%s`：%s" % (name, body)


def hint(text, distro="jazzy", ws_pkgs=None):
    """返回一句中文诊断；认不出来返回空串（不猜）。

    ws_pkgs：这次验证里属于工作区自己的包名。传了就多一层区分 ——
    `Package 'x' not found` 里的 x 若是自己的包，说的是「你少写了 package.xml」，
    而不是「镜像里没有这个包、去找管理员」。
    """
    text = text or ""
    if not text:
        return ""
    _w = _WS_MISS.search(text)
    if _w and ws_pkgs and _w.group(1) in ws_pkgs:
        return _own_pkg_advice(_w.group(1))
    m = _MOD_RE.search(text)
    if m:
        mod = m.group(1).split(".")[0]
        # 优先问基础包清单（201 个包，从软件源算的），比手工维护的集合准；
        # 没有这个发行版的清单时退回集合，别因为缺清单就不给提示。
        from . import base_pkgs
        _verdict = base_pkgs.is_base(mod, distro)
        _not_base = (_verdict is False) if _verdict is not None else (mod in _EXTRA_ROS_PKGS)
        if _not_base:
            return ("节点一启动就崩：基础镜像里没有 ROS 包 `%s`（代码 import 了它）。"
                    "这不是语法错。两条路：① 联系管理员把它补进依赖包池"
                    "（ros-%s-%s），package.xml 里再写 <depend>%s</depend>；"
                    "② 改用基础镜像自带的 std_msgs / sensor_msgs / geometry_msgs 类型"
                    % (mod, distro, mod.replace("_", "-"), mod))
        return _GENERIC.format(mod)
    for pat, tpl in _RULES:
        m = re.search(pat, text)
        if not m:
            continue
        g = [x or "" for x in m.groups()]
        # resource/xxx 这种路径只留包名，用户要照着建的是 resource/<包名>
        pkg = g[0] if g else ""
        mm = re.search(r"resource/([\w.\-]+)$", pkg)
        if mm:
            pkg = mm.group(1)
        out = tpl.replace("{pkg}", pkg)
        for i, v in enumerate(g):
            out = out.replace("{%d}" % i, v)
        # 缺依赖这条要按「基础包 / 需额外装」分岔，措辞得动态生成
        if "{dep_hint}" in out:
            out = out.replace("{dep_hint}", _dep_advice(pkg, distro))
        return out
    # 定位：能折回 src/ 的优先（build/ 与 src/ 同构，行号对得上，用户改的是 src
    # 里那一份）；install/ 下的入口壳没有对应源码，只记下来当兜底说明。
    gen = ""
    loc = ""
    for m in _TRACE.finditer(text):
        path = m.group(1)
        if any(s in path for s in _SYS_PATH):
            continue
        sp = _to_src(path)
        if sp:
            loc = "`%s` 第 %s 行" % (sp, m.group(2))
            break
        if not gen and any(s in path for s in _GEN_PATH):
            gen = path
    # 定位不到就报异常本身 —— 一条 `XxxError: ...` 用户可以拿去搜，
    # 而一个生成脚本的行号谁也改不了。
    excs = _EXC_LINE.findall(text)
    if excs:
        name, msg = excs[-1]
        name = name.split(".")[-1]
        msg = _exc_msg(msg)
        if name == "KeyboardInterrupt":
            # Ctrl-C 不是故障，但用户自己的文件里报了行号仍然有用（他自己按的停）
            return ("报错定位在 " + loc) if loc else ""
        out = "节点运行时抛了 `%s`" % name
        if msg:
            out += "：" + msg[:120]
        why = _exact_reason(name, text) or _EXC_HINT.get(name, "")
        if why:
            out += "。" + why
        if loc:
            out += "（位置：" + loc + "）"
        elif gen:
            out += "（报错位置在 colcon 生成的入口脚本里，不是你写的代码）"
        return out
    if loc:
        return "报错定位在 " + loc
    if gen:
        return "节点在 `%s` 里报错退出，但没留下具体异常" % gen
    return ""
