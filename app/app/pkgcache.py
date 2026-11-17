"""本地 deb 包池：一次下载，之后完全离线可用。

对应那条原则 —— 常用包留着，不常用的用到才拿，用完放回去：
  - 包池就是磁盘上的 pkgcache/pool/*.deb + 一份 pkgcache/index.json；
  - 验证时把 pool 只读挂进容器 /pkgpool，容器里缺什么就 dpkg -i 什么；
  - 容器是 --rm 的，跑完即销毁，镜像和宿主机都不会被改动 —— 这就是「放回去」。

为什么不用 apt：机房/云主机上的容器没有外网，apt-get 两秒就失败。
装包走本地 deb、不解析软件源，断网也能补依赖。
包池怎么来：在有网的机器上跑
    python tools/fetch_pkgs.py vision_msgs cv_bridge
"""
import json
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POOL = os.path.join(_ROOT, "pkgcache", "pool")
INDEX = os.path.join(_ROOT, "pkgcache", "index.json")
# 验证时发现缺的包记在这儿，管理员一条命令就能全部补齐：
#     python tools/fetch_pkgs.py --pending
PENDING = os.path.join(_ROOT, "pkgcache", "pending.txt")


def pool_dir():
    return POOL


def index():
    try:
        with open(INDEX, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def exists():
    try:
        return any(f.endswith(".deb") for f in os.listdir(POOL))
    except OSError:
        return False


def have(names):
    """names 里哪些包池能提供 → ({apt 名: deb 绝对路径}, [缺的 apt 名])"""
    idx = index()
    got, miss = {}, []
    for name in names:
        item = idx.get(name) or {}
        deb = str(item.get("deb") or "")
        path = os.path.join(POOL, os.path.basename(deb)) if deb else ""
        if path and os.path.isfile(path):
            got[name] = path
        else:
            miss.append(name)
    return got, miss


def summary():
    """给 /api/meta 看的：包池里有多少东西、多大。"""
    idx = index()
    total, count = 0, 0
    try:
        for f in os.listdir(POOL):
            if f.endswith(".deb"):
                count += 1
                total += os.path.getsize(os.path.join(POOL, f))
    except OSError:
        pass
    return {"packages": len(idx), "debs": count,
            "mb": round(total / 1048576.0, 1), "dir": POOL}


def describe(names, distro="jazzy"):
    """结果条上那句话：补装了哪些、还缺哪些。"""
    got, miss = have(names)
    return {"installed": sorted(got), "missing": sorted(miss)}


def note_missing(names):
    """把这次缺的包记进待补清单（幂等，已有的不重复记）。

    用户那头装不了包，管理员那头又不知道用户缺什么 —— 中间得有个交接物。
    包池里已经有的不再记，否则清单会越滚越大、管理员每次都重抓一遍。
    """
    want = [n for n in (names or []) if n]
    if not want:
        return []
    _got, still = have(want)
    if not still:
        return []
    old = pending()
    merged = sorted(set(old) | set(still))
    if merged == old:
        return merged
    try:
        os.makedirs(os.path.dirname(PENDING), exist_ok=True)
        with open(PENDING, "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(merged) + "\n")
    except OSError:
        return old
    return merged


def pending():
    """待补清单里的包名（一行一个）。"""
    try:
        with open(PENDING, encoding="utf-8") as f:
            return [l.strip() for l in f if l.strip()]
    except OSError:
        return []


def clear_pending(done):
    """管理员抓完之后，把已经进池的从待补清单里划掉。"""
    left = [n for n in pending() if n not in set(done or [])]
    try:
        with open(PENDING, "w", encoding="utf-8", newline="\n") as f:
            f.write(("\n".join(left) + "\n") if left else "")
    except OSError:
        pass
    return left
