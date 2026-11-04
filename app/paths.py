# -*- coding: utf-8 -*-
"""可变数据目录统一入口：会话 / 画像库 / 归档。

默认放在项目根目录（sessions/、data/、archive/）。
部署时把 ROS2_DATA_DIR 指向挂载卷，容器重建、进程重启都不会丢数据：

    ROS2_DATA_DIR=/data
      ├── sessions/<sid>/state.json + ros2_ws/     会话状态与生成的代码
      ├── data/profile.db                          学习画像（SQLite）
      └── archive/<sid>.zip                        30 天未活动的旧会话

所有函数每次都读环境变量，方便测试里临时切换目录。
"""
import os

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def root():
    """数据根目录。未设置 ROS2_DATA_DIR 时回到项目根目录（保持旧行为）。"""
    p = (os.environ.get("ROS2_DATA_DIR") or "").strip()
    if not p:
        return _ROOT
    return os.path.abspath(os.path.expanduser(p))


def _sub(name, create=True):
    d = os.path.join(root(), name)
    if create:
        try:
            os.makedirs(d, exist_ok=True)
        except OSError:
            pass
    return d


def sessions(create=True):
    return _sub("sessions", create)


def data(create=True):
    return _sub("data", create)


def archive(create=True):
    return _sub("archive", create)


def db_path(create=True):
    return os.path.join(data(create), "profile.db")


def ensure_all():
    """启动时一次建好三个目录，顺便验证卷是否可写。"""
    return {"root": root(), "sessions": sessions(),
            "data": data(), "archive": archive()}


def describe():
    return "数据目录 %s（sessions / data / archive）" % root()
