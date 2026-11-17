#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""重新生成「基础镜像自带哪些包」清单（app/base_pkgs.json）。

`ros:<distro>-ros-base` 里到底有什么，决定了缺包时该给谁下指令：
自带的缺了 = 包名写错（用户自己能查）；不自带的 = 用户装不了、得找管理员。
这份判断不能凭印象写，所以从软件源算一遍，落到 JSON，代码启动时读它。

    python tools/dump_base_pkgs.py            # 用 config.json 里的发行版
    python tools/dump_base_pkgs.py --distro jazzy

需要 Docker（在有网机器上跑一次即可；结果随项目一起提交）。
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "app", "base_pkgs.json")
CONFIG = os.path.join(ROOT, "config.json")


def _distro():
    try:
        with open(CONFIG, encoding="utf-8") as f:
            return ((json.load(f) or {}).get("ros") or {}).get("distro") or "jazzy"
    except (OSError, ValueError):
        return "jazzy"


def main(argv):
    distro = _distro()
    if "--distro" in argv:
        i = argv.index("--distro")
        if i + 1 < len(argv):
            distro = argv[i + 1]
    image = "ros:%s-ros-base" % distro
    script = (
        "apt-get update -qq || exit 10; "
        "apt-cache depends --recurse --no-recommends --no-suggests "
        "--no-conflicts --no-breaks --no-replaces --no-enhances "
        "ros-%s-ros-base | sed -n 's/^ros-%s-\\([a-z0-9._-]*\\)$/\\1/p' "
        "| tr '-' '_' | sort -u" % (distro, distro)
    )
    print("从 %s 计算 ros-%s-ros-base 的递归依赖…" % (image, distro))
    p = subprocess.run(["docker", "run", "--rm", image, "bash", "-lc", script],
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=900)
    names = [n.strip() for n in (p.stdout or "").splitlines() if n.strip()]
    if p.returncode != 0 or len(names) < 50:
        print("失败（退出码 %s，拿到 %d 个包）：" % (p.returncode, len(names)))
        print((p.stderr or p.stdout or "")[-800:])
        return 1
    data = {}
    if os.path.isfile(OUT):
        try:
            with open(OUT, encoding="utf-8") as f:
                data = json.load(f) or {}
        except (OSError, ValueError):
            data = {}
    data[distro] = names
    with open(OUT, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    print("写入 %s：%s 共 %d 个包" % (os.path.relpath(OUT, ROOT), distro, len(names)))
    print("抽查：tf2_ros=%s  nav2_msgs=%s" % ("tf2_ros" in names, "nav2_msgs" in names))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
