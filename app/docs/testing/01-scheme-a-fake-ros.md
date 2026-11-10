# 方案 A：假 ROS（Python 打桩）

> 定位：**3 秒级前置闸门**，专门挡"跑不起来"。
> 关联：[总览](./00-overview.md) · [方案 B](./02-scheme-b-real-ros.md) · [方案 C](./03-scheme-c-hybrid.md)

---

## 1. 一句话

不发真 ROS，用几百行 Python 顶替 `rclpy` 和常用消息包，让生成的节点"假装在 ROS 里跑一遍"，把错误定位到**文件:行**。

---

## 2. 能做什么 / 不能做什么

| ✅ 能（占低级错误大头） | ❌ 不能（必须真 ROS） |
|---|---|
| `import` 失败、包名拼错 | 真实 DDS 通信 |
| 消息字段名错（`msg.range` vs `ranges`） | QoS 兼容性（reliable vs best_effort） |
| 回调里未定义变量、`AttributeError`、除零 | 消息序列化、自定义 `.msg` 需编译 |
| 话题名拼错、发布订阅不匹配 | C++ 节点、CMake 编译 |
| `setup.py` entry_points 与 `main()` 不符 | install 空间能否 source |
| 运行时崩溃（traceback 精确到行列） | 启动顺序、多进程时序、TF 实时性 |

**一句话边界：它证明"逻辑没崩"，不证明"能跑"。**

---

## 3. 架构

```mermaid
flowchart LR
    A[生成的节点代码] -->|import rclpy| B[loader: import hook]
    B -->|有真 rclpy| C[真实 ROS]
    B -->|无真 rclpy 且 ROS2_SIM=1| D[fake_rclpy]
    D --> E[虚拟总线 Bus]
    E --> F[事件记录<br/>publish / subscribe / timer]
    F --> G[检查器<br/>字段 / 连线 / 合理性]
    G --> H[报告 ros2test/v1]
```

关键点：**节点代码一个字不改**，靠 `sys.meta_path` 的 finder 让 `import rclpy` 落到假模块。

---

## 4. 目录结构

```
app/ros_sim/
├── __init__.py
├── bus.py          # 虚拟总线：记录 pub/sub/publish/timer 事件
├── msgdefs.py      # 消息类生成（dataclass，严格字段）
├── fake_rclpy.py   # Node / Publisher / Subscription / Timer / Rate / Parameter / spin
├── fake_msgs.py    # std_msgs / geometry_msgs / sensor_msgs / nav_msgs 等 12 个包的入口
├── loader.py       # import hook：有真 rclpy 时优先真货
├── checks.py       # 检查规则：字段、连线、合理性
└── runner.py       # 子进程执行 + 超时 + 报告输出
```

---

## 5. 核心实现

### 5.1 虚拟总线（一切证据的来源）

```python
# bus.py
import time

class Bus:
    def __init__(self):
        self.pubs = []          # [(topic, msg_type, node)]
        self.subs = []          # [(topic, msg_type, node)]
        self.events = []        # 全量事件流
        self.t0 = time.time()

    def record(self, kind, **kw):
        kw["kind"] = kind
        kw["t"] = round(time.time() - self.t0, 4)
        self.events.append(kw)

BUS = Bus()
```

### 5.2 消息定义：必须严格

```python
# msgdefs.py
from dataclasses import dataclass, fields as dc_fields

@dataclass
class LaserScan:
    angle_min: float = 0.0
    angle_max: float = 0.0
    ranges: list = None
    intensities: list = None
```

> **绝对禁止用 `__getattr__` 兜底返回默认值。**
> 否则 `msg.range` 写成 `msg.ranges` 会静默返回一个假值，bug 被吞掉，这一层就白做了。

字段来源优先级：**项目内 `.msg` > `kb_data/msg格式.txt` > 内置常见定义**。

### 5.3 假 rclpy

```python
# fake_rclpy.py
from .bus import BUS
from .checks import assert_fields

class Publisher:
    def __init__(self, node, msg_type, topic):
        self.node, self.t, self.topic = node, msg_type, topic

    def publish(self, msg):
        assert_fields(self.t, msg)                    # 字段/类型严格校验
        BUS.record("publish", node=self.node, topic=self.topic,
                   type=self.t.__name__, fields=snapshot(msg))


class Node:
    def __init__(self, name):
        self._name = name
        BUS.record("node_init", node=name)

    def create_publisher(self, msg_type, topic, qos=10):
        BUS.record("declare_pub", node=self._name, topic=topic, type=msg_type.__name__)
        BUS.pubs.append((topic, msg_type, self._name))
        return Publisher(self._name, msg_type, topic)

    def create_subscription(self, msg_type, topic, callback, qos=10):
        def wrapper(msg):                              # 框架盖章，不是学生 print
            BUS.record("recv", node=self._name, topic=topic, type=msg_type.__name__)
            return callback(msg)
        BUS.record("declare_sub", node=self._name, topic=topic, type=msg_type.__name__)
        BUS.subs.append((topic, msg_type, self._name))
        return Subscription(topic, wrapper, msg_type)

    def create_timer(self, period_sec, callback):
        return Timer(period_sec, callback)

    def get_logger(self):
        return Logger(self._name)


def spin(node):
    """虚拟时钟：推 N 个 tick 就退出，绝不真等。"""
    for _ in range(TICKS):          # 默认 10
        for t in Timer.all_timers:
            t.tick()
```

### 5.4 import hook：有真 ROS 就让位

```python
# loader.py
import sys, importlib.abc, importlib.machinery

class RosFinder(importlib.abc.MetaPathFinder, importlib.abc.Loader):
    def find_spec(self, fullname, path=None, target=None):
        if not _should_hijack(fullname):
            return None
        return importlib.machinery.ModuleSpec(fullname, self)

def _should_hijack(name):
    if os.getenv("ROS2_SIM") != "1":
        return False
    root = name.split(".")[0]
    if root not in {"rclpy", "std_msgs", "geometry_msgs", "sensor_msgs",
                    "nav_msgs", "std_srvs", "action_msgs", "builtin_interfaces",
                    "tf2_ros", "launch"}:
        return False
    return _real_ros_absent(root)      # 真装了 ROS 就绝不抢
```

---

## 6. 判定规则

### 6.1 三态

| 状态 | 条件 |
|---|---|
| `pass` | 跑完无异常，且声明的话题都有对端或标记为外部接口 |
| `fail` | 抛出异常 / 字段不存在 / 类型不符 |
| `unknown` | 只有发布者没有订阅者、或反之，且不是内部声明的边 |

### 6.2 三类检查

**① 结构检查（fail 级）**

- 字段不存在：`LaserScan 没有 range 字段，应为 ranges`
- 字段类型不符：`ranges 应为 list，实际 float`
- `import` 的包不在清单：`missing_dependency: geometry_msgs`

**② 连线检查（warning / unknown）**

- `topic_orphan`：有发布无订阅（且是内部声明的边 → warning；否则 unknown）
- `topic_unmatched`：声明了 A→B，但 B 没订阅同名话题
- `type_mismatch`：同名话题两端类型不同

**③ 合理性检查（不需要期望值，价值最高）**

| 检查 | 说明 |
|---|---|
| 数组全 0 / 全 NaN / 全相同 | 传感器数据未赋值，直接 publish 默认对象 |
| `header.stamp` 为 0 | ROS 新手第一大坑 |
| `frame_id` 为空字符串 | TF 查不到 |
| 数值越界（距离 -1、1e9） | 单位搞错或未初始化 |
| 数组长度与声明不符（该 360 却 0） | 数据没填满 |
| 频率为 0 或异常高 | 定时器写错 / 死循环 |

---

## 7. 报告示例

```json
{
  "schema": "ros2test/v1",
  "mode": "simulated",
  "stage": "run",
  "verdict": "fail",
  "duration_ms": 240,
  "failure": {
    "kind": "unknown_field",
    "file": "src/lidar_pkg/lidar_pkg/lidar_node.py",
    "line": 18,
    "expected": "ranges",
    "actual": "range",
    "hint": "第 18 行改为 msg.ranges"
  },
  "checks": [
    { "id": "msg.unassigned_array", "status": "failed", "severity": "error",
      "subject": "/scan", "detail": "ranges len=360, min=0, max=0，疑似未赋值" },
    { "id": "header.stamp_zero", "status": "failed", "severity": "warning",
      "subject": "/scan", "detail": "header.stamp = 0" },
    { "id": "topic.orphan_publisher", "status": "unknown", "severity": "info",
      "subject": "/scan", "detail": "1 个发布者，0 个订阅者" }
  ]
}
```

---

## 8. 里程碑

| 阶段 | 内容 | 验收 | 人日 |
|---|---|---|---|
| M1 | `bus.py` + `fake_rclpy.py` + `runner.py` | 能跑通一个最简单的 talker 节点 | 1 |
| M2 | 12 个包 + 6 个高频消息 + 字段校验 | 字段拼错能报出文件:行 | 1 |
| M3 | 6 条合理性检查 + 三态报告 | 空消息 / stamp=0 能报 warning | 0.5 |
| M4 | 接入 `sandbox.py` 作为 `sim` 后端 | 一行开关切换 sim / docker | 0.5 |

---

## 9. 风险与对策

| 风险 | 说明 | 对策 |
|---|---|---|
| **mock 漂移** | 假 rclpy 不校验 QoS、不模拟异步 → sim 绿真机红 | 抽样比对"sim 过 / 真机挂"样本，反哺修正 |
| **假环境不隔离** | Python 照样能 `os.remove` / 出网 | 受限目录 + 只读挂载 + 无网络（见总览 8.1） |
| **消息类过宽容** | `__getattr__` 兜底把 bug 吞掉 | 强制 dataclass，禁止兜底 |
| **覆盖不全** | 只做 12 个包 | 缺口只报 `missing_dependency`，不静默跳过 |
| **报告污染** | 一次性打印 92 万个数字 | `snapshot()` 只取前 8 项 + min/max/len |

---

## 10. 验收标准

1. 生成的节点代码**一字不改**即可在 sim 下运行；
2. 字段拼错能在 **3 秒内**返回 `file:line:expected`；
3. 报告与方案 B 共用同一 schema，仅 `mode` 不同；
4. `ROS2_SIM=0` 时行为与当前完全一致（零回归）；
5. 假环境运行在受限目录 + 无网络下，不产生任何真实副作用。
