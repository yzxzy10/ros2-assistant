# -*- coding: utf-8 -*-
"""记忆改造自测：滚动摘要 / 画像入库 / 旧会话归档。全部用临时目录与桩模型，不产生真实费用。"""
import os
import shutil
import sys
import tempfile
import time
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import store, archive  # noqa: E402
import app.orchestrator as orch  # noqa: E402


def hr(t):
    print("\n=== %s ===" % t)


def test_summary():
    hr("1. 滚动摘要（32 条历史 → 压掉老的，保留最近 20 条）")
    s = orch.get_session("memtest_s1")
    s.user_id = "memtest_uid"
    store.delete(s.user_id)
    s.history = []
    for i in range(16):
        s.history.append({"role": "user", "content": "第%d轮：话题是啥" % i})
        s.history.append({"role": "assistant", "content": "第%d轮回答……" % i})
    print("压缩前消息数:", len(s.history))

    roles = []

    def fake_chat(cfg, role, system, user, **kw):
        roles.append(role)
        return "学生在学话题/发布者；做避障小车；踩过 rclpy 找不到的坑。"

    org_chat = orch.llm.chat
    orch.llm.chat = fake_chat
    try:
        orch._maybe_summarize({"memory": {"history_max": 30, "summary_keep": 20}}, s)
    finally:
        orch.llm.chat = org_chat

    print("压缩后消息数:", len(s.history), "(期望 20)")
    print("内存摘要:", s.summary[:40])
    print("调用角色:", roles, "(期望 ['summary'])")
    p = store.get(s.user_id)
    print("数据库摘要:", (p.get("summary") or "")[:40])
    ok = (len(s.history) == 20 and "summary" in roles
          and p.get("summary", "").startswith("学生在学"))
    print("→", "PASS" if ok else "FAIL")

    hr("1b. 摘要模型失败时退化为原文拼接，不丢历史")
    s.history = [{"role": "user", "content": "问题%d" % i} for i in range(34)]
    s.summary = ""

    def boom(*a, **k):
        raise RuntimeError("model down")

    orch.llm.chat = boom
    try:
        orch._maybe_summarize({"memory": {"history_max": 30, "summary_keep": 20}}, s)
    finally:
        orch.llm.chat = org_chat
    print("消息数:", len(s.history), "| 摘要长度:", len(s.summary))
    print("→", "PASS" if len(s.history) == 20 and len(s.summary) > 0 else "FAIL")

    hr("1c. 阈值以下不触发摘要")
    s2 = orch.get_session("memtest_s2")
    s2.user_id = "memtest_uid2"
    s2.history = [{"role": "user", "content": "x"}] * 10
    roles.clear()
    orch.llm.chat = fake_chat
    try:
        orch._maybe_summarize({"memory": {"history_max": 30, "summary_keep": 20}}, s2)
    finally:
        orch.llm.chat = org_chat
    print("调用次数:", len(roles), "(期望 0)")
    print("→", "PASS" if not roles else "FAIL")


def test_profile():
    hr("2. 画像采集：概念 / 报错 / 项目 / 渲染")
    s = orch.get_session("memtest_s3")
    s.user_id = "memtest_uid3"
    store.delete(s.user_id)

    orch._remember_message(s, "我想知道发布者节点怎么配合 QoS 和 tf2 用", "concept")
    orch._remember_message(s, "运行报错 ModuleNotFoundError: No module named 'rclpy'", "error")
    orch._update_level(s, "QoS 的可靠性策略区别是什么")   # 进阶信号

    s.plan = {"project": {"name_cn": "避障小车", "package_name": "avoid_car",
                          "difficulty": "入门"}}
    s.package_name = "avoid_car"
    s.node_list = [{"node_name": "a_node", "generated": True},
                   {"node_name": "b_node", "generated": False}]
    orch._remember_project(s, status="代码生成中")

    p = store.get(s.user_id)
    print("level:", p["level"])
    print("concepts:", [c["name"] for c in p["concepts"]])
    print("errors:", [e["type"] for e in p["errors"]])
    print("projects:", [(x["package_name"], x["nodes_done"], x["nodes_total"], x["status"])
                        for x in p["projects"]])
    print("turns:", p["turns"])
    text = store.render(p)
    print("--- 注入提示词的画像 ---")
    print(text)
    ok = (p["level"] == "advanced"
          and any("QoS" in c["name"] for c in p["concepts"])
          and p["errors"] and p["errors"][0]["type"] == "ModuleNotFoundError"
          and p["projects"][0]["nodes_done"] == 1
          and "避障小车" in text)
    print("→", "PASS" if ok else "FAIL")


def test_archive():
    hr("3. 旧会话归档（30 天阈值，zip 校验后删除）")
    tmp = tempfile.mkdtemp(prefix="archtest_")
    old_sess = os.path.join(tmp, "sessions")
    old_arch = os.path.join(tmp, "archive")
    os.makedirs(old_sess, exist_ok=True)

    def make(sid, age_days):
        d = os.path.join(old_sess, sid)
        os.makedirs(os.path.join(d, "ros2_ws", "src", "pkg"), exist_ok=True)
        with open(os.path.join(d, "state.json"), "w", encoding="utf-8") as f:
            f.write('{"sid": "%s"}' % sid)
        with open(os.path.join(d, "ros2_ws", "src", "pkg", "a_node.py"), "w",
                  encoding="utf-8") as f:
            f.write("# old file\n")
        ts = time.time() - age_days * 86400
        for dp, _dd, ff in os.walk(d):
            for fn in ff:
                os.utime(os.path.join(dp, fn), (ts, ts))
        os.utime(d, (ts, ts))

    make("oldsess01", 45)   # 应归档
    make("newsess01", 2)    # 应保留

    org_data = os.environ.get("ROS2_DATA_DIR")
    os.environ["ROS2_DATA_DIR"] = tmp          # 把数据根切到临时目录
    try:
        dry = archive.archive_old_sessions(days=30, dry_run=True)
        print("dry-run:", [(s, r) for s, r in dry])
        kept_before = os.path.isdir(os.path.join(old_sess, "oldsess01"))
        res = archive.archive_old_sessions(days=30)
        print("real:", [(s, r) for s, r in res])
        zip_path = os.path.join(old_arch, "oldsess01.zip")
        has_zip = os.path.isfile(zip_path)
        names = zipfile.ZipFile(zip_path).namelist() if has_zip else []
        gone = not os.path.isdir(os.path.join(old_sess, "oldsess01"))
        keep_new = os.path.isdir(os.path.join(old_sess, "newsess01"))
        print("dry-run 未删:", kept_before, "| zip:", has_zip, "| 原目录已删:", gone,
              "| 新会话保留:", keep_new)
        print("zip 内容:", names)
        ok = kept_before and has_zip and gone and keep_new and any(
            n.endswith("state.json") for n in names)
        print("→", "PASS" if ok else "FAIL")
    finally:
        if org_data is None:
            os.environ.pop("ROS2_DATA_DIR", None)
        else:
            os.environ["ROS2_DATA_DIR"] = org_data
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    test_summary()
    test_profile()
    test_archive()

    # 清理测试产生的会话目录与画像
    for sid in ("memtest_s1", "memtest_s2", "memtest_s3"):
        d = os.path.join(orch.sess_dir(), sid)
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)
    for uid in ("memtest_uid", "memtest_uid2", "memtest_uid3", "testuid001"):
        store.delete(uid)
    print("\n测试数据已清理")
