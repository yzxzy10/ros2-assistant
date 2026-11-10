# -*- coding: utf-8 -*-
"""接口访问控制：IP 白名单 + 访问令牌（两层可单独用，也可叠加）。

一、IP 白名单（ROS2_ALLOW_IPS，逗号分隔，支持 CIDR）
    ROS2_ALLOW_IPS=127.0.0.1,::1,192.168.0.0/16
  适合：内网、宿舍/实验室、固定公网 IP 的个人服务器。
  注意：公网 IP 会变（4G/5G、动态宽带）、NAT 后整个校园共用一个出口，
  所以它挡得住扫描器，但分不清"谁是谁"。

二、访问令牌（ROS2_AUTH_TOKEN）
    不配 → 不校验令牌；配了 → 除 /api/health 外所有 /api/* 都要带令牌。
    单人自用：ROS2_AUTH_TOKEN=s3cret
    多人共用：ROS2_AUTH_TOKEN=s3cret:u_alice,k9x:u_bob   （令牌绑 uid，只能看自己）

两者都配 = 先过 IP，再过令牌（推荐公网这样配）。
反向代理后面要读真实 IP 时，显式设置 ROS2_TRUST_PROXY=1（否则一律用 TCP 对端地址，
避免 X-Forwarded-For 被伪造）。

客户端如何带令牌：
    Header:  X-Auth-Token: s3cret          （前端默认用这个）
    Query:   ?token=s3cret                 （方便一次性的下载/分享链接）
"""
import hmac
import ipaddress
import os

TOKEN_ENV = "ROS2_AUTH_TOKEN"
ALLOW_ENV = "ROS2_ALLOW_IPS"
TRUST_PROXY_ENV = "ROS2_TRUST_PROXY"
HEADER = "X-Auth-Token"

_CACHE = [None, None]        # [原始令牌串, {token: uid 或 None}]
_IP_CACHE = [None, None]     # [原始 IP 串, [ip_network]]
_TRUE = ("1", "true", "yes", "on")


def _parse(raw):
    """'a:x,b:y' / 'a;b' / 'a' → {a: 'x', b: 'y'}"""
    table = {}
    for item in raw.replace("；", ",").replace(";", ",").split(","):
        item = item.strip()
        if not item:
            continue
        if ":" in item:
            tok, uid = item.split(":", 1)
            tok, uid = tok.strip(), uid.strip()
        else:
            tok, uid = item, ""
        if tok:
            table[tok] = uid or None
    return table


def tokens():
    raw = os.environ.get(TOKEN_ENV) or ""
    if _CACHE[0] != raw:
        _CACHE[0] = raw
        _CACHE[1] = _parse(raw)
    return _CACHE[1]


def enabled():
    return bool(tokens())


def extract(headers=None, query=None):
    tok = ""
    if headers is not None:
        try:
            tok = headers.get(HEADER) or ""
        except Exception:      # noqa: BLE001
            tok = ""
    if not tok and query:
        tok = query.get("token") or ""
    return str(tok).strip()


def verify(token):
    """返回 (是否有效, 绑定的 uid 或 None)。逐个恒定时间比较，避免时序侧信道。"""
    token = (token or "").strip()
    if not token:
        return False, None
    ok, bound = False, None
    for tok, uid in tokens().items():   # 不提前 break，比较次数与令牌数无关
        if hmac.compare_digest(tok, token):
            ok, bound = True, uid
    return ok, bound


def check(headers=None, query=None, ip=""):
    """返回 (ok, 绑定 uid, 原因, 建议状态码)。未做任何配置时恒为通过。"""
    ip_ok, ip_why = allowed_ip(ip)
    if not ip_ok:
        return False, None, ip_why, 403
    if not enabled():
        return True, None, "", 0
    ok, uid = verify(extract(headers, query))
    if not ok:
        return False, None, "缺少或错误的访问令牌", 401
    return True, uid, "", 0


# ---------------- IP 白名单 ----------------

def allow_networks():
    """解析 ROS2_ALLOW_IPS；返回 ip_network 列表（空列表 = 不限制）。"""
    raw = (os.environ.get(ALLOW_ENV) or "").strip()
    if _IP_CACHE[0] != raw:
        nets = []
        for item in raw.replace("；", ",").replace(";", ",").split(","):
            item = item.strip()
            if not item:
                continue
            try:
                nets.append(ipaddress.ip_network(item, strict=False))
            except ValueError:
                continue        # 写错的条目跳过，不影响其他条目
        _IP_CACHE[0] = raw
        _IP_CACHE[1] = nets
    return _IP_CACHE[1]


def ip_restricted():
    return bool(allow_networks())


def _norm_ip(ip):
    ip = (ip or "").strip()
    if ip.startswith("::ffff:"):        # IPv4-mapped IPv6
        ip = ip[7:]
    return ip


def allowed_ip(ip):
    """(是否允许, 原因)。没配白名单时一律允许。"""
    nets = allow_networks()
    if not nets:
        return True, ""
    ip = _norm_ip(ip)
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False, "无法识别的客户端地址"
    for net in nets:
        if addr in net:
            return True, ""
    return False, "当前 IP %s 不在允许列表内" % ip


def client_ip(headers=None, peer=""):
    """取客户端 IP。只有显式设置 ROS2_TRUST_PROXY 才相信反向代理头。"""
    trust = (os.environ.get(TRUST_PROXY_ENV) or "").strip().lower() in _TRUE
    if trust and headers is not None:
        try:
            xff = headers.get("X-Forwarded-For") or headers.get("X-Real-IP") or ""
        except Exception:      # noqa: BLE001
            xff = ""
        if xff:
            return xff.split(",")[0].strip()
    return peer or ""


def describe():
    bits = []
    if ip_restricted():
        bits.append("IP 白名单 %d 条" % len(allow_networks()))
    else:
        bits.append("IP 白名单未开启")
    if enabled():
        table = tokens()
        bound = sum(1 for v in table.values() if v)
        bits.append("令牌 %d 个（绑定 uid %d 个）" % (len(table), bound))
    else:
        bits.append("令牌未开启")
    if not ip_restricted() and not enabled():
        return "关闭（宽松模式，仅适合本机/内网）"
    return "已开启：" + "，".join(bits)
