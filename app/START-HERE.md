# 先看这个（5 分钟跑起来）

**ROS2 学习助手**：一个聊天网站，能生成 ROS 2 项目代码，并且**真的在 Docker 里编译、起节点、
逐跳验证消息有没有在链路上跑通**。目标发行版 ROS 2 Jazzy / Python 3.12。

版本 `1.18.0`（分叉合并版）· 纯 Python 标准库，**不需要 pip install 任何东西**。

---

## 1. 三步启动

```bash
# 1) 填密钥
cp .env.example .env            # Windows: copy .env.example .env
#   编辑 .env，把 DASHSCOPE_API_KEY 换成你自己的（阿里云百炼）

# 2) 起服务
python server.py                # 默认 http://localhost:8000
#   换端口：PORT=8901 python server.py

# 3) 浏览器打开
#   http://localhost:8000
```

只需要 **Python 3.10+**。没有密钥也能打开页面、能看示例工程，只是所有 AI 生成会失败。

## 2. 想用「编译 + 链路验证」，还要装 Docker

验证是真跑，不是模拟：

```bash
docker pull ros:jazzy-ros-base     # 镜像约 900MB，只需一次
docker version                     # 确认当前用户能执行 docker（不用 sudo）
```

然后打开任意项目页 → 点右上「编译运行」。程序会在容器里
`colcon build` → 起全部节点 → 逐跳订阅取证，最后给你一份「哪一段通、哪一段断」的报告。

资源占用：`config.json` 里默认 `--memory 6g --cpus 3`，C++ 包串行编译（`build_jobs=1`）。
内存低于 8G 的机器建议把 `memory` 调到 `4g` 并保持 `build_jobs=1`，否则编译器会被 OOM 杀掉。

> 没有 Docker 也能用：验证会明确提示「当前环境不支持真编译」，不会给你一个假的绿灯。

## 3. 页面里有什么

| 功能 | 在哪 |
|---|---|
| 聊天生成 ROS2 项目（需求 → 规划 → 代码 → 讲解） | 首页对话框 |
| 语料库/知识库问答（1550 个知识块，BM25 检索） | 同一个输入框，问概念/报错即可 |
| 通信拓扑图（节点 / 话题 / 服务自动解析） | 项目页「架构图」 |
| **3D 模型预览**（URDF/xacro + STL/DAE 网格，可上传 zip） | 项目页「模型预览」 |
| **全链路验证**（真编译 + 逐跳取证） | 架构图右上「编译运行」 |
| 学习画像（概念掌握、常见错误） | 左侧抽屉 |

## 4. 目录地图

```
server.py            HTTP 服务（stdlib，含访问控制/分享码）
config.json          模型、限流、验证资源参数（不含密钥）
.env                 你的密钥（不进压缩包，请看 .env.example）
app/                 后端：工作流、知识图谱、验证器、知识库检索
  chain_*.py         ← 全链路验证（规划→编译→起节点→逐跳取证→诊断）
public/              前端（纯原生 JS，无构建步骤）
kb_data/             知识库原始文本 + SOURCES.md（来源与版本）
pkgcache/            离线依赖包池（容器没外网，缺的 deb 从这儿装）
samples/             示例 ROS2 工程（demo_ros2_ws.zip）
tests/               回归测试：python tests/base_pkgs_test.py
docs/testing/        验证体系设计文档（三套方案与取舍）
```

## 5. 常见问题

**页面能开，但一问就报错**
`.env` 里的 `DASHSCOPE_API_KEY` 没填或填错；或额度用尽。

**点「编译运行」提示环境不支持**
`docker version` 跑不通。Linux 下把用户加进 docker 组，或确认 Docker Desktop 已启动。

**编译到一半 `Killed signal terminated program cc1plus`**
容器内存不够。调小 `config.json` 的 `ros.verify.memory` 没用的话，把
`build_jobs` 保持 1、`cpus` 降到 2，或换内存更大的机器。

**验证报告说「个别节点没起来」**
先看报告里的诊断：如果写的是「官方示例跟发行版不兼容」，那是 ROS 自带示例自身的问题，
不影响链路结论；如果是你自己包的路径 + 行号，那才需要改代码。

**验证报告说「镜像里缺 N 个依赖 / 请联系管理员」**
那些包不在验证镜像里（也就是**这台机器目前不支持**），而你这边装不了：容器没有外网、
默认也没有装包权限。缺的包会自动记进 `pkgcache/pending.txt`，把它交给管理员，
一条命令就能补齐（需要一台有网、装了 Docker 的机器）：

```bash
python tools/fetch_pkgs.py --pending     # 抓进 pkgcache/pool，索引自动更新
python tools/fetch_pkgs.py --check ros-jazzy-moveit-core   # 只想问「源里有没有」
```

补完重跑一次验证即可，容器会从 `/pkgpool` 自动补装。若管理员在 ROS 源里也查不到，
说明是第三方/私有包（GitHub 上那种），得把它的源码放进工作区 `src/` 一起编译。

**换 ROS 发行版（Humble）**
改 `config.json` 的 `ros.distro` 一处即可，镜像、提示词、界面口径会一起变。
知识库里 API 参考取自更新的文档，检索结果会标注来源版本。

## 6. 安全提醒

- `.env` 里是**你的真实密钥**，别人拿到就能花你的额度。给对方时只给 `.env.example`。
- 部署到公网前，务必设置 `ROS2_ID_SECRET`（32 字节随机串），否则匿名身份可被伪造。
- 想限制访问：`app/auth.py` 支持 IP 白名单 + 令牌，配置见 README「访问控制」。
