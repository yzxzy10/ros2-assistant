# ROS2学习主调度员

面向 ROS2 Humble、Python 初学者的单 Agent 应用。主 Agent 直接负责概念讲解、报错排查、练习和学习路线，并按既定状态机调度需求分析、项目规划和单节点代码生成工作流。

## 类型与分类

- 类型：Agent 型（单个 AI 专家）
- 分类：技术工程（`02-Engineering`）
- 主 Agent：`ros2-learning-orchestrator`

## 应用组成

- `agents/ros2-learning-orchestrator.md`：完整主提示词，含路由、项目流水线、状态追踪、文件纪律和失败兜底。
- `skills/ros2-workflow-orchestration/`：三个工作流的输入输出契约与调用纪律。
- `workflows/requirements/template.yml`：需求分析工作流。
- `workflows/planning/template.yml`：项目规划工作流，应用内名称已统一为 `ros2项目规划`。
- `workflows/codegen/template.yml`：单节点代码生成工作流，含代码检查与教学信封。
- `avatars/expert.png`：应用头像。

## 工作流绑定

在支持工作流工具的 Agent 平台中，分别导入三份 `template.yml`，并将工具名逐字绑定为：

1. `需求分析`
2. `ros2项目规划`
3. `ros2代码生成`

YAML 是平台工作流定义，不是本地可执行脚本。专家包本身负责保存主提示词、契约和模板；若当前运行时未挂载这些同名工作流工具，主 Agent 会严格执行用户提示词中的一次重试与显式兜底标注，不会伪装成工作流产出。

当前副本已完成以下适配：规划输出直接提供节点数组和完整规划对象；节点规格保留 `core_logic`；代码生成修复循环读取最新轮次代码和检查结果；代码检查增加入口保护；需求分析支持常见外部传感器话题并校验消息类型与包名；规划阶段只在收到 A 后开放 C/D。

## 使用示例

- 帮我用 ROS2 Humble 和 Python 做一个激光雷达避障小车项目。
- 用 ROS2 Humble 和 Python 写一个发布者节点，并讲讲关键代码。
- 我的 ROS2 Python 节点报 ImportError 了，帮我定位原因并给出修复步骤。

## 安装与分享

专家必须位于 WorkBuddy 专家目录中，并使用 expert-manager 的校验、注册脚本完成安装。不要直接编辑 `marketplace.json`。

打包时使用 expert-manager 的 `package_expert.py`，生成包含主 Agent、技能、工作流模板和头像的 ZIP 分享包。

头像可手动替换为 512×512、不超过 500KB 的 PNG/JPG。
