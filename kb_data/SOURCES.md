# 知识库来源与版本说明

本文件回答评审最关心的三个问题：**这些资料从哪来、是哪个 ROS 版本的、能不能用**。
任何一条主张都能在下表里查到对应文件与来源域名。

- 采集方式：按主题检索官方文档 / 社区问答 / 开源仓库，抽取字段后人工整理为 JSONL
- 采集时间：2026-10（各文件为同一批次导入）
- 目标版本（本系统默认）：**ROS 2 Jazzy Jalisco（Ubuntu 24.04 / Python 3.12）**
- 语种：中文摘要为主，保留英文原文关键字段（API 名、错误串、命令）

## 一、文件清单

| 目录 / 文件 | 条目 | 主要内容 | 主要来源 | 版本标注 |
|---|---|---|---|---|
| `API参考/API参考文档.txt` | 38 | rclpy 常用 API：节点、发布订阅、QoS、定时器、参数 | docs.ros.org | 每条含 `ros_distro: kilted` |
| `msg规范/msg格式.txt` | 37 | 消息/服务/动作定义规范与命名约定 | design.ros2.org | 未逐条标注 |
| `代码模板/ROS2_Python模板合集.txt` | 15 | Python 节点骨架、launch、setup.py | 本系统整理 | 按 Jazzy 编写 |
| `官方代码规范说明/ROS2官方代码规范说明知识库(2).txt` | 65 | REP-2004 等编码与接口规范 | reps.openrobotics.org、docs.ros.org、design.ros2.org | 未逐条标注 |
| `错误/ROS2错误数据集_part1~3.txt` | 1003 | 真实报错现象 + 原因 + 解法 | robotics.stackexchange.com、answers.ros.org、github.com | 未逐条标注 |
| `教学/ROS2真实错误案例库_JSONL(4).txt` | 20 | 教学场景错误案例 | github.com | 未逐条标注 |
| `教学/ROS2真实开源教学案例库_JSONL(2).txt` 等 | 88 | 开源教学仓库案例 | github.com | 未逐条标注 |
| `教学/ROS2教学案例关系索引_JSONL(3).txt` | 63 | 概念—案例关系索引 | 本系统整理 | — |
| `成功/ROS2成功案例_publisher_subscriber教学文档.md` | — | 发布/订阅完整教学案例 | 本系统整理 | — |
| `测评/ROS2教育工作流_评测结果_知识库导入.txt` | 66 | 教学工作流评测记录 | 内部评测 | — |
| `项目/项目.txt` | 12 | 项目级示例 | 本系统整理 | — |

## 二、版本口径（重要）

1. **知识库不是单一版本**。`API参考` 的 38 条取自 docs.ros.org 的 **Kilted** 文档
   （Kilted Kaiju，2025-05 发布），其余目录未逐条标注发行版。
2. **本系统生成与校验的目标版本是 Jazzy**（`config.json` → `ros.distro`）。
   rclpy 的节点/发布订阅/QoS/定时器等核心接口在 Humble → Jazzy → Kilted 之间保持稳定，
   因此这些条目可直接参考；但若涉及新增特性或已废弃接口，以目标版本为准。
3. **系统如何防止混版本**：
   - 检索结果前面会带 `【来源版本】ROS 2 kilted` 标注（`app/kb.py`）；
   - 系统提示词里含「目标版本硬约束」块（`app/versions.py`），要求冲突时按目标版本作答；
   - 代码生成的最终口径由编译沙箱决定——真的在 `ros:jazzy-ros-base` 里
     `colcon build` 通过才算过。
4. 若把目标版本切到 Humble，只需改 `config.json` 的 `ros.distro`，
   提示词、沙箱镜像、界面徽标会同步变化（见 `app/versions.py`）。

## 三、授权与使用边界

| 来源 | 授权情况 | 本知识库的使用方式 |
|---|---|---|
| docs.ros.org / design.ros2.org / reps.openrobotics.org | ROS 文档整体采用 CC BY 4.0（引用需署名） | 摘录 API 名、参数、要点，改写为中文摘要，保留 `source_url` |
| robotics.stackexchange.com / answers.ros.org | CC BY-SA 4.0 | 摘录问题现象与解法要点，保留原链接 |
| github.com 各仓库 | 遵循各仓库自身 LICENSE | 仅记录仓库路径与案例描述，未整体复制源码 |
| 本系统整理部分 | 随本项目 | — |

> 待逐条核对项：`msg规范`、`官方代码规范说明`、`错误` 三个目录未逐条写入
> `source_url` 与授权字段。若需对外正式发布，建议补齐每条记录的来源链接与许可标记。
> 已在上表如实标注，未做美化。

## 四、如何核查

- 任意一条：在对应 JSONL 文件里搜 `api_id` / `rule_id`，`source_url` 字段即原文地址；
- 版本：搜 `ros_distro` 字段；
- 系统实际加载了多少：启动日志或 `GET /api/meta` 的 `kb.docs` / `kb.files`。
