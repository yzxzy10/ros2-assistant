"""从架构图规划链路验证：注入口、拓扑序断言点、节点清单、下位机话题。"""
import os

from . import graph_api
from .chain_msg import _snake


def _interfaces(nodes):
    """汇总 话题→发布者/订阅者/消息类型，只用解析成功的接口。"""
    pubs, subs, types = {}, {}, {}
    for n in nodes:
        for key, bag in (("publishes", pubs), ("subscribes", subs)):
            for it in (n.get(key) or []):
                if not it.get("resolved", True):
                    continue
                t = it.get("topic") or ""
                if not t:
                    continue
                bag.setdefault(t, set()).add(n["id"])
                if it.get("type"):
                    types.setdefault(t, it["type"])
    return pubs, subs, types


# 这些消息类型是「机器人 → 上位机」的反馈，不是下发给硬件的指令
_FEEDBACK_TYPE_PREFIX = ("sensor_msgs/", "nav_msgs/Odometry", "nav_msgs/Path",
                         "tf2_msgs/", "diagnostic_msgs/", "rosgraph_msgs/",
                         "nav_msgs/OccupancyGrid")
# 兜底：类型缺失时按话题名判断（odom / imu 这类一定是反馈）。
# 注意不能收单字 "state"：/motor_state、/gripper_state 是下发给硬件的目标状态，
# 收进来会把它们误判成反馈，整条下位机跳直接消失（实测踩过）。
_FEEDBACK_WORDS = ("odom", "imu", "scan", "laser", "battery", "encoder",
                   "joint_states", "robot_state", "system_state", "tf",
                   "feedback", "sensor", "status")


def _is_hw_command(topic, mtype):
    """这个话题是不是「AI 下发/节点下发 给下位机」的指令。

    只看拓扑分不出来：/cmd_vel 和 /odom 都是没人订阅。
    所以按语义再筛一层——反馈类的排除掉，剩下的还得分清楚是「给硬件的指令」
    还是「随手发的调试信息」：只有名字带 cmd/target/vel 这类词，或者类型是
    常见控制消息，才值得配一台下位机。
    """
    t = (mtype or "").strip().lower()
    for p in _FEEDBACK_TYPE_PREFIX:
        if t.startswith(p.lower()):
            return False
    name = (topic or "").strip("/").lower()
    for w in _FEEDBACK_WORDS:
        if w in name:
            return False
    if any(w in name for w in _CMD_NAME_WORDS):
        return True
    return any(t.startswith(x.lower()) for x in _CMD_TYPES)


# 指令话题的特征词 / 常见控制类型
_CMD_NAME_WORDS = ("cmd", "command", "target", "goal", "setpoint", "servo",
                   "vel", "velocity", "throttle", "pwm", "effort", "torque")
_CMD_TYPES = ("geometry_msgs/msg/Twist", "geometry_msgs/msg/TwistStamped",
              "geometry_msgs/msg/Pose", "geometry_msgs/msg/PoseStamped",
              "std_msgs/msg/Float32", "std_msgs/msg/Float64",
              "std_msgs/msg/Int8", "std_msgs/msg/Int16", "std_msgs/msg/Int32",
              "trajectory_msgs/", "control_msgs/")


# 「无发布者、但有节点订阅」的话题，如果是这些语义，就是硬件回传上来的状态
_FEEDBACK_NAME_WORDS = ("odom", "state", "status", "battery", "voltage",
                        "current", "encoder", "joint", "imu", "scan", "laser",
                        "range", "sonar", "temp", "pose", "tf", "feedback",
                        "sensor", "gps", "magnetic")


def _is_hw_feedback(topic, mtype):
    """这个话题是不是「下位机 → 上位机」的回传。

    只在「没有任何节点发布它」的前提下调用，所以不用担心和指令话题撞车。
    """
    t = (mtype or "").strip().lower()
    for p in _FEEDBACK_TYPE_PREFIX:
        if t.startswith(p.lower()):
            return True
    name = (topic or "").strip("/").lower()
    return any(w in name for w in _FEEDBACK_NAME_WORDS)


# 话题名尾部的通用词，去掉后剩下的才是「设备名」：/motor_cmd、/motor_state → motor
_STEM_STRIP = ("cmd", "command", "commands", "target", "goal", "setpoint",
               "state", "status", "data", "msg", "feedback", "info", "raw")


def _device(topic):
    """从话题名猜设备，用来把「下发」和「回传」归到同一台下位机。"""
    parts = [p for p in (topic or "").split("/") if p]
    if not parts:
        return ""
    last = parts[-1].lower()
    for w in _STEM_STRIP:
        if last == w:
            return parts[-2].lower() if len(parts) > 1 else ""
        if last.endswith("_" + w):
            return last[: -len(w) - 1]
    return last


def _levels(nodes, edges):
    """最长路径分层：层数小的边排在前面断言，环上的边自然垫底。"""
    indeg = {n["id"]: 0 for n in nodes}
    nxt = {n["id"]: [] for n in nodes}
    for e in edges:
        a, b = e.get("from"), e.get("to")
        if a in indeg and b in indeg:
            indeg[b] += 1
            nxt[a].append(b)
    level, queue = {}, [k for k in indeg if not indeg[k]]
    for k in queue:
        level[k] = 0
    guard = 0
    while queue and guard < 10000:
        cur = queue.pop(0)
        guard += 1
        for nx in nxt[cur]:
            level[nx] = max(level.get(nx, 0), level.get(cur, 0) + 1)
            indeg[nx] -= 1
            if indeg[nx] == 0:
                queue.append(nx)
    for n in nodes:
        level.setdefault(n["id"], 0)
    return level


def plan(sess):
    g = graph_api.load(sess)
    # 外部节点（launch 里拉起的）不在工作区里，ros2 run 起不来，别塞进链路。
    # 老会话的图是已经缓存过的，里面可能还留着 hop_recorder 这类我们自己写的
    # 辅助脚本（扫描器现在不收了，但缓存不会自动清），这里再挡一道。
    helpers = {"hop_recorder.py", "hop_probe.py", "run_chain_verify.sh"}
    nodes = [n for n in (g.get("nodes") or [])
             if n.get("status") != "draft" and n.get("file")
             and os.path.basename(n.get("file") or "") not in helpers
             and not n.get("external")]
    edges = [e for e in (g.get("edges") or []) if not e.get("draft")]
    pubs, subs, types = _interfaces(nodes)
    level = _levels(nodes, edges)
    ordered = sorted(edges, key=lambda e: (level.get(e.get("from"), 0),
                                           level.get(e.get("to"), 0),
                                           e.get("topic") or ""))
    hops = [{"kind": "chain", "from": e.get("from") or "", "to": e.get("to") or "",
             "topic": e.get("topic") or "",
             "type": e.get("type") or types.get(e.get("topic") or "", "")}
            for e in ordered]
    # 下位机只在「需要」的时候才模拟。判据是话题语义：
    #   /odom 和 /cmd_vel 在拓扑上一模一样（都是没人订阅），但 /odom 是里程计
    #   回推，给它配假下位机等于断言「假下位机收到了里程计」，毫无意义。
    #
    # 下位机不是「一个话题一条尾巴」，而是一台设备：
    #   下行 = 没人订阅、语义是给硬件的指令（/cmd_vel、/motor_cmd）
    #   上行 = 没人发布、但节点在订阅、语义是硬件状态（/motor_state、/battery_state）
    # 两边按话题名里的设备词归并（motor / battery / lidar…），一台设备一棵线，
    # 图上才是「这条线下去、那条线上来」，而不是一条单向的尾巴。
    #
    # 「下行」不能用「没有出边的节点」来认：节点只要还有别的反向通道
    # （planner 还要发 /detector_enable），它就不是拓扑终点，下位机就漏了。
    # 真正的判据是「这个话题没有任何订阅者」。
    cmd = {}
    for t in sorted(pubs):
        if t in subs:
            continue
        if not _is_hw_command(t, types.get(t, "")):
            continue
        src = sorted(pubs[t])[0]
        cmd.setdefault(_device(t), {"topic": t, "type": types.get(t, ""), "from": src})
    back = {}
    for t in sorted(subs):
        if t in pubs:
            continue
        if not _is_hw_feedback(t, types.get(t, "")):
            continue
        for nid in sorted(subs[t]):
            back.setdefault(_device(t), []).append(
                {"topic": t, "type": types.get(t, ""), "to": nid})
    mcus = []
    for dev in sorted(set(cmd) | set(back)):
        c = cmd.get(dev) or {}
        mcus.append({"id": dev, "topic": c.get("topic", ""), "type": c.get("type", ""),
                     "from": c.get("from", ""), "feedback": back.get(dev) or []})
    hops += [{"kind": "mcu", "from": m["from"], "to": "__mcu__", "mcu": m["id"],
              "topic": m["topic"], "type": m["type"]} for m in mcus if m["topic"]]
    hops += [{"kind": "mcu_out", "from": "__mcu__", "to": fb["to"], "mcu": m["id"],
              "topic": fb["topic"], "type": fb["type"]}
             for m in mcus for fb in m["feedback"]]
    # 注入点：首跳话题（链路入口，哪怕项目里有节点自己在发也照注，让入口数据确实流过）
    entries = []
    if hops:
        head = hops[0]
        if head["topic"]:
            entries.append({"topic": head["topic"], "type": head["type"]})
    for t in sorted(subs):
        if t not in pubs and all(e["topic"] != t for e in entries):
            entries.append({"topic": t, "type": types.get(t, "")})
    node_plan = []
    for n in nodes:
        stem = os.path.splitext(os.path.basename(n.get("file") or ""))[0]
        node_plan.append({"id": n["id"], "name": n.get("name") or "",
                          "package": n.get("package") or "",
                          "entry": _snake(n.get("name") or stem),
                          "alt_entry": _snake(stem)})
    return {"nodes": node_plan, "entries": entries, "mcus": mcus, "hops": hops,
            "skipped": [n.get("name") or n.get("id")
                        for n in (g.get("nodes") or [])
                        if n.get("status") == "draft"]}
