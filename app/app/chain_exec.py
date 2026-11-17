"""启动验证（docker 真跑 / mock 离线），流式读输出维护状态，支持取消。

跑完的结果要落盘（data/verify/<sid>.json）：切会话、刷新页面、进程重启后
还能翻回上一次的绿线、节点日志和结论。落盘时带一个「图指纹」，
图一改旧结果就作废——把上次的绿线贴到新图上，比不显示更糟。
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time

from . import (chain_flow, chain_plan, chain_probe, chain_run, chain_script,
               dep_scan, paths, pkgcache, project_import, sandbox, versions)

_RUN = {}
_LOCK = threading.Lock()

# 单份结果的体积上限：原始日志动辄几十万字符，回看用不到那么多
_MAX_SNAP = 2 * 1024 * 1024


def plan_sig(p):
    """这张图的连线 + 节点指纹。

    验证结果是「这张图的这次运行」的证词，图一变它就不算数了。
    """
    hops = [[h.get("kind") or "", h.get("from") or "", h.get("to") or "",
             h.get("topic") or "", h.get("mcu") or ""] for h in (p.get("hops") or [])]
    nodes = [[n.get("id") or "", n.get("name") or "", n.get("entry") or ""]
             for n in (p.get("nodes") or [])]
    raw = json.dumps([hops, nodes], ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


def _snap_path(sid):
    safe = re.sub(r"[^A-Za-z0-9_.-]", "", sid or "")[:64]
    if not safe:
        return ""
    return os.path.join(paths.data(create=False), "verify", safe + ".json")


def _drop(sid):
    p = _snap_path(sid)
    if p and os.path.isfile(p):
        try:
            os.remove(p)
        except OSError:
            pass


def _save(sid, st):
    """把跑完的结果写盘。运行中的状态不存：那种状态恢复出来只会骗人。"""
    if not sid or not st or st.get("running"):
        return
    p = _snap_path(sid)
    if not p:
        return
    try:
        payload = json.loads(json.dumps(st, ensure_ascii=False, default=str))
        payload.pop("proc", None)
        payload["saved_at"] = time.time()
        # 内存里的这份也带上时间：同一个进程内再回看时，界面要能写出「上次运行几点」
        try:
            st["saved_at"] = payload["saved_at"]
        except Exception:                               # noqa: BLE001
            pass
        blob = json.dumps(payload, ensure_ascii=False)
        if len(blob) > _MAX_SNAP:
            payload["log"] = (payload.get("log") or "")[-20000:]
            blob = json.dumps(payload, ensure_ascii=False)
        if len(blob) > _MAX_SNAP:
            for n in payload.get("nodes") or []:
                if isinstance(n, dict):
                    n["log"] = (n.get("log") or "")[-4000:]
        d = os.path.dirname(p)
        if not os.path.isdir(d):
            os.makedirs(d, exist_ok=True)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=False))
        os.replace(tmp, p)
    except Exception:                                   # noqa: BLE001
        pass


def _load(sid, sess=None):
    """读回上次的结果；图改过（指纹对不上）就当没有，顺手删掉。"""
    p = _snap_path(sid)
    if not p or not os.path.isfile(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            st = json.load(f)
    except Exception:                                   # noqa: BLE001
        return None
    if not isinstance(st, dict) or not st.get("finished"):
        return None
    if sess is not None:
        try:
            now = plan_sig(chain_plan.plan(sess))
        except Exception:                               # noqa: BLE001
            now = ""
        if st.get("plan_sig") and now and st["plan_sig"] != now:
            _drop(sid)
            return None
    if st.get("running"):
        st.update(running=False, finished=True, ok=False,
                  reason="上次验证被中断（服务重启过），这份结果不完整")
    st["restored"] = True
    return st


def _backend(cfg):
    if os.environ.get("ROS2_CHAIN_MOCK"):
        return "mock", ""
    backend, reason = sandbox.resolve(cfg)
    # 演示兜底：机房/云主机通常没有 Docker，也没有本机 ROS。
    # 这时与其让按钮变成「本次未执行」，不如跑离线模拟链路，
    # 但结果里 backend 仍写 mock，界面会明确标注「模拟」——不是假装真跑过。
    if backend == "none":
        vcfg = (cfg.get("ros") or {}).get("verify") or {}
        if vcfg.get("fallback_mock"):
            return "mock", ""
    return backend, reason


def _cmd(cfg, backend, sess, script, name, pkgs=""):
    if backend == "mock":
        return [sys.executable, script]
    if backend == "local":
        return ["bash", script]
    vcfg = (cfg.get("ros") or {}).get("verify") or {}
    extra = str(vcfg.get("extra_apt") or "").strip()
    # 要在容器里 apt 装额外依赖，就必须放开网络；不装时仍然完全断网。
    net = "bridge" if extra else "none"
    # 资源额度可配（config.json → ros.verify.memory / cpus）。
    #
    # 原先是写死的 2g/2，对纯 Python 小项目够用，但一碰真实的 C++ 工作区
    # 就崩：17 个 C++ 包在 2 核上并行编译要吃满半小时，2G 内存下 cc1plus
    # 还会被 OOM killer 干掉，报出来是 `c++: fatal error: Killed` ——
    # 看起来像用户代码写错了，实际是容器太小。
    mem = str(vcfg.get("memory") or "4g")
    cpus = str(vcfg.get("cpus") or "3")
    cmd = ["docker", "run", "--rm", "--name", name,
           "--network", net, "--cap-drop", "ALL",
           "--security-opt", "no-new-privileges",
           "--memory", mem, "--cpus", cpus, "--pids-limit", "1024",
           # ROS_LOCALHOST_ONLY 在 Jazzy 已废弃，设了会在每个节点日志开头刷两条 WARN，
           # 把真正的输出挤掉；只用新的 LOCALHOST 发现范围，效果一样且不吵
           "-e", "ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST",
           "-e", "ROS2_EXTRA_APT=%s" % extra,
           "-e", "ROS2_PKGS=%s" % pkgs]
    # 本地包池只读挂进来：容器缺包就 dpkg -i，跑完随容器销毁
    pool = pkgcache.pool_dir()
    if pkgcache.exists():
        cmd += ["-v", "%s:/pkgpool:ro" % pool]
    return cmd + ["-v", "%s:/ws" % sess.ws_root(), "-w", "/ws",
                  versions.image(cfg), "bash", "/ws/run_chain_verify.sh"]


def _budget(cfg, plan):
    """整轮验证的硬上限。

    老公式把编译时间算进 base（120s 底数），等于让「编译」和「跑节点」
    抢同一个池子。纯 Python 小项目看不出问题，一碰 45 个包、17 个 C++
    的真实工作区就必然死在半路：光编译就要二十几分钟，还没进链路阶段
    就被硬超时掐断，最后报「编译没通过」——而它其实只是没编完。

    现在把编译单独给预算，链路部分按跳数累加；启动开销按节点数封顶
    2 分钟（节点是一次性全起的，不是逐个 wait）。
    """
    cv = (cfg.get("ros") or {}).get("chain_verify") or {}
    hop = int(cv.get("hop_timeout_sec") or 12)
    build = int(cv.get("build_timeout_sec") or 1500)
    base = int(cv.get("total_timeout_sec") or 120)
    boot = int(cv.get("boot_wait_sec") or 8)
    node_start = min(len(plan["nodes"]), 120)
    return build + base + boot + node_start \
        + hop * (len(plan["hops"]) + 1) + 60


def _pump(proc, st, tmp, sid=""):
    tail = []
    cap = {"id": None, "lines": []}
    try:
        for line in iter(proc.stdout.readline, ""):
            line = line.rstrip("\r\n")
            tail.append(line)
            if len(tail) > 400:
                del tail[:200]
            chain_flow.feed(st, line, cap)
    except Exception:                                   # noqa: BLE001
        pass
    code = proc.wait()
    chain_flow.finish(st, code, "\n".join(tail))
    _save(sid, st)
    if tmp:
        try:
            import shutil
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:                               # noqa: BLE001
            pass


def _park(sid, st):
    """早退（环境不支持/图里没连线）也要登记，前端才查得到原因。"""
    with _LOCK:
        _RUN[sid] = {"proc": None, "state": st, "name": ""}
    _save(sid, st)
    return st


def start(cfg, sess, sid):
    """返回状态快照；后台线程持续更新同一份 dict。"""
    p = chain_plan.plan(sess)
    backend, reason = _backend(cfg)
    st = chain_run.new_state(p, backend, versions.distro_name(cfg),
                             versions.image(cfg) if backend == "docker" else "")
    st["plan_sig"] = plan_sig(p)
    # 新一轮开始，上一次的结果立刻作废：中途刷新页面不该看到旧的绿线
    _drop(sid)
    if backend == "none":
        st.update(running=False, finished=True,
                  reason=reason or "本机没有 Docker，链路验证未执行")
        return _park(sid, st)
    if backend == "local":
        st.update(running=False, finished=True,
                  reason="本机是本地 ROS 模式，链路验证需要容器")
        return _park(sid, st)
    if not p["hops"]:
        st.update(running=False, finished=True,
                  reason="架构图里没有连线，无法验证链路")
        return _park(sid, st)
    # 老会话的工作区可能缺 `resource/<包名>`（导入链路修好之前留下的、用户手工删过、
    # 或者从别处搬来的工作区）。缺了它 colcon 一定在打包阶段失败，用户看到的是
    # 「编译没通过」——和真正的代码错误长得一样。挂给容器之前补一次，空的标记文件而已。
    healed = project_import.ensure_markers(sess.ws_root())
    # 光有 resource 标记还不够：src/<pkg>/ 下如果连 package.xml 都没有（早前版本生成
    # 的项目、或者用户自己丢进来的一堆 .py），colcon 根本不认这个包，编译阶段 0 个包，
    # 后面每个节点都报 `Package not found`。这类工作区不能要求用户手工补壳 —— 挂进
    # 容器前就地补一份，让它能被正常编译。
    try:
        from . import pkg_scaffold
        shelled = pkg_scaffold.ensure_ws(sess.ws_root())
    except Exception:                          # noqa: BLE001
        shelled = []
    tmp = None
    if backend == "mock":
        tmp = tempfile.mkdtemp(prefix="chainmock_")
        script = os.path.join(tmp, "run_chain_mock.py")
        body = chain_run.mock_script(p)
    else:
        script = os.path.join(sess.ws_root(), "run_chain_verify.sh")
        body = chain_script.build_script(st["distro"], p, cfg)
        # 逐跳取证靠「记录员」：起节点之前挂上，只记「哪个话题来过消息」。
        # 早先是一个话题一个探针、每个等 12 秒，三个毛病都在这里治：
        # 启动瞬间只发一次的消息抓不到、best effort 的话题订不上、大消息被 echo 卡死。
        with open(os.path.join(sess.ws_root(), "hop_recorder.py"), "w",
                  encoding="utf-8", newline="\n") as f:
            f.write(chain_probe.RECORDER_PY)
        # 待取证话题清单：类型来自源码解析，解析不出的留空，记录员等它上线后自己补
        with open(os.path.join(sess.ws_root(), "hop_topics.tsv"), "w",
                  encoding="utf-8", newline="\n") as f:
            seen = set()
            for h in (p.get("hops") or []):
                t = h.get("topic") or ""
                if not t or t in seen:
                    continue
                seen.add(t)
                f.write("%s\t%s\n" % (t, h.get("type") or ""))
    if shelled and backend != "mock":
        # 补壳是要说一声的：用户可能在别处也留了一份工作区，不知道这里动过什么
        note = 'echo "[prep] 已把 %s 补成可编译的包（package.xml / setup.py / setup.cfg）"' \
               % "、".join(shelled[:6])
        body = (body.replace("\n", "\n" + note + "\n", 1)
                if body.startswith("#!") else note + "\n" + body)
    if healed and backend != "mock":
        # 留下痕迹：「查看编译输出」里能看见这轮补了什么，别让用户以为项目本来就有
        note = 'echo "[prep] 已补 %d 个 resource 标记文件：%s"' % (
            len(healed), "、".join(healed[:6]))
        body = (body.replace("\n", "\n" + note + "\n", 1)
                if body.startswith("#!") else note + "\n" + body)
    with open(script, "w", encoding="utf-8", newline="\n") as f:
        f.write(body)
    name = "ros2chain-" + re.sub(r"[^a-z0-9]", "", sid.lower())[:22]
    # 先清同名残留容器。
    # 容器名是按会话固定生成的，而上一轮验证的容器如果服务重启过（发布更新、
    # 进程被杀），就没人负责收尾：--rm 要等脚本自己跑完才删。这时再点验证，
    # docker 直接报 Conflict，前端显示「节点未启动」，看起来像代码坏了。
    if backend == "docker":
        try:
            subprocess.run(["docker", "rm", "-f", name],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=30)
        except Exception:                               # noqa: BLE001
            pass
    # 这个项目到底可能需要哪些包：源码里的 msg/srv action 导入 + package.xml 声明。
    # 交给容器自己用 dpkg -s 判断缺哪个（已装的不动），缺的从只读包池补。
    pkgs = ""
    if backend == "docker":
        try:
            pkgs = " ".join(dep_scan.needed(sess.ws_root(), st["distro"]))
            st["pkgs_needed"] = pkgs.split()
        except Exception:                               # noqa: BLE001
            pkgs = ""
    proc = subprocess.Popen(_cmd(cfg, backend, sess, script, name, pkgs),
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    with _LOCK:
        _RUN[sid] = {"proc": proc, "state": st, "name": name}
    th = threading.Thread(target=_pump, args=(proc, st, tmp, sid), daemon=True)
    th.start()

    # 单独算一次：超时文案里要报出「给了多少秒」，两边必须同一个数
    budget = _budget(cfg, p)

    def _kill():
        if st["running"]:
            # 超时不是「用户点了退出」，两者处置完全不同：超时要调额度/精简项目，
            # 手动退出什么都不用做。以前这里只设 reason 就调 cancel，而 cancel
            # 会把 reason 硬覆盖成「已手动退出验证」，于是 28 分钟的等待最后
            # 得到一句「你退出了」—— 既误导又没法排查。
            st["timed_out"] = True
            st["budget"] = budget
            st["reason"] = "验证超时"
            cancel(sid)

    timer = threading.Timer(budget, _kill)
    timer.daemon = True
    timer.start()
    return st


def status(sid, sess=None):
    """内存里的活状态优先；没有就读上次落盘的结果（切会话/重启后回看）。"""
    with _LOCK:
        item = _RUN.get(sid)
    if item:
        return item["state"]
    return _load(sid, sess)


def cancel(sid):
    with _LOCK:
        item = _RUN.get(sid)
    if not item:
        return False
    st = item["state"]
    # 已经收尾的结果别被「退出验证」改写：那一下只是关掉回看，没取消任何东西
    if st["finished"]:
        return True
    st["cancelled"] = True
    try:
        if st.get("backend") == "docker":
            subprocess.run(["docker", "rm", "-f", item["name"]],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=15)
        if item["proc"] is not None:
            item["proc"].kill()
    except Exception:                                   # noqa: BLE001
        pass
    if not st["finished"]:
        chain_flow.finish(st, -1, st.get("log") or "")
        st["ok"] = False
        if st.get("timed_out"):
            b = int(st.get("budget") or 0)
            if st.get("stage") == "build":
                st["reason"] = (
                    "编译超时：%d 秒内只编完 %d/%d 个包，剩下的没轮到"
                    % (b, int(st.get("build_done") or 0),
                       int(st.get("build_total") or 0)))
                st["hint"] = (
                    "这个工作区有 %d 个包，容器 %s 核算不过来。"
                    "想看到完整结果有两条路：换成只包含你真正要验证的那几个包的精简工作区，"
                    "或者让管理员调大 ros.verify.build_timeout_sec / cpus。"
                    % (len(st.get("nodes") or []), st.get("image") or "ROS 2"))
            else:
                st["reason"] = ("验证超时：%d 秒内没跑完（卡在 %s 阶段）"
                                % (b, st.get("stage") or "未知"))
        else:
            st["reason"] = "已手动退出验证"
    _save(sid, st)
    return True
