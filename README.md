# ROS2 学习助手网站

把「主调度员 + 需求分析 + ros2项目规划 + ros2代码生成」三个工作流和 ROS2 知识库，
打包成一个开箱即用的聊天网站（**ROS 2 Jazzy / Python** 教学）。

> 本文同时是**项目状态报告**：当前功能状态、相较最初「工作流 + 主调度员」形式的提升、
> 测试结果、后续规划，见下面前三节。

## 版本清单（先看这里）

系统里有**三个互不相干的版本号**，混起来最容易出错，所以单独列一表：

| 是什么 | 当前值 | 在哪里改 | 影响范围 |
|---|---|---|---|
| **系统软件版本** | `v1.2.3` | `app/versions.py` → `APP_VERSION` | 启动日志、`/api/meta`、界面徽标 |
| **目标 ROS 发行版** | `jazzy`（Jazzy Jalisco） | `config.json` → `ros.distro` | 提示词约束、沙箱镜像、界面标注、知识库版本提示 |
| **编译沙箱镜像** | `ros:jazzy-ros-base` | `config.json` → `ros.verify.image` | 编译验证实际使用的容器 |

目标发行版对应的环境，全部由 `app/versions.py` 一处定义、全项目引用：

| 项 | Jazzy（默认 / primary） | Humble（兼容 / compat） |
|---|---|---|
| Ubuntu | 24.04 | 22.04 |
| Python | 3.12 | 3.10 |
| Gazebo | Gazebo Harmonic | Gazebo Classic 11 / Fortress |
| 环境命令 | `source /opt/ros/jazzy/setup.bash` | `source /opt/ros/humble/setup.bash` |
| 镜像 | `ros:jazzy-ros-base` | `ros:humble-ros-base` |
| EOL | 2029-05 | 2027-05 |

改 `ros.distro` 一处，提示词、沙箱、界面、文档口径会一起变，不会出现
「界面写 Humble、环境是 Jazzy」这种自相矛盾。切到 Humble 只需：

```json
"ros": { "distro": "humble" }
```

> 注：知识库中 `API参考` 的 38 条取自 docs.ros.org 的 **Kilted** 文档，
> 与目标版本不同。系统会在检索结果前加 `【来源版本】ROS 2 kilted` 标注，
> 并由提示词要求冲突时以目标版本为准；详见 `kb_data/SOURCES.md`。

## 当前功能状态（截至 2026-11-05）

| 模块 | 状态 | 说明 |
|---|---|---|
| 主调度员路由 | ✅ 完成 | 确定性状态机：项目意图 → 流水线，报错/概念/练习/FAQ/闲聊 → 直接回答 |
| 工作流① 需求分析 | ✅ 完成 | LLM 生成 → JSON 结构校验 → 修复循环 → 信封输出，含选择题追问（最多 2 轮） |
| 工作流② 项目规划 | ✅ 完成 | 节点规划 + 学习指南 + 知识库注入，A/B/C/D 选项流转 |
| 工作流③ 代码生成 | ✅ 完成 | 节点规格 → 知识库检索 → 代码 → AST 检查 → 修复循环 → **编译验证** → 教学讲解 |
| 编译验证（沙箱） | ✅ 完成 | 生成 ament_python 包 → `colcon build` → 起节点 → `ros2 node list`；失败日志回灌修复器 |
| **知识图谱（通信图谱）** | ✅ 完成 | AST 解析项目里的节点/话题/服务/消息类型并出交互图，支持草稿编辑、`commit` 改图写回代码（详见下文） |
| **项目导入** | ✅ 完成 | 选文件夹或上传 zip/tar，自动解压并识别包/节点/launch/接口，作为后续开发上下文；支持二进制、单文件 50MB / 总量 100MB |
| **会话管理** | ✅ 完成 | 侧栏历史会话列表、整段回放（transcript）、单会话删除/归档；规划待办态问新问题可自动退出不再卡死 |
| 版本口径统一 | ✅ 完成 | 单一事实来源 `app/versions.py`；目标 Jazzy，Humble 为兼容分支（见上表） |
| 知识库检索 | ✅ 完成 | 内置 BM25，15 个文件 / 1550 个文档块（一条记录一块），top_k=5 |
| 直接回答 | ✅ 完成 | 报错三段式 / 概念 / 练习题 / FAQ / 闲聊，画像注入提示词 |
| SSE 流式输出 | ✅ 完成 | `/api/chat/stream`，逐块渲染；分步进度 + 2 秒心跳，长任务不再静默 |
| 多模态（截图） | ✅ 完成 | 粘贴/拖拽/选择图片 → 视觉模型结构化识别 → 报错截图直接进三段式诊断 |
| 任务状态可见 | ✅ 完成 | 分步状态 + 独立计时 + 断流提示 + 完成后折叠摘要（详见下文） |
| 记忆系统 | ✅ 完成 | 三层：30 条窗口 + qwen-flash 滚动摘要 + SQLite 跨会话画像；30 天归档 |
| 学习画像 | ✅ 完成 | SQLite 按 userId 存概念/报错/项目/水平/摘要，每轮自动更新并注入提示词 |
| 身份与访问控制 | ✅ 完成 | IP 白名单（CIDR）→ 令牌（可绑 uid）→ 服务端签发匿名身份（签名 Cookie）→ 限流 |
| 限流与保险丝 | ✅ 完成 | 每身份 20 次/分对话、120 次/分读接口；全站每日 2000 次对话封顶 |
| 密钥管理 | ✅ 完成 | 密钥只从环境变量/.env 读，config.json 无任何密钥 |
| 数据持久化 | ✅ 完成 | `ROS2_DATA_DIR` 统一 sessions/data/archive；Docker 命名卷挂 /data |
| 前端 | ✅ 完成 | 聊天 UI + 流式光标 + 画像面板 + 节点清单 + 文件树 + **通信图谱** + **暗色模式** + 移动端抽屉 |
| 测试 | ✅ 完成 | 19 个测试脚本（含图谱/导入/会话/安全/记忆等）；核心离线测试全绿，10 个 UI 自动化测试需额外装 `websocket-client`（详见「测试结果」节） |
| **公网部署** | 🟡 演示已上线 | 已有在线演示实例；生产化（自有域名 + HTTPS + 反代）仍待做，Dockerfile/compose 已就绪 |

## 相较最初「工作流 + 主调度员」的提升

原始形态：三个 YAML DSL 工作流（LLM/Script/Judge/Iterator 节点）+ 一份很长的主调度员提示词，
只能在百炼平台上手动编排运行。现在的形态：一个零第三方依赖的独立网站。

| 维度 | 原始形态 | 现在 |
|---|---|---|
| 载体 | 百炼平台内编排，无法独立运行 | `python server.py` 一条命令起网站，stdlib 实现，无 Nginx/Flask 依赖 |
| 调度可靠性 | 全靠 LLM 读长提示词自主路由，会漏走工作流 | 确定性状态机执行路由与流转，规则逐条对应原提示词；实测修掉两类误路由（「我想做一个项目」不追问直出规划代码、「出现什么问题」被误判为出题） |
| 记忆 | 只有单轮/会话内上下文，新会话失忆 | 三层记忆：30 条窗口、flash 滚动摘要（≤300 字，摘要失败退化为原文拼接不丢失）、SQLite 跨会话学习画像 |
| 身份 | 无概念，平台账号即身份 | 服务端签发 HMAC 签名 HttpOnly Cookie，前端自报 uid 无效；可选令牌绑 uid / IP 白名单 |
| 安全 | 无 | 密钥外置（env/.env）、四道访问关、限流 + 全站每日保险丝 |
| 数据 | 会话结束即散落在平台 | 会话状态 JSON + 画像 SQLite，`ROS2_DATA_DIR` 可整体挂卷，30 天自动归档 zip |
| 输出体验 | 平台一次性返回 | SSE 流式 + 可点击追问卡片 + 网页文件树 + 一键 zip 下载 |
| 知识库 | 平台插件 | 内置 BM25（参数与原插件一致），离线可用，随代码走 |
| 质量保障 | 平台内脚本校验 | **原样复用**：需求 JSON 校验器、Python AST 检查器（Script_B9sf）逐字保留，修复循环最多 3 轮保留 |
| 部署 | 绑定平台 | Dockerfile + compose（命名卷）+ systemd 示例 + Nginx 反代说明 |

一句话总结：**把「平台里的一套编排」升级成了「一个能独立部署、有身份、有记忆、有防线的产品」**，
工作流的内核（提示词 + 校验器 + 修复循环）没有动。

## 多模态与执行进度（2026-11-01 新增）

这两项都是冲着真实用户反馈做的，不是功能堆砌。

### 一、截图理解：学生丢张图就能问

旧链路只认文字。可学生的真实习惯是「截个报错图丢过来」，图片等于白屏。
现在补上了这条路：输入框左侧按钮 / `Ctrl+V` 直接粘贴 / 拖进窗口，三种方式都能传。

```
图片 → 魔数校验（只收 PNG/JPG/WEBP/GIF）→ 按内容 sha256 落盘
     → 视觉模型结构化识别 → {kind, title, text, hint}
     → 按 kind 分流
```

| kind | 走向 |
|---|---|
| error / code | 直接进「报错三段式」诊断，不进项目流水线 |
| slide / diagram / hardware | 概念讲解；且**只发图不写字时绝不擅自开项目流水线** |
| 识别失败 | 不阻断对话，退回纯文本回答并提示学生把报错贴出来 |

工程细节：

- **不信任前端**：扩展名和 Content-Type 都可以伪造，一律按文件头魔数判定；
- **内容寻址**：同一张图重复上传不会存第二份，识别结果按图片内容缓存，重复提问不重复计费；
- **会话隔离**：图片存在 `sessions/<sid>/uploads/`，跨会话取不到别人的图；
- **本地 data URL 送模型**：不需要公网可访问的图片地址，答辩现场断网也能演示；
- **只做抄录不做诊断**：视觉模型只负责「图里写了什么」，诊断仍走原有 SYS_MASTER_ERROR，
  不引入第二套答题风格。

### 二、任务状态可见：让「在想」和「卡死」可区分

完整项目端到端 200 秒以上，旧版本中这段全是空白，学生原话是
「不知道它到底是在思考还是卡住」。现在三层信号：

| 层 | 做法 |
|---|---|
| 分步状态 | 后端每步（意图识别 → 检索 → 需求分析 → 项目规划 → 代码生成 → 回答）发 `progress` 事件，带 state 与独立耗时 |
| 实时计时 | 前端本地每 100ms 走表，不依赖服务端事件；代码批量生成时上报「2/4 · 正在生成 xxx_node」 |
| 心跳兜底 | 后端每 2 秒补一个 `tick`；前端超过 6 秒无新事件即显示「已 12.4s 无新进展，仍在等待模型响应」 |

完成后进度卡自动折叠成一行「✓ 6 步完成 · 41.2s」，点开可看每步耗时——
这既是给学生的交代，也是「过程可追溯」的直接证据。

SSE 事件协议：

```jsonc
{"type":"progress","value":{"id":"plan","label":"项目规划","state":"start|run|done|fail",
                            "detail":"已拆出 4 个节点","elapsed":9.1,"total_elapsed":23.6}}
{"type":"tick","value":{"elapsed":23.6,"label":"代码生成","detail":"2/4"}}
{"type":"delta","value":"增量文本"}
{"type":"final", ... "elapsed":41.2,"steps":[{"id":"plan","label":"项目规划","elapsed":9.1}]}
```

配置（`config.json`）：

```json
"models":  { "vision": "qwen-vl-max" },
"multimodal": { "enabled": true, "max_bytes": 4194304 }
```

`multimodal.enabled=false` 或视觉模型不可用时，上传接口仍可用但识别会降级，
对话照常进行，不会因为一张图卡住整个流程。

## 编译验证闭环（2026-11-01 新增）

### 为什么要它

原来的校验链止于 AST（`checker_code.py`），只能查出 `rclpy.Node` 这类写法错误。
它查不到的四类问题全都发生在编译和启动阶段：

- import 了不存在的 msg 类型（`from sensor_msgs.msg import LaserScann`）
- `setup.py` 入口与函数名对不上，`ros2 run` 直接找不到可执行文件
- 依赖没在 `package.xml` 里声明，编译报 `Could not find a package configuration file`
- 代码能编译，但节点一起来就 `AttributeError` 崩掉

所以补一层真实执行：**生成包 → colcon build → 起节点 → 确认节点真的注册上了**。

### 怎么跑

```
生成代码 → AST 检查 → 修复循环
        → 编译验证（沙箱）
            ├ 通过 → 教学讲解
            └ 失败 → 真实日志回灌 coder_fix → 重新生成 → 再校验（轮数由 ros.verify.fix_rounds 控制，默认 1）
```

沙箱内部做的事（`app/sandbox.py`）：

1. 按节点名生成最小 `ament_python` 包：`package.xml` / `setup.py` / `setup.cfg` / `resource/` / 模块目录；
2. `colcon build --symlink-install --packages-select <pkg>`；
3. `ros2 run <pkg> <node>` 起 6 秒，再 `ros2 node list` 看它有没有出现；
4. 把编译输出与节点日志（截断到 2600 字符）随结果返回，失败时进修复提示词。

### 后端与降级

| 后端 | 触发条件 | 说明 |
|---|---|---|
| `docker` | 有 Docker 且 `docker info` 通 | 容器内跑，`--network none`、`--cap-drop ALL`、1G/1CPU/256pid 限额 |
| `local` | 本机装了 colcon + ros2 | 直接在本机工作区跑，适合已部署 ROS 的服务器 |
| `none` | 两者都没有 | **明确降级**：`attempted=false`，回复里写「编译验证：未执行（原因）」 |
| `mock` | `ROS2_VERIFY_BACKEND=mock` | 仅离线测试用，结果标注「模拟」，不会伪装成真实验证 |

关键设计：**降级绝不假装通过**。没有编译环境时，`verify.attempted=false`、
`ok=false`，界面上写清楚「未执行」和原因，而不是给个绿色的「验证通过」。
环境变量 `ROS2_VERIFY_BACKEND`（docker/local/mock/none）优先级高于配置，
用于测试与临时关闭。

**部署时怎么让它真的可用**：默认镜像只有 Python（~150MB），没有 ROS，沙箱会降级。
需要真编译验证就用 `Dockerfile.verify`——基础镜像 `ros:jazzy-ros-base`，
自带 ROS 2 Jazzy + colcon，走 `local` 后端，不需要 Docker-in-Docker：

```bash
docker build -f Dockerfile.verify -t ros2-tutor:jazzy .
docker run -d -p 8000:8000 --env-file .env -v ros2-data:/data ros2-tutor:jazzy
```

代价是镜像从 ~150MB 涨到 ~900MB。两种镜像跑的是同一份代码，区别只在
「有没有 ROS 环境可供编译验证」，`/api/meta` 的 `sandbox.backend` 会如实显示是
`docker` / `local` 还是 `none`。

### 安全边界（别误解）

这是**编译校验沙箱**，不是安全隔离层。容器虽然加了网络禁用与资源限额，
但它存在的目的是跑「本系统自己生成的代码」，不是执行来路不明的第三方代码。
要对外开放执行能力，必须换成 gVisor / Firecracker 一类的强隔离方案。

### 版本标注

每一次生成都会在回复末尾写明这一版是按哪个发行版生成、有没有真的校验过：

```
> ROS 2 Jazzy Jalisco · 编译验证：通过·启动（ros:jazzy-ros-base 镜像）· 9.5s
```

未通过时额外折叠一段「查看校验日志」，点开就是真实编译输出。
侧栏徽标同时显示：目标发行版、Ubuntu/Python 版本、编译验证是否可用、系统版本号。

配置（`config.json`）：

```json
"ros": {
  "distro": "jazzy",
  "verify": {
    "enabled": true,
    "backend": "auto",
    "image": "",
    "timeout_build_sec": 300,
    "timeout_run_sec": 25,
    "fix_rounds": 1
  }
}
```

## 知识图谱 · ROS2 通信拓扑

把「项目里谁发谁收、用什么消息类型」画成一张可交互的图，不用学生在多个文件里对着消息名找。

### 怎么解析的（AST，不用正则）

`app/graph.py` 用 Python `ast` 解析会话工作区里的源码，口径是「宁可标未解析，也不猜」：

| 解析对象 | 依据 |
|---|---|
| 节点 | 继承 `rclpy` `Node` 的类；模块级 `rclpy` 调用时用文件名兜底 |
| 节点名 | `super().__init__("名字")` 的第一个字符串字面量 |
| 接口 | `create_publisher` / `create_subscription` / `create_service` / `create_client` / ActionClient / ActionServer |
| 消息类型 | 顺着 `import` 反推成 `std_msgs/msg/String` 这类标准写法，解析不出就保留原文 |
| 话题名 | 只认字符串字面量；f-string / 变量 / 拼接 → `resolved=False` |
| 连线 | 同名话题下 publisher → subscriber 的有向边；动态话题进 `unresolved`，图上用虚线提示 |

产物写在 `sessions/<sid>/graph.json`，草图另存 `graph_draft.json`；`build/install/log/.git` 等目录自动跳过。

### 三个阶段：看 → 改 → 落

`app/graph_api.py` 把图谱做成「草稿（draft）→ 审阅（intro）→ 提交（commit）」三段式：

| 阶段 | 接口 | 作用 |
|---|---|---|
| 看 | `GET /api/graph` | 返回节点、连线、包与未解析项 |
| 改 | `POST /api/graph/draft`、`POST /api/graph/node` | 保存节点位置 / 重命名 / 删除 / 新增草稿节点，只动 `.json` 草稿 |
| 落 | `POST /api/graph/commit` | **唯一**会把图纸改动写回源码的接口 |

> 安全边界：`commit` 会改代码，属于强操作；它只作用于当前会话自己导入/生成的工作区，不触碰其他会话。

### 前端

`public/graph.js` 渲染可拖拽的节点图（发布者/订阅者按话题连线、未解析项画虚线），并带「询问 Graph」入口（`ask-graph-toggle`）——可以直接对着图问「这条话题谁在发、谁在收」，回答沿用主调度员的风格。

## 测试结果（2026-11-05 更新）

自动化测试共 **19 个脚本**（`tests/`）；下表是核心的离线/端到端回归，另外 10 个是浏览器自动化（UI）测试，需先 `pip install websocket-client`。核心离线测试（如 `security_test.py` 40/40）无需任何第三方依赖：

| 测试 | 结果 | 覆盖 |
|---|---|---|
| `tests/security_test.py` | **40/40 通过** | 密钥外置来源、IP 白名单（CIDR/伪造头）、令牌 401/200/绑 uid、数据目录切换后 SQLite 落点 |
| `tests/identity_test.py` | **16/16 通过** | 身份签发与持久化、无 Cookie 401、伪造签名 401、画像与会话跨身份隔离、限流 429、每日保险丝 |
| `tests/memory_test.py` | **5/5 PASS** | 32 条历史压缩到 20 条且摘要仅调 1 次、摘要失败不丢数据、未达阈值零调用、画像渲染注入、归档 45 天打包删除 / dry-run 不删 |
| `tests/e2e_mock.py` | **ALL DONE** | 桩模型跑通三条工作流全流程（不产生费用） |
| `tests/sandbox_version_test.py` | **42/42 通过** | 版本单一事实来源、切 Humble、非法值回落、提示词注入、沙箱后端探测与降级、编译失败回灌重生成闭环 |
| `tests/smoke_http.py` | **通过** | 真起服务：health 200、无令牌 401、带令牌 200、真实调 qwen-max 正常回答（验证密钥链路不是假通） |

真实模型端到端验收（qwen-max / qwen-flash，此前人工验收记录）：

- 直接回答「话题和服务的区别」约 6.6s；
- 「我想做一个项目」→ 追问卡片 → 规划约 24s（2 节点 + A/B/C/D 选项）；
- 选 A → 分批生成约 23s，AST 检查一次通过；单节点直生成约 10s；
- 报错场景返回「原因/排查/修复」三段式，且不出现「pip install rclpy」这类错误建议；
- 追问两轮后强制出规划（防无限追问）；
- 新会话能说出学过的话题、用过的 rclpy、踩过的 ModuleNotFoundError（跨会话画像生效）。

过程中发现并修复的真实 bug（均有测试覆盖防止回退）：
全局锁自死锁、DashScope 国际站 401（改国内 baseUrl）、文件树缺 `ros2_ws/` 前缀、
重复追问同一问题、「出现什么问题」误判为出题、项目意图漏判、HTTP_PROXY 拦截回环请求、
令牌绑定短 uid 被前端校验误拒、**知识库 JSONL 整段拼块导致 95% 条目检索不到**等。

> 最后一条的来龙去脉：原实现把一个文件里所有 JSONL 记录拼成一块再截断到 2200 字符，
> 结果 1003 条错误数据只剩前十几条可被检索，命中时返回的还是一坨混杂条目、说不清依据。
> 改成「一条记录一块」后总块数 267 → 1550，加载 0.19s、单次检索 3ms，
> 能精确命中 `ROS2-QOS-003` 这类单条记录，引用来源也说得清了。

## 后续规划与优化方向

**P0 · 上线前必做**

1. **公网部署**：已有在线演示实例；生产化（自有域名 + HTTPS + 反代）仍待做。
   代码侧零改动，`docker compose up -d` 即可；Nginx 记得 `proxy_buffering off`（SSE）并设 `ROS2_TRUST_PROXY=1`。
2. **身份跟人走**：当前清 Cookie/换浏览器 = 新身份。公开给学生用前，做令牌分发（`ROS2_AUTH_TOKEN=xxx:u_学号`
   已支持）或简单的邀请码/注册页。
3. **用量可视化**：加一张 `usage` 表记录每身份每次对话的 token 消耗，做一个最小管理页——
   现在只有每日保险丝，看不到「谁用了多少」。

**P1 · 质量与体验**

4. **检索升级**：BM25 是纯关键词召回，「串口通信」搜不到「serial」这类同义表述。加向量检索做混合召回
   （bge-small 本地 embedding 即可，仍可保持轻依赖）。
5. **评估基准**：固定 20~30 个典型问题做快照测试，改提示词/换模型后跑一遍对比，避免「改好一处坏一处」。
   顺带统计修复循环成功率、追问点击率。
6. **画像面板增强**：导出/清空按钮（「我的数据」），概念掌握度可视化。
7. **练习题判分**：练习题目前只出题不批改，可接代码校验器做自动判分——校验器是现成的。
8. ~~会话管理~~（**已实现**：会话列表 / 历史回放 / 删除 / 归档）；剩「误选 A 后回退重选」未做。

**P2 · 工程**

9. `server.py` 按路由拆模块；`ThreadingHTTPServer` 换异步框架仅在并发上来后考虑。
10. 状态机补更多单元测试（现主要靠 e2e 与 graph/import 测试覆盖）；加 CI 在每次提交时跑全部 19 个测试（UI 测试需先装 `websocket-client`）。
11. 前端移动端适配；教师端视图（查看全班学习画像）。

**刻意不做的**：注册中心/多租户（当前规模不需要）、MySQL/Postgres（SQLite 量级足够，接口层已隔离可随时换）。

## 功能

- **项目流水线**：需求分析（含选择题追问，最多 2 次）→ 节点规划 + 学习指南 → 规划书渲染 →
  按选项 A/B/C/D 流转 → 分批生成节点代码（每批最多 2 个）
- **单节点模式**：直接描述「帮我写一个订阅激光雷达的节点」即可生成
- **质量保障**：需求 JSON 结构校验（原样复用原工作流脚本）+ Python AST 代码检查器
  （原样复用 Script_B9sf）+ 失败自动修复循环（最多 3 轮）
- **知识库**：内置 BM25 检索（15 个文件 / 1550 个文档块），自动注入报错排查、概念讲解、
  项目规划与代码生成
- **直接回答**：报错排查（三段式）/ 概念讲解 / 练习题 / 闲聊，均按主调度员提示词风格
- **项目导入**：选中已有 ROS2 项目文件夹，或直接上传 zip/tar，自动解压并识别包/节点/launch/接口，
  作为后续开发的上下文（支持二进制文件，单文件 50MB / 总量 100MB）
- **通信图谱**：把项目里的节点、话题、服务、消息类型解析成可交互的拓扑图，支持改图后写回代码
- **会话管理**：侧栏历史会话列表、整段回放、单会话删除/归档
- **项目文件**：生成的节点代码实时保存到会话工作区，网页端可浏览并可一键下载 zip

## 快速开始

```bash
# 1. 准备密钥（密钥不写进 config.json）
cp .env.example .env          # Windows: copy .env.example .env
# 编辑 .env，填入 DASHSCOPE_API_KEY=sk-xxx

# 2. 启动
python server.py              # 默认 8000；PORT=8901 python server.py 可改端口

# 3. 打开 http://localhost:8000
#    若 .env 里启用了 ROS2_AUTH_TOKEN，首次会要求输入令牌，之后自动记住
```

密钥只从环境变量读：`config.json` 里**没有** `apiKey` 字段，可以放心进版本库。
优先级：环境变量 `DASHSCOPE_API_KEY` > 项目根目录 `.env` > `config.json`（仅为兼容旧部署保留）。

## 模型配置

`config.json` 中 `models` 按角色映射模型（OpenAI 兼容接口）：

| 角色 | 用途 | 默认模型 |
|---|---|---|
| analysis | 需求分析 | qwen-max |
| analysis_repair | 需求 JSON 修复 | qwen-flash |
| plan_architect | 节点规划 | qwen-flash |
| plan_guide | 学习指南 | qwen-flash |
| node_spec | 节点规格标准化 | qwen-flash |
| coder / coder_fix | 代码生成 / 修复 | qwen3-coder-flash |
| teaching | 教学讲解 | qwen-flash |
| chat | 直接回答（概念/报错/闲聊） | qwen-max |

`baseUrl` 默认阿里云百炼 DashScope 兼容模式；换其他 OpenAI 兼容服务只需改 baseUrl + 模型名。

## 访问控制

请求依次经过四道关：

```
IP 白名单（可选） → 访问令牌（可选） → 匿名身份（必过） → 限流（必过）
```

### 身份：服务端签发，前端说了不算

公开分享给不特定的人时，不能让前端自报 `uid`——否则谁都能改成别人的 id 看画像。
所以身份是服务端签发的：

1. 浏览器首次打开调 `GET /api/identity`，服务端生成 `u<16位随机>`，用
   HMAC-SHA256 签名后写入 **HttpOnly Cookie**（`ros2_id=<uid>.<sig>`）；
2. 之后所有请求以 Cookie 为准，**请求体里的 `userId` 一律忽略**；
3. 画像、会话、文件下载都按这个身份校验，别人的会话直接 403；
4. 签名密钥存在数据目录 `.id_secret`（或用 `ROS2_ID_SECRET` 指定），
   跟随数据卷，重启不失效；密钥泄露才可能伪造，所以别把 `.env` 和 `data/` 发出去。

代价：清 Cookie / 换浏览器 = 新身份，旧画像拿不回来。需要「跟着人走」就改用令牌绑定 uid。

### 令牌与 IP（可选的两道门）

| 变量 | 作用 | 不配时 |
|---|---|---|
| `ROS2_AUTH_TOKEN` | 访问令牌，除 `/api/health` 外所有接口都要带 | 不校验（公开分享就这样） |
| `ROS2_ALLOW_IPS` | IP 白名单，支持 CIDR：`127.0.0.1,::1,192.168.0.0/16` | 不限来源 |
| `ROS2_TRUST_PROXY` | 置 `1` 才从 `X-Forwarded-For` 取真实 IP | 只认真实 TCP 对端 |

令牌两种写法：

```bash
ROS2_AUTH_TOKEN=s3cret                     # 内部小范围，输一次即可
ROS2_AUTH_TOKEN=s3cret:u_alice,k9x:u_bob   # 固定几个人，令牌绑定 uid（token:uid）
```

绑定的 uid 优先级高于 Cookie，等于「这把钥匙对应这个人」。

**IP 白名单适合什么、不适合什么**（重要）：

- 适合：内网/实验室、家庭宽带、固定公网 IP 的个人服务器、临时挡扫描器；
- 不适合单独当鉴权：动态 IP 会变（4G/5G、拨号宽带重播就进不去）、NAT 后整个校园共用一个出口
  （白名单等于对全校开放）、无法区分「谁是谁」；
- 所以它只当第一道门，认人靠身份/令牌。

### 限流与总量保险丝

| 变量 | 默认 | 作用 |
|---|---|---|
| `ROS2_RATE_CHAT_PER_MIN` | 20 | 每个身份每分钟对话次数，超了 429 |
| `ROS2_RATE_API_PER_MIN` | 120 | 每个身份每分钟读接口次数 |
| `ROS2_DAILY_CHAT_MAX` | 2000 | **全站**每天对话总次数，当保险丝用；0 = 不限 |

`ROS2_DAILY_CHAT_MAX` 是公开分享时唯一挡「一夜被刷爆」的东西：按 qwen-max 粗算
2000 次约 20-40 元/天封顶。不想设就改 0，但公开链接建议留一个数。

`/api/health` 与 `/api/identity` 永远不需要令牌（探针和首次发身份要用），
但开了 IP 白名单时同样受 IP 限制；严格模式下 health 不再返回模型清单与上游地址。

## 部署

### Docker（推荐）

```bash
cp .env.example .env && vim .env     # 填密钥；公网再加 ROS2_AUTH_TOKEN
docker compose up -d                 # 端口 8000，重启策略 unless-stopped
docker compose logs -f               # 看启动信息（数据目录、鉴权状态、密钥来源）
```

`docker-compose.yml` 里用命名卷 `ros2-data` 挂在容器的 `/data`：

```
/data
├── sessions/       会话状态 + 生成的工作区代码
├── data/profile.db 学习画像（SQLite）
└── archive/        旧会话 zip
```

容器删除、镜像升级都不会丢；备份就是备份这个卷：

```bash
docker run --rm -v ros2-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/ros2data-$(date +%F).tar.gz -C /data .
```

### 不用 Docker

```bash
# systemd：/etc/systemd/system/ros2-tutor.service
[Unit]
Description=ROS2 Tutor
After=network.target
[Service]
WorkingDirectory=/opt/ros2-assistant
EnvironmentFile=/opt/ros2-assistant/.env
Environment=ROS2_DATA_DIR=/var/lib/ros2-tutor
ExecStart=/usr/bin/python3 -X utf8 server.py
Restart=always
[Install]
WantedBy=multi-user.target
```

前面挂 Nginx 反代时记得：`proxy_buffering off;`（否则 SSE 流式会被缓冲），
并设 `ROS2_TRUST_PROXY=1` 让服务读到真实 IP，再用 `ROS2_ALLOW_IPS` 拦内网地址。

## 目录结构

```
ros2-assistant/
├── server.py            # HTTP 服务器（stdlib，无第三方依赖）+ 访问控制入口
├── config.json          # 模型与记忆参数（不含任何密钥）
├── .env                 # 密钥/令牌/数据目录（已被 .gitignore 排除，勿提交）
├── .env.example         # 配置模板
├── Dockerfile           # 默认镜像（仅 Python，~150MB，沙箱降级）
├── Dockerfile.verify    # ros:jazzy-ros-base 镜像（真编译验证用，~900MB）
├── docker-compose.yml   # 一条命令部署，命名卷挂 /data
├── start.bat            # Windows 一键启动
├── app/
│   ├── identity.py      # 匿名身份（HMAC 签名 Cookie，uid 由服务端签发）
│   ├── ratelimit.py     # 每分钟限流 + 全站每日保险丝
│   ├── orchestrator.py  # 主调度员状态机（总路由/流水线/选项流转/状态追踪/会话历史）
│   ├── progress.py      # 进度事件（分步状态 + 2 秒心跳 + 耗时统计）
│   ├── graph.py         # ROS2 通信图谱解析（AST：节点/话题/服务/消息类型/连线）
│   ├── graph_api.py     # 图谱 草稿→审阅→提交（commit 才写回代码）
│   ├── project_import.py# 项目导入（文本/二进制/zip·tar 解压 + 结构识别）
│   ├── vision.py        # 截图理解（魔数校验/内容寻址落盘/结构化识别/缓存）
│   ├── versions.py      # 版本单一事实来源（系统版本 + ROS 发行版表 + 提示词约束）
│   ├── sandbox.py       # 编译验证沙箱（docker/local/mock/none 四后端 + 降级）
│   ├── wf_need.py       # 工作流① 需求分析（LLM→校验→修复循环→信封）
│   ├── wf_plan.py       # 工作流② 项目规划（规划JSON→标记块→KB检索→学习指南）
│   ├── wf_code.py       # 工作流③ 代码生成（规格→KB→代码→AST检查→修复→教学）
│   ├── checker_requirement.py  # 原样抽取自 需求分析/template.yml
│   ├── checker_code.py         # 原样抽取自 ros2代码生成/template.yml (Script_B9sf)
│   ├── kb.py            # 知识库 BM25 检索（top_k=5，与原插件一致）
│   ├── store.py         # 学习画像（SQLite：concepts/projects/errors/summary）
│   ├── archive.py       # 旧会话归档（30 天未活动 → archive/<sid>.zip）
│   ├── auth.py          # 访问控制（IP 白名单 + 令牌，可绑定 uid）
│   ├── env.py           # .env 加载（零依赖）
│   ├── paths.py         # 数据目录统一入口（ROS2_DATA_DIR 可指向挂载卷）
│   ├── prompts.py       # 全部提示词（逐字取自工作流与主调度员提示词）
│   └── llm.py           # OpenAI 兼容客户端
├── kb_data/             # 知识库（15 文件 / 1550 块；来源与版本见 SOURCES.md）
├── public/              # 前端（聊天 UI + 画像 + 节点清单 + 文件树 + 通信图谱 + 暗色 + 移动抽屉）
├── samples/             # demo_ros2_ws.zip（示例项目）+ make_dist.py（打包脚本）
├── tests/               # 19 个测试：security/identity/memory/e2e_mock/sandbox_version/
│                        #   multimodal_progress/graph_*/import_*/commit_*/drawer_*/smoke_http
├── data/profile.db      # 学习画像数据库（SQLite，自动创建，可被 ROS2_DATA_DIR 改位置）
├── archive/             # 归档的旧会话 zip
└── sessions/            # 会话状态、生成/导入的工作区文件、graph.json
```

## 记忆系统

三层记忆，长度各不相同：

| 层 | 存在哪 | 保留多久 | 说明 |
|---|---|---|---|
| 短期对话 | `sessions/<sid>/state.json` 的 `history` | 最近 30 条（≈15 轮） | 超过后由 `qwen-flash` 压成摘要，只留最近 20 条原文 |
| 长期摘要 | `state.json` 的 `summary` + 画像表 `summary` | 永久 | 摘要模型把老对话合并成 ≤300 字的滚动记忆，失败时退化为原文拼接 |
| 学习画像 | `data/profile.db` 的 `profiles` 表 | 永久，按 `userId` 区分 | 概念（带次数）、项目（节点进度）、踩过的报错、水平、对话轮次 |

要点：

- `userId` 与 `sessionId` 解耦：点「新对话」只换会话，画像照旧带过来；
- 画像会自动注入「直接回答」与「需求分析」的提示词，所以新会话第一句它就知道你学过什么；
- 归档：服务启动时与之后每 24 小时扫描 `sessions/`，30 天未活动（按目录内最新文件时间）的会话打包成
  `archive/<sid>.zip`，zip 校验通过后才删除原目录；正在使用的会话永远跳过；
- 参数都在 `config.json` 的 `memory` 段：`history_max` / `summary_keep` / `archive_days`。

手动触发归档（`dry_run=true` 只列出不删除）：

```bash
curl -X POST http://localhost:8000/api/archive -H "Content-Type: application/json" -d '{"dry_run": true}'
```

## 对原提示词的修改说明

1. **路由由代码执行**：总路由/流水线/选项流转由确定性状态机执行（规则逐条对应原提示词），
   概念/报错/练习/闲聊四类直接回答仍由 LLM 按原提示词风格生成——更稳定，不会漏走工作流。
2. **文件工具**：原提示词的 write/read/edit（百炼平台工具）→ 服务端会话工作区 + 网页文件树/zip 下载。
3. **知识库**：原平台「检索ROS2知识库」插件 → 内置 BM25 检索器，top_k=5 与原插件参数一致。
4. **模型名**：qwen3.8-max/flash 等 → 映射为当前 DashScope 现行模型，可在 config.json 修改。
5. **兜底规则保留**：工作流失败重试 1 次，仍失败则带「⚠️ 工作流暂时不可用…」标注直接撰写。

## 测试

```bash
python -X utf8 tests/security_test.py   # 密钥外置 / IP 白名单+令牌 / 数据目录（40 项，全离线）
python -X utf8 tests/identity_test.py   # 匿名身份隔离 / 伪造 Cookie / 限流（16 项）
python -X utf8 tests/memory_test.py     # 滚动摘要 / 学习画像 / 归档
python -X utf8 tests/e2e_mock.py        # 三条工作流全流程（桩模型，不产生费用）
python -X utf8 tests/sandbox_version_test.py  # 版本口径 + 编译闭环（离线，不需 Docker）
python -X utf8 tests/multimodal_progress_test.py  # 进度事件/心跳 + 截图链路（桩模型）
python -X utf8 tests/smoke_http.py      # 真起服务 + 真调一次模型，确认密钥与令牌都通
```

改完配置想确认「密钥到底从哪来」，看这两个地方：

```bash
curl -s http://localhost:8000/api/health   # key_source: env:DASHSCOPE_API_KEY / none
python -X utf8 server.py                    # 启动时打印：数据目录、鉴权状态、密钥提示
```
