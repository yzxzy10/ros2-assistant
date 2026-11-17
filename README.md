# ROS2 智能体开发与教学项目

面向 ROS 2 的智能体开发与教学工具集，覆盖 **Ubuntu 24.04 + ROS2 Jazzy + Gazebo Harmonic**。

仓库包含两个可直接安装的 WorkBuddy 专家包，以及一个开箱即用的 ROS2 学习助手网站（**v1.18.0**）。

## 项目组成

| 组件 | 目录 | 面向 | 说明 |
|---|---|---|---|
| ROS2规划与教学专家 | [`experts/ros2-project-mentor/`](experts/ros2-project-mentor/) | 开发者 | 项目规划、C++/Python 功能包生成、消息通信、机械臂/导航/Gazebo 仿真排障、分层教学 |
| ROS2学习主调度员 | [`experts/ros2-learning-orchestrator/`](experts/ros2-learning-orchestrator/) | 学习者 | 概念讲解、报错排查、练习与学习路线，按状态机调度「需求分析 / 项目规划 / 代码生成」三个工作流 |
| ROS2 学习助手网站 | [`app/`](app/) | 全体 | 聊天式教学平台：教学流水线 + 仿真流水线 + 架构图 + 真编译链路验证 + 3D 模型预览 |

## v1.18.0 能力概览

| 能力 | 说明 |
|---|---|
| 教学流水线 | 需求分析 → 节点规划 → 代码生成 → 讲解，三阶段带质量闸门与修复循环（11 个 Agent 协作，可查 `/api/agents`） |
| **链路验证（真编译 + 逐跳取证）** | Docker `ros:jazzy-ros-base` 或本机 colcon 真编译，起全部节点，**逐跳注入消息并确认对端收到**，产出「哪一段通、哪一段断」报告 |
| **仿真建模** | URDF + `ros2_control` + Gazebo 插件 + launch 生成，七道闸门（G1–G7）检查，支持差速底盘 / 通用机械臂 / panda 真模型 |
| **3D 模型预览** | URDF/xacro → 场景 JSON，Babylon.js PBR 渲染，DAE→STL 服务端转换，mesh 路径容错 |
| 架构图（通信拓扑） | AST 解析 rclpy 节点（非正则），可改话题 / 加节点 / 提交生成标准包 |
| 模型体检 | 导入即体检：结构 + 控制层是否齐备，就地修复并出 Markdown 报告 |
| 项目导入 | 文件夹 / zip / 分片上传，支持 `.dae/.stl/.obj` 网格 |
| deb 包池 | 31 个常用 Jazzy 包本地缓存，编译不依赖外网 |
| 账号与画像 | 用户名密码登录（PBKDF2 20 万轮）、学习画像、会话历史与 30 天归档 |
| 多模态 | 报错截图识别（qwen-vl-max） |

## 目录结构

```
.
├── experts/                          # WorkBuddy 专家包
│   ├── ros2-project-mentor/          #   ROS2规划与教学专家（开发者）
│   └── ros2-learning-orchestrator/   #   ROS2学习主调度员（学习者）
└── app/                              # ROS2 学习助手网站（v1.18.0）
    ├── server.py                     #   服务入口（纯标准库，单命令启动，全部 API 路由）
    ├── app/                          #   51 个后端模块（编排/工作流/沙箱/链路验证/仿真/图谱/鉴权）
    ├── public/                       #   前端（聊天 / 架构图 / Babylon 3D 预览）
    ├── assets/meshes/panda/          #   仿真用真实网格（自包含）
    ├── kb_data/                      #   知识库（15 文件 / 1550 块）
    ├── pkgcache/                     #   deb 包池（31 个 Jazzy 包）+ index.json
    ├── docs/testing/                 #   编译验证方案（fake / real / hybrid 三方案）
    ├── samples/                      #   示例工作空间（四节点演示工程）
    ├── tests/                        #   45 个测试脚本
    ├── scripts/  tools/              #   辅助脚本 / 打包发布
    ├── START-HERE.md                 #   5 分钟上手
    ├── 交接文档.md                    #   完整交接说明（架构/模块/踩坑）
    ├── verify_ci.py                  #   编译验证 CI 脚本（配合 .github/workflows）
    └── Dockerfile / Dockerfile.verify / docker-compose.yml
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
cp .env.example .env              # 填写 DASHSCOPE_API_KEY（阿里云百炼）
python -X utf8 server.py          # 默认 8000；PORT=8901 python -X utf8 server.py 可改端口
```

零第三方依赖（纯标准库 + 原生 JS），Python 3.10+ 即可运行；无 Key 也能启动看界面。

私有化 / 容器部署：

```bash
cd app
docker compose up -d              # 端口 8000，命名卷 ros2-data 挂 /data
```

### 3. 启用「编译运行」（链路验证）

验证是真跑，不是模拟，需要 Docker 或本机 ROS 2：

```bash
docker pull ros:jazzy-ros-base    # 约 900MB，只需一次
docker version                    # 确认当前用户可直接执行 docker
```

资源默认 `--memory 6g --cpus 3`、串行编译（`build_jobs=1`）；内存 < 8G 建议调到 `4g`。没有 Docker 时会明确提示"环境不支持真编译"，**不会给假绿灯**。

完整配置、鉴权、限流、IP 白名单与部署说明见 [`app/README.md`](app/README.md)、[`app/START-HERE.md`](app/START-HERE.md)、[`app/交接文档.md`](app/交接文档.md)。

## 自检

```bash
cd app
# 模块导入检查
python -c "import importlib,glob,os; [importlib.import_module('app.'+os.path.basename(f)[:-3]) for f in glob.glob('app/*.py') if not f.endswith('__init__.py')]; print('全部导入 OK')"

# 核心回归
python tests/test_accounts.py && python tests/test_sim.py && python tests/test_model_inspect.py && PYTHONPATH=. python tests/test_agents.py

# 健康检查（启动后）
curl -s http://127.0.0.1:8000/api/health
```

## 环境说明

- 版本固定：Ubuntu 24.04、ROS2 Jazzy、Gazebo Harmonic（默认 primary）。
- 兼容发行版 Humble（Ubuntu 22.04）切换方式见 `app/README.md` 的「版本清单」一节。
- 三个版本号（系统软件 / 目标 ROS 发行版 / 编译沙箱镜像）的区分与修改位置，见 `app/README.md`；系统版本单一事实来源为 `app/app/versions.py`。

## 仓库维护

- `.github/workflows/compile-sandbox-verify.yml` + `app/verify_ci.py`：在真实 `ros:jazzy-ros-base` 容器里跑 `colcon build` + 节点注册验证，日志上传为 artifact，用于留下带时间戳的可复现证据。
- 历史版本归档：[`ros2-learning-assistant`](https://github.com/yzxzy10/ros2-learning-assistant)（v1.0 平铺版）。
