# ROS2 成功案例：发布/订阅项目完整解析教学文档

> **项目来源**：[YehiaElkh/ros2_yehia_learning](https://github.com/YehiaElkh/ros2_yehia_learning)（GitHub 开源项目，Apache-2.0 协议）
> **运行环境**：ROS 2 Humble + C++（rclcpp）
> **项目定位**：一个最经典、最适合入门的 ROS2 通信示例，演示了「话题（Topic）发布/订阅」「自定义消息 msg / 服务 srv / 动作 action」以及「QoS 服务质量」三个核心概念。

---

## 1. 项目概述

这个项目由 **两个 ROS2 包** 组成：

| 包名 | 作用 |
|------|------|
| `custom_msg_and_srv` | 自定义接口包：定义自己的消息（msg）、服务（srv）、动作（action） |
| `publisher_and_subscriber` | 节点包：包含 4 个可执行节点，演示发布/订阅 |

它解决了一个完整的问题：**一个节点（发布者）持续产生数据，另一个节点（订阅者）接收并处理这些数据**。数据既可以是 ROS2 自带的 `std_msgs/msg/String`（字符串），也可以是项目自己定义的 `EmployeeSalary`（员工薪资）消息。

**为什么适合当"成功案例"**：它把 ROS2 最核心的通信机制用最短的代码讲清楚，代码结构规范、注释充分，是理解 ROS2 分布式节点通信的黄金范本。

---

## 2. 项目整体目录结构

```
ros2_yehia_learning/
└── ros2_fundamentals/
    ├── custom_msg_and_srv/          # 包一：自定义接口包
    │   ├── CMakeLists.txt           # 构建脚本（关键：rosidl_generate_interfaces）
    │   ├── package.xml              # 包描述文件
    │   ├── msg/
    │   │   └── EmployeeSalary.msg   # 自定义消息：员工薪资
    │   ├── srv/
    │   │   └── AddTwoInts.srv       # 自定义服务：两个整数相加
    │   └── action/
    │       └── Concatenate.action   # 自定义动作：字符串拼接
    │
    └── publisher_and_subscriber/    # 包二：发布订阅包
        ├── CMakeLists.txt           # 构建脚本（声明 4 个可执行文件）
        ├── package.xml              # 包描述文件（依赖 custom_msg_and_srv）
        └── src/
            ├── simple_publisher.cpp             # 节点1：简单发布者（字符串）
            ├── simple_subscriber.cpp            # 节点2：简单订阅者（字符串）
            ├── publisher_with_custom_msg.cpp    # 节点3：自定义消息发布者
            └── subscriber_with_custom_msg.cpp   # 节点4：自定义消息订阅者
```

---

## 3. 包一：custom_msg_and_srv（自定义接口包）

在 ROS2 中，**接口（消息/服务/动作）必须单独放在一个包里**，这样其它包才能引用。这个包不包含任何可运行的节点，它只负责"定义数据类型"。

### 3.1 package.xml —— 包描述文件

```xml
<?xml version="1.0"?>
<package format="3">
  <name>custom_msg_and_srv</name>
  <version>0.0.1</version>
  <description>Custom messages and services definitions</description>
  <maintainer email="yehiaelkh@gmail.com">Yehia Elkh</maintainer>
  <license>Apache-2.0</license>
  <buildtool_depend>ament_cmake</buildtool_depend>
  <buildtool_depend>rosidl_default_generators</buildtool_depend>
  <exec_depend>rosidl_default_runtime</exec_depend>
  <member_of_group>rosidl_interface_packages</member_of_group>
  <export>
    <build_type>ament_cmake</build_type>
  </export>
</package>
```

**逐项说明：**

- `<name>`：包名，必须和文件夹名、CMakeLists 里的 project 名一致。
- `<buildtool_depend>rosidl_default_generators</buildtool_depend>`：**关键**。这是生成接口代码（把 .msg/.srv/.action 编译成 C++/Python 代码）所必需的构建工具。
- `<exec_depend>rosidl_default_runtime</exec_depend>`：运行期依赖，保证编译好的接口代码在运行时可用。
- `<member_of_group>rosidl_interface_packages</member_of_group>`：**接口包特有的声明**。告诉 ROS2"这个包是接口包"，缺少这一行会导致接口无法被正确发现。
- `<build_type>ament_cmake</build_type>`：使用 ament_cmake 构建体系（C++ 包）。

### 3.2 CMakeLists.txt —— 构建脚本

```cmake
cmake_minimum_required(VERSION 3.8)
project(custom_msg_and_srv)

if(CMAKE_COMPILER_IS_GNUCXX OR CMAKE_CXX_COMPILER_ID MATCHES "Clang")
  add_compile_options(-Wall -Wextra -Wpedantic)
endif()

find_package(ament_cmake REQUIRED)
find_package(rosidl_default_generators REQUIRED)

# 声明并生成接口文件
rosidl_generate_interfaces(${PROJECT_NAME}
  "msg/EmployeeSalary.msg"
  "srv/AddTwoInts.srv"
  "action/Concatenate.action"
)

ament_package()
```

**逐项说明：**

- `find_package(rosidl_default_generators REQUIRED)`：引入接口代码生成器。
- `rosidl_generate_interfaces(...)`：**这是整个接口包的核心**。它把 `.msg`、`.srv`、`.action` 三种接口文件编译成对应的 C++ 头文件（如 `employee_salary.hpp`）和类型支持代码。
- `ament_package()`：结束标志，每个 ament_cmake 包都必须有。

### 3.3 msg/EmployeeSalary.msg —— 自定义消息

```
string first_name
string last_name
float32 salary
```

**说明：** 这是一个"员工薪资"消息，包含三个字段：

| 字段 | 类型 | 含义 |
|------|------|------|
| first_name | string | 名字 |
| last_name | string | 姓氏 |
| salary | float32 | 薪资（32位浮点数） |

编译后会生成 C++ 类型 `custom_msg_and_srv::msg::EmployeeSalary`，节点可以直接用 `msg.first_name`、`msg.salary` 访问字段。

### 3.4 srv/AddTwoInts.srv —— 自定义服务

```
int64 a
int64 b
---
int64 sum
```

**说明：** 服务（Service）是"请求-响应"模式。分隔线 `---` 上面是**请求部分**，下面是**响应部分**：

| 部分 | 字段 | 含义 |
|------|------|------|
| 请求 | a、b（int64） | 客户端发来的两个整数 |
| 响应 | sum（int64） | 服务端返回的两数之和 |

### 3.5 action/Concatenate.action —— 自定义动作

```
# GOAL —— 客户端发送的目标
string first_string
string second_string
---
# RESULT —— 任务完成后的最终结果
string concatenated_string
---
# FEEDBACK —— 执行过程中的进度反馈
string current_status
```

**说明：** 动作（Action）比服务更高级，适合**长时间运行的任务**（如导航、机械臂移动）。它有三部分，用两个 `---` 分隔：

| 部分 | 字段 | 含义 |
|------|------|------|
| Goal（目标） | first_string、second_string | 任务目标（要拼接的两个字符串） |
| Result（结果） | concatenated_string | 最终结果（拼接后的字符串） |
| Feedback（反馈） | current_status | 执行过程中的实时状态 |

---

## 4. 包二：publisher_and_subscriber（发布订阅包）

这个包包含 **4 个可执行节点**，是项目真正的"干活"部分。

### 4.1 package.xml —— 包描述文件

```xml
<?xml version="1.0"?>
<package format="3">
  <name>publisher_and_subscriber</name>
  <version>0.0.1</version>
  <description>Publisher and Subscriber examples in C++</description>
  <maintainer email="yehiaelkh@gmail.com">Yehia Elkh</maintainer>
  <license>Apache-2.0</license>
  <buildtool_depend>ament_cmake</buildtool_depend>
  <depend>rclcpp</depend>
  <depend>std_msgs</depend>
  <depend>custom_msg_and_srv</depend>
  <export>
    <build_type>ament_cmake</build_type>
  </export>
</package>
```

**关键点：**

- `<depend>rclcpp</depend>`：C++ 客户端库。
- `<depend>std_msgs</depend>`：标准消息（含 String 类型）。
- `<depend>custom_msg_and_srv</depend>`：**依赖刚才的自定义接口包**，这样本包才能使用 `EmployeeSalary` 消息。

### 4.2 CMakeLists.txt —— 构建脚本

```cmake
cmake_minimum_required(VERSION 3.8)
project(publisher_and_subscriber)

find_package(ament_cmake REQUIRED)
find_package(rclcpp REQUIRED)
find_package(std_msgs REQUIRED)
find_package(custom_msg_and_srv REQUIRED)

add_executable(simple_publisher src/simple_publisher.cpp)
ament_target_dependencies(simple_publisher rclcpp std_msgs)

add_executable(simple_subscriber src/simple_subscriber.cpp)
ament_target_dependencies(simple_subscriber rclcpp std_msgs)

add_executable(publisher_with_custom_msg src/publisher_with_custom_msg.cpp)
ament_target_dependencies(publisher_with_custom_msg rclcpp custom_msg_and_srv)

add_executable(subscriber_with_custom_msg src/subscriber_with_custom_msg.cpp)
ament_target_dependencies(subscriber_with_custom_msg rclcpp custom_msg_and_srv)

install(TARGETS
  simple_publisher
  simple_subscriber
  publisher_with_custom_msg
  subscriber_with_custom_msg
  DESTINATION lib/${PROJECT_NAME}
)

ament_package()
```

**关键点：**

- `add_executable(可执行文件名 源文件路径)`：把每个 .cpp 编译成一个可执行节点。
- `ament_target_dependencies(目标 依赖...)`：为每个可执行文件指定它依赖的 ROS2 包。
- `install(TARGETS ... DESTINATION lib/${PROJECT_NAME})`：把编译好的可执行文件安装到 `lib` 目录，`ros2 run` 才能找到它们。

### 4.3 节点1：simple_publisher.cpp（简单发布者）

```cpp
#include "rclcpp/rclcpp.hpp"
#include "std_msgs/msg/string.hpp"

class SimplePublisher : public rclcpp::Node
{
public:
    SimplePublisher() : Node("simple_publisher"), count_(0)
    {
        // 1. 创建发布者：发布 String 类型消息到 "chatter" 话题
        publisher_ = this->create_publisher<std_msgs::msg::String>(
            "chatter",   // 话题名
            10           // QoS：队列深度，保留最近 10 条消息
        );

        // 2. 创建定时器：每 5000 毫秒（5 秒）触发一次回调
        timer_ = this->create_wall_timer(
            std::chrono::milliseconds(5000),
            std::bind(&SimplePublisher::timerCallback, this)
        );

        RCLCPP_INFO(this->get_logger(), "Publisher started !");
    }

private:
    void timerCallback()
    {
        auto msg = std_msgs::msg::String();
        msg.data = "Hello ROS2 n:" + std::to_string(count_++);
        RCLCPP_INFO(this->get_logger(), "Publishing: '%s'", msg.data.c_str());
        publisher_->publish(msg);   // 发布消息
    }

    rclcpp::Publisher<std_msgs::msg::String>::SharedPtr publisher_;
    rclcpp::TimerBase::SharedPtr timer_;
    int count_;
};

int main(int argc, char **argv)
{
    rclcpp::init(argc, argv);                      // 初始化 ROS2
    rclcpp::spin(std::make_shared<SimplePublisher>()); // 进入事件循环，阻塞直到 Ctrl+C
    rclcpp::shutdown();                            // 清理
    return 0;
}
```

**功能说明：**

- **节点名**：`simple_publisher`
- **发布的话题**：`chatter`，消息类型 `std_msgs::msg::String`
- **QoS 队列深度**：`10`
- **工作流程**：定时器每 5 秒触发一次 `timerCallback`，生成一条 "Hello ROS2 n:序号" 字符串并发布出去。
- **main 函数三步走**：`init`（初始化）→ `spin`（事件循环，程序停在这里处理定时器/消息）→ `shutdown`（退出清理）。

> **注意**：代码里定时器周期写的是 `5000`（5 秒），但注释写的是 "500ms"。这属于原作者的一处笔误——实际运行是每 5 秒发一条。对比节点3（`500ms`）就能看出差异。

### 4.4 节点2：simple_subscriber.cpp（简单订阅者）

```cpp
#include "rclcpp/rclcpp.hpp"
#include "std_msgs/msg/string.hpp"

class SimpleSubscriber : public rclcpp::Node
{
public:
    SimpleSubscriber() : Node("simple_subscriber")
    {
        // 订阅 "chatter" 话题，类型必须和发布者一致
        subscriber_ = this->create_subscription<std_msgs::msg::String>(
            "chatter", 10,
            std::bind(&SimpleSubscriber::messageCallback, this, std::placeholders::_1)
        );
        RCLCPP_INFO(this->get_logger(), "Subscriber started !");
    }

private:
    // 收到消息时自动执行的回调
    void messageCallback(const std_msgs::msg::String::SharedPtr msg)
    {
        RCLCPP_INFO(this->get_logger(), "Received: '%s'", msg->data.c_str());
    }

    rclcpp::Subscription<std_msgs::msg::String>::SharedPtr subscriber_;
};

int main(int argc, char **argv)
{
    rclcpp::init(argc, argv);
    rclcpp::spin(std::make_shared<SimpleSubscriber>());
    rclcpp::shutdown();
    return 0;
}
```

**功能说明：**

- **节点名**：`simple_subscriber`
- **订阅的话题**：`chatter`（和发布者一致），消息类型 `std_msgs::msg::String`
- **回调函数** `messageCallback`：每次收到消息自动执行，打印消息内容。
- **`std::placeholders::_1`**：占位符，表示回调函数接收的第一个参数（即消息本身）。
- **`SharedPtr`（智能指针）**：`msg` 用共享指针传递，自动管理内存，无需手动 delete。

### 4.5 节点3：publisher_with_custom_msg.cpp（自定义消息发布者）

```cpp
#include "rclcpp/rclcpp.hpp"
#include "custom_msg_and_srv/msg/employee_salary.hpp"

class PublisherCustomMsg : public rclcpp::Node
{
public:
    PublisherCustomMsg() : Node("publisher_custom_msg"), count_(0)
    {
        // 发布自定义消息 EmployeeSalary 到 "employee" 话题
        publisher_ = this->create_publisher<custom_msg_and_srv::msg::EmployeeSalary>("employee", 10);

        // 每 500 毫秒发布一次
        timer_ = this->create_wall_timer(
            std::chrono::milliseconds(500),
            std::bind(&PublisherCustomMsg::timerCallback, this)
        );
        RCLCPP_INFO(this->get_logger(), "Publisher Custom Msg started !");
    }

private:
    void timerCallback()
    {
        auto msg = custom_msg_and_srv::msg::EmployeeSalary();
        msg.first_name = "John";
        msg.last_name  = "Doe";
        msg.salary     = 3000.0 + count_ * 100.0;  // 薪资每次递增 100
        count_++;
        RCLCPP_INFO(this->get_logger(), "Publishing: %s %s - %.2f",
            msg.first_name.c_str(), msg.last_name.c_str(), msg.salary);
        publisher_->publish(msg);
    }

    rclcpp::Publisher<custom_msg_and_srv::msg::EmployeeSalary>::SharedPtr publisher_;
    rclcpp::TimerBase::SharedPtr timer_;
    int count_;
};

int main(int argc, char **argv)
{
    rclcpp::init(argc, argv);
    rclcpp::spin(std::make_shared<PublisherCustomMsg>());
    rclcpp::shutdown();
    return 0;
}
```

**功能说明：**

- **节点名**：`publisher_custom_msg`
- **发布的话题**：`employee`，消息类型是**自定义的** `custom_msg_and_srv::msg::EmployeeSalary`
- **头文件引入**：`#include "custom_msg_and_srv/msg/employee_salary.hpp"`——注意自定义消息的头文件是**小写蛇形命名**（`employee_salary`），而消息类型是**大写驼峰**（`EmployeeSalary`）。
- **工作流程**：每 0.5 秒发布一条员工信息，薪资从 3000 开始每次 +100。

### 4.6 节点4：subscriber_with_custom_msg.cpp（自定义消息订阅者）

```cpp
#include "rclcpp/rclcpp.hpp"
#include "custom_msg_and_srv/msg/employee_salary.hpp"

class SubscriberCustomMsg : public rclcpp::Node
{
public:
    SubscriberCustomMsg() : Node("subscriber_custom_msg")
    {
        subscriber_ = this->create_subscription<custom_msg_and_srv::msg::EmployeeSalary>(
            "employee", 10,
            std::bind(&SubscriberCustomMsg::messageCallback, this, std::placeholders::_1)
        );
        RCLCPP_INFO(this->get_logger(), "Subscriber Custom Msg started !");
    }

private:
    void messageCallback(const custom_msg_and_srv::msg::EmployeeSalary::SharedPtr msg)
    {
        RCLCPP_INFO(this->get_logger(), "Received: %s %s - %.2f",
            msg->first_name.c_str(), msg->last_name.c_str(), msg->salary);
    }

    rclcpp::Subscription<custom_msg_and_srv::msg::EmployeeSalary>::SharedPtr subscriber_;
};

int main(int argc, char **argv)
{
    rclcpp::init(argc, argv);
    rclcpp::spin(std::make_shared<SubscriberCustomMsg>());
    rclcpp::shutdown();
    return 0;
}
```

**功能说明：**

- **节点名**：`subscriber_custom_msg`
- **订阅的话题**：`employee`（和节点3一致），消息类型 `EmployeeSalary`
- **回调函数**：打印收到的员工姓名和薪资（`%.2f` 表示保留两位小数）。

---

## 5. 关键知识点总结

### 5.1 QoS（服务质量）

`create_publisher` 和 `create_subscription` 的第三个参数 `10` 就是 QoS 的**队列深度（depth）**，含义是"最多缓存最近 10 条消息"。深度越大，能容忍短暂的处理延迟，但内存占用越高。发布者和订阅者的 **QoS 必须兼容**，否则会出现"收不到消息"的静默失败。

### 5.2 话题（Topic）通信

话题是 ROS2 最基础的**多对多异步通信**机制：

- 发布者（Publisher）把消息发到某个命名话题（如 `chatter`、`employee`）。
- 订阅者（Subscriber）订阅同名话题即可收到消息。
- **发布者和订阅者必须使用相同的话题名和相同的消息类型**，否则无法通信。

### 5.3 自定义接口（msg / srv / action）

| 接口类型 | 文件后缀 | 通信模式 | 适用场景 |
|---------|---------|---------|---------|
| 消息 msg | `.msg` | 单向发布/订阅 | 持续的数据流（传感器、状态） |
| 服务 srv | `.srv` | 请求-响应（一问一答） | 短任务（查询、计算） |
| 动作 action | `.action` | 目标-反馈-结果 | 长任务（导航、抓取） |

接口文件必须放在独立的接口包中，并用 `rosidl_generate_interfaces` 生成代码。

### 5.4 定时器与回调

- `create_wall_timer(周期, 回调)`：创建一个定时器，周期性触发回调函数。
- 回调函数（Callback）不是主动调用的，而是由 ROS2 在事件发生时**自动调用**。
- 所有回调都在 `rclcpp::spin()` 的事件循环中被处理。

### 5.5 spin 事件循环

`rclcpp::spin(node)` 是节点的"心脏"，它会阻塞并持续处理定时器、订阅消息等事件。程序必须停在 `spin` 里，节点才能正常工作；一旦 `spin` 返回（通常是按 Ctrl+C），节点就停止。

---

## 6. 编译与运行

### 6.1 编译

```bash
# 1. 创建工作空间并放入源码（src/ 目录下）
mkdir -p ~/ros2_ws/src

# 2. 编译（必须先编译接口包，再编译依赖它的包）
source /opt/ros/humble/setup.bash
cd ~/ros2_ws
colcon build --symlink-install

# 3. 刷新环境
source install/setup.bash
```

**重要：** 自定义接口包 `custom_msg_and_srv` 必须先于 `publisher_and_subscriber` 编译，否则后者会报"找不到 `employee_salary.hpp`"或"找不到包 custom_msg_and_srv"。

### 6.2 运行

打开多个终端，每个终端都要先 `source install/setup.bash`。

```bash
# 终端1：运行简单发布者
ros2 run publisher_and_subscriber simple_publisher

# 终端2：运行简单订阅者（会打印 "Received: Hello ROS2 n:..."）
ros2 run publisher_and_subscriber simple_subscriber

# 终端3：运行自定义消息发布者
ros2 run publisher_and_subscriber publisher_with_custom_msg

# 终端4：运行自定义消息订阅者
ros2 run publisher_and_subscriber subscriber_with_custom_msg
```

### 6.3 验证

```bash
ros2 node list                  # 查看当前运行的节点
ros2 topic list                 # 查看所有话题（应包含 /chatter 和 /employee）
ros2 topic echo /chatter        # 手动监听 /chatter 话题，验证数据
ros2 topic info /employee -v    # 查看 /employee 话题的类型和 QoS 详情
```

---

## 7. 常见错误与避坑

| 现象 | 原因 | 解决办法 |
|------|------|---------|
| 编译报找不到 `employee_salary.hpp` | 接口包没先编译 | 先 `colcon build --packages-select custom_msg_and_srv` |
| 运行时报 `No executable found` | 没 source 工作空间 | 每个新终端执行 `source install/setup.bash` |
| 订阅者收不到消息 | 话题名/消息类型/QoS 不一致 | 用 `ros2 topic info /topic -v` 核对 |
| 节点卡死不响应 | 回调里写了 `sleep` 或耗时操作 | 耗时操作放到独立线程或多线程 executor |
| 定时器周期异常 | 周期单位写错（5000 是 5 秒不是 500 毫秒） | 确认 `std::chrono::milliseconds()` 参数 |

---

## 8. 学习总结

这个项目用 **两个包、四个节点、三种接口**，完整演示了 ROS2 分布式通信的核心套路：

1. **接口先行**：先用独立包定义好消息/服务/动作（`custom_msg_and_srv`）。
2. **节点分工**：发布者产生数据，订阅者消费数据，两者通过话题解耦、互不直接调用。
3. **统一配置**：话题名、消息类型、QoS 三要素必须匹配，通信才能建立。
4. **标准骨架**：每个节点都是"定义类 → 创建发布/订阅/定时器 → 写回调 → main 里 init/spin/shutdown"的固定模板。

掌握了这个模板，就掌握了 ROS2 中最常用的 80% 的通信场景，是进入机器人、自动驾驶、无人系统开发的基础。
