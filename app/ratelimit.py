# -*- coding: utf-8 -*-
"""按身份的滑动窗口限流（进程内，单机足够）。

公开分享时，一个脚本循环请求就能把服务和模型额度拖垮；
这里按「身份 + 接口类别」限速，超限返回 429 + Retry-After。

可调环境变量：
    ROS2_RATE_CHAT_PER_MIN   对话每分钟上限（默认 20）
    ROS2_RATE_API_PER_MIN    其他读接口每分钟上限（默认 120）
    ROS2_DAILY_CHAT_MAX      全站每天对话总次数上限（默认 2000，作"保险丝"）
设成 0 或负数 = 关闭该类限流。
"""
import os
import threading
import time
from collections import deque

_LOCK = threading.Lock()
_HITS = {}
_DAILY = {}
_MAX_KEYS = 8000          # 防内存被海量身份撑爆


def _limit(env_name, default):
    raw = os.environ.get(env_name)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def chat_limit():
    return _limit("ROS2_RATE_CHAT_PER_MIN", 20)


def api_limit():
    return _limit("ROS2_RATE_API_PER_MIN", 120)


def daily_limit():
    return _limit("ROS2_DAILY_CHAT_MAX", 2000)


def hit(key, limit, window=60.0):
    """记一次访问。返回 (是否允许, 建议重试秒数)。limit<=0 表示不限。"""
    if limit <= 0:
        return True, 0
    now = time.time()
    with _LOCK:
        dq = _HITS.get(key)
        if dq is None:
            if len(_HITS) >= _MAX_KEYS:
                _HITS.clear()
            dq = _HITS[key] = deque()
        while dq and now - dq[0] > window:
            dq.popleft()
        if len(dq) >= limit:
            return False, max(1, int(window - (now - dq[0])) + 1)
        dq.append(now)
        return True, 0


def hit_daily(limit=None):
    """全站每日对话总次数（保险丝）。返回 (是否允许, 今日已用次数, 上限)。"""
    limit = daily_limit() if limit is None else limit
    day = time.strftime("%Y-%m-%d")
    if limit <= 0:
        return True, 0, 0
    with _LOCK:
        _prune_daily(day)
        used = _DAILY.get(day, 0)
        if used >= limit:
            return False, used, limit
        _DAILY[day] = used + 1
        return True, used + 1, limit


def _prune_daily(today):
    for k in list(_DAILY.keys()):
        if k != today:
            _DAILY.pop(k, None)


def daily_usage():
    return _DAILY.get(time.strftime("%Y-%m-%d"), 0)


def snapshot():
    """当前窗口内的计数（排障用）。"""
    with _LOCK:
        return {k: len(v) for k, v in _HITS.items()}


def reset():
    with _LOCK:
        _HITS.clear()
        _DAILY.clear()


def describe():
    d = daily_limit()
    tail = "；全站每日上限 %d 次" % d if d > 0 else "；每日总量不限"
    return "对话 %d 次/分钟，其他接口 %d 次/分钟%s" % (chat_limit(), api_limit(), tail)
