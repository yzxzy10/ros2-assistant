"""链路验证用到的消息伪造模板与命令行工具函数。"""
import re

_SNAKE = re.compile(r"[^0-9a-zA-Z_]+")


# 常用消息的伪造数据模板；没命中就退回 '{}'（默认值消息）
_TEMPLATES = {
    "sensor_msgs/msg/LaserScan":
        "{header: {frame_id: 'laser'}, range_min: 0.1, range_max: 30.0, "
        "ranges: [1.5, 1.4, 1.4, 1.3, 1.1, 0.9, 1.2, 1.5, 1.6, 2.0, 2.2]}",
    "sensor_msgs/msg/Image":
        "{header: {frame_id: 'cam'}, height: 8, width: 8, encoding: 'mono8', "
        "is_bigendian: 0, step: 8, data: [0, 0, 0, 0, 0, 0, 0, 0]}",
    "sensor_msgs/msg/Imu":
        "{header: {frame_id: 'imu'}, orientation: {w: 1.0}, "
        "angular_velocity: {x: 0.0, y: 0.0, z: 0.1}, linear_acceleration: {z: 9.8}}",
    "sensor_msgs/msg/Range":
        "{header: {frame_id: 'sonar'}, radiation_type: 0, field_of_view: 0.5, "
        "min_range: 0.02, max_range: 2.0, range: 1.2}",
    "sensor_msgs/msg/JointState":
        "{header: {frame_id: 'base'}, name: ['j1'], position: [0.1], velocity: [0.0]}",
    # 下位机回传类：注入这两个话题就是在「假装下位机在回传」。
    # 给的是有业务含义的值（电量 18%、实测 0.05 m/s），空模板全 0 会让
    # 用户的限速/闭环逻辑一直走最低档，看不出反馈到底有没有被用上。
    "sensor_msgs/msg/BatteryState":
        "{percentage: 0.18, voltage: 11.6, current: -1.4, temperature: 36.0, "
        "present: true}",
    "nav_msgs/msg/Odometry":
        "{header: {frame_id: 'odom'}, pose: {pose: {position: {x: 1.2, y: 0.0}}}, "
        "twist: {twist: {linear: {x: 0.05}}}}",
    "geometry_msgs/msg/Twist":
        "{linear: {x: 0.2, y: 0.0, z: 0.0}, angular: {z: 0.3}}",
    "geometry_msgs/msg/TwistStamped":
        "{header: {frame_id: 'base'}, twist: {linear: {x: 0.2}, angular: {z: 0.3}}}",
    "geometry_msgs/msg/PointStamped":
        "{header: {frame_id: 'map'}, point: {x: 1.0, y: 2.0, z: 0.0}}",
    "std_msgs/msg/String": "{data: 'ai-inject'}",
    "std_msgs/msg/Int32": "{data: 42}",
    "std_msgs/msg/Float32": "{data: 3.14}",
    "std_msgs/msg/Bool": "{data: true}",
}


def _fake_value(mtype):
    """给 ros2 topic pub 用的 YAML 值；未知类型给 '{}'（各字段默认值）。"""
    t = str(mtype or "").strip()
    if t in _TEMPLATES:
        return _TEMPLATES[t]
    tail = t.rsplit("/", 1)[-1].lower() if t else ""
    if "laser" in tail:
        return _TEMPLATES["sensor_msgs/msg/LaserScan"]
    if "image" in tail or "camera" in tail:
        return _TEMPLATES["sensor_msgs/msg/Image"]
    if "imu" in tail:
        return _TEMPLATES["sensor_msgs/msg/Imu"]
    if "twist" in tail or "cmd" in tail:
        return _TEMPLATES["geometry_msgs/msg/Twist"]
    return "{}"


def _snake(name):
    s = _SNAKE.sub("_", str(name or "node")).strip("_").lower()
    return s or "node"


def _slug(s):
    return _SNAKE.sub("_", str(s)).strip("_") or "x"


def _sh_quote(s):
    return "'" + str(s).replace("'", "'\\''") + "'"
