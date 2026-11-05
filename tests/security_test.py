# -*- coding: utf-8 -*-
"""三项改造的验证脚本：密钥外置、访问控制（IP + 令牌）、数据目录可挂载。

不联网、不调用模型，全部用本地桩数据跑：
    python -X utf8 tests/security_test.py
"""
import json
import os
import shutil
import sys
import tempfile
import threading
import urllib.error
import urllib.request

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

os.environ["no_proxy"] = "127.0.0.1,localhost"        # 本机代理会拦截回环请求
os.environ["NO_PROXY"] = "127.0.0.1,localhost"

PASS, FAIL = [], []


def ok(name, cond, extra=""):
    (PASS if cond else FAIL).append(name)
    flag = "PASS" if cond else "FAIL"
    line = "  [%s] %s" % (flag, name)
    if not cond and extra != "":
        line += "   -> %r" % (extra,)
    print(line)


def sect(t):
    print("\n" + t)


TMP = tempfile.mkdtemp(prefix="ros2_sec_")
EMPTY_ENV = os.path.join(TMP, "empty.env")
open(EMPTY_ENV, "w").close()

# ---------------------------------------------------------------- 1. 密钥外置
sect("1. API Key 只从环境变量 / .env 来")
from app import env as envmod                      # noqa: E402
from app import config as cfgmod                   # noqa: E402

pairs = envmod.parse('A=1\n# comment\nB = "x y"\nexport C=3\n\n')
ok(".env 解析（注释/引号/export）", pairs == {"A": "1", "B": "x y", "C": "3"}, pairs)

raw_cfg = json.load(open(os.path.join(_ROOT, "config.json"), encoding="utf-8"))
ok("config.json 不再含明文密钥",
   "apiKey" not in raw_cfg or not (raw_cfg.get("apiKey") or "").strip(), raw_cfg.get("apiKey"))

os.environ["ROS2_ENV_FILE"] = EMPTY_ENV
os.environ.pop("DASHSCOPE_API_KEY", None)
os.environ.pop("LLM_API_KEY", None)
c = cfgmod.load_config()
ok("两者都没有时 keySource=none", c["keySource"] == "none" and not cfgmod.api_configured(c), c["keySource"])
ok("未配置时给出可读提示", "DASHSCOPE_API_KEY" in cfgmod.key_hint(c), cfgmod.key_hint(c))

os.environ["DASHSCOPE_API_KEY"] = "sk-test-from-env"
c = cfgmod.load_config()
ok("环境变量优先", c["apiKey"] == "sk-test-from-env" and c["keySource"] == "env:DASHSCOPE_API_KEY", c["keySource"])

dotenv = os.path.join(TMP, ".env")
with open(dotenv, "w", encoding="utf-8") as f:
    f.write("DASHSCOPE_API_KEY=sk-test-from-dotenv\n")
os.environ.pop("DASHSCOPE_API_KEY", None)
os.environ["ROS2_ENV_FILE"] = dotenv
c = cfgmod.load_config()
ok(".env 文件可注入", c["apiKey"] == "sk-test-from-dotenv", c["apiKey"])
ok("已存在的环境变量不被 .env 覆盖",
   (lambda: (os.environ.update({"DASHSCOPE_API_KEY": "sk-keep"}), cfgmod.load_config()["apiKey"] == "sk-keep")[1])())

# ---------------------------------------------------------------- 2. 访问控制
sect("2. 访问控制：IP 白名单 + 令牌")
from app import auth                               # noqa: E402

os.environ["ROS2_ENV_FILE"] = EMPTY_ENV
os.environ.pop("ROS2_AUTH_TOKEN", None)
os.environ.pop("ROS2_ALLOW_IPS", None)
os.environ.pop("ROS2_TRUST_PROXY", None)
ok("两项都没配 = 宽松放行", auth.check({}, {}, "8.8.8.8")[0] is True)
ok("宽松模式描述", "宽松" in auth.describe(), auth.describe())

os.environ["ROS2_AUTH_TOKEN"] = "tok-a,tok-b:u2"
ok("无令牌 -> 401", auth.check({}, {}, "8.8.8.8")[3] == 401)
ok("错误令牌 -> 401", auth.check({"X-Auth-Token": "nope"}, {}, "8.8.8.8")[3] == 401)
ok("Header 令牌通过", auth.check({"X-Auth-Token": "tok-a"}, {}, "8.8.8.8")[:2] == (True, None))
ok("Query 令牌通过", auth.check({}, {"token": "tok-a"}, "8.8.8.8")[0] is True)
ok("令牌能绑定 uid（写法 token:uid）",
   auth.check({"X-Auth-Token": "tok-b"}, {}, "8.8.8.8")[1] == "u2",
   auth.tokens())

os.environ["ROS2_ALLOW_IPS"] = "127.0.0.1,10.0.0.0/8,::1"
ok("单 IP 命中", auth.allowed_ip("127.0.0.1")[0])
ok("CIDR 命中", auth.allowed_ip("10.255.0.9")[0])
ok("IPv4-mapped IPv6 归一", auth.allowed_ip("::ffff:127.0.0.1")[0])
ok("白名单外 -> 403", auth.check({}, {}, "8.8.8.8")[3] == 403)
ok("IP 先于令牌判断（IP 外即使有令牌也拒）",
   auth.check({"X-Auth-Token": "tok-a"}, {}, "8.8.8.8")[3] == 403)
ok("默认不信任 X-Forwarded-For",
   auth.client_ip({"X-Forwarded-For": "1.1.1.1"}, "10.0.0.5") == "10.0.0.5")
os.environ["ROS2_TRUST_PROXY"] = "1"
ok("ROS2_TRUST_PROXY=1 才读代理头",
   auth.client_ip({"X-Forwarded-For": "1.1.1.1, 2.2.2.2"}, "10.0.0.5") == "1.1.1.1")
os.environ.pop("ROS2_TRUST_PROXY", None)

# ---------------------------------------------------------------- 3. 数据目录
sect("3. 数据目录可指向挂载卷（ROS2_DATA_DIR）")
VOL = os.path.join(TMP, "vol")
os.environ["ROS2_DATA_DIR"] = VOL
from app import paths                              # noqa: E402
from app import store                              # noqa: E402
from app import orchestrator                       # noqa: E402
from app import archive                            # noqa: E402

made = paths.ensure_all()
ok("三个子目录自动建在数据卷下",
   all(os.path.isdir(os.path.join(VOL, d)) for d in ("sessions", "data", "archive")), made)

store.record_turn("u_vol", 1)
store.add_concepts("u_vol", ["话题(topic)"])
db = os.path.join(VOL, "data", "profile.db")
ok("画像库落在数据卷", os.path.isfile(db), db)
ok("画像可读回", (store.get("u_vol") or {}).get("turns") == 1, store.get("u_vol"))

sess = orchestrator.get_session("sessv0l01")
sess.user_id = "u_vol"
sess.save()
sfile = os.path.join(VOL, "sessions", "sessv0l01", "state.json")
ok("会话状态落在数据卷", os.path.isfile(sfile), sfile)
ok("工作区根目录在数据卷", sess.ws_root().startswith(VOL), sess.ws_root())
ok("项目根目录未被写入", not os.path.exists(os.path.join(_ROOT, "sessions", "sessv0l01")))

os.makedirs(os.path.join(VOL, "sessions", "sessold01"), exist_ok=True)
old = os.path.join(VOL, "sessions", "sessold01", "state.json")
open(old, "w").write("{}")
os.utime(old, (1000000000, 1000000000))
res = archive.archive_old_sessions(days=30, keep=(), dry_run=False)
ok("归档只处理数据卷里的会话",
   any(n == "sessold01" and r == "archived" for n, r in res)
   and os.path.isfile(os.path.join(VOL, "archive", "sessold01.zip")), res)

# ---------------------------------------------------------------- 4. HTTP 实测
sect("4. 真实 HTTP 请求（127.0.0.1 临时端口）")
os.environ["ROS2_AUTH_TOKEN"] = "tok-a,tok-b:u2"
os.environ["ROS2_ALLOW_IPS"] = "127.0.0.1,::1"

import server                                      # noqa: E402
from http.server import ThreadingHTTPServer        # noqa: E402

srv = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
PORT = srv.server_address[1]
threading.Thread(target=srv.serve_forever, daemon=True).start()

OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
COOKIE = [None]


def _eat(headers):
    sc = headers.get("Set-Cookie") or ""
    if "ros2_id=" in sc:
        COOKIE[0] = sc.split("ros2_id=", 1)[1].split(";")[0].strip()


def get(path, headers=None):
    h = dict(headers or {})
    if COOKIE[0]:
        h["Cookie"] = "ros2_id=" + COOKIE[0]
    req = urllib.request.Request("http://127.0.0.1:%d%s" % (PORT, path), headers=h)
    try:
        with OPENER.open(req, timeout=8) as r:
            _eat(r.headers)
            return r.status, _loads(r.read())
    except urllib.error.HTTPError as e:
        _eat(e.headers)
        return e.code, _loads(e.read())


def _loads(raw):
    try:
        return json.loads(raw.decode("utf-8") or "{}")
    except Exception:
        return {"_raw": True}          # 非 JSON（如 HTML 页面）


st, body = get("/api/identity")
ok("匿名身份可签发", st == 200 and body.get("fresh") is True, (st, body))


st, body = get("/api/health")
ok("health 公开可访问", st == 200 and body.get("ok") is True, (st, body))
ok("health 报告已开令牌", body.get("auth_required") is True, body)
ok("health 报告密钥来源", str(body.get("key_source", "")).startswith("env:"), body.get("key_source"))
ok("严格模式 health 不泄露模型清单", "models" not in body, list(body))

st, body = get("/api/profile?uid=u_other")
ok("无令牌访问画像 -> 401", st == 401 and body.get("need_token") is True, (st, body))

st, body = get("/api/profile?uid=u_other", {"X-Auth-Token": "tok-a"})
ok("通用令牌可访问", st == 200, (st, body))

st, body = get("/api/profile?uid=u_other", {"X-Auth-Token": "tok-b"})
ok("绑定令牌强制本人 uid（读不到别人的）",
   st == 200 and body.get("user_id") == "u2", (st, body.get("user_id")))

st, body = get("/api/session/sessv0l01", {"X-Auth-Token": "tok-a"})
ok("访问他人的会话 -> 403（身份隔离生效）", st == 403, (st, body))

st, body = get("/", {})
ok("页面本身不需要令牌（好让用户输入）", st == 200, st)

os.environ["ROS2_ALLOW_IPS"] = "10.0.0.0/8"
st, body = get("/api/health")
ok("IP 不在白名单 -> 403（连 health 也拦）", st == 403, (st, body))
st, body = get("/api/profile?uid=u_other", {"X-Auth-Token": "tok-a"})
ok("IP 外即使带对令牌也 403", st == 403, (st, body))

srv.shutdown()

# ---------------------------------------------------------------- 汇总
print("\n" + "=" * 56)
print("通过 %d 项，失败 %d 项" % (len(PASS), len(FAIL)))
if FAIL:
    for n in FAIL:
        print("  失败: " + n)
print("=" * 56)

shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
