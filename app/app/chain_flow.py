"""解析脚本输出标记，实时更新状态，最后给出逐跳结论。"""
import re
import time

from . import base_pkgs, pkgcache
from .chain_msg import _slug

# colcon 的阶段行。脚本只把这些行放行到 stdout（其余全写日志文件），
# 所以这里的正则就是「进度条」的唯一来源。
_RE_START = re.compile(r"^Starting\s+>>>\s+(\S+)")
_RE_DONE = re.compile(r"^Finished\s+<<<\s+(\S+)")
_RE_BAD = re.compile(r"^(?:Failed|Aborted)\s+<<<\s+(\S+)")


def _build_progress(st, line):
    """把 colcon 的一行阶段输出折算成「已完成 N / 总数」。

    以前编译输出被 `tail` 堵死，前端只能显示「正在编译项目...」原地转圈：
    用户无法区分「45 个包在慢慢编」和「进程已经卡死」。有了这个计数，
    界面上是一个真的会往前走的数字。
    """
    if _RE_DONE.match(line) or _RE_BAD.match(line):
        st["build_done"] = int(st.get("build_done") or 0) + 1
        if _RE_BAD.match(line):
            st["build_bad"] = int(st.get("build_bad") or 0) + 1
        st["build_cur"] = ""
        return
    m = _RE_START.match(line)
    if m:
        st["build_cur"] = m.group(1)


def _hop(st, kind, result, parts):
    """按 (from, to, topic) 找到对应的那一跳并落结果。

    标记行的字段顺序是 `from|to|备注|topic`：备注（发布者数量之类）放中间，
    topic 保持在最后一位，下面按 parts[-1] 取话题名的逻辑才不受影响。
    """
    topic = parts[-1] if parts else ""
    note = parts[-2] if len(parts) >= 3 else ""
    for h in st["hops"]:
        if h["result"] != "pending":
            continue
        if h["kind"] != kind:
            continue
        if kind == "mcu":
            if h["topic"] == topic:
                h["result"] = result
                h["note"] = note
                h["at"] = round(time.time() - st["started_at"], 1)
                return True
        elif h["topic"] == topic and _slug(h["from"]) in parts \
                and _slug(h["to"]) in parts:
            h["result"] = result
            h["note"] = note
            h["at"] = round(time.time() - st["started_at"], 1)
            return True
    return False


def feed(st, line, cap):
    """吃一行输出。cap 是正在采集的节点日志缓冲 dict。"""
    if line.startswith("__NODELOG_BEGIN__|"):
        cap["id"] = line.split("|", 1)[1]
        cap["lines"] = []
        return
    if line.startswith("__NODELOG_END__|"):
        nid = line.split("|", 1)[1]
        for n in st["nodes"]:
            if _slug(n["id"]) == nid:
                n["log"] = "\n".join(cap["lines"])[-8000:]
        cap["id"] = None
        return
    if cap.get("id"):
        cap["lines"].append(line)
        return
    if line == "__VERIFY_DONE__":
        st["stage"] = "done"
        return
    if line.startswith("__ENV_MISSING__|"):
        st["env_missing"] = line.split("|", 1)[1].strip()
        return
    if line == "__ENV_FAIL__":
        st["env_fail"] = True
        return
    if line == "__APT_FAIL__":
        st["apt_failed"] = True
        return
    if line.startswith("__PKG_OK__|"):
        name = line.split("|", 1)[1].strip()
        if name and name not in st["pkgs_installed"]:
            st["pkgs_installed"].append(name)
        return
    if line.startswith("__PKG_MISSING__|"):
        name = line.split("|", 1)[1].strip()
        if name and name not in st["pkgs_missing"]:
            st["pkgs_missing"].append(name)
        return
    if line.startswith("__PKG_FAIL__|"):
        name = line.split("|", 1)[1].strip()
        if name and name not in st["pkgs_failed"]:
            st["pkgs_failed"].append(name)
        return
    if line == "__DEPS_FAIL__":
        st["deps_failed"] = True
        return
    if line.startswith("__STAGE__|"):
        st["stage"] = line.split("|", 1)[1]
        if st["stage"] == "build":
            st["build_log"] = []
            st["build_done"] = 0
            st["build_bad"] = 0
            st["build_cur"] = ""
            st["build_total"] = 0
        return
    if line.startswith("__BUILD_TOTAL__|"):
        try:
            st["build_total"] = int(line.split("|", 1)[1].strip())
        except ValueError:
            st["build_total"] = 0
        return
    if line.startswith("__DIAG__|"):
        # 容器内的自检行（路径、install 布局、可执行清单）。
        # 节点「一启动就崩」有好几种完全不同的原因，靠猜会一直修错地方，
        # 所以让容器自己把看到的东西报上来。
        st.setdefault("diag", []).append(line[len("__DIAG__|"):][:300])
        return
    if line.startswith("__EXE__|"):
        # 容器里最终选中的可执行名（want → got）。
        # 解析层给的是代码里的节点名，经常不等于包里注册的可执行名；
        # 留一份对照，出问题时能直接看出是「名字没对上」还是「包没装好」。
        parts = line[len("__EXE__|"):].split("|")
        if len(parts) >= 3:
            st.setdefault("exe_map", {})[parts[0]] = {"want": parts[1], "got": parts[2]}
        return
    if line.startswith("__RETRY__|"):
        # 内存不足被 OOM 杀掉后，失败的包会单线程补编。
        # 这期间 build_done 已经等于 build_total，进度条会停在 N/N 不动，
        # 用户会以为卡死；把「正在补编谁」透出去，前端换成补编文案。
        parts = line[len("__RETRY__|"):].split("|")
        st["retry_pkg"] = parts[0] if parts and parts[0] else ""
        try:
            st["retry_round"] = int(parts[1]) if len(parts) > 1 else 1
        except ValueError:
            st["retry_round"] = 1
        return
    if line.startswith("__RETRY_OK__|") or line.startswith("__RETRY_FAIL__|"):
        st["retry_pkg"] = ""
        return
    if line.startswith("__BUILD_REAL__|"):
        # 真实编译证据：gcc 被调用次数 + install 下产物个数。
        # 两个都是 0 只可能来自离线模拟，界面上要能据此区分真假。
        parts = line[len("__BUILD_REAL__|"):].split("|")
        for key, idx in (("build_cxx", 0), ("build_artifacts", 1)):
            try:
                st[key] = int(str(parts[idx]).strip())
            except (ValueError, IndexError):
                st[key] = 0
        return
    if line == "__BUILD_OK__":
        st["build_ok"] = True
        return
    if line == "__BUILD_FAIL__":
        st["build_failed"] = True
        return
    for tag, kind, res in (("__HOP_OK__|", "chain", "ok"),
                           ("__HOP_FAIL__|", "chain", "fail"),
                           ("__HOP_NA__|", "chain", "na"),
                           ("__MCU_OK__|", "mcu", "ok"),
                           ("__MCU_FAIL__|", "mcu", "fail"),
                           ("__MCU_NA__|", "mcu", "na"),
                           ("__MCUB_OK__|", "mcu_out", "ok"),
                           ("__MCUB_FAIL__|", "mcu_out", "fail"),
                           ("__MCUB_NA__|", "mcu_out", "na")):
        if line.startswith(tag):
            _hop(st, kind, res, line[len(tag):].split("|"))
            return
    if line.startswith("__NOEXE__|"):
        st.setdefault("noexe", []).append(line[len("__NOEXE__|"):].strip())
        return
    if line.startswith("__NODE_RC__|"):
        nid, rc = line[len("__NODE_RC__|"):].split("|")[:2]
        for n in st["nodes"]:
            if _slug(n["id"]) == nid:
                try:
                    n["rc"] = int(rc)
                except ValueError:
                    n["rc"] = None
        return
    # 编译阶段的输出都留着：失败时这份日志就是给用户看的错因；
    # 同时折算成进度，界面上的数字才动得起来。
    if st.get("stage") == "build":
        bl = st.setdefault("build_log", [])
        if len(bl) < 400 and line.strip():
            bl.append(line[:220])
        _build_progress(st, line)
        return
    return


def _ws_pkgs(st):
    """工作区自己的包名。节点都是从 src/ 扫出来的，它们的 package 就是自家包。

    有这个集合，`Package 'x' not found` 才能分岔：自家的包是「没写成 package.xml、
    colcon 没编」，别人的包才是「镜像里缺、要找管理员」。
    """
    return {n.get("package") for n in (st.get("nodes") or [])
            if n.get("package")}


def _hint(text, distro="", ws=None):
    """诊断一层兜底：翻译失败也不该让收尾流程崩。"""
    try:
        from .chain_diag import hint
        return hint(text, distro or "jazzy", ws)
    except Exception:                                   # noqa: BLE001
        return ""


def _node_of(st, nid):
    """按 hop 里的节点 id 找状态里的节点（id 是完整路径，name 是类名）。"""
    if not nid:
        return None
    for n in st["nodes"]:
        if n.get("id") == nid or n.get("name") == nid:
            return n
    short = nid.split("::")[-1]
    for n in st["nodes"]:
        if n.get("name") == short or (n.get("id") or "").endswith(short):
            return n
    return None


def dep_note(st, brief=False):
    """把「缺了哪些依赖」翻成一句能照着办的话。

    缺的包不是一个性质，混成一句「缺依赖」等于让人自己猜该找谁：
      · ros-base 自带的（tf2_ros、nav_msgs…）—— 镜像本该有，缺了多半是包名写错，
        用户自己就能查；
      · 基础镜像里没有的（backward_ros、rviz2…）—— 得额外安装。容器没有外网，
        用户自己装不了，只能找管理员。让他去 apt install 是白折腾。

    brief=True 只报「缺哪几个」，不重复「找管理员」那套 —— 编译失败的场景里
    诊断行已经把处置动作写清楚了，同一句指令在一行里出现两遍纯属噪音。
    """
    miss = list(st.get("pkgs_missing") or [])
    bad = list(st.get("pkgs_failed") or [])
    dis = st.get("distro") or "jazzy"
    pre = "ros-%s-" % dis

    def show(xs):
        """包名还原成人写的样子：ROS 包用下划线（package.xml 里就是 tf2_ros），
        系统包（libqt6-core / python3-matplotlib）本来就是连字符，原样保留。
        fetch_pkgs 两种写法都收，所以命令里也用它。"""
        return [x[len(pre):].replace("-", "_") if x.startswith(pre) else x for x in xs]

    bits = []
    if miss:
        base_miss, extra, unknown = base_pkgs.classify(miss, dis)
        need = extra + unknown
        if need:
            if brief:
                bits.append("镜像里缺 %d 个依赖：%s（本机没有、目前不支持，要管理员补包）"
                            % (len(need), "、".join(show(need))))
            else:
                # 命令只在括号里点一下工具名，不重复罗列包名：结论条本来就长，
                # 用户要的是「找谁」，包名清单已经在他眼前了，管理员照着拼即可。
                cmd = ("python tools/fetch_pkgs.py " + " ".join(need)
                       if len(need) <= 4 else "python tools/fetch_pkgs.py --pending")
                bits.append(
                    "镜像里缺 %d 个依赖：%s —— 这几项本机没有、也就是目前不支持，"
                    "需要额外安装；容器没有外网，你自己装不了，请联系管理员补进依赖包池"
                    "（已记入 pkgcache/pending.txt，管理员一条命令补齐：%s）"
                    % (len(need), "、".join(show(need)), cmd))
        if base_miss:
            bits.append(
                "%s 属于 ROS 基础包、镜像本该自带，缺了多半是 package.xml 里包名写错了"
                % "、".join(show(base_miss)))
    elif bad:
        bits.append("包池里的 %s 装不进容器（缺系统依赖），本机补不了 —— "
                    "请联系管理员重新抓一遍这几个包"
                    "（python tools/fetch_pkgs.py %s）"
                    % ("、".join(bad), " ".join(bad[:4])))
    # extra_apt 联网安装失败也不该一票否决：包池可能已经补齐了，编译照样能过
    if st.get("apt_failed"):
        bits.append("容器里联网装额外依赖失败（多半没外网），已改用本地包池继续")
    return "；".join(bits)


def finish(st, code, tail=""):
    """收尾：逐节点判定、逐跳结论、断点定位。"""
    from .chain_run import judge_node
    st["running"] = False
    st["finished"] = True
    st["elapsed"] = round(time.time() - st["started_at"], 1)
    st["log"] = (tail or "")[-3000:]
    st["exit_code"] = code
    # 编译阶段的原始输出单独留一份，前端「查看编译输出」直接给它
    bl = st.get("build_log") or []
    if isinstance(bl, list):
        st["build_log"] = "\n".join(bl)[-6000:]
    skip = set(st.get("noexe") or [])
    for n in st["nodes"]:
        if _slug(n["id"]) in skip:
            # 组件包（composition 的 Client/Server/talker）：包里只有 .so 插件，
            # 没有可执行，ros2 run 必然 No executable found —— 这不是失败。
            n["result"] = "skip"
            n["reason"] = "cpp 组件，没有独立可执行（只能塞进组件容器加载）"
            continue
        res, why = judge_node(n["rc"], n["log"])
        if not res and n["rc"] is None:
            res, why = "fail", "未启动（容器里没跑到这个节点）"
        n["result"] = res or "ok"
        n["reason"] = why
    hops = st["hops"]
    bad_hop = next((h for h in hops if h["result"] == "fail"), None)
    pend = next((h for h in hops if h["result"] == "pending"), None)
    bad_node = next((n for n in st["nodes"] if n["result"] == "fail"), None)
    # na = 记录员没能订上（接口解析不出来）。既不算过也不算断，单独报出来，
    # 免得「没取证」被悄悄当成通过。
    na_n = len([h for h in hops if h["result"] == "na"])
    skip_n = len([n for n in st["nodes"] if n["result"] == "skip"])
    st["hops_na"] = na_n
    st["broken_at"] = hops.index(bad_hop) if bad_hop else -1
    # 依赖只当线索，不当罪名。
    # 静态扫描会把工作空间自己的包（robot_msgs）误当成外部依赖，拿它判失败
    # 等于冤枉用户项目；真正该说话的是 colcon 和节点本身。缺的包记下来，
    # 挂在失败原因后面当补充说明；真跑通了就只提一句，不影响结论。
    _dep_note = dep_note(st)
    # 缺的包顺手记进待补清单：用户看到的「请联系管理员」要能落到具体动作上，
    # 管理员那边一条 `python tools/fetch_pkgs.py --pending` 就能全部抓好。
    if st.get("pkgs_missing"):
        try:
            pkgcache.note_missing(st["pkgs_missing"])
        except Exception:                     # noqa: BLE001
            pass
    # 链路断点和「镜像里少一个 python 包」通常没有因果关系。
    # 直接拼在断点原因后面，读起来像「断在这里是因为缺 matplotlib」——
    # 上一版就这么写了，用户照着去查依赖，查了个空。
    # 「与本次断点无关」在 partial（没有断点，只是个别节点没起来）里说不通，
    # 要防的是同一件事：别让人以为缺的包是这次不通的原因。
    _dep_side = ("另注（跟这次结论没关系）：" + _dep_note.replace("另外缺", "缺", 1)
                 if _dep_note else "")
    if st.get("env_missing"):
        st["ok"] = False
        st["reason"] = ("容器镜像里没有 %s，编译起不来。用项目里的 Dockerfile.verify "
                        "构建镜像，或把 config.json 的 ros.verify.image 指到装了 colcon 的镜像"
                        % st["env_missing"])
        return st
    if st.get("env_fail"):
        st["ok"] = False
        st["reason"] = "容器里 ROS 环境加载失败（source /opt/ros/*/setup.bash 报错）"
        return st
    if st.get("build_failed"):
        st["ok"] = False
        # 原始输出交给「查看编译输出」，这里再给一句能照着改的话
        st["hint"] = _hint(st.get("build_log") or st.get("log") or "",
                          ws=_ws_pkgs(st))
        st["reason"] = "colcon build 没通过，链路验证未执行"
        # 诊断行已经写了「哪个包 + 找管理员 + 命令」时，结论条只报缺了哪几个，
        # 不再把同一句指令重复一遍。
        _brief = "联系管理员补进依赖包池" in (st["hint"] or "")
        _note = dep_note(st, brief=_brief) if _brief else _dep_note
        if _note:
            st["reason"] += "；" + _note
        return st
    if st["cancelled"]:
        st["ok"] = False
        st["reason"] = "已手动退出验证"
        return st
    if bad_hop:
        st["ok"] = False
        # 回传跳的「发送方」是假下位机，说「下位机没把消息发到」会让用户去查硬件
        if bad_hop["kind"] == "mcu_out":
            dst = _node_of(st, bad_hop.get("to") or "")
            dname = (dst or {}).get("name") or (bad_hop.get("to") or "").split("::")[-1]
            if dst and dst.get("result") == "fail":
                st["hint"] = _hint(dst.get("log") or "", st.get("distro") or "",
                                  ws=_ws_pkgs(st))
                st["reason"] = ("下位机回传 %s 没送到「%s」：这个节点已经退出（%s）"
                                % (bad_hop["topic"], dname, dst.get("reason") or "进程结束"))
            else:
                st["reason"] = "下位机回传 %s 没送到「%s」" % (bad_hop["topic"], dname)
            if _dep_side:
                st["reason"] += "；" + _dep_side
            return st
        who = bad_hop["from"].split("::")[-1] if bad_hop["from"] else "下位机"
        if who == "__mcu__":
            who = "下位机"
        src = _node_of(st, bad_hop["from"])
        if src and src.get("result") == "fail":
            # 「节点自己启动就崩」和「节点活着但没发消息」是两回事，
            # 混成一句「没把消息发到」会让人去查发布逻辑，其实该看 traceback
            st["hint"] = _hint(src.get("log") or "", st.get("distro") or "",
                              ws=_ws_pkgs(st))
            st["reason"] = "「%s」启动就崩了（%s），%s 上没有消息" % (
                src.get("name") or who, src.get("reason") or "进程退出",
                bad_hop["topic"])
        else:
            note = (bad_hop.get("note") or "").strip()
            if note == "pubs=0":
                # 发布者数量为 0：不是「发了没收到」的时序问题，是压根没人发
                st["reason"] = ("「%s」→ %s 断了：容器里这个话题上一个发布者都没有，"
                                "说明发布方没起来，或者代码里的话题名和图上不一致"
                                % (who, bad_hop["topic"]))
            else:
                st["reason"] = ("「%s」→ %s 断了：取证窗口里一条消息都没收到（%s）"
                                % (who, bad_hop["topic"], note or "pubs=?") + "")
        if _dep_side:
            st["reason"] += "；" + _dep_side
        if na_n:
            st["reason"] += "；另有 %d 段连线没能取证（接口类型解析不出来）" % na_n
        return st
    if bad_node:
        # 节点自己崩 vs 链路没通是两件事。数据在链上全跑通了，只是有几个示例
        # 节点在这套容器里起不来（要真实时优先级、要配套的 server、或者本身是
        # 组件包），把它们说成「链路没有跑通」会让人去查根本没断的链路。
        st["ok"] = False
        st["partial"] = True
        bad = [n for n in st["nodes"] if n["result"] == "fail"]
        try:
            from .chain_diag import summary, upstream
        except Exception:                                     # noqa: BLE001
            summary = upstream = lambda *a, **k: ""            # noqa: E731
        # 「官方示例没跟上发行版」和「你的代码写错了」必须分开说：
        # 前者一行代码都不用改，摆在报告里当成报错，用户只会以为是自己写错了
        # （实测原话：「我不想看见报错」）。分开之后，前者只出现在灰色说明里，
        # 红色的诊断只留给真正需要改代码的那种。
        up, mine = [], []
        for n in bad:
            try:
                why = upstream(n.get("log") or "", n.get("package") or "")
            except Exception:                                  # noqa: BLE001
                why = ""
            if why:
                n["upstream"] = why
                up.append(n)
            else:
                mine.append(n)
        # 红色诊断只留给「要改代码」的那种；纯官方示例不兼容时说清原因即可，
        # 否则用户看到的是「报错」，但他其实一行都不用改。
        st["hint"] = ""
        if mine:
            first = mine[0]
            st["hint"] = _hint(first.get("log") or "", st.get("distro") or "",
                              ws=_ws_pkgs(st))
            # 诊断里要带上「是哪个节点」：结论里点了两个名字，诊断只说「抛了 X」，
            # 用户还得自己去配对（实测直接问过「下面这是什么意思」）
            if st["hint"]:
                st["hint"] = "「%s」%s" % (first.get("name") or "?", st["hint"])
            # 其余没起来的节点也各给一句：它们死因常常不一样（示例各自撞上不同的
            # 兼容问题），只解释第一个，用户会默认第二个也是同一个原因。
            for n in mine[1:3]:
                try:
                    one = summary(n.get("log") or "")
                except Exception:                              # noqa: BLE001
                    one = ""
                if not one:
                    continue
                line = "「%s」%s" % (n.get("name") or "?", one)
                st["hint"] = (st["hint"] + "\n另：" + line) if st["hint"] else line
        if up:
            st["note"] = "%d 个 ROS 官方示例在这个发行版上跑不起来，是它们自己没跟上" \
                         "发行版（不用改你的代码）：\n%s" % (
                             len(up),
                             "\n".join("· %s：%s" % (n.get("name") or "?", n["upstream"])
                                       for n in up[:4]))
        names = "、".join((n.get("name") or "?") for n in mine[:4])
        if len(mine) > 4:
            names += " 等 %d 个" % len(mine)
        if mine or not up:
            st["reason"] = ("逐跳取证 %d 段全部通过，但有 %d 个节点自己没起来：%s"
                            "（节点自身的问题，链路上的数据没断）"
                            % (len([h for h in hops if h["result"] == "ok"]),
                               len(mine) or len(bad), names))
            if up:
                st["reason"] += "；另有 %d 个官方示例不兼容" % len(up)
        else:
            # 「几个示例不兼容」标题里已经写了，这里只说链路本身，
            # 同一句话在一行里说两遍，读起来像复读（上几版就被这么吐槽过）
            st["reason"] = ("逐跳取证 %d 段全部通过"
                            % len([h for h in hops if h["result"] == "ok"]))
        if skip_n:
            st["reason"] += "；另有 %d 个是 cpp 组件、没有独立可执行" % skip_n
        if _dep_side:
            st["reason"] += "；" + _dep_side
        return st
    if pend:
        st["ok"] = False
        st["reason"] = "验证被提前终止，有 %d 跳没来得及断言" % len(
            [h for h in hops if h["result"] == "pending"])
        return st
    if not hops:
        st["ok"] = False
        st["reason"] = "架构图里没有可验证的连线"
        return st
    st["ok"] = True
    _out = len([h for h in hops if h["kind"] == "mcu_out"])
    _proven = len([h for h in hops if h["result"] == "ok"])
    st["reason"] = "全链路 %d 跳全部收到真实消息" % _proven
    if _out:
        st["reason"] += "（其中 %d 条是下位机回传）" % _out
    if na_n:
        st["reason"] += "；另有 %d 段连线没能取证（接口类型解析不出来），未计入" % na_n
    if skip_n:
        st["reason"] += "；%d 个 cpp 组件没有独立可执行（要在组件容器里加载），未启动" % skip_n
    # 通过了也要提一句缺包：链路能跑通不代表那些包用不上，
    # 只是这次没触发。等到用户跑到 demos 里画图的那个节点时才炸，
    # 那时候已经想不起「验证时提示过缺 matplotlib」了。
    if _dep_note:
        st["reason"] += "；" + _dep_note
    return st
