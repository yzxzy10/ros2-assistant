# -*- coding: utf-8 -*-
"""验证结果持久化：跑完 → 落盘 → 「换个进程」再查还能拿到上次的绿线与结论。

    python -X utf8 tests/verify_persist_test.py

用 mock 后端跑（不需要 Docker），三步：
1. 导入 demo 项目 → 开验证 → 等跑完 → 断言 data/verify/<sid>.json 存在；
2. 清掉内存里的状态（模拟进程重启/切会话），再查 status：必须能读回上次结果，
   且带 restored=True、hops 齐全；
3. 断言「图改了旧结果作废」：plan_sig 变了一个字节就必须读不回来。
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile
from http.server import ThreadingHTTPServer

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

os.environ["no_proxy"] = "127.0.0.1,localhost"
os.environ["NO_PROXY"] = "127.0.0.1,localhost"
os.environ["ROS2_CHAIN_MOCK"] = "1"          # 离线跑，别去找 Docker
_DATA = tempfile.mkdtemp(prefix="ros2vpersist_")
os.environ["ROS2_DATA_DIR"] = _DATA

from app import (chain_exec, chain_plan, chain_verify, env,          # noqa: E402
                 orchestrator, paths)
import server                                                        # noqa: E402

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
COOKIE = [None]
FAILED = []


def check(name, ok, extra=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + (("  " + extra) if extra else ""))
    if not ok:
        FAILED.append(name)


def req(port, path, body=None, raw=None, headers=None):
    h = dict(headers or {})
    if COOKIE[0]:
        h["Cookie"] = "ros2_id=" + COOKIE[0]
    data = raw if raw is not None else (
        json.dumps(body).encode("utf-8") if body is not None else None)
    r = urllib.request.Request("http://127.0.0.1:%d%s" % (port, path),
                               data=data, headers=h)
    try:
        with OPENER.open(r, timeout=60) as resp:
            sc = resp.headers.get("Set-Cookie") or ""
            if "ros2_id=" in sc:
                COOKIE[0] = sc.split("ros2_id=", 1)[1].split(";")[0].strip()
            out = resp.read()
            try:
                return resp.status, json.loads(out.decode("utf-8") or "{}")
            except Exception:                                       # noqa: BLE001
                return resp.status, out
    except urllib.error.HTTPError as e:
        out = e.read()
        try:
            return e.code, json.loads(out.decode("utf-8") or "{}")
        except Exception:                                           # noqa: BLE001
            return e.code, {"_raw": out[:120]}


def demo_zip():
    src = os.path.join(_ROOT, "samples", "demo_ros2_ws.zip")
    return src


def main():
    env.load()
    paths.ensure_all()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print("服务端口 %d，数据目录 %s" % (port, _DATA))

    st, me = req(port, "/api/identity")
    check("身份下发 /api/identity", st == 200 and bool(COOKIE[0]))
    sid = "sv" + str(int(time.time() * 1000))[-8:]

    with open(demo_zip(), "rb") as f:
        blob = f.read()
    st, r = req(port, "/api/project/import-zip?sessionId=%s&name=demo.zip" % sid, raw=blob,
                headers={"Content-Type": "application/octet-stream"})
    check("导入 demo 项目", st == 200 and not r.get("error"), str(r)[:160])

    st, g = req(port, "/api/graph?sid=" + sid)
    check("出图有节点", st == 200 and len(g.get("nodes") or []) > 0,
          "nodes=%d edges=%d" % (len(g.get("nodes") or []), len(g.get("edges") or [])))

    st, st0 = req(port, "/api/graph/verify/status?sid=" + sid)
    check("跑之前没有历史结果", st == 200 and not st0.get("finished"), str(st0.get("reason")))

    st, r = req(port, "/api/graph/verify", body={"sessionId": sid})
    check("启动验证", st == 200 and not r.get("error"), str(r)[:120])

    fin = None
    for _ in range(60):
        time.sleep(0.8)
        _, s = req(port, "/api/graph/verify/status?sid=" + sid)
        if s.get("finished"):
            fin = s
            break
    check("mock 跑完", bool(fin), (fin or {}).get("reason", ""))
    hops = (fin or {}).get("hops") or []
    check("逐跳结果齐全", bool(hops), "hops=%d ok=%s" % (len(hops), (fin or {}).get("ok")))

    snap = os.path.join(_DATA, "data", "verify", sid + ".json")
    check("结果已落盘", os.path.isfile(snap), snap)

    # ---- 模拟进程重启：内存状态清空，只剩磁盘 ----
    with chain_exec._LOCK:
        chain_exec._RUN.pop(sid, None)
    sess = orchestrator.get_session(sid)
    back = chain_verify.snapshot(sid, sess)
    check("重启后能读回上次结果", bool(back.get("finished")) and bool(back.get("restored")))
    check("读回的逐跳数一致", len(back.get("hops") or []) == len(hops),
          "%d vs %d" % (len(back.get("hops") or []), len(hops)))
    check("带运行时间戳", bool(back.get("saved_at")), str(back.get("saved_at")))
    check("结论口径不变", back.get("ok") == (fin or {}).get("ok") and
          back.get("backend") == (fin or {}).get("backend"),
          "ok=%s backend=%s" % (back.get("ok"), back.get("backend")))

    # 不带 sess 也应读得到（cancel/busy 走的是这条路径）
    check("无会话上下文也能读回", bool(chain_verify.snapshot(sid).get("restored")))

    # ---- 图改了，旧结果必须作废 ----
    p = chain_plan.plan(sess)
    sig_now = chain_exec.plan_sig(p)
    check("指纹稳定", sig_now == back.get("plan_sig"), "%s vs %s" % (sig_now, back.get("plan_sig")))
    fake = json.loads(json.dumps(p))
    if fake.get("hops"):
        fake["hops"][0]["topic"] = (fake["hops"][0].get("topic") or "") + "_changed"
    check("改一条线指纹就变", chain_exec.plan_sig(fake) != sig_now)

    # 用一个假的 sess（图变了）去查 → 必须读不到，并且文件被清掉
    class _S(object):
        pass
    _fake_sess = _S()
    _fake_sess.ws_root = lambda: sess.ws_root()
    orig_plan = chain_plan.plan
    try:
        chain_plan.plan = lambda s: fake
        stale = chain_exec.status(sid, _fake_sess)
    finally:
        chain_plan.plan = orig_plan
    check("图改了旧结果读不回来", stale is None)
    check("过期文件已清理", not os.path.isfile(snap))

    srv.shutdown()
    print("\n%s" % ("全部通过" if not FAILED else "失败 %d 项：%s" % (len(FAILED), FAILED)))
    return 0 if not FAILED else 1


if __name__ == "__main__":
    try:
        code = main()
    finally:
        shutil.rmtree(_DATA, ignore_errors=True)
    sys.exit(code)
