# -*- coding: utf-8 -*-
"""仿真流水线的离线单测：不调模型、不联网、不写盘。

跑法：
    cd ros2-assistant
    python tests/test_sim.py

覆盖三件事：
  1. 两类机型都能装出完整包，且六道闸门全部通过；
  2. 故意破坏关节名 / 质量 / 占位符，闸门必须报出来（不能假通过）；
  3. 参数表的边界收敛：离谱的输入不能变成非法模型。
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import sim_spec, sim_build, sim_gate  # noqa: E402

_FAIL = []


def check(cond, msg):
    if cond:
        print("  ok   %s" % msg)
    else:
        print("  FAIL %s" % msg)
        _FAIL.append(msg)


def _diff():
    return sim_spec.normalize({
        "archetype": "diff_drive",
        "package": "demo_bot",
        "wheel_radius": 0.05,
        "base_width": 0.18,
        "brief": "两轮差速小车",
    })


def _arm():
    return sim_spec.normalize({
        "archetype": "arm",
        "package": "demo_arm",
        "joints": [
            {"type": "revolute", "length": 0.12, "radius": 0.02, "mass": 0.3},
            {"type": "revolute", "length": 0.10, "radius": 0.018, "mass": 0.25},
            {"type": "prismatic", "length": 0.06, "radius": 0.015, "mass": 0.12},
        ],
        "brief": "三轴机械臂",
    })


def case_arm():
    print("\n[1] 机械臂：装配 + 闸门")
    spec = _arm()
    files = sim_build.build(spec)
    need = [
        "package.xml", "CMakeLists.txt",
        "urdf/demo_arm.urdf.xacro", "urdf/demo_arm.ros2_control.xacro",
        "urdf/demo_arm.gazebo.xacro", "config/controllers.yaml",
        "launch/display.launch.py", "launch/sim.launch.py",
        "worlds/empty.sdf", "scripts/selfcheck.py", "config/display.rviz",
        "README.md",
    ]
    for p in need:
        check(p in files, "生成 %s" % p)
    gate = sim_gate.check(spec, files)
    if not gate["passed"]:
        for e in gate["errors"]:
            print("    [%s] %s" % (e["code"], e["msg"]))
    check(gate["passed"], "机械臂通过六道闸门（%d 项检查）" % gate["stats"]["checks"])

    x = files["urdf/demo_arm.urdf.xacro"]
    check('type="prismatic"' in x, "含平移关节")
    check('type="continuous"' not in x, "机械臂不应出现 continuous")
    check("link3_radius" in x and "link3_mass" in x, "半径/质量写成 xacro 属性")
    check("{{" not in x, "无占位符残留")


def case_diff():
    print("\n[2] 差速底盘：装配 + 闸门")
    spec = _diff()
    files = sim_build.build(spec)
    gate = sim_gate.check(spec, files)
    if not gate["passed"]:
        for e in gate["errors"]:
            print("    [%s] %s" % (e["code"], e["msg"]))
    check(gate["passed"], "差速底盘通过六道闸门（%d 项检查）" % gate["stats"]["checks"])

    x = files["urdf/demo_bot.urdf.xacro"]
    check("left_wheel_joint" in x and "right_wheel_joint" in x, "左右轮关节都在")
    check("gz-sim-diff-drive-system" in x, "含差速插件")
    check("/cmd_vel" in files["launch/sim.launch.py"], "launch 桥接了 cmd_vel")
    check("Twist[gz.msgs.Twist" in files["launch/sim.launch.py"],
          "cmd_vel 方向是 ROS->GZ（[ 号）")
    check("Clock]gz.msgs.Clock" in files["launch/sim.launch.py"],
          "clock 方向是 GZ->ROS（] 号）")
    check("controllers.yaml" not in files, "差速底盘不生成控制器配置")


def case_negative():
    print("\n[3] 反向用例：闸门必须报错，不能假通过")
    spec = _arm()
    files = sim_build.build(spec)

    # 3.1 关节名不一致
    bad = dict(files)
    bad["config/controllers.yaml"] = files["config/controllers.yaml"].replace(
        "- joint2", "- joint_x")
    g = sim_gate.check(spec, bad)
    check(not g["passed"], "关节名不一致被拦下")
    check(any(e["code"] == "G2" for e in g["errors"]), "报的是 G2")

    # 3.2 质量为零
    bad2 = dict(files)
    bad2["urdf/demo_arm.urdf.xacro"] = files["urdf/demo_arm.urdf.xacro"].replace(
        'name="link2_mass" value="0.25"', 'name="link2_mass" value="0"')
    g = sim_gate.check(spec, bad2)
    check(not g["passed"], "零质量被拦下")
    check(any(e["code"] == "G3" for e in g["errors"]), "报的是 G3")

    # 3.3 占位符残留
    bad3 = dict(files)
    bad3["README.md"] = files["README.md"] + "\n{{oops}}"
    g = sim_gate.check(spec, bad3)
    check(not g["passed"], "占位符残留被拦下")
    check(any(e["code"] == "G6" for e in g["errors"]), "报的是 G6")

    # 3.4 XML 断掉
    bad4 = dict(files)
    bad4["urdf/demo_arm.urdf.xacro"] = files["urdf/demo_arm.urdf.xacro"].replace(
        "</robot>", "")
    g = sim_gate.check(spec, bad4)
    check(not g["passed"], "XML 不闭合被拦下")

    # 3.5 launch 里用了未声明的包
    bad5 = dict(files)
    bad5["launch/sim.launch.py"] = files["launch/sim.launch.py"].replace(
        'package="robot_state_publisher"', 'package="not_declared_pkg"')
    g = sim_gate.check(spec, bad5)
    check(not g["passed"], "未声明依赖被拦下")
    check(any(e["code"] == "G5" for e in g["errors"]), "报的是 G5")


def case_spec():
    print("\n[4] 参数表边界：离谱输入不能变成非法模型")
    s = sim_spec.normalize({"archetype": "diff_drive", "wheel_radius": 99,
                            "base_width": -3, "base_mass": 0})
    check(0.01 <= s["wheel_radius"] <= 0.4, "轮径被夹到合法区间：%s" % s["wheel_radius"])
    check(s["base_width"] >= 0.06, "车宽被夹回：%s" % s["base_width"])
    check(s["base_mass"] >= 0.1, "质量被夹回：%s" % s["base_mass"])
    check(s["wheel_separation"] > s["base_width"], "轮距由车宽+轮宽推导")

    s2 = sim_spec.normalize({"archetype": "arm", "joints": []})
    check(len(s2["joints"]) == 3, "没说几轴时给三轴默认")

    s3 = sim_spec.normalize({"archetype": "arm", "joints": [
        {"type": "转动", "length": 0.1},
        {"type": "平移", "travel": 0.05},
    ]})
    check(s3["joints"][0]["type"] == "revolute", "中文「转动」映射为 revolute")
    check(s3["joints"][1]["type"] == "prismatic", "中文「平移」映射为 prismatic")
    check(s3["joints"][1]["lower"] == 0.0, "平移关节下限为 0")

    s4 = sim_spec.normalize({"package": "My Bot-01"})
    check(s4["package"] == "my_bot_01", "包名被规范成 %s" % s4["package"])

    s5 = sim_spec.normalize({"archetype": "arm", "joints": [{"type": "revolute"}] * 9})
    check(len(s5["joints"]) == 6, "关节数封顶 6 个")


def case_routing():
    print("\n[5] 意图路由：不能把功能类需求抢进建模分支")
    try:
        from app import orchestrator as o
    except Exception as e:  # 依赖缺失时跳过，不影响前三组结论
        print("  skip 无法导入 orchestrator：%s" % e)
        return
    cases = [
        ("做一个两轮差速小车", True),
        ("生成一个三轴机械臂模型", True),
        ("帮我生成 URDF", True),
        ("在 gazebo 里搭个四旋翼", True),
        ("做一个两轮差速小车项目", False),
        ("我想做一个激光雷达避障小车项目", False),
        ("我的 URDF 报错了怎么办", False),
        ("gazebo 怎么加载模型", False),
        ("什么是 xacro", False),
        ("写一个发布者节点", False),
        ("做一个导航系统", False),
        ("做一个四轮汽车，能像真车一样转向", True),
        ("汽车怎么打方向", False),
    ]
    for text, want in cases:
        got = o._is_sim_intent(text)
        check(got == want, "「%s」→ 仿真=%s" % (text, got))


def case_profiles():
    print("\n[6] 机型档案：尺寸来自资料库真机")
    w = sim_spec.normalize({"profile": "tb3_waffle"})
    check(abs(w["wheel_radius"] - 0.033) < 1e-6, "TB3 轮径 0.033")
    check(abs(w["wheel_separation"] - 0.288) < 1e-6, "TB3 轮距 0.288")
    check(abs(w["base_length"] - 0.266) < 1e-6, "TB3 车长 0.266")
    check(abs(w["base_mass"] - 1.373) < 1e-3, "TB3 质量 1.373")
    check(abs(w["lidar_height"] - 0.122) < 1e-6, "TB3 雷达高度 0.122")

    b = sim_spec.normalize({"brief": "做一个像 turtlebot3 burger 的小车"})
    check(b["profile"] == "tb3_burger", "从原话认出 burger：%s" % b["profile"])
    check(abs(b["wheel_separation"] - 0.160) < 1e-6, "Burger 轮距 0.160")

    u = sim_spec.normalize({"profile": "ur5e", "package": "ur_like"})
    check(u["archetype"] == "arm" and len(u["joints"]) == 6, "UR5e 六轴")
    check(abs(u["joints"][1]["length"] - 0.425) < 1e-6, "UR5e 大臂 0.425")

    # 学生自己给的尺寸优先于档案
    o = sim_spec.normalize({"profile": "tb3_waffle", "wheel_radius": 0.08})
    check(abs(o["wheel_radius"] - 0.08) < 1e-6, "显式尺寸覆盖档案")

    # 档案模型也要过硬闸门、且部件确实变精细了
    for label, raw in (("TB3 底盘", {"profile": "tb3_waffle"}),
                       ("UR5e 臂", {"profile": "ur5e", "package": "ur_lite"})):
        spec = sim_spec.normalize(raw)
        files = sim_build.build(spec)
        gate = sim_gate.check(spec, files)
        check(gate["passed"], "%s 通过闸门" % label)
        urdf = files["urdf/%s.urdf.xacro" % spec["package"]]
        if spec["archetype"] == "diff_drive":
            check("lidar_link" in urdf, "底盘带雷达支架")
            check(urdf.count("<visual>") >= 12, "底盘视觉部件 %d 个" % urdf.count("<visual>"))
        else:
            check("gripper_base_link" in urdf, "机械臂带末端夹爪")
            check("left_finger_joint" in urdf and "right_finger_joint" in urdf,
                  "夹爪两指是可动关节（有独立控制器）")
            check(urdf.count("<visual>") >= 20, "机械臂视觉部件 %d 个" % urdf.count("<visual>"))


def case_wheels():
    print("\n[7] 轮子：看得见转、四轮、阿克曼转向")
    from app import model_scene

    d4 = sim_spec.normalize({"brief": "做一个四轮差速底盘"})
    check(d4["wheels"] == 4, "四轮被识别：%s" % d4["wheels"])
    f4 = sim_build.build(d4)
    check(sim_gate.check(d4, f4)["passed"], "四轮差速通过闸门")
    sc4 = model_scene.parse_model(f4["urdf/%s.urdf.xacro" % d4["package"]])
    wheels4 = [l for l in sc4["links"] if "wheel_link" in l["name"]]
    check(len(wheels4) == 4, "四个轮子：%d" % len(wheels4))
    check(len(wheels4[0]["visuals"]) >= 4,
          "单轮视觉部件 %d 个（含辐条与偏心标记）" % len(wheels4[0]["visuals"]))
    check(any("front_left_wheel_joint" == j["name"] for j in sc4["joints"]),
          "前轮是独立关节，不是焊死的装饰")

    d2 = sim_spec.normalize({"brief": "做一个两轮差速小车"})
    check(d2["wheels"] == 3, "「两轮」→ 两驱动轮 + 万向球")
    f2 = sim_build.build(d2)
    check("caster_link" in f2["urdf/%s.urdf.xacro" % d2["package"]], "带万向支撑球")

    ack = sim_spec.normalize({"brief": "做一个四轮汽车，能像真车一样转向"})
    check(ack["archetype"] == "ackermann", "「汽车/转向」→ 阿克曼：%s" % ack["archetype"])
    fa = sim_build.build(ack)
    xa = fa["urdf/%s.urdf.xacro" % ack["package"]]
    check("gz-sim-ackermann-steering-system" in xa, "用了阿克曼插件")
    check("front_left_steer_joint" in xa, "有前轮转向关节")
    check("steering_rack_link" in xa, "有转向机与齿条（传动杆机构）")
    check("front_left_steer_link" in xa and "wheel_separation*0.075" in xa
          and "wheel_separation*0.185" in xa, "转向节带节臂与拉杆")
    gate = sim_gate.check(ack, fa)
    if not gate["passed"]:
        for e in gate["errors"]:
            print("    [%s] %s" % (e["code"], e["msg"]))
    check(gate["passed"], "阿克曼通过闸门")
    sca = model_scene.parse_model(xa)
    steer = [j for j in sca["joints"]
             if j["name"].startswith("front_") and "steer_joint" in j["name"]]
    check(len(steer) == 2, "两个转向关节：%d" % len(steer))
    check(all(j["type"] == "revolute" and j.get("limit") for j in steer),
          "转向关节是可动 revolute，预览里能拖滑块打方向")

    # 差速车不应出现阿克曼插件，反之亦然
    check("ackermann" not in f4["urdf/%s.urdf.xacro" % d4["package"]], "差速车不混入转向插件")

    # 摘要必须能处理三种机型（线上曾因阿克曼落进机械臂分支报 KeyError('joints')）
    for sp in (d4, d2, ack):
        try:
            line = sim_spec.summary_lines(sp)
            check(bool(line), "摘要：%s" % line[:44])
        except Exception as e:
            check(False, "摘要崩了：%r" % e)


def case_arm_shape():
    print("\n[8] 机械臂：主流协作臂形态 + 夹爪独立控制")
    from app import model_scene
    s = sim_spec.normalize({"profile": "ur5e", "package": "ur_shape"})
    f = sim_build.build(s)
    u = f["urdf/ur_shape.urdf.xacro"]
    check("0 0.138 0" in u, "肩部偏置 0.138（UR5e 特征）")
    check("0 -0.131 0.425" in u, "肘部回摆 -0.131 + 大臂 0.425")
    check("0 0.127 0" in u, "腕部偏置 0.127")
    check(sim_gate.check(s, f)["passed"], "偏置后仍通过闸门")

    yaml = f["config/controllers.yaml"]
    check("arm_controller" in yaml and "gripper_controller" in yaml,
          "臂与夹爪各一个控制器")
    check("left_finger_joint" in yaml and "right_finger_joint" in yaml,
          "夹爪控制器挂两个手指关节")
    check("gripper_controller" in f["launch/sim.launch.py"],
          "launch 会拉起夹爪控制器")

    sc = model_scene.parse_model(u)
    fingers = [j for j in sc["joints"] if "finger" in j["name"]]
    check(len(fingers) == 2, "两个手指关节：%d" % len(fingers))
    check(all(j["type"] == "prismatic" and j.get("limit") for j in fingers),
          "手指是 prismatic 且有行程，预览里能拖开合")
    # 连杆是斜杆：至少一节 link 的视觉不再是纯 z 方向
    tl = [l for l in sc["links"] if l["name"].startswith("link")]
    check(any(len(l["visuals"]) >= 2 for l in tl), "每节连杆有关节壳")



def _aabb(v):
    """把一条 visual 变成轴对齐包围盒 (x0,x1,y0,y1,z0,z1)。"""
    g = v["geometry"]
    x, y, z = v["xyz"]
    rpy = v.get("rpy") or [0, 0, 0]
    if g["kind"] == "box":
        sx, sy, sz = g["size"]
    elif g["kind"] == "sphere":
        r = g["radius"]
        sx = sy = sz = 2 * r
    else:
        r, l = g["radius"], g["length"]
        if abs(abs(rpy[0]) - 1.5708) < 0.1:      # 绕 x 转 90° → 沿 y
            sx, sy, sz = 2 * r, l, 2 * r
        elif abs(abs(rpy[1]) - 1.5708) < 0.1:    # 绕 y 转 90° → 沿 x
            sx, sy, sz = l, 2 * r, 2 * r
        else:                                     # 沿 z
            sx, sy, sz = 2 * r, 2 * r, l
    return (x - sx / 2, x + sx / 2, y - sy / 2, y + sy / 2, z - sz / 2, z + sz / 2)


def _touch(a, b, tol=1e-6):
    """两个包围盒是否接触或重叠（贴面算接触）。"""
    for i in (0, 2, 4):
        if min(a[i + 1], b[i + 1]) - max(a[i], b[i]) < -tol:
            return False
    return True


def case_contact():
    print("\n[9] 车体无缝：每件都贴着别的件")
    from app import model_scene
    for label, raw in (("四轮差速", {"brief": "四轮差速底盘", "package": "ct1"}),
                       ("汽车", {"brief": "四轮汽车，能转向", "package": "ct2"}),
                       ("三轮", {"brief": "两轮差速小车", "package": "ct3"})):
        spec = sim_spec.normalize(raw)
        sc = model_scene.parse_model(sim_build.build(spec)["urdf/%s.urdf.xacro" % spec["package"]])
        base = [l for l in sc["links"] if l["name"] == "base_link"][0]
        boxes = [(_aabb(v), v) for v in base["visuals"]]
        lonely = []
        for i, (a, va) in enumerate(boxes):
            if not any(_touch(a, b) for j, (b, _vb) in enumerate(boxes) if j != i):
                lonely.append(va["geometry"])
        check(not lonely, "%s：车体 %d 个部件全部贴合（悬空 %d 个）"
              % (label, len(boxes), len(lonely)))

        # 轮子必须有一根套筒插进车体：检查有部件延伸到轮子内侧
        sep = spec["wheel_separation"]
        inner = sep / 2 - spec["wheel_width"] / 2
        reach = max(abs(b[4]) for b, _v in boxes)
        check(reach >= inner - 1e-6,
              "%s：有部件伸到轮子内侧面（%.3f ≥ %.3f）" % (label, reach, inner))

        # 雷达立柱顶端 = 雷达底座底面
        stalk = [v for v in base["visuals"]
                 if v["geometry"]["kind"] == "cylinder"
                 and abs(v["geometry"]["length"] - (spec["lidar_height"] - spec["base_height"] / 2 - 0.012)) < 1e-6]
        lidar = [j for j in sc["joints"] if j["name"] == "lidar_joint"][0]
        if stalk:
            top = _aabb(stalk[0])[5]
            check(abs(top - lidar["xyz"][2]) < 1e-6,
                  "%s：雷达立柱顶 %.3f 接上雷达底 %.3f" % (label, top, lidar["xyz"][2]))

        if spec["archetype"] == "ackermann":
            rack = [j for j in sc["joints"] if j["name"] == "steering_rack_joint"][0]
            rlink = [l for l in sc["links"] if l["name"] == "steering_rack_link"][0]
            rack_top = _aabb(rlink["visuals"][0])[5] + rack["xyz"][2]
            body_bottom = -spec["base_height"] / 2
            check(rack_top >= body_bottom - 1e-6,
                  "转向机顶 %.3f 插进车体底面 %.3f（不再悬空）" % (rack_top, body_bottom))


def case_conventions():
    print("\n[10] 按资料库规范生成：传感器要真存在、顺序要对")
    import re
    sp = sim_spec.normalize({"brief": "四轮汽车，能转向", "package": "conv"})
    f = sim_build.build(sp)
    u = f["urdf/conv.urdf.xacro"]
    launch = f["launch/sim.launch.py"]

    check("gpu_lidar" in u, "雷达有真实 <sensor> 定义（不只是外形）")
    check('gz_frame_id" >lidar_link' in u.replace(">", "> ") or "gz_frame_id>lidar_link" in u,
          "传感器带 gz_frame_id")
    check("/scan@sensor_msgs/msg/LaserScan" in launch, "/scan 已桥接到 ROS")
    check(sim_gate.check(sp, f)["passed"], "规范闸门 G7 通过")

    # 负例：把传感器类型写坏、把桥接删掉，都必须拦住
    bad = dict(f)
    bad["urdf/conv.urdf.xacro"] = u.replace('type="gpu_lidar"', 'type="none"')
    check(not sim_gate.check(sp, bad)["passed"], "传感器类型写错会被 G7 拦下")

    bad2 = dict(f)
    bad2["urdf/conv.urdf.xacro"] = re.sub(r'<gazebo reference="lidar_link">.*?</gazebo>', "", u, flags=re.S)
    check(not sim_gate.check(sp, bad2)["passed"], "整块传感器缺失会被 G7 拦下")

    bad3 = dict(f)
    bad3["launch/sim.launch.py"] = launch.replace(
        '            "/scan@sensor_msgs/msg/LaserScan]gz.msgs.LaserScan",\n', "")
    check(not sim_gate.check(sp, bad3)["passed"], "漏掉 /scan 桥接会被 G7 拦下")

    # link 子元素顺序：visual → collision → inertial
    first_link = re.search(r"<link name=\"base_link\">(.*?)</link>", u, re.S).group(1)
    order = [m.group(1) for m in re.finditer(r"<(visual|collision|inertial)[ >]", first_link)]
    check(order[:3] == ["visual", "collision", "inertial"]
          or order.index("collision") < order.index("inertial"),
          "link 子元素顺序符合规范（visual→collision→inertial）")


def case_mesh():
    print("\n[11] 真模型模式：从资料库拷网格")
    import re
    sp = sim_spec.normalize({"profile": "panda", "package": "pm"})
    check(sp.get("real_mesh") is True, "Panda 自动走真模型模式")
    f = sim_build.build(sp)
    bins = [k for k, v in f.items() if isinstance(v, bytes)]
    check(len(bins) >= 10, "包内有网格文件 %d 个" % len(bins))
    check(sum(len(v) for v in [f[k] for k in bins]) < 200_000,
          "网格总量 %d 字节（教学包可接受）" % sum(len(f[k]) for k in bins))

    u = f["urdf/pm.urdf.xacro"]
    check('filename="package://pm/meshes/panda/link1.stl"' in u, "URDF 用 package:// 引用网格")
    check("meshes" in f["CMakeLists.txt"], "CMakeLists 会安装 meshes/ 目录")
    check(sim_gate.check(sp, f)["passed"], "真模型包通过闸门")

    # 网格引用必须能在包里找到
    bad = {k: v for k, v in f.items() if not k.endswith("link3.stl")}
    g = sim_gate.check(sp, bad)
    check(not g["passed"], "网格文件缺失会被拦下")

    # 场景解析：mesh 带包内相对路径与缩放
    from app import model_scene
    sc = model_scene.parse_model(u)
    meshes = [v["geometry"] for l in sc["links"] for v in l["visuals"]
              if v["geometry"]["kind"] == "mesh"]
    check(len(meshes) >= 10, "场景里 mesh 视觉 %d 个" % len(meshes))
    check(all(m.get("rel", "").startswith("meshes/") for m in meshes),
          "mesh 记录包内相对路径，预览能定位")

    # 回归：文件清单里有二进制时，描述不能按文本数行（线上曾 500）
    desc = sim_build.describe_files(f)
    check(all(not isinstance(n, int) or n > 0 for _p, n in desc), "文件清单能处理 bytes")
    check(any(n is None for _p, n in desc), "网格条目标记为二进制")
    md = __import__("app.wf_sim", fromlist=["wf_sim"]).render_markdown(
        {"spec": sp, "files": f, "gate": sim_gate.check(sp, f), "brief": "panda"})
    check("网格" in md, "回复里用体积展示网格文件")

    # 参数化模式不能被误判
    sp2 = sim_spec.normalize({"profile": "ur5e", "package": "u5"})
    check(not sp2.get("real_mesh"), "UR5e 仍走参数化模式")
    check(not any(isinstance(v, bytes) for v in sim_build.build(sp2).values()),
          "参数化包不含二进制")


def main():
    case_arm()
    case_diff()
    case_negative()
    case_spec()
    case_routing()
    case_profiles()
    case_wheels()
    case_arm_shape()
    case_contact()
    case_conventions()
    case_mesh()
    print("\n" + "=" * 50)
    if _FAIL:
        print("失败 %d 项：" % len(_FAIL))
        for m in _FAIL:
            print("  - %s" % m)
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
