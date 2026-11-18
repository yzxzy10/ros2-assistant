# -*- coding: utf-8 -*-
"""接线诊断：导入一个 ROS 2 包后，回答「这些节点之间接上了吗、哪里断了」。

为什么要单开一层
----------------
`graph.analyze` 只保证「有连线的画出来」，不负责解释「为什么这里是断的」。
学生导入自己的项目，最常卡的根本不是语法，而是接线：

- 订阅端写 `/scan`，发布端写 `/scan_raw` —— 图上是两个孤立点，代码都「没错」；
- 发布方压根没写（或者没被识别），订阅端起来后永远等不到第一帧；
- 同名话题两边塞了不同消息类型，ROS 2 只按话题名匹配，连接直接建不起来。

这三类静态就能查出来，而且必须说清「谁在等谁、去哪一行改」，否则学生只能
对着架构图干瞪眼。

边界
----
只做静态分析。QoS 不匹配（reliable 发布 / best_effort 订阅）、多播被防火墙挡、
`ROS_DOMAIN_ID` 不一致、DDS 发现失败这些运行时问题，这里查不出来——结论里
必须写明，避免学生误以为「检查通过 = 真机能通」。
"""
import os

from . import graph

_MAX_ISSUES = 12

LEVEL_ORDER = {"error": 0, "warn": 1, "info": 2}


def _where(by_id, ids, field, topic):
    """把「谁订阅/发布了这个话题」落到 文件:行。"""
    key = graph.norm_topic(topic)
    out = []
    for i in ids:
        n = by_id.get(i)
        if not n:
            continue
        line = 0
        for it in n.get(field) or []:
            if graph.norm_topic(it.get("topic") or "") == key:
                line = it.get("line") or 0
                break
        out.append({"node": n.get("name") or "", "file": n.get("file") or "",
                    "line": line})
    return out


def _names(ids, by_id, limit=4):
    names = []
    for i in ids:
        n = by_id.get(i)
        if n and n.get("name") and n["name"] not in names:
            names.append(n["name"])
    if len(names) > limit:
        return "、".join(names[:limit]) + " 等 %d 个" % len(names)
    return "、".join(names) or "—"


def _lev(a, b, cap=3):
    """编辑距离（带上限，超了直接返回 cap+1，省得算完整矩阵）。"""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1,
                           prev[j - 1] + (0 if ca == cb else 1)))
        prev = cur
    return prev[-1]


def _similar(x, y):
    """两个话题名像不像「同一个话题被写成了两个名字」。

    只做保守判断：一个是另一个的子串（`/scan` vs `/scan_raw`、`/odom` vs
    `/odom_filtered`），或编辑距离很小（`/cmd_val` 少写一个 e）。
    宁可漏判——误报会让每个多传感器项目都刷一堆「疑似写错」。
    """
    x, y = graph.norm_topic(x), graph.norm_topic(y)
    if not x or not y or x == y:
        return False
    if len(x) >= 3 and len(y) >= 3 and (x in y or y in x):
        return True
    return _lev(x, y) <= 2 and abs(len(x) - len(y)) <= 2


def diagnose(root, models=0):
    """扫描工作区，返回 {stats, issues, counts, markdown}。

    解析失败不抛异常给调用方：导入本身已经完成，接线检查只是附加信息，
    检查不出来也不该让「导入成功」变成「导入失败」。
    """
    g = graph.analyze(root)
    nodes = g["nodes"]
    by_id = {n["id"]: n for n in nodes}
    issues = []
    orphan_pub = []       # 有发布、没人订阅的话题
    orphan_sub = []       # 有订阅、没人发布的话题

    # ---------- 话题级：断点主要在这里 ----------
    for t in g["topics"]:
        name = t["name"]
        pubs, subs = list(t["pubs"]), list(t["subs"])
        types = [x for x in (t["types"] or []) if x]

        if subs and not pubs:
            orphan_sub.append(name)
            issues.append({
                "code": "no_publisher",
                "level": "error",
                "topic": name,
                "nodes": [by_id[i]["name"] for i in subs if i in by_id],
                "detail": "**%s** 订阅了 `%s`，但整个包里没有任何节点发布它"
                          "—— 这些节点启动后会一直等不到数据，表现为「节点活着但不动/不输出」。"
                          % (_names(subs, by_id), name),
                "fix": "确认发布方是否漏写、或是否在别的包里没一起导入；"
                       "再核对两边话题名是否一致（`/scan` 与 `/scan_raw`、`/base_scan` 这类最常见）",
                "where": _where(by_id, subs, "subscribes", name),
            })
        elif pubs and not subs:
            orphan_pub.append(name)
            issues.append({
                "code": "no_subscriber",
                "level": "info",
                "topic": name,
                "nodes": [by_id[i]["name"] for i in pubs if i in by_id],
                "detail": "**%s** 发布了 `%s`，但包内没有订阅者。"
                          % (_names(pubs, by_id), name),
                "fix": "如果这是发给外部节点、硬件驱动或给 `ros2 topic echo` 看的，属正常；"
                       "如果本该有下游，检查下游是不是订阅了别的话题名",
                "where": _where(by_id, pubs, "publishes", name),
            })

        if len(types) > 1:
            issues.append({
                "code": "type_conflict",
                "level": "error",
                "topic": name,
                "nodes": [by_id[i]["name"] for i in (pubs + subs) if i in by_id],
                "detail": "`%s` 被赋了 %d 种消息类型：%s。"
                          "ROS 2 只按话题名匹配，类型不一致时两端**建立不了连接**。"
                          % (name, len(types), " / ".join(types)),
                "fix": "统一成一个类型；如果是想同时支持 Twist 和 TwistStamped，"
                       "应该用两个不同的话题名",
                "where": _where(by_id, pubs, "publishes", name)
                         + _where(by_id, subs, "subscribes", name),
            })

    # ---------- 疑似同一个话题写成了两个名字 ----------
    # 这是「连不上」里最冤的一类：两边代码都对，就是名字差个后缀，
    # 图上呈现为两个孤立点，光看报错永远想不到。
    for sub in orphan_sub:
        for pub in orphan_pub:
            if not _similar(sub, pub):
                continue
            issues.append({
                "code": "possible_typo",
                "level": "warn",
                "topic": sub,
                "detail": "`%s` 没人发布，`%s` 没人订阅 —— 两个名字高度相似，"
                          "很可能是同一个话题被写成了两种名字。" % (sub, pub),
                "fix": "若确实指同一个话题，把订阅端改成 `%s`（或把发布端改成 `%s`），"
                       "图上的线就会连起来" % (pub, sub),
                "where": [],
            })

    # ---------- 节点级 ----------
    # 话题级已经点过名的节点不再重复报孤立：同一件事说两遍只会把真正的问题淹掉
    mentioned = set()
    for it in issues:
        for nm in it.get("nodes") or []:
            mentioned.add(nm)
        if it.get("node"):
            mentioned.add(it["node"])

    for n in nodes:
        if n.get("external"):
            continue
        ifaces = (n.get("publishes") or []) + (n.get("subscribes") or []) \
            + (n.get("service_servers") or []) + (n.get("service_clients") or []) \
            + (n.get("actions") or [])
        if n.get("orphan") and ifaces and n["name"] not in mentioned:
            issues.append({
                "code": "orphan_node",
                "level": "warn",
                "node": n["name"],
                "detail": "节点 **%s** 声明了接口，但没有任何一条连线"
                          "—— 它发布的话题没人订阅、订阅的话题没人发布（或只有它自己）。"
                          % n["name"],
                "fix": "打开节点详情看它用的话题名，常是拼写/命名空间差了前缀（`/`、`/robot1/`）",
                "where": [{"node": n["name"], "file": n.get("file") or "",
                           "line": n.get("line") or 0}],
            })
        elif n.get("orphan") and not ifaces:
            issues.append({
                "code": "isolated_node",
                "level": "info",
                "node": n["name"],
                "detail": "节点 **%s** 没有声明任何话题/服务/动作。" % n["name"],
                "fix": "如果它只是参数节点或纯计算，属正常；否则可能是接口写法没被识别"
                       "（如把话题名拼成了 f-string）",
                "where": [{"node": n["name"], "file": n.get("file") or "",
                           "line": n.get("line") or 0}],
            })

        if n.get("unresolved_count"):
            issues.append({
                "code": "unresolved",
                "level": "warn",
                "node": n["name"],
                "detail": "节点 **%s** 有 %d 处接口名不是字面量（f-string / 变量 / 参数），"
                          "静态解析不出真实话题名，图上连不起来。"
                          % (n["name"], n["unresolved_count"]),
                "fix": "把话题名改成常量字符串，或在架构图上手工补一条连线",
                "where": [{"node": n["name"], "file": n.get("file") or "",
                           "line": n.get("line") or 0}],
            })

    # ---------- 包级 ----------
    for w in g["warnings"]:
        if "没有 package.xml" in w:
            issues.append({"code": "no_package_xml", "level": "warn",
                           "detail": w,
                           "fix": "补一个 package.xml，否则 `colcon build` 不会把它当包编译",
                           "where": []})

    issues.sort(key=lambda x: (LEVEL_ORDER.get(x["level"], 9), x.get("topic") or x.get("node") or ""))

    counts = {"error": 0, "warn": 0, "info": 0}
    for it in issues:
        counts[it["level"]] = counts.get(it["level"], 0) + 1

    styled = set()
    for n in nodes:
        for f in ("publishes", "subscribes"):
            for it in n.get(f) or []:
                if it.get("type"):
                    styled.add(it["type"])

    stats = {
        "packages": len(g["packages"]),
        "nodes": len([n for n in nodes if not n.get("external")]),
        "external": len([n for n in nodes if n.get("external")]),
        "topics": len(g["topics"]),
        "edges": len(g["edges"]),
        "models": models,
        "typed_topics": len(styled),
    }
    report = {"ok": True, "stats": stats, "issues": issues, "counts": counts}
    report["markdown"] = render(report, g)
    return report


def render(report, g=None):
    s = report["stats"]
    c = report["counts"]
    issues = report["issues"]

    out = ["### 🔌 接线检查"]
    out.append("")

    if not s["nodes"] and not s["external"]:
        out.append("包里没有可解析的节点（`rclpy`/`rclcpp` 一个都没识别到），"
                   "所以没有接线可查。")
        if s["models"]:
            out.append("")
            out.append("识别到 **%d 个模型文件**，模型本身是能预览的；"
                       "要让它动起来，还需要控制节点。" % s["models"])
        return "\n".join(out)

    bits = ["**%d 个包**" % s["packages"], "**%d 个节点**" % s["nodes"]]
    if s["external"]:
        bits.append("%d 个 launch 拉起的外部节点" % s["external"])
    bits.append("**%d 个话题**" % s["topics"])
    if s["models"]:
        bits.append("**%d 个模型文件**" % s["models"])
    out.append("识别到 " + " · ".join(bits) + "，已按话题关系连出 "
               "**%d 条连线**。" % s["edges"])

    errors = [i for i in issues if i["level"] == "error"]
    warns = [i for i in issues if i["level"] == "warn"]
    infos = [i for i in issues if i["level"] == "info"]

    if errors:
        out.append("")
        out.append("#### ❌ 有 %d 处接不上（会直接导致收不到数据/起不来）" % len(errors))
        out.append("")
        for k, it in enumerate(errors[:_MAX_ISSUES], 1):
            out.append("%d. %s" % (k, it["detail"]))
            if it.get("fix"):
                out.append("   - 怎么改：%s" % it["fix"])
            for w in (it.get("where") or [])[:3]:
                loc = w["file"] + (":%d" % w["line"] if w.get("line") else "")
                out.append("   - `%s`" % loc)
    else:
        out.append("")
        if s["edges"] == 0 and s["nodes"] > 0:
            # 「没有断点」在零连线时是错的话术：不是接好了，是一个都没接上
            out.append("**没有发现「接到一半」的断点**，但当前有 **0 条连线**——"
                       "每个节点都是各自独立跑的。如果它们本该互相通信，看下面的提示。")
        else:
            out.append("**没有发现断点**：每个被订阅的话题都有发布者，类型也一致。")

    if warns:
        out.append("")
        out.append("#### ⚠️ %d 处需要留意" % len(warns))
        out.append("")
        for it in warns[:_MAX_ISSUES]:
            out.append("- %s" % it["detail"])
            if it.get("fix"):
                out.append("  - 怎么改：%s" % it["fix"])

    if infos:
        out.append("")
        out.append("#### 💡 %d 条提示（多半可忽略）" % len(infos))
        out.append("")
        for it in infos[:_MAX_ISSUES]:
            out.append("- %s" % it["detail"])

    if s["edges"] or s["nodes"]:
        out.append("")
        out.append("> 这是**静态检查**：只看代码里写了什么。运行时的 QoS 不匹配"
                   "（reliable 发布 / best_effort 订阅）、多播被防火墙挡、"
                   "`ROS_DOMAIN_ID` 不一致，这些查不出来——真机不通时要另查这几项。")

    return "\n".join(out)
