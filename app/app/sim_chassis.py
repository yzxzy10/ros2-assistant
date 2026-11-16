# -*- coding: utf-8 -*-
"""底盘生成：车体、轮子、转向机构，差速与阿克曼共用一套结构。

一条硬规则：**不留缝**。
每个附件的位置都用「贴着谁」来算，而不是拍一个坐标：
  顶板底面 = 车体顶面；保险杠内端 = 车体端面；侧裙内面 = 车体侧面；
  雷达立柱底 = 顶板面；轮毂套从轮心一路插进车体侧面。
这样即使改 xacro 顶部的尺寸属性，件与件之间也不会裂开。

表达式一律写成单个 `${...}` 记号（内部不出现裸空格），
因为坐标是按空格切分的，`${a - b}` 这种一旦被拆开就会退化成 (0,0,0)。
"""
# ---------- 复用的部件模板 ----------

_WHEEL_LINK = """  <link name="%(name)s">
    <visual>
      <origin xyz="0 0 0" rpy="${pi/2} 0 0"/>
      <geometry><cylinder radius="${wheel_radius}" length="${wheel_width}"/></geometry>
      <material name="rubber"/>
    </visual>
    <visual>
      <origin xyz="0 0 0" rpy="${pi/2} 0 0"/>
      <geometry><cylinder radius="${wheel_radius*0.74}" length="${wheel_width*1.06}"/></geometry>
      <material name="plate"/>
    </visual>
    <visual>
      <origin xyz="0 0 0" rpy="0 0 0"/>
      <geometry><box size="${wheel_radius*1.46} ${wheel_width*1.02} ${wheel_radius*0.13}"/></geometry>
      <material name="spoke"/>
    </visual>
    <visual>
      <origin xyz="0 0 0" rpy="0 ${pi/2} 0"/>
      <geometry><box size="${wheel_radius*1.46} ${wheel_width*1.02} ${wheel_radius*0.13}"/></geometry>
      <material name="spoke"/>
    </visual>
    <visual>
      <origin xyz="${wheel_radius*0.60} 0 0" rpy="0 0 0"/>
      <geometry><box size="${wheel_radius*0.26} ${wheel_width*1.06} ${wheel_radius*0.26}"/></geometry>
      <material name="hub"/>
    </visual>
    <collision>
      <origin xyz="0 0 0" rpy="${pi/2} 0 0"/>
      <geometry><cylinder radius="${wheel_radius}" length="${wheel_width}"/></geometry>
    </collision>
    <inertial>
      <origin xyz="0 0 0" rpy="0 0 0"/>
      <mass value="${wheel_mass}"/>
      <inertia ixx="${wheel_mass*(3*wheel_radius**2+wheel_width**2)/12}" ixy="0" ixz="0"
               iyy="${wheel_mass*wheel_radius**2/2}" iyz="0"
               izz="${wheel_mass*(3*wheel_radius**2+wheel_width**2)/12}"/>
    </inertial>
  </link>

"""

_WHEEL_JOINT = """  <joint name="%(joint)s" type="continuous">
    <parent link="%(parent)s"/>
    <child link="%(child)s"/>
    <origin xyz="%(xyz)s" rpy="0 0 0"/>
    <axis xyz="0 1 0"/>
    <dynamics damping="%(damping)s" friction="0.0"/>
  </joint>

"""

_STEER_LINK = """  <link name="%(name)s">
    <visual>
      <origin xyz="0 0 0" rpy="0 0 0"/>
      <geometry><cylinder radius="${wheel_radius*0.40}" length="${base_height*0.50}"/></geometry>
      <material name="joint"/>
    </visual>
    <visual>
      <origin xyz="0 ${%(inward)s*wheel_separation*0.075} 0" rpy="0 0 0"/>
      <geometry><box size="${wheel_width*0.62} ${wheel_separation*0.20} ${base_height*0.13}"/></geometry>
      <material name="spoke"/>
    </visual>
    <visual>
      <origin xyz="0 ${%(inward)s*wheel_separation*0.185} ${-base_height*0.06}" rpy="${pi/2} 0 0"/>
      <geometry><cylinder radius="${wheel_width*0.15}" length="${wheel_separation*0.125}"/></geometry>
      <material name="hub"/>
    </visual>
    <inertial>
      <origin xyz="0 ${%(inward)s*wheel_separation*0.09} 0" rpy="0 0 0"/>
      <mass value="0.14"/>
      <inertia ixx="1.4e-05" ixy="0" ixz="0" iyy="1.4e-05" iyz="0" izz="1.8e-05"/>
    </inertial>
  </link>

"""

_STEER_JOINT = """  <joint name="%(joint)s" type="revolute">
    <parent link="base_link"/>
    <child link="%(child)s"/>
    <origin xyz="%(xyz)s" rpy="0 0 0"/>
    <axis xyz="0 0 1"/>
    <limit lower="${-steer_limit}" upper="${steer_limit}" effort="50" velocity="2.0"/>
    <dynamics damping="0.05" friction="0.0"/>
  </joint>

"""

_CASTER = """  <link name="caster_link">
    <visual>
      <origin xyz="0 0 0" rpy="0 0 0"/>
      <geometry><sphere radius="${caster_radius}"/></geometry>
      <material name="rubber"/>
    </visual>
    <visual>
      <origin xyz="0 0 ${caster_radius*0.9}" rpy="0 0 0"/>
      <geometry><box size="${caster_radius*2.2} ${caster_radius*2.2} ${caster_radius*1.6}"/></geometry>
      <material name="plate"/>
    </visual>
    <collision>
      <origin xyz="0 0 0" rpy="0 0 0"/>
      <geometry><sphere radius="${caster_radius}"/></geometry>
    </collision>
    <inertial>
      <origin xyz="0 0 0" rpy="0 0 0"/>
      <mass value="0.05"/>
      <inertia ixx="2e-06" ixy="0" ixz="0" iyy="2e-06" iyz="0" izz="2e-06"/>
    </inertial>
  </link>

  <joint name="caster_joint" type="fixed">
    <parent link="base_link"/>
    <child link="caster_link"/>
    <origin xyz="${-base_length*0.34} 0 ${-base_height/2-wheel_radius+caster_radius}" rpy="0 0 0"/>
  </joint>

"""


# ---------- 小工具：拼 visual ----------

def _vis_box(sx, sy, sz, x, y, z, mat, rpy=""):
    r = ' rpy="%s"' % rpy if rpy else ""
    return ('    <visual>\n'
            '      <origin xyz="%s %s %s"%s/>\n'
            '      <geometry><box size="%s %s %s"/></geometry>\n'
            '      <material name="%s"/>\n'
            '    </visual>\n' % (x, y, z, r, sx, sy, sz, mat))


def _vis_cyl(r_, l, x, y, z, mat, rpy=""):
    rr = ' rpy="%s"' % rpy if rpy else ""
    return ('    <visual>\n'
            '      <origin xyz="%s %s %s"%s/>\n'
            '      <geometry><cylinder radius="%s" length="%s"/></geometry>\n'
            '      <material name="%s"/>\n'
            '    </visual>\n' % (x, y, z, rr, r_, l, mat))


def _vis_sph(r_, x, y, z, mat):
    return ('    <visual>\n'
            '      <origin xyz="%s %s %s" rpy="0 0 0"/>\n'
            '      <geometry><sphere radius="%s"/></geometry>\n'
            '      <material name="%s"/>\n'
            '    </visual>\n' % (x, y, z, r_, mat))


# ---------- 轮子布局 ----------

def wheel_plan(spec):
    """算出每个轮子放在哪、连到谁。返回装配需要的全部信息。"""
    L = spec["base_length"]
    H = spec["base_height"]
    sep = spec["wheel_separation"]
    ax = spec["wheel_offset_x"]
    z = "-base_height/2"
    links, drive, names, items = [], [], [], []

    if spec["archetype"] == "ackermann":
        half_wb = spec["wheelbase"] / 2.0
        for side, sign in (("left", 1), ("right", -1)):
            y = sign * sep / 2.0
            steer = "front_%s_steer_link" % side
            wheel = "front_%s_wheel_link" % side
            links.append(_STEER_LINK % {"name": steer, "inward": -sign})
            links.append(_STEER_JOINT % {
                "joint": "front_%s_steer_joint" % side, "child": steer,
                "xyz": "%s %s %s" % (round(half_wb, 4), round(y, 4), z)})
            links.append(_WHEEL_LINK % {"name": wheel})
            links.append(_WHEEL_JOINT % {
                "joint": "front_%s_wheel_joint" % side, "parent": steer, "child": wheel,
                "xyz": "0 0 0", "damping": "0.01"})
            names += ["front_%s_steer_joint" % side, "front_%s_wheel_joint" % side]
            items.append({"x": round(half_wb, 4), "y": round(y, 4), "side": side})

        for side, sign in (("left", 1), ("right", -1)):
            y = sign * sep / 2.0
            wheel = "rear_%s_wheel_link" % side
            links.append(_WHEEL_LINK % {"name": wheel})
            links.append(_WHEEL_JOINT % {
                "joint": "rear_%s_wheel_joint" % side, "parent": "base_link",
                "child": wheel,
                "xyz": "%s %s %s" % (round(-half_wb, 4), round(y, 4), z),
                "damping": "0.01"})
            drive.append("rear_%s_wheel_joint" % side)
            names.append("rear_%s_wheel_joint" % side)
            items.append({"x": round(-half_wb, 4), "y": round(y, 4), "side": side})

        refs = "".join(
            '  <gazebo reference="%s"><mu1>1.2</mu1><mu2>1.2</mu2></gazebo>\n' % n
            for n in ("front_left_wheel_link", "front_right_wheel_link",
                      "rear_left_wheel_link", "rear_right_wheel_link"))
        refs += '  <gazebo reference="base_link"><material>Gazebo/Blue</material></gazebo>\n'
        return {"xml": "".join(links), "drive": drive, "joints": names,
                "refs": refs, "items": items}

    wheels = spec.get("wheels", 4)
    drive_x = ax - L * 0.30 if wheels >= 4 else ax
    for side, sign in (("left", 1), ("right", -1)):
        y = sign * sep / 2.0
        wheel = "%s_wheel_link" % side
        links.append(_WHEEL_LINK % {"name": wheel})
        links.append(_WHEEL_JOINT % {
            "joint": "%s_wheel_joint" % side, "parent": "base_link", "child": wheel,
            "xyz": "%s %s %s" % (round(drive_x, 4), round(y, 4), z), "damping": "0.02"})
        drive.append("%s_wheel_joint" % side)
        names.append("%s_wheel_joint" % side)
        items.append({"x": round(drive_x, 4), "y": round(y, 4), "side": side})

    if wheels >= 4:
        for side, sign in (("left", 1), ("right", -1)):
            y = sign * sep / 2.0
            wheel = "front_%s_wheel_link" % side
            links.append(_WHEEL_LINK % {"name": wheel})
            links.append(_WHEEL_JOINT % {
                "joint": "front_%s_wheel_joint" % side, "parent": "base_link",
                "child": wheel,
                "xyz": "%s %s %s" % (round(ax + L * 0.30, 4), round(y, 4), z),
                "damping": "0.01"})
            names.append("front_%s_wheel_joint" % side)
            items.append({"x": round(ax + L * 0.30, 4), "y": round(y, 4), "side": side})
        refs = "".join(
            '  <gazebo reference="%s"><mu1>0.4</mu1><mu2>0.4</mu2></gazebo>\n' % n
            for n in ("front_left_wheel_link", "front_right_wheel_link"))
    else:
        links.append(_CASTER)
        items.append({"x": round(-L * 0.34, 4), "y": 0.0, "side": "caster"})
        refs = '  <gazebo reference="caster_link"><mu1>0.05</mu1><mu2>0.05</mu2></gazebo>\n'

    for side in ("left", "right"):
        refs += ('  <gazebo reference="%s_wheel_link"><mu1>1.2</mu1><mu2>1.2</mu2></gazebo>\n'
                 % side)
    refs += '  <gazebo reference="base_link"><material>Gazebo/Blue</material></gazebo>\n'
    return {"xml": "".join(links), "drive": drive, "joints": names,
            "refs": refs, "items": items}


# ---------- 车体 ----------

def _base_link_xml(spec, items):
    """车体：一整块箱体 + 贴着它的顶板/保险杠/侧裙/传感器/悬挂座。"""
    L = "${base_length}"
    W = "${base_width}"
    H = "${base_height}"
    out = ['  <link name="base_link">\n']

    # 主箱体
    out.append(_vis_box(L, W, H, "0", "0", "0", "chassis"))

    # 顶板：底面正好压在箱体顶面
    out.append(_vis_box("${base_length*0.94}", "${base_width*0.94}", "0.012",
                        "0", "0", "${base_height/2+0.006}", "plate"))

    # 前后保险杠：内端与箱体端面重合
    for x_expr in ("${base_length/2+0.020}", "${-base_length/2-0.020}"):
        out.append(_vis_box("0.040", "${base_width*0.90}", "${base_height*0.55}",
                            x_expr, "0", "0", "sensor"))

    # 左右侧裙：内面与箱体侧面重合
    for y_expr in ("${base_width/2+0.007}", "${-base_width/2-0.007}"):
        out.append(_vis_box("${base_length*0.80}", "0.014", "${base_height*0.45}",
                            "0", y_expr, "0", "sensor"))

    # 相机：支架坐在顶板上，镜头从支架前脸伸出去
    cam_z = "${base_height/2+0.012+0.011}"
    out.append(_vis_box("0.036", "0.050", "0.022",
                        "${base_length/2-0.050}", "0", cam_z, "sensor"))
    out.append(_vis_cyl("0.011", "0.020",
                        "${base_length/2-0.032}", "0", cam_z, "joint",
                        rpy="0 ${pi/2} 0"))

    # 天线：杆底压在顶板上，顶端一个球
    ant_x = "${-base_length/2+0.045}"
    ant_y = "${base_width/2-0.030}"
    out.append(_vis_cyl("0.0035", "0.090", ant_x, ant_y,
                        "${base_height/2+0.012+0.045}", "joint"))
    out.append(_vis_sph("0.008", ant_x, ant_y,
                        "${base_height/2+0.012+0.090}", "hub"))

    # 雷达立柱：从顶板面顶到雷达底座，中间不留缝
    out.append(_vis_cyl("0.016", "${lidar_height-base_height/2-0.012}",
                        "${lidar_x}", "0",
                        "${base_height/2+0.012+(lidar_height-base_height/2-0.012)/2}",
                        "plate"))

    # 悬挂座 + 轮毂套：每个轮子一套，把轮子接到车体侧面
    for it in items:
        if it.get("side") == "caster":
            continue
        up = it["y"] > 0
        out.append(_vis_box("0.060", "0.030", "0.034", str(it["x"]),
                            "${base_width/2+0.010}" if up else "${-base_width/2-0.010}",
                            "${-base_height/2+0.010}", "joint"))
        out.append(_vis_cyl("${wheel_width*0.46}",
                            "${wheel_separation/2-wheel_width*0.10}", str(it["x"]),
                            ("${wheel_separation/4+wheel_width*0.05}" if up
                             else "${-wheel_separation/4-wheel_width*0.05}"),
                            "${-base_height/2}", "hub", rpy="${pi/2} 0 0"))

    # 碰撞体：和主箱体外廓一致
    out.append('    <collision>\n'
               '      <origin xyz="0 0 0" rpy="0 0 0"/>\n'
               '      <geometry><box size="${base_length} ${base_width} ${base_height}"/></geometry>\n'
               '    </collision>\n')
    out.append('''    <inertial>
      <origin xyz="0 0 0" rpy="0 0 0"/>
      <mass value="${base_mass}"/>
      <inertia ixx="${base_mass*(base_width**2+base_height**2)/12}" ixy="0" ixz="0"
               iyy="${base_mass*(base_length**2+base_height**2)/12}" iyz="0"
               izz="${base_mass*(base_length**2+base_width**2)/12}"/>
    </inertial>
''')
    out.append('  </link>\n\n')
    return "".join(out)


def _lidar_xml(spec):
    """雷达：底座直接贴立柱顶端。"""
    return '''  <link name="lidar_link">
    <visual>
      <origin xyz="0 0 0.014" rpy="0 0 0"/>
      <geometry><cylinder radius="0.021" length="0.028"/></geometry>
      <material name="plate"/>
    </visual>
    <visual>
      <origin xyz="0 0 0.048" rpy="0 0 0"/>
      <geometry><cylinder radius="0.035" length="0.040"/></geometry>
      <material name="sensor"/>
    </visual>
    <visual>
      <origin xyz="0 0 0.072" rpy="0 0 0"/>
      <geometry><cylinder radius="0.026" length="0.010"/></geometry>
      <material name="hub"/>
    </visual>
    <inertial>
      <origin xyz="0 0 0.040" rpy="0 0 0"/>
      <mass value="${lidar_mass}"/>
      <inertia ixx="${lidar_mass*(3*0.035**2+0.04**2)/12}" ixy="0" ixz="0"
               iyy="${lidar_mass*(3*0.035**2+0.04**2)/12}" iyz="0"
               izz="${lidar_mass*0.035**2/2}"/>
    </inertial>
  </link>

  <joint name="lidar_joint" type="fixed">
    <parent link="base_link"/>
    <child link="lidar_link"/>
    <origin xyz="${lidar_x} 0 ${lidar_height}" rpy="0 0 0"/>
  </joint>

  <!-- 传感器要写在 <gazebo reference> 里才算真存在：只挂 sensors 插件的话，
       模型有个雷达的外形，但 /scan 永远没有数据。写法对标 andino_gz。 -->
  <gazebo reference="lidar_link">
    <sensor name="lidar" type="gpu_lidar">
      <gz_frame_id>lidar_link</gz_frame_id>
      <ray>
        <scan>
          <horizontal>
            <samples>360</samples>
            <resolution>1</resolution>
            <min_angle>-3.14159265</min_angle>
            <max_angle>3.14159265</max_angle>
          </horizontal>
        </scan>
        <range>
          <min>0.12</min>
          <max>3.50</max>
          <resolution>0.01</resolution>
        </range>
        <noise>
          <type>gaussian</type>
          <mean>0.0</mean>
          <stddev>0.01</stddev>
        </noise>
      </ray>
      <always_on>true</always_on>
      <visualize>true</visualize>
      <update_rate>10.0</update_rate>
    </sensor>
  </gazebo>

'''


def _rack_xml(spec):
    """转向机 + 齿条：壳体向上插进车体，车底不允许有悬空件。"""
    return '''  <link name="steering_rack_link">
    <visual>
      <origin xyz="0 0 0" rpy="0 0 0"/>
      <geometry><box size="${base_height*0.34} ${wheel_separation*0.60} ${base_height*0.30}"/></geometry>
      <material name="joint"/>
    </visual>
    <visual>
      <origin xyz="0 0 0" rpy="${pi/2} 0 0"/>
      <geometry><cylinder radius="${wheel_width*0.16}" length="${wheel_separation*0.86}"/></geometry>
      <material name="spoke"/>
    </visual>
    <visual>
      <origin xyz="0 ${wheel_separation*0.43} 0" rpy="0 0 0"/>
      <geometry><sphere radius="${wheel_width*0.24}"/></geometry>
      <material name="hub"/>
    </visual>
    <visual>
      <origin xyz="0 ${-wheel_separation*0.43} 0" rpy="0 0 0"/>
      <geometry><sphere radius="${wheel_width*0.24}"/></geometry>
      <material name="hub"/>
    </visual>
    <inertial>
      <origin xyz="0 0 0" rpy="0 0 0"/>
      <mass value="0.45"/>
      <inertia ixx="4.0e-05" ixy="0" ixz="0" iyy="4.0e-05" iyz="0" izz="4.5e-05"/>
    </inertial>
  </link>

  <joint name="steering_rack_joint" type="fixed">
    <parent link="base_link"/>
    <child link="steering_rack_link"/>
    <origin xyz="${wheelbase/2} 0 ${-base_height/2-0.004}" rpy="0 0 0"/>
  </joint>

'''


_JS_PLUGIN = """    <plugin filename="gz-sim-joint-state-publisher-system"
            name="gz::sim::systems::JointStatePublisher">
      <topic>/joint_states</topic>
%s    </plugin>"""


def _joint_state_plugin(lay):
    names = "".join("      <joint_name>%s</joint_name>\n" % n for n in lay["joints"])
    return _JS_PLUGIN % names


def diff_plugin(lay):
    return """  <gazebo>
    <plugin filename="gz-sim-diff-drive-system" name="gz::sim::systems::DiffDrive">
      <left_joint>%s</left_joint>
      <right_joint>%s</right_joint>
      <wheel_separation>${wheel_separation}</wheel_separation>
      <wheel_radius>${wheel_radius}</wheel_radius>
      <topic>cmd_vel</topic>
      <odom_topic>odom</odom_topic>
      <tf_topic>tf</tf_topic>
      <frame_id>odom</frame_id>
      <child_frame_id>base_link</child_frame_id>
      <odom_publish_frequency>50</odom_publish_frequency>
      <max_linear_acceleration>1.0</max_linear_acceleration>
    </plugin>
%s
  </gazebo>

%s""" % (lay["drive"][0], lay["drive"][1], _joint_state_plugin(lay), lay["refs"])


def ackermann_plugin(lay):
    return """  <gazebo>
    <plugin filename="gz-sim-ackermann-steering-system"
            name="gz::sim::systems::AckermannSteering">
      <left_joint>%s</left_joint>
      <right_joint>%s</right_joint>
      <left_steering_joint>front_left_steer_joint</left_steering_joint>
      <right_steering_joint>front_right_steer_joint</right_steering_joint>
      <wheel_base>${wheelbase}</wheel_base>
      <wheel_separation>${wheel_separation}</wheel_separation>
      <wheel_radius>${wheel_radius}</wheel_radius>
      <steering_limit>${steer_limit}</steering_limit>
      <topic>cmd_vel</topic>
      <odom_topic>odom</odom_topic>
      <tf_topic>tf</tf_topic>
      <frame_id>odom</frame_id>
      <child_frame_id>base_link</child_frame_id>
      <max_linear_acceleration>1.0</max_linear_acceleration>
    </plugin>
%s
  </gazebo>

%s""" % (lay["drive"][0], lay["drive"][1], _joint_state_plugin(lay), lay["refs"])


_HEAD = """<?xml version="1.0"?>
<robot name="%s" xmlns:xacro="http://www.ros.org/wiki/xacro">

  <xacro:property name="base_length" value="%s"/>
  <xacro:property name="base_width" value="%s"/>
  <xacro:property name="base_height" value="%s"/>
  <xacro:property name="base_mass" value="%s"/>
  <xacro:property name="wheel_radius" value="%s"/>
  <xacro:property name="wheel_width" value="%s"/>
  <xacro:property name="wheel_separation" value="%s"/>
  <xacro:property name="wheel_mass" value="%s"/>
  <xacro:property name="wheel_offset_x" value="%s"/>
  <xacro:property name="caster_radius" value="%s"/>
  <xacro:property name="lidar_height" value="%s"/>
  <xacro:property name="lidar_x" value="%s"/>
  <xacro:property name="lidar_mass" value="%s"/>
"""

_MATS = """
  <material name="chassis"><color rgba="0.16 0.42 0.68 1.0"/></material>
  <material name="plate"><color rgba="0.82 0.84 0.88 1.0"/></material>
  <material name="rubber"><color rgba="0.10 0.10 0.11 1.0"/></material>
  <material name="hub"><color rgba="0.92 0.62 0.18 1.0"/></material>
  <material name="sensor"><color rgba="0.22 0.24 0.28 1.0"/></material>
  <material name="spoke"><color rgba="0.96 0.96 0.98 1.0"/></material>
  <material name="joint"><color rgba="0.28 0.30 0.34 1.0"/></material>

  <link name="base_footprint"/>

"""


def build_chassis_urdf(spec):
    """整份底盘 URDF。所有附件位置都由「贴着谁」推导，保证无缝隙。"""
    lay = wheel_plan(spec)
    out = [_HEAD % (spec["package"], spec["base_length"], spec["base_width"],
                    spec["base_height"], spec["base_mass"],
                    spec["wheel_radius"], spec["wheel_width"],
                    spec["wheel_separation"], spec["wheel_mass"],
                    spec["wheel_offset_x"], spec["caster_radius"],
                    spec["lidar_height"], spec["lidar_x"], spec["lidar_mass"])]
    if spec["archetype"] == "ackermann":
        out.append('  <xacro:property name="wheelbase" value="%s"/>\n'
                   '  <xacro:property name="steer_limit" value="%s"/>\n'
                   % (spec["wheelbase"], spec["steer_limit"]))
    out.append(_MATS)
    out.append(_base_link_xml(spec, lay["items"]))
    out.append('''  <joint name="base_joint" type="fixed">
    <parent link="base_footprint"/>
    <child link="base_link"/>
    <origin xyz="0 0 ${wheel_radius+base_height/2}" rpy="0 0 0"/>
  </joint>

''')
    out.append(lay["xml"])
    out.append(_lidar_xml(spec))
    if spec["archetype"] == "ackermann":
        out.append(_rack_xml(spec))
    out.append(ackermann_plugin(lay) if spec["archetype"] == "ackermann"
               else diff_plugin(lay))
    out.append("</robot>\n")
    return "".join(out)
