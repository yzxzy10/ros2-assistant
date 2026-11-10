# ROS2规划与教学专家

面向 ROS2 Humble / Jazzy 的单 Agent 专家，覆盖项目规划、C++/Python 功能包生成、消息通信、机器人节点开发、仿真排障与项目式教学。

## 类型

Agent 型（单个 AI 专家），分类为技术工程（02-Engineering）。

## 功能

- 将自然语言需求整理为包、节点、话题/服务/动作、TF、参数与验收方案。
- 生成完整 rclcpp/rclpy 工程、自定义接口、构建配置、launch 与运行步骤。
- 支持机械臂、移动机器人感知、导航、控制及 Gazebo 仿真问题排查。
- 依据日志和计算图证据给出诊断命令、最小修复和复验方法。
- 按学习层次讲解知识点、易错点和拓展练习。

附带 `ros2-engineering-playbook` 技能、三份工程参考规范及 rclpy 静态检查脚本。检查脚本不能替代 colcon 构建、运行测试或实机验收。

## 使用示例

- 帮我规划一个 ROS2 Humble 项目：设计功能包结构、节点、话题和消息类型，并给出完整可运行代码与验证步骤。
- 生成一个使用自定义消息的 ROS2 C++ 发布订阅功能包，包含接口、构建文件、launch 和测试。
- 我的 ROS2 仿真出错了。请根据日志定位原因，给出诊断命令和最小修复，并讲清相关知识点。

## 头像

头像已生成在 `avatars/expert.png`。如需替换，请使用 512×512 的 PNG/JPG，文件不超过 500KB。

## 安装

专家目录：

```text
~/.workbuddy/plugins/marketplaces/my-experts/plugins/ros2-project-mentor/
```

使用 expert-manager 的校验和注册脚本完成安装；不要直接编辑 marketplace.json。

## 打包分享

```bash
python scripts/package_expert.py <expert-dir>
```
