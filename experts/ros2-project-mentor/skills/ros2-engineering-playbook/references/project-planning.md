# ROS2 项目规划参考

## 需求到工程的转换

先提取项目目标、机器人平台、传感器、执行器、环境、输入输出和验收现象。将模块整理为以下逻辑契约；自然语言输入也可以补齐合理默认值，但需列出假设。

```json
{
  "schema_version": "1.0",
  "project": {
    "name_en": "obstacle_avoidance",
    "name_cn": "避障小车",
    "package_name": "obstacle_avoidance",
    "ros_distro": "humble",
    "language": "python",
    "difficulty": "入门",
    "description": "激光雷达检测障碍物并控制差速底盘"
  },
  "modules": [
    {
      "module_name": "obstacle_detector",
      "function": "过滤扫描并估计前方障碍物距离",
      "inputs": [{"topic": "/scan", "message_type": "sensor_msgs/msg/LaserScan", "external": true}],
      "outputs": [{"topic": "/obstacle_distance", "message_type": "std_msgs/msg/Float32"}],
      "dependencies": ["rclpy", "sensor_msgs", "std_msgs"]
    }
  ]
}
```

需求只有方向而没有具体型号、阈值或底盘形式时，采用常见默认值，不因此阻塞。无法判断项目目标或核心功能时才集中追问。

## 节点规格

规划进一步形成节点契约：

- `node_name`：小写蛇形，通常以 `_node` 结尾。
- `class_name`：大驼峰，Python/C++ 通常以 `Node` 结尾。
- `file_name`：与节点职责一致；C++ 可执行目标在 CMake 中显式映射。
- `function` / `core_logic`：接收什么、如何处理、输出什么以及异常状态。
- `publish_topics` / `subscribe_topics`：名称、完整消息类型、方向、QoS、用途和内外部来源。
- 服务/动作：名称、接口类型、服务端/客户端、超时、反馈和取消行为。
- 参数：名称、类型、默认值、单位和允许范围。
- TF：父/子 frame、静态或动态、发布者与时间来源。

## 通信闭环检查

1. 汇总所有输出，逐个检查内部输入是否有生产者。
2. `/scan`、`/odom`、`/imu`、`/clock`、相机、关节状态、`/cmd_vel` 等是否外部提供，应按实际系统判断，不能只靠固定白名单。
3. 同一通信链路的话题/服务/动作名称与接口类型一致，QoS 兼容。
4. 描述文字中出现的接口必须在 inputs/outputs 中声明，不能为通过校验删字段。
5. 检查单位、坐标系、时间戳、频率、启动顺序、无数据和超时行为。

## 规划交付模板

1. 项目目标、假设、ROS2 版本、语言与运行环境。
2. 架构图或箭头数据流。
3. 包、节点和职责清单。
4. 通信接口表及 QoS/TF/参数说明。
5. 依赖与完整目录树。
6. 分阶段实施顺序。
7. 构建、启动、观察命令与量化/可观察验收标准。
8. 风险：传感器异常、定位失败、控制超时、实机安全等。

## 难度与范围

入门项目重在一个清晰通信闭环；进阶项目可包含多传感器、参数化决策和 launch；综合项目再引入 SLAM、Nav2、MoveIt 2、多状态行为或完整 bringup。不得为了形式固定节点数量，也不得把入门需求扩张成复杂分布式系统。
