# ROS2 智能体开发与教学项目

面向 ROS 2 的智能体开发与教学工具集，覆盖 **Ubuntu 24.04 + ROS2 Jazzy + Gazebo Harmonic**。
仓库包含两个可直接安装的 WorkBuddy 专家包，以及一个开箱即用的 ROS2 学习助手网站。

网站除了生成节点代码，还能**一句话生成能在 Gazebo 里跑起来的仿真模型**（差速底盘 / 阿克曼转向车 /
六轴机械臂），并在网页里给出可交互的三维预览。站点当前版本 **v1.11.0**。

## 项目组成

| 组件 | 目录 | 面向 | 说明 |
|---|---|---|---|
| ROS2规划与教学专家 | [`experts/ros2-project-mentor/`](experts/ros2-project-mentor/) | 开发者 | 项目规划、C++/Python 功能包生成、消息通信、机械臂/导航/Gazebo 仿真排障、分层教学 |
| ROS2学习主调度员 | [`experts/ros2-learning-orchestrator/`](experts/ros2-learning-orchestrator/) | 学习者 | 概念讲解、报错排查、练习与学习路线，按状态机调度「需求分析 / 项目规划 / 代码生成」三个工作流 |
| ROS2 学习助手网站 | [`app/`](app/) | 全体 | 三个工作流 + ROS2 知识库 + **仿真建模 + 三维预览**，打包成聊天网站（ROS2 Jazzy / Python 教学） |

## 能力亮点（v1.11.0）

| 能力 | 说明 |
|---|---|
| 仿真建模 | 「做一个四轮汽车，能像真车一样转向」→ 出 Gazebo 仿真包（URDF + launch + world + 控制器），六道静态闸门把关 |
| 机型档案 | TB3 Waffle/Burger、linorobot2、Andino、UR5e、Panda 的真实尺寸，说型号即命中；自己给的尺寸覆盖档案 |
| 三维预览 | 网页内 three.js 渲染，拖关节滑块看运动、点部件高亮关节链，不用开虚拟机 |
| 模型体检 | 导入/生成的 URDF 逐条报问题：缺 inertial、零质量、惯量非法、mesh 尺寸未知 |
| 代码生成 | 需求分析 → 节点规划 → 节点代码 → AST 检查 → 沙箱编译验证 → 教学讲解 |
| 通信图谱 | AST 解析节点/话题/服务并出可拖拽拓扑图，改图可写回代码 |

细节见 [`app/README.md`](app/README.md) 的「仿真建模与三维预览」一节。

## 目录结构

```
.
├── experts/                          # WorkBuddy 专家包
│   ├── ros2-project-mentor/          #   ROS2规划与教学专家（开发者）
│   └── ros2-learning-orchestrator/   #   ROS2学习主调度员（学习者）
└── app/                              # ROS2 学习助手网站
    ├── server.py                     #   服务入口（标准库，单命令启动）
    ├── app/                          #   后端逻辑（编排、工作流、仿真建模、沙箱、鉴权等）
    ├── public/                       #   前端页面（HTML/CSS/JS，含三维预览 preview.js）
    ├── kb_data/                      #   ROS2 知识库
    ├── docs/testing/                 #   ROS2 验证体系文档
    ├── samples/                      #   示例 ROS2 工作空间
    ├── tests/                        #   自动化测试
    ├── Dockerfile / docker-compose.yml
    └── ...
```

## 快速开始

### 1. 安装专家包

将两个专家目录放入 WorkBuddy 专家目录，用 expert-manager 的校验、注册脚本完成安装（不要直接编辑 `marketplace.json`）：

```text
~/.workbuddy/plugins/marketplaces/my-experts/plugins/ros2-project-mentor/
~/.workbuddy/plugins/marketplaces/my-experts/plugins/ros2-learning-orchestrator/
```

### 2. 启动学习助手网站

```bash
cd app
cp .env.example .env   # 填写 DASHSCOPE_API_KEY（阿里云百炼）
python server.py       # 默认 8000；PORT=8901 python server.py 可改端口
```

私有化 / 容器部署：

```bash
cd app
docker compose up -d   # 端口 8000，命名卷 ros2-data 挂 /data
```

完整配置、鉴权、限流、IP 白名单与部署说明见 [`app/README.md`](app/README.md)。

## 环境说明

- 版本固定：Ubuntu 24.04、ROS2 Jazzy、Gazebo Harmonic（默认 primary）。
- 兼容发行版 Humble（Ubuntu 22.04）切换方式见 `app/README.md` 的「版本清单」一节。
- 三个版本号（系统软件 / 目标 ROS 发行版 / 编译沙箱镜像）的区分与修改位置，见 `app/README.md`。
