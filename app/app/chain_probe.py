# -*- coding: utf-8 -*-
"""逐跳取证：在起节点之前先挂一个「记录员」，而不是之后逐个话题去等。

为什么不再用「一个话题一个探针、每个等 12 秒」：

* **一次性的消息抓不到**。demo_nodes_cpp 的 use_logger_service 只在启动时发 4 条，
  等我们按拓扑序轮到 `output` 这个话题时，消息早就发完了 —— 报「没收到消息」，
  其实是我们到得太晚。
* **QoS 不配对就一条都收不到**。cbg_executor 用的是 `SensorDataQoS()`（best effort），
  探针用默认 reliable 去订，DDS 直接不匹配，现象和「节点没发消息」一模一样。
* **大消息会被打印卡死**。兜底分支 `ros2 topic echo --once` 会把 8MB 的 Image
  打成 YAML，超时被掐，同样表现为「没消息」。
* **慢**。72 跳逐个等，最坏 72×12 秒。

所以改成：节点起来之前就把所有待取证话题订上（类型来自源码解析，缺的等它出现在
图上再补订），用一个统一的时间窗收集「哪些话题真的来过消息」，最后一次性对账。
记录员只写「谁来过」，不打印内容，8MB 的图也不怕。

窗口里还有一层保险：某个话题到一半还没消息，就再用「reliable」「transient_local」
两种 QoS 各补订一次，看是不是匹配策略的问题 —— 是的话记录里会写明是哪种救回来的。
"""

RECORDER_PY = '''# -*- coding: utf-8 -*-
# 自动生成：链路取证记录员（起节点之前启动，收尾时 SIGTERM 停）
import signal
import sys
import time

import rclpy
from rclpy.qos import (DurabilityPolicy, HistoryPolicy, QoSProfile,
                       ReliabilityPolicy)

STOP = [False]


def _term(*_a):
    STOP[0] = True


def _norm(t):
    t = (t or "").strip()
    if not t:
        return ""
    return t if t.startswith("/") else "/" + t


def _load(typ):
    parts = typ.split("/")
    mod = __import__(".".join(parts[:-1]), fromlist=[parts[-1]])
    return getattr(mod, parts[-1])


def main():
    if len(sys.argv) < 3:
        print("usage: hop_recorder.py <out.tsv> <topics.tsv> [window_sec]")
        return 2
    out, spec = sys.argv[1], sys.argv[2]
    win = float(sys.argv[3]) if len(sys.argv) > 3 else 12.0
    want = {}
    try:
        with open(spec, "r", encoding="utf-8") as f:
            for ln in f:
                ln = ln.rstrip("\\n")
                if not ln:
                    continue
                parts = ln.split("\\t")
                if parts and parts[0] and parts[0] not in want:
                    want[parts[0]] = parts[1] if len(parts) > 1 else ""
    except OSError as e:
        print("读不到 %s: %r" % (spec, e))
        return 3
    signal.signal(signal.SIGTERM, _term)
    signal.signal(signal.SIGINT, _term)
    rclpy.init()
    node = rclpy.create_node("hop_recorder")
    fh = open(out, "a", encoding="utf-8", buffering=1)
    got = set()
    subs = {}            # topic -> subscription（或 None，表示占位）
    types = {}           # topic -> 真正订上的类型
    bad = set()          # 解析层给的裸类型名（Int32 这种），只用来少写重复日志
    alts = set()         # (topic, 方案) 已经补订过的组合

    def qos(rel=ReliabilityPolicy.BEST_EFFORT,
            dur=DurabilityPolicy.VOLATILE):
        # 主订用 best effort + volatile：
        # 这个组合能收 reliable 的发布方，也能收 best effort 的（反过来不行）。
        # depth 10 是为了不被 8MB 的 image 示例挤掉。
        return QoSProfile(depth=10, history=HistoryPolicy.KEEP_LAST,
                          reliability=rel, durability=dur)

    def mark(topic, via=""):
        if topic in got:
            return
        got.add(topic)
        fh.write("got\\t%s\\t%.2f\\t%s\\n" % (topic, time.time(), via))

    def add(topic, typ):
        if topic in subs:
            return
        if len((typ or "").split("/")) < 2:
            # 解析层有时只给裸类型名（源码里 `using Int32 = std_msgs::msg::Int32;`
            # 之后 create_publisher<Int32>）。这种订不上，但**不能记成已处理**——
            # 否则等话题真上线、拿到真类型时也不会再试，最后报「未取证」。
            if typ and topic not in bad:
                bad.add(topic)
                fh.write("err\\t%s\\tbad type %s\\n" % (topic, typ))
            return
        subs[topic] = None                      # 占位，避免反复重试
        try:
            cls = _load(typ)
        except Exception as e:                   # noqa: BLE001
            fh.write("err\\t%s\\t%s\\n" % (topic, repr(e)[:160]))
            return
        # 一开始就订两份：best effort + reliable。
        # 线上实测，有些示例（message_lost / qos_overrides / topic_statistics）
        # 的发布方是 reliable，best effort 的订阅方却一条都收不到 —— 理论上
        # 同 RMW 下应该能匹配，实测就是不匹配。与其研究 DDS 行为，不如两份都订：
        # 哪份先收到就记哪份，收不到的也不占额外开销（每条话题多一个 reader）。
        try:
            node.create_subscription(
                cls, topic, lambda m, t=topic: mark(t, "be"), qos())
            node.create_subscription(
                cls, topic, lambda m, t=topic: mark(t, "reliable"),
                qos(ReliabilityPolicy.RELIABLE))
        except Exception as e:                   # noqa: BLE001
            fh.write("err\\t%s\\t%s\\n" % (topic, repr(e)[:160]))
            return
        subs[topic] = True
        types[topic] = typ
        fh.write("sub\\t%s\\t%s\\n" % (topic, typ))

    def rescue(topic, rel, dur, tag):
        """同话题换个 QoS 再订一次：只在窗口内还没收到消息时用。

        DDS 的匹配规则理论上 best effort + volatile 已经最宽松，但线上确实遇到过
        「发布者在、类型对、就是收不到」的示例（参数文件覆盖过 QoS）。与其猜，
        不如在窗口里补订一次，让结果自己说话。
        """
        key = (topic, tag)
        if key in alts or topic not in types:
            return
        alts.add(key)
        try:
            node.create_subscription(
                _load(types[topic]), topic, lambda m, t=topic, g=tag: mark(t, g),
                qos(rel, dur))
            fh.write("alt\\t%s\\t%s\\n" % (topic, tag))
        except Exception as e:                   # noqa: BLE001
            fh.write("err\\t%s\\talt %s %r\\n" % (topic, tag, repr(e)[:120]))

    # 先按源码里解析出的类型订上：节点还没起，启动瞬间发一次的消息也能抓住。
    for t, ty in list(want.items()):
        if ty:
            add(t, ty)

    def snapshot():
        """把「还没收到消息的话题，此刻有几个发布者」落盘。

        没有这个数字，失败原因只能写「没收到消息」；有了它就能区分
        「压根没人发」（pubs=0）和「有人发但我们没收到」（QoS/时序）。
        """
        for t in list(subs):
            if t in got or subs.get(t) is None:
                continue
            try:
                n = node.count_publishers(_norm(t))
            except Exception:                    # noqa: BLE001
                n = -1
            fh.write("pub\\t%s\\t%d\\n" % (t, n))

    t0 = time.time()
    last_pub = t0
    stage = 0
    while not STOP[0]:
        left = [t for t, ty in want.items() if t not in subs]
        if left:
            live = {}
            try:
                for name, tps in node.get_topic_names_and_types():
                    if tps:
                        live[_norm(name)] = tps[0]
            except Exception:                    # noqa: BLE001
                live = {}
            for t in left:
                ty = live.get(_norm(t))
                if ty:
                    add(t, ty)
        # 窗口过半还没消息的，换两种 QoS 各补订一次
        el = time.time() - t0
        if stage == 0 and el > win * 0.25:
            stage = 1
        elif stage == 1 and el > win * 0.55:
            stage = 2
            for t in list(subs):
                if t not in got:
                    rescue(t, ReliabilityPolicy.RELIABLE,
                           DurabilityPolicy.TRANSIENT_LOCAL, "transient")
        rclpy.spin_once(node, timeout_sec=0.2)
        if time.time() - last_pub > 3.0:
            last_pub = time.time()
            snapshot()
    # 收尾再拍一次：这时节点都还在，数字最准
    for t in list(subs):
        try:
            n = node.count_publishers(_norm(t))
        except Exception:                        # noqa: BLE001
            n = 0
        fh.write("pub\\t%s\\t%d\\n" % (t, n))
    fh.write("sum\\t%d\\t%d\\n" % (len(subs), len(got)))
    fh.write("end\\t-\\t%.2f\\n" % time.time())
    fh.flush()
    fh.close()
    try:
        node.destroy_node()
        rclpy.shutdown()
    except Exception:                            # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''
