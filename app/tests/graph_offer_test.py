# -*- coding: utf-8 -*-
"""真实链路验证：单节点生成结束后，final 事件里是否带 graph_ready。

会真的调用大模型（一次约 20-60 秒，消耗少量额度）。
用法：python tests/graph_offer_test.py
"""
import http.cookiejar
import json
import os
import random
import string
import sys
import urllib.request

BASE = os.environ.get("BASE") or "http://127.0.0.1:8125"
opener = urllib.request.build_opener(
    urllib.request.ProxyHandler({}),
    urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()),
)

MSG = ("帮我写一个节点：订阅 /scan 话题，把距离小于 0.3 米的点过滤掉，"
       "剩下的重新发布到 /scan_filtered")

FIELDS = {"reply", "options", "stages", "nodes", "files", "pending"}


def call(path, payload, timeout=30):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with opener.open(req, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def get(path, timeout=30):
    with opener.open(BASE + path, timeout=timeout) as r:
        return r.status, json.loads(r.read().decode("utf-8"))


def main():
    st, ident = get("/api/identity")
    print("identity:", st, ident.get("user_id"))
    sid = "offer" + "".join(random.choice(string.ascii_lowercase) for _ in range(7))

    data = json.dumps({"sessionId": sid, "message": MSG}).encode("utf-8")
    req = urllib.request.Request(BASE + "/api/chat/stream", data=data,
                                 headers={"Content-Type": "application/json",
                                          "Accept": "text/event-stream"})
    print("请求已发，等待模型生成……")
    final = None
    stages = []
    with opener.open(req, timeout=300) as r:
        for raw in r:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                ev = json.loads(line[5:].strip())
            except Exception:
                continue
            t = ev.get("type")
            if t == "progress":
                p = ev.get("value") or ev
                note = p.get("note") or p.get("title") or ""
                if note:
                    stages.append(str(note)[:40])
            elif t == "final":
                final = ev
                break

    if not final:
        print("没收到 final 事件")
        return 1

    print("\n--- 进度轨迹 ---")
    for s in stages[-8:]:
        print("  ·", s)
    print("\n--- final 顶层字段 ---")
    for k in sorted(final.keys()):
        v = final[k]
        if k == "reply":
            print("  %-14s %d 字符" % (k, len(str(v))))
        elif isinstance(v, (list, dict)):
            print("  %-14s %s" % (k, json.dumps(v, ensure_ascii=False)[:110]))
        else:
            print("  %-14s %s" % (k, v))

    gr = final.get("graph_ready")
    print("\ngraph_ready:", json.dumps(gr, ensure_ascii=False) if gr else "（无）")

    # 生成后的工作区应该真有 py 文件，才能画图
    st2, files = get("/api/files/" + sid)
    py = [f for f in files.get("files", []) if str(f).endswith(".py")]
    print("工作区 py 文件:", py[:5] or "无")

    st3, g = get("/api/graph?sid=" + sid)
    print("架构图: nodes=%d edges=%d" % (len(g.get("nodes", [])), len(g.get("edges", []))))
    for n in g.get("nodes", []):
        print("   -", n["name"], "| pub", [p["topic"] for p in n["publishes"]],
              "| sub", [s["topic"] for s in n["subscribes"]])

    ok = bool(gr) and gr.get("nodes", 0) >= 1 and len(g.get("nodes", [])) >= 1
    print("\n结果:", "通过" if ok else "不通过")
    print("会话 sid:", sid)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
