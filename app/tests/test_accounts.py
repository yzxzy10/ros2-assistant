# -*- coding: utf-8 -*-
"""账号体系回归测试：注册/登录规则、uid 稳定性、数据迁移。

跑法：python tests/test_accounts.py
"""
import os
import sys
import tempfile

# 必须在导入 app 之前指到临时目录，否则会写进真实数据目录
os.environ["ROS2_DATA_DIR"] = tempfile.mkdtemp(prefix="acct_test_")
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import accounts, store          # noqa: E402
import app.orchestrator as O            # noqa: E402

_PASS = [0]
_FAIL = [0]


def check(cond, msg):
    if cond:
        _PASS[0] += 1
        print("  ok   %s" % msg)
    else:
        _FAIL[0] += 1
        print("  FAIL %s" % msg)


def case_rules():
    print("\n[1] 注册规则")
    ok, msg, uid, name = accounts.register("a", "123456")
    check(not ok and "用户名" in msg, "用户名太短被拒：%s" % msg)

    ok, msg, uid, name = accounts.register("bad name!", "123456")
    check(not ok, "用户名含空格/符号被拒：%s" % msg)

    ok, msg, uid, name = accounts.register("zhangsan", "123")
    check(not ok and "密码" in msg, "密码太短被拒：%s" % msg)

    ok, msg, uid, name = accounts.register("zhangsan", "ros2pass")
    check(ok and uid.startswith("acct_"), "注册成功，uid=%s" % uid)
    check(name == "zhangsan", "返回用户名")

    ok, msg, uid2, _n = accounts.login("Zhangsan", "ros2pass")
    check(ok and uid2 == uid, "登录大小写不敏感，落到同一账号")
    check(accounts.uid_of("ZHANGSAN") == accounts.uid_of("zhangsan"),
          "uid 与用户名大小写无关")


def case_login():
    print("\n[2] 登录失败")
    ok, msg, uid, name = accounts.login("zhangsan", "wrong-pass")
    check(not ok and "不对" in msg, "密码错误被拒：%s" % msg)

    ok, msg, uid, name = accounts.login("nobody", "whatever")
    check(not ok, "不存在的用户被拒：%s" % msg)

    ok, msg, uid, name = accounts.register("zhangsan", "another1")
    check(not ok and "已被注册" in msg, "重复注册被拒：%s" % msg)

    # 失败太多要限速（用独立用户名，避免影响上面的用例）
    for i in range(accounts.FAIL_MAX):
        accounts.login("ratelimit_user", "bad%d" % i)
    ok, msg, uid, name = accounts.login("ratelimit_user", "bad-final")
    check(not ok and "次数过多" in msg, "连续失败后限速：%s" % msg)


def case_migrate():
    print("\n[3] 匿名数据迁移到账号")
    ok, msg, uid, name = accounts.register("mover", "ros2pass")
    check(ok, "建账号 %s" % uid)
    anon = "u_anon_probe"

    for sid in ("session_aaa1", "session_bbbb2"):
        s = O.Session(sid)
        s.user_id = anon
        s.transcript = [{"role": "user", "content": "你好"}]
        s.save()
    store.add_concepts(anon, ["话题 topic"])
    store.record_turn(anon, 3)
    store.set_level(anon, "middle")

    before = len(O.list_user_sessions(anon))
    check(before == 2, "匿名身份下有 %d 条会话" % before)

    moved = O.reassign(anon, uid)
    check(moved == 2, "迁移会话 %d 条" % moved)
    check(len(O.list_user_sessions(uid)) == 2, "账号能看到这 2 条会话")
    check(len(O.list_user_sessions(anon)) == 0, "匿名身份已无会话")
    p = store.get(uid)
    check(p["turns"] == 3 and [c["name"] for c in p["concepts"]] == ["话题 topic"],
          "画像已跟着迁移：轮次 %s 概念 %s" % (p["turns"], [c["name"] for c in p["concepts"]]))
    check(store.get(anon)["turns"] == 0, "匿名画像已清空")


def case_merge():
    print("\n[4] 目标账号已有画像时合并")
    ok, msg, uid, name = accounts.register("merger", "ros2pass")
    store.add_concepts(uid, ["服务 service"])
    store.record_turn(uid, 2)

    anon = "u_anon_merge"
    store.add_concepts(anon, ["话题 topic"])
    store.record_turn(anon, 5)
    store.set_summary(anon, "这是匿名期间的摘要")

    O.reassign(anon, uid)
    p = store.get(uid)
    names = sorted(c["name"] for c in p["concepts"])
    check(p["turns"] == 7, "轮次相加：2 + 5 = %s" % p["turns"])
    check(names == sorted(["话题 topic", "服务 service"]), "概念取并集：%s" % names)
    check(p["summary"] == "这是匿名期间的摘要", "摘要取较长的一份")


def main():
    case_rules()
    case_login()
    case_migrate()
    case_merge()
    print("\n" + "=" * 50)
    print("通过 %d 项，失败 %d 项" % (_PASS[0], _FAIL[0]))
    return 1 if _FAIL[0] else 0


if __name__ == "__main__":
    sys.exit(main())
