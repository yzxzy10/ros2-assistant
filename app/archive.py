# -*- coding: utf-8 -*-
"""旧会话归档：把长期没活动的 sessions/<sid> 打包进 archive/<sid>.zip 后删除原目录。

安全策略（避免误删）：
1. 只处理 sessions 目录下的一级子目录，且目录名必须匹配会话 id 规则；
2. 打包后先用 zipfile 校验能正常读出，校验通过才删除原目录；
3. 正在内存中活跃的会话（keep 参数）一律跳过；
4. 文件缺失、无权限等情况只记录日志，不抛出。
"""
import os
import re
import shutil
import time
import zipfile

from . import paths

_SAFE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")
DEFAULT_DAYS = 30


def _last_active(path):
    """目录内最新文件的修改时间（state.json 与生成的代码都算活动）。"""
    newest = 0.0
    for dirpath, _dirs, files in os.walk(path):
        for fn in files:
            try:
                newest = max(newest, os.path.getmtime(os.path.join(dirpath, fn)))
            except OSError:
                continue
    if newest == 0.0:
        try:
            newest = os.path.getmtime(path)
        except OSError:
            newest = time.time()
    return newest


def archive_old_sessions(days=None, keep=None, dry_run=False):
    """归档超过 days 天未活动的会话，返回 [(sid, 结果)]。

    结果：'archived' | 'skipped' | 'failed'
    """
    try:
        days = int(days if days is not None else DEFAULT_DAYS)
    except Exception:
        days = DEFAULT_DAYS
    keep = set(keep or ())
    cutoff = time.time() - days * 86400
    out = []

    sess_dir = paths.sessions(create=False)
    archive_dir = paths.archive(create=False)
    if not os.path.isdir(sess_dir):
        return out
    if not dry_run:
        os.makedirs(archive_dir, exist_ok=True)

    for name in sorted(os.listdir(sess_dir)):
        path = os.path.join(sess_dir, name)
        if not os.path.isdir(path) or not _SAFE.match(name) or name in keep:
            out.append((name, "skipped"))
            continue
        try:
            if _last_active(path) >= cutoff:
                out.append((name, "skipped"))
                continue
            if dry_run:
                out.append((name, "archived"))
                continue

            target = os.path.join(archive_dir, "%s.zip" % name)
            tmp = target + ".tmp"
            with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
                for dirpath, _dirs, files in os.walk(path):
                    for fn in files:
                        full = os.path.join(dirpath, fn)
                        rel = os.path.relpath(full, path).replace("\\", "/")
                        z.write(full, rel)
            with zipfile.ZipFile(tmp) as z:      # 校验：能完整读出才算成功
                if z.testzip() is not None:
                    raise IOError("归档校验失败")
            os.replace(tmp, target)
            shutil.rmtree(path, ignore_errors=True)
            if os.path.isdir(path):
                out.append((name, "failed"))
            else:
                out.append((name, "archived"))
                print("[归档] %s → archive/%s.zip" % (name, name))
        except Exception as e:  # noqa: BLE001
            print("[归档] %s 失败：%r" % (name, e))
            out.append((name, "failed"))
    return out


def start_scheduler(interval_hours=24.0, days=None, keep_fn=None):
    """后台线程：启动时跑一次，之后每 interval_hours 跑一次。返回线程对象。"""
    import threading

    def loop():
        while True:
            try:
                archive_old_sessions(days=days, keep=(keep_fn() if keep_fn else ()))
            except Exception as e:  # noqa: BLE001
                print("[归档] 巡检异常：%r" % e)
            time.sleep(max(1.0, interval_hours) * 3600)

    t = threading.Thread(target=loop, name="session-archiver", daemon=True)
    t.start()
    return t


def archive_session(sid):
    """归档单个会话：打包成 archive/<sid>.zip，校验通过后删除原目录。

    返回 (ok, message)。归档后可随时用 zip 找回，属于「软删除」。
    """
    if not _SAFE.match(sid):
        return False, "会话 id 非法"
    sess_dir = paths.sessions(create=False)
    src = os.path.join(sess_dir, sid)
    if not os.path.isdir(src):
        return False, "会话不存在"
    archive_dir = paths.archive()
    target = os.path.join(archive_dir, "%s.zip" % sid)
    tmp = target + ".tmp"
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for dirpath, _dirs, files in os.walk(src):
                for fn in files:
                    full = os.path.join(dirpath, fn)
                    rel = os.path.relpath(full, src).replace("\\", "/")
                    z.write(full, rel)
        with zipfile.ZipFile(tmp) as z:
            if z.testzip() is not None:
                raise IOError("归档校验失败")
        os.replace(tmp, target)
        shutil.rmtree(src, ignore_errors=True)
        if os.path.isdir(src):
            return False, "归档后原目录未清除，请稍后重试"
        print("[归档] %s → archive/%s.zip" % (sid, sid))
        return True, "已归档"
    except Exception as e:  # noqa: BLE001
        print("[归档] %s 失败：%r" % (sid, e))
        try:
            if os.path.isfile(tmp):
                os.remove(tmp)
        except OSError:
            pass
        return False, "归档失败：%r" % e


def delete_session(sid):
    """删除单个会话（不可恢复）。返回 (ok, message)。

    只删会话目录，不动学习画像（画像跨会话保留，属于长期记忆）。
    """
    if not _SAFE.match(sid):
        return False, "会话 id 非法"
    sess_dir = paths.sessions(create=False)
    src = os.path.join(sess_dir, sid)
    if not os.path.isdir(src):
        return False, "会话不存在"
    try:
        shutil.rmtree(src, ignore_errors=True)
        if os.path.isdir(src):
            return False, "删除后目录仍存在，请稍后重试"
        print("[删除] 会话 %s 已删除" % sid)
        return True, "已删除"
    except Exception as e:  # noqa: BLE001
        print("[删除] %s 失败：%r" % (sid, e))
        return False, "删除失败：%r" % e
