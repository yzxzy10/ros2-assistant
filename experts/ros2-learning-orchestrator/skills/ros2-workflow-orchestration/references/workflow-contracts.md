# 工作流接口契约

本文件只描述三个既有工作流的对接方式，不改变主 Agent 提示词中的路由、确认和输出规则。

## 1. 需求分析

**工具名：** `需求分析`

**主输入：** 学生需求的完整自然语言描述。若有追问补充，必须合并原始需求、追问问题和学生回答；需要表达学生水平时，也在描述中明确 beginner 或 advanced。

**模板自定义输入：**

- `leader_context`：主 Agent 的初步拆解，不得覆盖学生原话。
- `history`：追问后的历史补充，首轮为空字符串。

若运行平台只提供 prompt/query 入参，把完整描述放入 query；若能传递 biz_params，再同步提供上述字段。

**输出：** JSON 信封，主 Agent 关注 `valid`、`need_ask`、`ask_question`、`data`。

- `need_ask=true`：原样透传选择题，记录追问待答，最多追问两次。
- `need_ask=false`：保留 `data` 的完整 JSON 原文，作为下一工作流的唯一输入。
- 解析失败或 `valid=false`：按主规则重试一次，再进入兜底。

## 2. ros2项目规划

**工具名：** `ros2项目规划`

**模板自定义输入：** `requirement_json`。

**主输入：** 需求分析信封中 `data` 字段的完整 JSON 原文。禁止摘要、改写、重排字段或再包一层自然语言说明。

**输出：**

- 标记块之前的正文：学习指南，原样保留给学生。
- `<!--NODE_LIST_START-->...<!--NODE_LIST_END-->`：**节点数组**，直接取数组元素，仅供内部状态使用。
- `<!--PLAN_JSON_START-->...<!--PLAN_JSON_END-->`：**完整规划对象**，直接使用 `project`、`nodes`、`topic_overview`、`dependencies`、`file_structure` 字段，只用于按主提示词模板渲染规划书。

不得向学生显示标记、原始 plan JSON 或 node_list JSON。节点规格必须保留 `node_name/class_name/file_name/what_to_do/core_logic/publish_topics/subscribe_topics`，后续按 `node_name` 精确匹配。

## 3. ros2代码生成

**工具名：** `ros2代码生成`

**模板自定义输入：** `node_spec_json`。

**主输入：** node_list 中一个节点的完整 JSON 原文，包括 `node_name`、`class_name`、`file_name`、`what_to_do`、`core_logic`、`publish_topics`、`subscribe_topics`；保留规划给出的其他字段，不自行补写另一套规格。

**输出：** `<!--ENV_START-->...<!--ENV_END-->` 信封。主 Agent 内部解析有效状态、代码、检查结果、摘要和教学内容，不向学生展示信封。

- 无效或代码为空：重试一次；仍失败只报告该节点失败，不阻塞其他节点。
- 检查未完全通过：仍展示代码，并附上主提示词规定的警告行。
- 成功：展示节点标题、代码和教学说明，立即保存到主提示词规定的功能包路径，并更新已生成清单。

## 状态一致性检查

1. 待办优先级高于新路由，不重复询问已有信息。
2. 项目模式保存需求 data、节点规格、生成状态和学生水平；直接回答不清空这些状态。
3. 已输出过的规划书和学习指南不长期重复放入上下文，只保留节点清单和必要状态。
4. 遵守规划确认门槛、单节点精确匹配、每批最多两个节点和已生成节点去重。
5. 修改已有代码时先读取再精确编辑，不重新生成整个文件。
