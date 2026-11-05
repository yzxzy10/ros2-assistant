# -*- coding: utf-8 -*-
"""版本单一事实来源。

系统里凡是要提「哪个版本」的地方——提示词、代码模板、编译沙箱镜像、
界面徽标、健康检查——都必须从这里取值，避免出现「文档写 Humble、
实际环境是 Jazzy」这种自相矛盾。

两套版本号互不相干，别混：
- APP_VERSION：本系统的软件版本（改了代码就升）；
- ROS 发行版（distro）：生成/校验的代码所面向的 ROS 2 目标版本。
"""

APP_VERSION = "1.2.3"

# 发行版元数据。新增发行版只需在这里加一项，其余模块自动跟随。
DISTROS = {
    "jazzy": {
        "display": "Jazzy Jalisco",
        "short": "Jazzy",
        "ubuntu": "24.04",
        "python": "3.12",
        "gazebo": "Gazebo Harmonic",
        "image": "ros:jazzy-ros-base",
        "released": "2024-05",
        "eol": "2029-05",
        "role": "primary",       # 本系统默认目标版本
    },
    "humble": {
        "display": "Humble Hawksbill",
        "short": "Humble",
        "ubuntu": "22.04",
        "python": "3.10",
        "gazebo": "Gazebo Classic 11 / Fortress",
        "image": "ros:humble-ros-base",
        "released": "2022-05",
        "eol": "2027-05",
        "role": "compat",        # 兼容分支，需显式指定
    },
}

DEFAULT_DISTRO = "jazzy"


def distro_name(cfg=None):
    """从配置读目标发行版；非法值回落到默认，不抛异常。"""
    name = ""
    if isinstance(cfg, dict):
        name = str((cfg.get("ros") or {}).get("distro") or "").strip().lower()
    return name if name in DISTROS else DEFAULT_DISTRO


def info(cfg=None):
    """返回当前发行版的元数据副本（附带 key）。"""
    name = distro_name(cfg)
    d = dict(DISTROS[name])
    d["key"] = name
    return d


def label(cfg=None):
    """完整标注：ROS 2 Jazzy（Ubuntu 24.04 · Python 3.12 · Gazebo Harmonic）"""
    d = info(cfg)
    return "ROS 2 %s（Ubuntu %s · Python %s · %s）" % (
        d["display"], d["ubuntu"], d["python"], d["gazebo"])


def short(cfg=None):
    """短标注：ROS 2 Jazzy"""
    return "ROS 2 " + info(cfg)["short"]


def setup_cmd(cfg=None):
    """环境加载命令，带具体发行版名，避免学生照抄错目录。"""
    return "source /opt/ros/%s/setup.bash" % distro_name(cfg)


def apt_pkg(pkg, cfg=None):
    """apt 包名，如 ros-jazzy-rclpy。"""
    return "ros-%s-%s" % (distro_name(cfg), pkg)


def image(cfg=None):
    """沙箱镜像名（可被 config.ros.verify.image 覆盖）。"""
    override = ""
    if isinstance(cfg, dict):
        override = str((cfg.get("ros", {}).get("verify") or {}).get("image") or "").strip()
    return override or info(cfg)["image"]


def constraint_block(cfg=None):
    """注入系统提示词的版本约束块。

    这段文字的作用是让模型明确「站在哪个发行版说话」，而不是凭训练数据
    里的老版本（很多语料默认 Foxy/Humble）随手作答。
    """
    d = info(cfg)
    return (
        "【目标版本（硬约束）】\n"
        "- 目标发行版：ROS 2 %s（Ubuntu %s / Python %s / %s）\n"
        "- 环境加载命令：%s\n"
        "- 安装 ROS 自带 Python 包用 apt，例如 sudo apt install %s\n"
        "- 只使用该发行版存在的 API；不确定某接口是否可用时，改用 rclpy 稳定接口，"
        "并提示学生用 `ros2 --version` 与 `printenv ROS_DISTRO` 自行核对。\n"
        "- 知识库检索结果里可能带「【来源版本】ROS 2 XXX」标注，若与目标版本不同，"
        "按目标版本作答，并在涉及差异处点明「该资料来自 XXX」。\n"
        "- 例外：学生明确说明自己用的是别的发行版时，按学生的版本来，"
        "并在回答开头标注「以下按 ROS 2 XXX 给出」。\n"
        % (d["display"], d["ubuntu"], d["python"], d["gazebo"],
           setup_cmd(cfg), apt_pkg("rclpy", cfg))
    )


def meta(cfg=None):
    """给 /api/meta 与前端徽标用的结构化版本信息。"""
    d = info(cfg)
    return {
        "app_version": APP_VERSION,
        "ros": {
            "distro": d["key"],
            "display": d["display"],
            "label": label(cfg),
            "short": short(cfg),
            "ubuntu": d["ubuntu"],
            "python": d["python"],
            "gazebo": d["gazebo"],
            "image": image(cfg),
            "eol": d["eol"],
            "role": d["role"],
            "setup": setup_cmd(cfg),
        },
        "distros": {k: {"display": v["display"], "role": v["role"],
                        "ubuntu": v["ubuntu"], "eol": v["eol"]}
                    for k, v in DISTROS.items()},
    }
