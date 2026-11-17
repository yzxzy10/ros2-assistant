# -*- coding: utf-8 -*-
"""把项目打成可以直接交给别人的 zip（默认落在桌面）。

给谁用：要把整个系统拷给同学/管理员，对方解压后 `python server.py` 就能跑。
所以：
  · 不带 `.env`（真实 API Key 不能出门）、不带 sessions/data/archive（别人的会话数据）
  · 不带 `__pycache__`（换个 Python 版本反而会让人怀疑代码坏了）
  · 带 `pkgcache/pool`（离线 deb 包池，对方没外网也能补依赖）和 `kb_data`

用法：
    python tools/pack_release.py                # → 桌面/ros2-assistant-live-v<版本>.zip
    python tools/pack_release.py --out D:/x.zip # 指定路径
"""
import argparse
import os
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app.versions import APP_VERSION                            # noqa: E402

# 目录名黑名单：整棵跳过
SKIP_DIRS = {"__pycache__", ".git", ".idea", ".vscode", ".workbuddy",
             "sessions", "data", "archive", "node_modules", ".venv", "venv"}
# 文件名黑名单：只跳过具体的文件
SKIP_FILES = {".env", ".DS_Store", "Thumbs.db"}
SKIP_EXT = (".pyc", ".pyo", ".log", ".genie")
# .env.local / .env.prod 这类是真配置，不能出门；
# 但示例模板必须带上 —— START-HERE 第一步就是 cp .env.example .env，
# 少了它对方不知道要填哪些键
ENV_TEMPLATES = {".env.example", ".env.sample", ".env.template"}


def _iter_files(root):
    for dp, dn, fn in os.walk(root):
        dn[:] = sorted(d for d in dn if d not in SKIP_DIRS)
        for f in sorted(fn):
            if f in SKIP_FILES or f.endswith(SKIP_EXT):
                continue
            if f.startswith(".env.") and f not in ENV_TEMPLATES:
                continue
            yield os.path.join(dp, f)


def pack(out_path, root=ROOT):
    top = os.path.basename(root.rstrip("/\\")) or "ros2-assistant-live"
    n = 0
    with zipfile.ZipFile(out_path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in _iter_files(root):
            arc = top + "/" + os.path.relpath(path, root).replace("\\", "/")
            z.write(path, arc)
            n += 1
    return n, os.path.getsize(out_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="")
    ap.add_argument("--root", default=ROOT)
    a = ap.parse_args()
    out = a.out or os.path.join(
        os.path.expanduser("~"), "Desktop",
        "ros2-assistant-live-v%s.zip" % APP_VERSION)
    n, size = pack(out, a.root)
    print("打包完成：%s\n  %d 个文件，%.1f MB" % (out, n, size / 1048576.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
