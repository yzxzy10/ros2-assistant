# -*- coding: utf-8 -*-
"""回归：`resource/<包名>` 这张「包身份证」不能在路上丢了。

    python -X utf8 tests/resource_marker_test.py

背景：ament_python 包的 `resource/<包名>` 是个**空文件、没有扩展名**。
只要导入链路里有一道按扩展名过滤的关卡，它就会被悄悄丢掉，然后 colcon 在
打包阶段报

    can't copy '.../resource/<包名>': doesn't exist or not a regular file

用户看到的是「我的包编译不通过」，排查方向完全被带偏（实测踩过两次：
后端 _keep 一次、前端 folder 导入一次）。

所以这里从两个方向钉住：
1. 后端白名单必须收它（前端 js 里那份同名规则由 tests/folder_import_ui_test.py 盯）；
2. 就算上游还是丢了，导入时必须自动补回来，不能让用户拿着一个必然编不过的
   工作区去点「编译运行」。
"""
import io
import os
import shutil
import sys
import tempfile
import zipfile

os.environ["ROS2_DATA_DIR"] = os.path.join(tempfile.gettempdir(), "gdata_marker")
shutil.rmtree(os.environ["ROS2_DATA_DIR"], ignore_errors=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from app import orchestrator, project_import            # noqa: E402

FAILED = []


def check(name, ok, extra=""):
    print(("  PASS  " if ok else "  FAIL  ") + name + (("  " + str(extra)) if extra else ""))
    if not ok:
        FAILED.append(name)


def zip_without_markers():
    """把样例 zip 里的 resource 标记文件全部抠掉，模拟「导入链路丢了它」。"""
    src = os.path.join(ROOT, "samples", "demo_ros2_ws.zip")
    buf = io.BytesIO()
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(buf, "w") as zout:
        for info in zin.infolist():
            if "/resource/" in info.filename:
                continue
            zout.writestr(info, zin.read(info))
    return buf.getvalue()


def test_keep():
    print("\n[1] 白名单：无扩展名的 ROS 约定文件")
    keep = project_import._keep
    check("resource/<包名> 标记文件", keep("src/control_pkg/resource/control_pkg"))
    check("嵌套更深的 resource/", keep("ws/src/a/b/resource/b"))
    check("scripts/ 下无后缀脚本", keep("src/pkg/scripts/run"))
    check("COLCON_IGNORE", keep("src/COLCON_IGNORE"))
    check("真的产物仍然拒收（png）", not keep("src/pkg/docs/diagram.png"))
    check("真的产物仍然拒收（无后缀但不在约定目录）",
          not keep("src/pkg/data/blob"))


def import_zip(name, data):
    sess = orchestrator.get_session("mk" + name)
    ok, msg, _struct = project_import.import_to_session(
        sess, [{"path": "demo_ros2_ws.zip", "data": data}])
    return sess, ok, msg


def test_heal_after_zip():
    print("\n[2] zip 里缺标记文件 → 导入时自动补回")
    sess, ok, msg = import_zip("zip1", zip_without_markers())
    check("导入成功", ok, msg.splitlines()[0] if msg else "")
    ws = sess.ws_root()
    for pkg in ("control_pkg", "perception_pkg"):
        p = os.path.join(ws, "src", pkg, "resource", pkg)
        check("%s 的标记文件被补上" % pkg, os.path.isfile(p), p)
        check("%s 的标记文件是空的" % pkg,
              os.path.isfile(p) and os.path.getsize(p) == 0)
    check("摘要里说明了这件事", "自动补了" in (msg or ""), (msg or "")[:120])

    # 用户代码一个字节都不能被动：节点文件仍然在
    node = os.path.join(ws, "src", "control_pkg", "control_pkg", "planner_node.py")
    check("节点源码没被影响", os.path.isfile(node))


def test_folder_style_import():
    print("\n[3] 散文件（选文件夹）导入，同样要补")
    files = [
        {"path": "src/control_pkg/package.xml", "content":
         '<?xml version="1.0"?><package format="3"><name>control_pkg</name>'
         '<version>0.1.0</version><description>d</description>'
         '<maintainer email="a@b.cd">a</maintainer><license>MIT</license>'
         '<export><build_type>ament_python</build_type></export></package>'},
        {"path": "src/control_pkg/setup.py", "content":
         "from setuptools import setup\npackage_name = 'control_pkg'\n"},
        {"path": "src/control_pkg/control_pkg/planner_node.py",
         "content": "import rclpy\nfrom rclpy.node import Node\n"},
    ]
    sess = orchestrator.get_session("mkfolder1")
    ok, msg, _ = project_import.import_to_session(sess, files)
    check("导入成功", ok, (msg or "").splitlines()[0] if msg else "")
    p = os.path.join(sess.ws_root(), "src", "control_pkg", "resource", "control_pkg")
    check("标记文件被补上", os.path.isfile(p), p)


def test_cmake_untouched():
    print("\n[4] C++ 包不该被塞进 resource/")
    files = [
        {"path": "ws/src/cpp_pkg/package.xml", "content":
         '<?xml version="1.0"?><package format="3"><name>cpp_pkg</name>'
         '<version>0.1.0</version><description>d</description>'
         '<maintainer email="a@b.cd">a</maintainer><license>MIT</license>'
         '<export><build_type>ament_cmake</build_type></export></package>'},
        {"path": "ws/src/cpp_pkg/CMakeLists.txt",
         "content": "find_package(ament_cmake REQUIRED)\n"},
        {"path": "ws/src/cpp_pkg/src/main.cpp",
         "content": "int main(){return 0;}\n"},
    ]
    sess = orchestrator.get_session("mkcpp1")
    ok, _msg, _ = project_import.import_to_session(sess, files)
    check("导入成功", ok)
    check("没有凭空造 resource/",
          not os.path.exists(os.path.join(sess.ws_root(), "src", "cpp_pkg", "resource")))


def test_heal_before_verify():
    """老会话（工作区已经没有标记文件）不该被逼着重新导入一遍。"""
    print("\n[5] 点「编译运行」之前也要补一次")
    import time
    from app import chain_exec, config

    sess, ok, _msg = import_zip("verify1", open(
        os.path.join(ROOT, "samples", "demo_ros2_ws.zip"), "rb").read())
    check("导入成功", ok)
    ws = sess.ws_root()
    marks = [os.path.join(ws, "src", p, "resource", p)
             for p in ("control_pkg", "perception_pkg")]
    for p in marks:
        os.remove(p)                       # 模拟导入链路修好之前留下的工作区
    check("标记文件已删掉（复现现场）", not any(os.path.isfile(p) for p in marks))

    os.environ["ROS2_CHAIN_MOCK"] = "1"    # 只验「补标记」这一步，不跑真容器
    cfg = config.load_config()
    sid = sess.sid
    chain_exec.start(cfg, sess, sid)
    t0 = time.time()
    while time.time() - t0 < 40:
        st = chain_exec.status(sid) or {}
        if st.get("finished"):
            break
        time.sleep(0.3)
    check("验证跑完了（mock）", bool((chain_exec.status(sid) or {}).get("finished")))
    for p in marks:
        check("%s 验证前被补回" % os.path.basename(p), os.path.isfile(p), p)


def main():
    test_keep()
    test_heal_after_zip()
    test_folder_style_import()
    test_cmake_untouched()
    test_heal_before_verify()
    print("\n%s（失败 %d 项）" % ("全部通过" if not FAILED else "有失败", len(FAILED)))
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
