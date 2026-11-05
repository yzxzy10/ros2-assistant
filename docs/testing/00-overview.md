# ROS2 代码生成验证体系 · 总览与决策建议

> 版本：v1.0（2026-11-06）
> 适用代码基线：`ros2-assistant` v1.2.3
> 关联文档：[方案 A 假 ROS](./01-scheme-a-fake-ros.md) · [方案 B 真 ROS](./02-scheme-b-real-ros.md) · [方案 C 混合](./03-scheme-c-hybrid.md)

---

## 1. 要解决的问题

当前 v1.2.3 的 `app/sandbox.py` 能做到：

```
生成 ament_python 包 → colcon build → 起节点 → ros2 node list
```

它验证的是**"活着"**（编译过、进程起、节点出现），没有验证**"对"**。

后果：

- 学生拿到"✅ 通过"，真机却不动；
- 生成端（LLM）拿不到可执行的反馈，只能靠人看日志猜；
- 失败归因停留在"某个包失败了"，无法定位到"第 18 行字段名拼错"。

---

## 2. 三个必须先厘清的误区

这一节是整个体系的地基，讨论中出现过三次，必须写死。

### 2.1 日志是"自述"，不是"证据"

```python
print("已发布到 /scan")      # ① 先把话说出去
self.pub.publish(msg)        # ② 再做动作，这里抛异常呢？
```

日志会在四种情况下撒谎：

| 情形 | 代码 | 结果 |
|---|---|---|
| 顺序颠倒 | `print` 在前，`publish` 在后 | 抛异常了，日志已发出 |
| 吞异常 | `try: publish() ... except: pass` | 证据被销毁，进程继续跑 |
| 写死字符串 | `print("发布到 /scan")`，实际发到 `"/scan "` | 日志永不报错，只是复述想象 |
| 空消息 | `publish(String())` 但没赋值 | 调用成功，内容是空的 |

**结论：日志由"被检查者"产生，不能作为判据。**

### 2.2 "没有日志" ≠ 异常

正确代码本来就不打日志。三种"安静"从外部看一模一样：

| 类型 | 实际状态 | 日志 |
|---|---|---|
| 正常的安静 | 一切正常 | 空 |
| 错误的安静 | 一条消息没收到 | 空 |
| 假死的安静 | 死锁/卡住 | 空 |

**判据必须是"该有的事件有没有发生"，不是"有没有打印"。**

### 2.3 能跑 ≠ 对

撞墙的车，故障码是干净的。以下情况全部零报错：

- QoS 不兼容 → 一条都收不到
- 话题名差一个字符 → 永远等不到
- 频率写成 0.1Hz → 控制慢得离谱
- 左右转向写反 → 逻辑跑得完美
- 消息字段全 0 → 下游判断永远 False
- 角度没做 ±π 归一化 → 只在特定输入错

---

## 3. 验证的五个层次

| 层 | 回答的问题 | 手段 | 耗时 | 判 fail 是否可信 |
|---|---|---|---|---|
| **L0 静态** | 语法/字段/依赖对不对 | AST、mypy、依赖清单比对 | 毫秒 | 高 |
| **L1 启动** | 崩不崩、异常在哪行 | 假 ROS 或真容器跑一次 | 3 秒 / 分钟 | 高 |
| **L2 连通** | 话题有没有人接、类型/QoS 一致、频率对不对 | graph 静态声明 vs 运行时观测 diff | 秒级 | 中（有假失败） |
| **L3 行为** | 给输入，输出符合预期吗 | 断言（需要期望值） | 秒级 | 高 |
| **L4 系统** | 多节点/仿真整体行为 | launch + Gazebo | 分钟级 | 中 |

当前项目：L0 部分具备（`checker_code.py`）、L1 具备（`sandbox.verify`）、L2–L4 缺失。

---

## 4. 核心定律：两层可信方向相反

```
sim 判 fail   → 几乎可信（就是 Python 崩了，没有环境因素）
sim 判 pass   → 不可信（假环境不代表真环境）

真 ROS 判 pass → 几乎可信（真跑通了）
真 ROS 判 fail → 不可信（可能是环境/时序，不是代码问题）
```

**这是"为什么不能二选一"的数学理由**，也是方案 C 存在的唯一依据。

---

## 5. 三套方案速览

| | 方案 A 假 ROS | 方案 B 真 ROS | 方案 C 混合（推荐） |
|---|---|---|---|
| 文档 | [01](./01-scheme-a-fake-ros.md) | [02](./02-scheme-b-real-ros.md) | [03](./03-scheme-c-hybrid.md) |
| 隔离方式 | Python 打桩，无需 ROS | Docker/VM + 真 ROS 镜像 | A 当前置闸门，B 做终审 |
| 单次耗时 | 毫秒～3 秒 | 1～5 分钟 | 90% 走 A，10% 走 B |
| 依赖体积 | 0 | 镜像 1.5–3 GB | 同 B |
| 能覆盖 | L0 + L1 + 部分 L2 | L0～L4 | 全 |
| 部署位置 | 现有 Python 进程内 | 独立容器/服务器 | 两者都有 |
| 主要风险 | 假通过（mock 漂移） | 假失败（时序/环境） | 编排复杂度 |
| 工作量 | 2～3 人日 | 5～8 人日 | A+B+3 人日 |

**决策建议**：先做 A（立刻见效、零成本），再做 C 的编排骨架（短路规则 + 统一报告），B 的容器能力可以后置到有服务器时。

---

## 6. 三套方案必须共用的契约

无论接哪个后端，下面五件事必须一致，否则报告无法被前端和 LLM 消费。

### 6.1 三态判定（最重要）

```
pass     观测到了，且符合预期
fail     观测到了，且明确不符
unknown  未观测到（没数据 / 没下游 / 超时），无法判断
```

> 绝大多数测试系统只有 pass/fail，把"没观测到"一律判 fail，导致噪音爆炸、学生不信任。
> **三态能把约 90% 的假失败挡在门外。**

### 6.2 统一报告 schema

```json
{
  "schema": "ros2test/v1",
  "task_id": "t_8f2a",
  "trace_id": "tr_7f2a",
  "mode": "simulated",
  "stage": "connectivity",
  "verdict": "fail",
  "duration_ms": 3120,
  "checks": [
    {
      "id": "topic.orphan_publisher",
      "status": "failed",
      "severity": "warning",
      "subject": "/scan",
      "detail": "1 个发布者，0 个订阅者",
      "expected": ">=1 subscriber",
      "actual": "0",
      "locator": { "file": "src/lidar_pkg/lidar_pkg/lidar_node.py", "line": 18 },
      "hint": "若该话题供 RViz/底盘等外部系统使用可忽略；否则检查订阅端话题名"
    }
  ],
  "failure": {
    "kind": "unknown_field",
    "file": "src/lidar_pkg/lidar_pkg/lidar_node.py",
    "line": 18,
    "expected": "ranges",
    "actual": "range"
  },
  "artifacts": { "log_path": "logs/t_8f2a/run.log", "log_tail_lines": 40 },
  "env": { "image_digest": "sha256:...", "ros_distro": "jazzy", "python": "3.12" }
}
```

### 6.3 固定的 `kind` 枚举

`kind` 必须固定，生成端才知道"这一类该怎么修"。

```
syntax | import | missing_dependency | unknown_field | type_mismatch
runtime_exception | timeout | killed
topic_orphan | topic_unmatched | qos_incompatible | empty_message
build_failed | env_missing
```

### 6.4 探针（probe）

没有下游节点时，测试框架自己扮演下游：

| 探针 | 作用 |
|---|---|
| 订阅探针 | 订阅目标话题，证明数据真的流出来了 |
| 发布探针 | 向"有订阅者无发布者"的话题发一条，验证回调能跑 |
| 系统探针 | `ros2 topic list/info/hz`、`ros2 node info` |

### 6.5 全名规范化 + 动态窗口

- 话题名必须先展开 `namespace + remap` 再比对，否则满屏假断线；
- 观测窗口 `= max(3s, 3 × 声明周期)`，否则低频话题必然误判。

---

## 7. 六条假失败来源（L2 必踩）

| 来源 | 后果 | 防护 |
|---|---|---|
| 命名空间/remap 未展开 | 比原字符串必错 | 全名规范化 |
| 低频话题 | 5 秒窗口内 0 条 | 窗口按周期算 |
| DDS 发现延迟 | 探针起晚，错过前几条 | 先起探针；1–2s 预热期不计入 |
| 一次性消息（latched） | 永远收不到 | `transient_local` 或改启动顺序 |
| `ros2 topic hz` QoS 不兼容 | 发布者 best_effort 时收不到 | 显式 `--qos-reliability best_effort` |
| mock 漂移 | sim 绿、真机红 | 抽样比对"sim 过 / 真机挂"样本 |

---

## 8. 风险清单

### 8.1 假环境 ≠ 安全环境（最容易被误解）

假 ROS 只替换了 `rclpy`，**没有替换 `os` / `socket` / `open`**：

```python
os.remove("/important/file")      # 假 ROS 拦不住
open("/etc/passwd").read()        # 照样能读
requests.get("http://evil.com")   # 照样能出网
```

**它省的是"装 ROS"的成本，不是"隔离"的成本。** 任何时候都必须：受限目录、只读挂载、无网络。

### 8.2 其他风险

| 风险 | 影响 | 对策 |
|---|---|---|
| 探针自身有 bug | 假绿/假红 | 探针需要 golden case 自测 |
| 规则主观性（9.5Hz 算不算 10Hz） | 误判 | 容差可配置 + 白名单 + 案例反哺 |
| 修复闭环震荡 | 反复改同一处 | 轮数上限 + 相同失败去重 |
| 报告过长烧 token | 成本 | 只回摘要 + 前 40 行 + artifacts 引用 |
| 并发干扰 | 互相看到对方话题 | 每任务独立 `ROS_DOMAIN_ID` + 独立工作区 |

---

## 9. 路线图建议

| 阶段 | 内容 | 交付判据 | 人日 |
|---|---|---|---|
| **P0** | 统一报告 schema + 三态判定（先定契约，不写后端） | 前端能渲染 checks 列表 | 1 |
| **P1** | 方案 A：`app/ros_sim/` 最小版（12 包 + 6 消息 + 3 条检查） | 生成的节点 3 秒内报出字段拼错 | 2–3 |
| **P2** | L2 静态声明 vs 运行时 diff（`graph.py` + sim 总线） | 能报"孤儿发布/孤儿订阅" | 1–2 |
| **P3** | 方案 B：镜像 + docker 后端 + 探针 | 一个真实项目 build+run+probe 全绿 | 5–8 |
| **P4** | 方案 C：短路规则 + 抽样一致性校验 | 90% 走 sim，平均反馈 < 10s | 3 |

---

## 10. 待团队决策的开放问题

1. **L3 的"期望值"从哪来**：AI 生成代码时同步生成测试用例（推荐）？还是从 `graph.py` 静态声明反推？还是从参考实现？
2. **容器放哪**：本机 Docker、自建云服务器、还是托管沙箱（E2B/Modal 这类）。
3. **是否对外开放**：只给自己/协作者用，容器的安全等级可以降；要给学生开放，必须 gVisor + 断网 + 只读根。
4. **规则白名单归谁维护**：每条规则都会遇到"这是合法场景"的反例，需要有人负责收敛。
