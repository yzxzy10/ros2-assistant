# -*- coding: utf-8 -*-
"""极简 .env 加载（标准库实现，零第三方依赖）。

用法：在项目根目录放一个 `.env`（不进版本控制）：

    DASHSCOPE_API_KEY=sk-xxxxxxxx
    ROS2_AUTH_TOKEN=my-token

服务启动时自动读进 os.environ；已经存在的环境变量优先，不会被文件覆盖
（所以线上可以直接用平台注入的环境变量，文件只作为本地开发的便利）。
"""
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STATE = [None]        # (路径, mtime)：文件没变就不重复读盘


def env_path():
    """可用 ROS2_ENV_FILE 指定其他路径。"""
    return os.environ.get("ROS2_ENV_FILE") or os.path.join(_ROOT, ".env")


def parse(text):
    """解析 KEY=VALUE 文本，支持 # 注释、空行、引号、export 前缀。"""
    out = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.lower().startswith("export "):
            line = line[7:].lstrip()
        key, val = line.split("=", 1)
        key, val = key.strip(), val.strip()
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1]
        if key:
            out[key] = val
    return out


def load(path=None, override=False):
    """读取 .env 并写入 os.environ，返回本次真正设置了哪些键。

    带 mtime 缓存：文件没改动时直接返回（可以放心在每次请求前调用）。
    """
    p = path or env_path()
    try:
        mtime = os.path.getmtime(p)
    except OSError:
        return []
    if not override and _STATE[0] == (p, mtime):
        return []
    try:
        with open(p, "r", encoding="utf-8-sig") as f:
            pairs = parse(f.read())
    except OSError:
        return []
    _STATE[0] = (p, mtime)
    done = []
    for key, val in pairs.items():
        if override or not os.environ.get(key):
            os.environ[key] = val
            done.append(key)
    return done
