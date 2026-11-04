# -*- coding: utf-8 -*-
"""学习画像存储层（SQLite，Python 标准库实现，零第三方依赖）。

设计要点：
- 一处 sqlite 文件（默认 data/profile.db，可用 ROS2_DATA_DIR 改位置），
  表 profiles 以 user_id 为主键；
- user_id 是「学生身份」，与 sessionId 解耦：换会话（新 sid）也能带着走；
- JSON 列（concepts / projects / errors）在读写时做序列化，保持结构清晰；
- 每个写操作单独开连接（WAL 模式），配合进程级锁，避免并发写冲突。
"""
import json
import os
import sqlite3
import threading
import time

from . import paths

_LOCK = threading.RLock()
_READY = [None]          # 已初始化建表的 db 路径（切目录后会自动重建）

_SCHEMA = """
CREATE TABLE IF NOT EXISTS profiles (
  user_id       TEXT PRIMARY KEY,
  level         TEXT NOT NULL DEFAULT 'beginner',
  turns         INTEGER NOT NULL DEFAULT 0,
  concepts      TEXT NOT NULL DEFAULT '[]',
  projects      TEXT NOT NULL DEFAULT '[]',
  errors        TEXT NOT NULL DEFAULT '[]',
  summary       TEXT NOT NULL DEFAULT '',
  summary_turns INTEGER NOT NULL DEFAULT 0,
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_profiles_updated ON profiles(updated_at);
"""

_JSON_COLS = ("concepts", "projects", "errors")
_WRITABLE = {"level", "turns", "concepts", "projects", "errors",
             "summary", "summary_turns", "updated_at"}

# 概念词表：正则 → 显示名（顺序即展示顺序）
CONCEPT_RULES = [
    ("话题 topic", r"话题|topic"),
    ("发布者 publisher", r"发布者|publisher|talker"),
    ("订阅者 subscriber", r"订阅者|subscriber|listener"),
    ("服务 service", r"服务|service"),
    ("动作 action", r"动作|action"),
    ("参数 parameter", r"参数|parameter|declare_parameter"),
    ("消息接口 msg", r"\bmsg\b|消息类型|消息接口|\.msg\b"),
    ("自定义消息", r"自定义.{0,4}(消息|msg)|自定义接口"),
    ("QoS", r"\bqos\b|服务质量"),
    ("节点 node", r"节点|\bnode\b"),
    ("回调 callback", r"回调|callback"),
    ("执行器 executor", r"执行器|executor"),
    ("launch 文件", r"launch"),
    ("工作空间 colcon", r"colcon|工作空间|workspace|install/setup"),
    ("rclpy", r"rclpy"),
    ("TF 坐标变换", r"\btf2\b|坐标变换|\btf\b"),
    ("URDF", r"urdf"),
    ("Gazebo 仿真", r"gazebo|仿真环境"),
    ("RViz 可视化", r"rviz"),
    ("生命周期节点", r"生命周期|lifecycle"),
    ("命名空间", r"命名空间|namespace"),
    ("时间同步", r"时间同步|use_sim_time|sim time"),
    ("ros2 命令行", r"ros2\s+(run|topic|node|service|param|launch|bag)"),
]

_ERR_RE = None


def _ensure():
    db = paths.db_path(create=False)
    if _READY[0] == db:
        return
    with _LOCK:
        if _READY[0] == db:
            return
        paths.data()
        conn = sqlite3.connect(paths.db_path(), timeout=10)
        try:
            conn.executescript(_SCHEMA)
            conn.commit()
        finally:
            conn.close()
        _READY[0] = db


def _conn():
    _ensure()
    c = sqlite3.connect(paths.db_path(), timeout=10)
    c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    return c


def _now():
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _loads(val, default):
    try:
        v = json.loads(val) if val else default
        return v if isinstance(v, type(default)) else default
    except Exception:
        return default


def _row(row):
    if row is None:
        return None
    d = dict(row)
    for k in _JSON_COLS:
        d[k] = _loads(d.get(k), [])
    return d


def get(user_id):
    """取画像；不存在时返回一份空画像（不落库）。"""
    if not user_id:
        return None
    with _LOCK:
        c = _conn()
        try:
            row = c.execute("SELECT * FROM profiles WHERE user_id=?",
                            (user_id,)).fetchone()
        finally:
            c.close()
    if row is None:
        return {"user_id": user_id, "level": "beginner", "turns": 0,
                "concepts": [], "projects": [], "errors": [],
                "summary": "", "summary_turns": 0,
                "created_at": "", "updated_at": ""}
    return _row(row)


def _touch(c, user_id):
    now = _now()
    c.execute("INSERT OR IGNORE INTO profiles (user_id, created_at, updated_at) "
              "VALUES (?, ?, ?)", (user_id, now, now))
    c.execute("UPDATE profiles SET updated_at=? WHERE user_id=?", (now, user_id))


def record_turn(user_id, n=1):
    """记一次对话轮次。"""
    if not user_id:
        return
    with _LOCK:
        c = _conn()
        try:
            _touch(c, user_id)
            c.execute("UPDATE profiles SET turns=turns+? WHERE user_id=?",
                      (int(n), user_id))
            c.commit()
        finally:
            c.close()


def set_level(user_id, level):
    """水平只升不降（beginner → advanced）。"""
    if not user_id or level not in ("beginner", "advanced"):
        return
    with _LOCK:
        c = _conn()
        try:
            _touch(c, user_id)
            if level == "advanced":
                c.execute("UPDATE profiles SET level='advanced' WHERE user_id=?",
                          (user_id,))
            c.commit()
        finally:
            c.close()


def add_concepts(user_id, names, limit=40):
    """累加概念，按出现次数排序保留前 limit 个。"""
    if not user_id or not names:
        return
    with _LOCK:
        c = _conn()
        try:
            _touch(c, user_id)
            row = c.execute("SELECT concepts FROM profiles WHERE user_id=?",
                            (user_id,)).fetchone()
            items = _loads(row["concepts"] if row else "[]", [])
            by_name = {it.get("name"): it for it in items if it.get("name")}
            now = _now()
            for n in names:
                if n in by_name:
                    by_name[n]["count"] = int(by_name[n].get("count", 1)) + 1
                    by_name[n]["last"] = now
                else:
                    by_name[n] = {"name": n, "count": 1, "last": now}
            merged = sorted(by_name.values(),
                            key=lambda it: (-int(it.get("count", 1)),
                                            it.get("name", "")))[:limit]
            c.execute("UPDATE profiles SET concepts=? WHERE user_id=?",
                      (json.dumps(merged, ensure_ascii=False), user_id))
            c.commit()
        finally:
            c.close()


def add_error(user_id, err_type, message, limit=20):
    """记录踩过的报错（同类型只留最新一条，去重）。"""
    if not user_id or not (err_type or message):
        return
    err_type = (err_type or "报错").strip()[:40]
    message = (message or "").strip().replace("\n", " ")[:200]
    with _LOCK:
        c = _conn()
        try:
            _touch(c, user_id)
            row = c.execute("SELECT errors FROM profiles WHERE user_id=?",
                            (user_id,)).fetchone()
            items = _loads(row["errors"] if row else "[]", [])
            now = _now()
            items = [it for it in items if it.get("type") != err_type]
            items.insert(0, {"type": err_type, "message": message,
                             "count": 1, "last": now})
            c.execute("UPDATE profiles SET errors=? WHERE user_id=?",
                      (json.dumps(items[:limit], ensure_ascii=False), user_id))
            c.commit()
        finally:
            c.close()


def upsert_project(user_id, project, limit=10):
    """写入/更新做过的项目（按 package_name 去重）。"""
    if not user_id or not project:
        return
    pkg = (project.get("package_name") or project.get("name_cn") or "").strip()
    if not pkg:
        return
    rec = {
        "package_name": pkg,
        "name_cn": (project.get("name_cn") or "")[:40],
        "difficulty": (project.get("difficulty") or "")[:20],
        "nodes_total": int(project.get("nodes_total") or 0),
        "nodes_done": int(project.get("nodes_done") or 0),
        "status": (project.get("status") or "规划完成")[:20],
        "last": _now(),
    }
    with _LOCK:
        c = _conn()
        try:
            _touch(c, user_id)
            row = c.execute("SELECT projects FROM profiles WHERE user_id=?",
                            (user_id,)).fetchone()
            items = _loads(row["projects"] if row else "[]", [])
            items = [it for it in items if it.get("package_name") != pkg]
            items.insert(0, rec)
            c.execute("UPDATE profiles SET projects=? WHERE user_id=?",
                      (json.dumps(items[:limit], ensure_ascii=False), user_id))
            c.commit()
        finally:
            c.close()


def set_summary(user_id, text, covered_turns=0):
    """保存滚动摘要。"""
    if not user_id:
        return
    text = (text or "").strip()[:2000]
    with _LOCK:
        c = _conn()
        try:
            _touch(c, user_id)
            c.execute("UPDATE profiles SET summary=?, summary_turns=? WHERE user_id=?",
                      (text, int(covered_turns), user_id))
            c.commit()
        finally:
            c.close()


# ---------- 抽取与渲染 ----------

def extract_concepts(text):
    """从一句话里抽取命中的 ROS2 概念（返回显示名列表）。"""
    import re
    if not text:
        return []
    out = []
    low = text.lower()
    for name, pattern in CONCEPT_RULES:
        if re.search(pattern, low, re.I):
            out.append(name)
    return out


def extract_error(text):
    """抽取报错类型 + 摘要。"""
    import re
    if not text:
        return (None, "")
    m = re.search(r"([A-Za-z_][A-Za-z0-9_]*(?:Error|Exception|Warning))", text)
    first = ""
    for line in text.splitlines():
        line = line.strip()
        if line:
            first = line[:200]
            break
    return (m.group(1) if m else "报错", first)


def render(profile, max_chars=900, include_hint=True):
    """把画像渲染成可注入提示词的一段文本；空画像返回空串。"""
    if not profile:
        return ""
    parts = []
    level = "进阶" if profile.get("level") == "advanced" else "入门"
    concepts = [it.get("name") for it in (profile.get("concepts") or [])][:10]
    projects = (profile.get("projects") or [])[:3]
    errors = (profile.get("errors") or [])[:4]

    if concepts:
        parts.append("- 已经问过的概念：%s" % "、".join(concepts))
    for p in projects:
        parts.append("- 做过的项目：%s（%s，%s/%s 个节点已生成，%s）" % (
            p.get("name_cn") or p.get("package_name"),
            p.get("package_name") or "",
            p.get("nodes_done", 0), p.get("nodes_total", 0),
            p.get("status") or ""))
    for e in errors:
        parts.append("- 踩过的报错：%s（%s）" % (e.get("type"), e.get("message", "")[:80]))
    summary = (profile.get("summary") or "").strip()
    if summary:
        parts.append("- 往期对话摘要：%s" % summary)
    if not parts:
        return ""
    head = "【学生的学习画像（跨会话保留，来自历史对话）· 水平：%s】\n" % level
    body = "\n".join(parts)
    if include_hint:
        body += ("\n（说明：他问到上面已掌握的概念时可以用一句话带过、不用从零讲；"
                 "如果他重复踩同一个坑，直接给出上次的解法并提醒他别忘了。）")
    text = head + body
    return text[:max_chars]


def stats(profile):
    """面板用的统计。"""
    if not profile:
        return {}
    return {
        "turns": int(profile.get("turns") or 0),
        "concepts": len(profile.get("concepts") or []),
        "projects": len(profile.get("projects") or []),
        "errors": len(profile.get("errors") or []),
        "has_summary": bool((profile.get("summary") or "").strip()),
    }


def list_all(limit=50):
    """调试/运营用：列出最近的画像。"""
    _ensure()
    with _LOCK:
        c = _conn()
        try:
            rows = c.execute(
                "SELECT user_id, level, turns, summary, updated_at "
                "FROM profiles ORDER BY updated_at DESC LIMIT ?",
                (int(limit),)).fetchall()
        finally:
            c.close()
    return [dict(r) for r in rows]


def delete(user_id):
    """删除一份画像（测试/管理用）。"""
    if not user_id:
        return
    with _LOCK:
        c = _conn()
        try:
            c.execute("DELETE FROM profiles WHERE user_id=?", (user_id,))
            c.commit()
        finally:
            c.close()
