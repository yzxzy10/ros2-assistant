# ROS2 双语言代码生成参考

## 通用原则

- 最少满足需求，不添加无关功能；但构建、安装、启动和必要错误处理不能省略。
- 已有项目优先沿用其语言、目录、依赖和命名风格。
- 关键注释解释“为什么”，避免逐行翻译代码。
- 所有文件给完整内容并标注路径；实际生成到磁盘时按包结构放置。

## rclpy 节点

基本骨架：`import rclpy`、`from rclpy.node import Node`、继承 `Node`、`super().__init__(node_name)`、在 `__init__` 创建通信对象与定时器、`main` 中 `init → spin → destroy_node → shutdown`。

- 禁止把 `rclpy.Node` 当作基类。
- 订阅回调通常是 `callback(self, msg)`，发布/订阅创建参数数量正确且消息类型已导入。
- 简单示例不强制加入参数、生命周期、自定义 QoS 或无关异常处理。
- ament_python 包检查 setup.py 的 `entry_points`、data_files 中 package.xml、launch/配置安装规则，以及 setup.cfg 的脚本安装目录。
- 销毁顺序建议先 `destroy_node()`，再 `rclpy.shutdown()`，并合理处理键盘中断。

## rclcpp 节点

- 使用 `rclcpp::Node`、合适的智能指针和成员变量；`main` 中 `rclcpp::init → spin → shutdown`。
- CMakeLists.txt 使用 `find_package`、`add_executable`、`ament_target_dependencies` 或现代 target 链接方式，并安装 targets。
- 每个源码目标、可执行名、launch executable 和 ros2 run 名称保持一致。
- package.xml 声明构建/运行依赖；包含自定义接口时依赖对应接口包。

## 自定义 msg/srv/action

优先独立 ament_cmake 接口包：

- CMakeLists.txt 配置 `rosidl_default_generators` 和 `rosidl_generate_interfaces`，列出接口文件并声明所需字段类型依赖。
- package.xml 配置 `rosidl_default_generators`、`rosidl_default_runtime` 和 `member_of_group`。
- 节点包显式依赖接口包；先构建接口包，再构建使用方。
- Python 导入形式如 `from package_name.msg import TypeName`；C++ 头文件和命名空间与生成接口一致。
- 使用 `ros2 interface show package_name/msg/TypeName` 验证字段，不凭印象访问嵌套属性。

## 话题、服务、动作和 QoS

- 话题用于持续数据流；服务用于短请求响应；动作用于可反馈、可取消的长任务。
- 异步服务客户端避免在单线程回调中自阻塞等待；动作考虑目标句柄、反馈、结果和取消。
- QoS 至少检查 reliability、durability、history/depth；传感器常用 best effort 时订阅端要兼容，静态地图/锁存数据考虑 transient local。
- 回调避免长耗时阻塞；需要并发时明确 MutuallyExclusive/Reentrant 回调组与 SingleThreaded/MultiThreadedExecutor 的组合。

## 传感器与控制细节

- LaserScan：按 range_min/range_max 和有限值过滤，处理空集合；按需求选择前方角度窗口，而非默认全局最小值。
- Twist：线/角速度字段与单位明确，未使用字段保持合理零值，考虑限幅和停止。
- Image：通过 cv_bridge 转换并检查 encoding，注意 bgr8/rgb8/mono8；重计算不宜阻塞高频率回调。
- 时间/TF：使用消息时间戳和正确 frame；仿真按需 use_sim_time，避免混用系统时间与 ROS 时间。

## 验证层次

1. 静态：Python AST/编译检查、C++ 编译、命名和接口契约一致性。
2. 构建：接口包优先，随后节点包；处理错误后重跑目标包。
3. 启动：加载正确工作空间，仅 source 对应环境，检查节点和 launch。
4. 通信：list/info/echo/hz/interface show，核对类型、QoS、数据与频率。
5. 功能：传感器输入、算法输出、TF、导航/机械臂状态与停止行为。

本地检查器用于 rclpy 初筛，不等价于 colcon build、运行时测试或硬件验收：

```bash
python scripts/check_python_node.py --code path/to/node.py --spec path/to/node_spec.json
```
