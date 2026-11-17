#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把缺的 deb 抓进本地包池 pkgcache/pool —— 管理员用的那一半。

用户那头的容器没有外网，`apt install` 必然失败，所以「缺一个包」在用户那头
是死路；唯一的出路是管理员在有网机器上把它抓好放进包池，用户重跑验证时
容器挂载 /pkgpool 自动补装，全程不需要联网。

用法::

    # 抓包（最常用：把结论条上点名的包原样贴进来）
    python tools/fetch_pkgs.py ros-jazzy-nav2-bringup python3-opencv

    # 只问「源里有没有」，不下载（判断一个包我们到底支不支持）
    python tools/fetch_pkgs.py --check ros-jazzy-moveit-core

    # 看包池现状
    python tools/fetch_pkgs.py --list

下载在容器里做（默认 ros:<distro>-ros-base，可用 config.json 的
ros.verify.image 覆盖），只把 deb 落到 pkgcache/pool，宿主机不会被改动；
依赖交给 apt 自己解析，所以抓下来的正好是「镜像之外还差的那几个」。
"""
import json
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POOL = os.path.join(ROOT, "pkgcache", "pool")
INDEX = os.path.join(ROOT, "pkgcache", "index.json")
PENDING = os.path.join(ROOT, "pkgcache", "pending.txt")
CONFIG = os.path.join(ROOT, "config.json")

# 这些前缀已经是 apt 包名了，不再加 ros-<distro>-
_APT_LIKE = ("ros-", "python3-", "lib", "qt", "gazebo", "boost", "pcl-", "sdl")


def _cfg():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, ValueError):
        return {}


def _target():
    ros = (_cfg().get("ros") or {})
    distro = ros.get("distro") or "jazzy"
    image = ((ros.get("verify") or {}).get("image") or "").strip()
    return distro, image or "ros:%s-ros-base" % distro


def apt_name(name, distro):
    """nav2_bringup → ros-jazzy-nav2-bringup；python3-opencv 原样保留。"""
    n = str(name or "").strip()
    if "/" in n:                     # 允许直接贴 "ros-jazzy-foo/jammy"
        n = n.split("/")[0]
    if not n or n.startswith(_APT_LIKE) or "-" in n:
        return n
    if "_" in n:                     # ROS 包习惯下划线，apt 里是连字符
        return "ros-%s-%s" % (distro, n.replace("_", "-"))
    return n                         # 单个词（opencv）不猜，交给 apt 报错


def _docker():
    return shutil.which("docker")


def _run(script, image, host=False):
    """在容器（或本机）里跑一段 bash，返回 (rc, 输出)。"""
    if host:
        p = subprocess.run(["bash", "-lc", script], capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           timeout=1800)
    else:
        os.makedirs(POOL, exist_ok=True)
        cmd = ["docker", "run", "--rm",
               "-v", "%s:/out" % POOL.replace("\\", "/"),
               image, "bash", "-lc", script]
        p = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=1800)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _pending():
    try:
        with open(PENDING, encoding="utf-8") as f:
            return [l.strip() for l in f if l.strip()]
    except OSError:
        return []


def _clear_pending(done):
    left = [n for n in _pending() if n not in set(done or [])]
    try:
        with open(PENDING, "w", encoding="utf-8", newline="\n") as f:
            f.write(("\n".join(left) + "\n") if left else "")
    except OSError:
        pass
    return left


def _index():
    try:
        with open(INDEX, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_index(idx):
    os.makedirs(os.path.dirname(INDEX), exist_ok=True)
    with open(INDEX, "w", encoding="utf-8", newline="\n") as f:
        json.dump(idx, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")


def check(names, distro, image, host):
    """只问一句：这些包在源里有没有、镜像里是不是已经自带了。"""
    q = " ".join(names)
    script = "\n".join([
        "for p in %s; do" % q,
        '  if dpkg -s "$p" >/dev/null 2>&1; then echo "__HAVE__|$p"; continue; fi',
        '  c=$(apt-cache policy "$p" 2>/dev/null | sed -n "s/^ *Candidate: *//p" | head -n1)',
        '  if [ -z "$c" ] || [ "$c" = "(none)" ]; then echo "__MISS__|$p";'
        ' else echo "__AVAIL__|$p|$c"; fi',
        "done",
    ])
    rc, out = _run(script, image, host)
    have, avail, miss = [], [], []
    for line in out.splitlines():
        if line.startswith("__HAVE__|"):
            have.append(line.split("|", 1)[1])
        elif line.startswith("__AVAIL__|"):
            _, p, v = line.split("|", 2)
            avail.append((p, v))
        elif line.startswith("__MISS__|"):
            miss.append(line.split("|", 1)[1])
    print("检查 %d 个包（镜像 %s）：" % (len(names), image))
    for p in have:
        print("  = %-40s 镜像自带，不用补" % p)
    for p, v in avail:
        print("  + %-40s 源里有 %s，可以抓" % (p, v))
    for p in miss:
        print("  ! %-40s 源里没有 —— 这个名字不存在，或它是第三方/私有包" % p)
    if miss:
        print("\n源里没有的包，`fetch_pkgs` 抓不到，只能：\n"
              "  · 先确认包名（ROS 包在 apt 里是 ros-<distro>-<名字>，名字用连字符）；\n"
              "  · 若是 GitHub/私有包，把它源码放进工作区的 src/ 一起编译，"
              "不能指望 apt。")
    return 0 if not miss else 1


def fetch(names, distro, image, host):
    idx = _index()
    before = set(idx)
    q = " ".join(names)
    script = "\n".join([
        "set -o pipefail",
        "echo __STAGE__|update",
        "apt-get update -qq || { echo __UPDATE_FAIL__; exit 10; }",
        "echo __STAGE__|download",
        "apt-get install -y --download-only "
        "-o Dir::Cache::archives=/out %s || { echo __DL_FAIL__; exit 11; }" % q,
        "echo __STAGE__|meta",
        ": > /out/_meta.tsv",
        "for d in /out/*.deb; do",
        '  [ -e "$d" ] || continue',
        '  p=$(dpkg-deb -f "$d" Package)',
        '  v=$(dpkg-deb -f "$d" Version)',
        "  dep=$(dpkg-deb -f \"$d\" Depends | tr '\\n' ' ' | sed 's/([^)]*)//g'"
        " | tr ',' ' ' | tr -s ' ')",
        '  printf "%s\\t%s\\t%s\\t%s\\n" "$p" "$v" "$dep" "$(basename "$d")" >> /out/_meta.tsv',
        "done",
    ])
    rc, out = _run(script, image, host)
    meta = os.path.join(POOL, "_meta.tsv")
    added = 0
    if os.path.isfile(meta):
        with open(meta, encoding="utf-8", errors="replace") as f:
            for line in f:
                parts = line.rstrip("\n").split("\t")
                if len(parts) < 4:
                    continue
                pkg, ver, dep, deb = parts[0], parts[1], parts[2], parts[3]
                idx[pkg] = {"deb": deb, "version": ver,
                            "depends": [d for d in dep.split() if d]}
                added += 1
        os.remove(meta)
    if added:
        _save_index(idx)
    total, size = 0, 0
    try:
        for f in os.listdir(POOL):
            if f.endswith(".deb"):
                total += 1
                size += os.path.getsize(os.path.join(POOL, f))
    except OSError:
        pass
    if rc != 0:
        if ("failed to connect to the docker API" in out
                or "Cannot connect to the Docker daemon" in out
                or "is the docker daemon running" in out):
            print("Docker 没有启动 —— 先打开 Docker Desktop（Linux: systemctl start docker），"
                  "再跑一次。")
            return 1
        tail = [l for l in out.splitlines() if l.strip()][-12:]
        print("抓包失败（退出码 %d）：" % rc)
        for l in tail:
            print("  " + l)
        if "Unable to locate package" in out:
            print("\n有包名在源里查不到 —— 先跑 --check 确认，"
                  "第三方包得把源码放进 src/ 一起编译。")
        return 1
    new = [k for k in idx if k not in before]
    if names:
        left = _clear_pending([n for n in names if n in idx])
        if left:
            print("待补清单还剩 %d 个（源里没抓到，需要单独处理）：%s"
                  % (len(left), "、".join(left)))
    print("包池：新增 %d 个 deb，共 %d 个 / %.1f MB" % (len(new), total, size / 1048576.0))
    for k in sorted(new):
        print("  + %-42s %s" % (k, idx[k].get("version") or ""))
    if not new:
        print("（这些包镜像里已经自带，或已经抓过了，没有新文件）")
    print("\n索引已更新：pkgcache/index.json")
    print("接下来：用户重跑一次验证，容器会从 /pkgpool 自动补装。")
    return 0


def listing():
    idx = _index()
    try:
        debs = [f for f in os.listdir(POOL) if f.endswith(".deb")]
    except OSError:
        debs = []
    size = 0
    for f in debs:
        try:
            size += os.path.getsize(os.path.join(POOL, f))
        except OSError:
            pass
    print("包池 %s" % POOL)
    print("  索引 %d 个包，磁盘 %d 个 deb，共 %.1f MB" % (len(idx), len(debs), size / 1048576.0))
    orphan = [d for d in debs if not any((v or {}).get("deb") == d for v in idx.values())]
    if orphan:
        print("  以下 deb 没进索引（不会被容器看到）：%s" % "、".join(sorted(orphan)[:8]))
    pend = _pending()
    if pend:
        print("  待补清单 %d 个（用户验证时缺的）：%s" % (len(pend), "、".join(pend[:6])))
        print("  一条命令补齐：python tools/fetch_pkgs.py --pending")
    for k in sorted(idx):
        print("  %-44s %s" % (k, idx[k].get("version") or ""))
    return 0


def main(argv):
    args = [a for a in argv[1:]]
    host = "--host" in args
    args = [a for a in args if a != "--host"]
    distro, image = _target()
    if args[:1] == ["--list"] or not args:
        return listing()
    check_only = args[:1] == ["--check"]
    if check_only:
        args = args[1:]
    if "--pending" in args:
        args = [a for a in args if a != "--pending"]
        pend = _pending()
        if not pend:
            print("待补清单是空的（%s）—— 最近一次验证没缺包，或管理员已经补过了。"
                  % PENDING)
            return 0
        print("从待补清单读入 %d 个包：%s" % (len(pend), "、".join(pend)))
        args = pend + args
    if not args:
        print(__doc__)
        return 2
    names = []
    for a in args:
        n = apt_name(a, distro)
        if n and n not in names:
            names.append(n)
    if not host and not _docker():
        print("没有找到 docker。请装 Docker（项目本来就需要），"
              "或在一台 Ubuntu 24.04 + ROS 源的有网机器上加 --host 运行。")
        return 2
    if check_only:
        return check(names, distro, image, host)
    return fetch(names, distro, image, host)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
