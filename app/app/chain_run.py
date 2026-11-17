"""跑链路验证：mock 脚本、逐跳/逐节点判定、状态快照。"""
import os
import time

from .chain_msg import _slug


def mock_script(plan):
    """离线测试用：用 Python 按顺序打标记行（不依赖 bash），走同一套解析。"""
    fail = os.environ.get("ROS2_CHAIN_MOCK_FAIL", "")
    build_fail = os.environ.get("ROS2_CHAIN_MOCK_BUILD_FAIL", "")
    # 测试用：指定某个节点「一行日志都没有」，验证前端不会永远停在「日志收集中」
    nolog = os.environ.get("ROS2_CHAIN_MOCK_NOLOG", "")
    L = ["import time", "", "def hit(x):", "    print(x, flush=True)", "    time.sleep(1)",
         "", "time.sleep(0.5)", "hit('__STAGE__|build')"]
    if build_fail:
        # 模拟一次真的编译失败：错误行要能被前端「查看编译输出」摊开
        for ln in ("Starting >>> perception_pkg",
                   "--- stderr: perception_pkg",
                   "error: package 'vision_msgs' not found",
                   "Failed   <<< perception_pkg [0.42s, exited with code 1]",
                   "Summary: 1 package finished [1.10s]"):
            L.append("print(%r, flush=True)" % ln)
        L.append("hit('__BUILD_FAIL__')")
        L.append("raise SystemExit(2)")
        return "\n".join(L) + "\n"
    L += ["hit('__BUILD_OK__')",
          "hit('__STAGE__|run')"]
    for n in plan["nodes"]:
        L.append("hit('__STAGE__|run')")
    if plan["entries"]:
        L.append("hit('__STAGE__|inject')")
    L.append("hit('__STAGE__|hop')")
    for h in plan["hops"]:
        tag = {"mcu": "MCU", "mcu_out": "MCUB"}.get(h["kind"], "HOP")
        ok = "FAIL" if fail and h["topic"] == fail else "OK"
        L.append("hit('__%s_%s__|%s|%s|%s')" % (tag, ok, _slug(h["from"]),
                                               _slug(h["to"]), h["topic"]))
    L.append("hit('__STAGE__|done')")
    for n in plan["nodes"]:
        nid = _slug(n["id"])
        nm = n.get("name") or nid
        L.append("print('__NODE_RC__|%s|130', flush=True)" % nid)
        L.append("print('__NODELOG_BEGIN__|%s', flush=True)" % nid)
        # 多打几行：真实容器里节点日志本来就长，前 3 行会被折叠
        if not (nolog and nolog in nid):
            for ln in ("[INFO] [%s]: 节点启动完成，等待数据" % nm,
                       "[INFO] [%s]: 收到 1 条消息，开始处理" % nm,
                       "[INFO] [%s]: 字段校验通过" % nm,
                       "[INFO] [%s]: 处理耗时 0.8 ms" % nm,
                       "[INFO] [%s]: 已发布下游话题，等待回执" % nm):
                L.append("print(%r, flush=True)" % ln)
        L.append("print('__NODELOG_END__|%s', flush=True)" % nid)
    L.append("hit('__VERIFY_DONE__')")
    return "\n".join(L) + "\n"


def judge_node(rc, log):
    """被收尾 SIGINT 掐掉的节点（rc=130）算存活；只认日志中部的 Traceback 为真崩溃。"""
    log = log or ""
    if "No executable found" in log or "No executable" in log:
        return "fail", "找不到可执行（entry 名与 package 不符？）"
    if "Traceback" in log:
        tail = log.strip().splitlines()[-3:]
        if not any("KeyboardInterrupt" in x for x in tail):
            return "fail", "运行中抛出 Traceback"
    if rc not in (0, 130, 131, None):
        return "fail", "异常退出 rc=%s" % rc
    return "", ""


def new_state(plan, backend, distro, image):
    return {
        "running": True, "finished": False, "cancelled": False,
        "backend": backend, "distro": distro, "image": image,
        "stage": "prepare", "started_at": time.time(), "elapsed": 0.0,
        "ok": False, "partial": False, "hops_na": 0, "reason": "", "log": "",
        "build_failed": False, "build_log": [], "hint": "",
        # 编译进度：done/total 让前端能显示「已完成 12 / 45」，
        # cur 是正在编的包名。没有这三个字段，长编译就只能转圈。
        "build_total": 0, "build_done": 0, "build_bad": 0, "build_cur": "",
        "build_cxx": 0, "build_artifacts": 0,
        "retry_pkg": "", "retry_round": 0,
        # 超时和用户主动退出必须能分开：前者要调额度/精简项目，后者什么都不用做
        "timed_out": False, "budget": 0,
        "env_missing": "", "env_fail": False, "apt_failed": False,
        # 依赖补装：容器里缺的包从本地包池 dpkg 进来，装了哪些、缺哪些都要能看见
        "pkgs_needed": [], "pkgs_installed": [], "pkgs_missing": [],
        "pkgs_failed": [], "deps_failed": False,
        "skipped": plan.get("skipped") or [],
        "entries": plan.get("entries") or [],
        "mcus": plan.get("mcus") or [],
        "hops": [{"kind": h["kind"], "from": h["from"], "to": h["to"],
                  "topic": h["topic"], "type": h["type"],
                  "mcu": h.get("mcu") or "",
                  "result": "pending", "at": 0} for h in plan["hops"]],
        "nodes": [{"id": n.get("id") or "", "name": n.get("name") or "",
                   "package": n.get("package") or "", "entry": n.get("entry") or "",
                   "result": "", "reason": "", "rc": None, "log": ""}
                  for n in plan["nodes"]],
    }
