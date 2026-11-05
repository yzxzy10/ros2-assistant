# -*- coding: utf-8 -*-
"""打包一个可以直接发给别人的干净副本。

排除：.env（真实密钥）、.git、运行时数据（sessions/data/archive）、缓存。
额外塞一份「先看我-怎么跑.txt」，对方不用问你怎么启动。
"""
import os
import re
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NAME = "ros2-assistant"


def app_version():
    """版本号只认 app/versions.py，避免包名和代码里的版本对不上。"""
    src = open(os.path.join(ROOT, "app", "versions.py"), encoding="utf-8").read()
    m = re.search(r'APP_VERSION\s*=\s*"([\d.]+)"', src)
    return m.group(1) if m else "0.0.0"


VER = app_version()
OUT = os.path.join(os.path.expanduser("~"), "Desktop",
                   "ros2-assistant-v%s.zip" % VER)

# 目录级排除（按路径段匹配）
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", ".idea", ".vscode",
             "sessions", "data", "archive", ".pytest_cache", ".mypy_cache",
             # 工作流编排器的导出物（节点摘要/画布 json），跑应用不需要，且占 2MB+
             "_source"}
# 文件级排除
SKIP_FILES = {".env", ".env.local", ".DS_Store", "Thumbs.db"}
SKIP_EXT = {".pyc", ".pyo", ".log", ".swp"}

README = """ROS2 学习助手 v%(ver)s —— 本地跑起来只要 3 步
=====================================

需要：Python 3.10 或更高（开发用的是 3.13）
不需要：Docker、ROS2 环境、Node.js

【第 1 步】解压到任意目录。

【第 2 步】配一个模型 Key。
  在这个目录里新建文件 .env（注意前面有个点），只写一行：

      DASHSCOPE_API_KEY=sk-你的Key

  Key 在阿里云百炼（DashScope）控制台申请。
  没有 Key 也能启动、能看界面，但对话和代码生成会不可用。
  想调端口、限流、访问令牌，看 .env.example，里面每一项都有中文注释。

【第 3 步】启动。
  Windows：双击 start.bat
  或命令行：python -X utf8 server.py

  然后浏览器打开 http://127.0.0.1:8000/
  （端口被占用就在 .env 里写 PORT=8010 之类）


怎么用（架构图那套）
--------------------
1. 点输入框左边的文件夹图标，选一个 ROS2 工作空间文件夹；
   或者直接把 .zip 拖进页面，会自动解开。
2. 左边画布自动画出节点之间的通信关系，箭头方向 = 数据流向。
   鼠标停在箭头上才显示话题名和消息类型。
3. 单击节点 → 右侧看简介、接口清单、源码，可直接改代码保存。
4. 单击连线 → 改话题名，相关节点代码里的字符串会同步改。
5. 右上角「新建节点」放一个空壳，写完需求、连好线，再点「提交」
   让它生成真正的 ROS2 包（package.xml + setup.py + 入口点）。
   图随时能关掉，左栏「项目文件」里的「架构图」按钮可以再打开，
   不需要它每次弹卡片也能自己进。
6. 中间那条竖线可以左右拖，调整画布和对话的比例，双击复位。
7. 右上角「下载 zip」或左栏「下载」把生成的项目拿走，
   里面的 架构图.json 就是当前这张图。
8. 左栏「移除」把项目从当前对话里清掉（聊天记录保留）；
   想同时放两个项目，就开两个对话，各自独立。


不做项目，只有一个想法也行
--------------------------
直接跟它说「我想做一个避障小车」，它会走完需求分析 → 节点规划 →
代码生成。生成完会弹一张卡问你要不要打开架构图手动改；
这个提示可以在左栏底部「生成后提示看图」关掉。图关掉了也没关系，
左栏「项目文件」里那个常驻的「架构图」按钮随时能再打开。


导入别人项目时它认什么
----------------------
· 有 package.xml：包名取里面的 <name>，构建类型取 <build_type>。
· 只有 setup.py 或 CMakeLists.txt：从文件里取包名，取不到就用目录名。
· 三样都没有：按目录推断（src/xxx/... 取 xxx），图上照常出节点和连线，
  摘要会注明「包名是按目录推断的」，新建节点时会补齐标准包结构。
· 包名不叫 xxx_pkg 完全没关系，全流程不依赖这个后缀。
· 架构图只解析 Python（rclpy）节点。纯 C++ 包能导入、能看文件、能下载，
  但图上不会有节点 —— 界面会直接告诉你原因，不会让你对着空画布猜。
· 落盘目录固定是 工作区/ros2_ws/src/<包名>/，不会写到你原来的工程里。


两个提醒
--------
· 这个包里没有 .env —— 原作者的 API Key 不随代码分发，别把自己的 Key 发给别人。
· sessions/ data/ archive/ 是运行时自动创建的，第一次启动会自己建好，不用管。
""" % {"ver": VER}


def skip(rel):
    parts = rel.replace("\\", "/").split("/")
    if any(p in SKIP_DIRS for p in parts[:-1]):
        return True
    base = parts[-1]
    if base in SKIP_FILES:
        return True
    return os.path.splitext(base)[1].lower() in SKIP_EXT


def main():
    files = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, ROOT).replace("\\", "/")
            if skip(rel):
                continue
            files.append((full, rel))
    files.sort(key=lambda x: x[1])

    if os.path.exists(OUT):
        os.remove(OUT)

    total = 0
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr(NAME + "/先看我-怎么跑.txt", README)
        for full, rel in files:
            z.write(full, NAME + "/" + rel)
            total += os.path.getsize(full)

    size = os.path.getsize(OUT)
    print("生成:", OUT)
    print("文件数: %d（另有说明文件 1 个）" % len(files))
    print("压缩前 %.1f MB → 压缩后 %.1f MB" % (total / 1048576.0, size / 1048576.0))

    # 安全自检：绝对不能把 .env 带出去
    with zipfile.ZipFile(OUT) as z:
        names = z.namelist()
    bad = [n for n in names if n.endswith("/.env") or "/.env." in n and not n.endswith(".example")]
    if bad:
        print("!! 危险：包内含密钥文件", bad)
        return 1
    print("密钥自检: 通过（.env 未包含）")
    print("\n目录树（前 40 项）：")
    for n in names[:40]:
        print("  ", n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
