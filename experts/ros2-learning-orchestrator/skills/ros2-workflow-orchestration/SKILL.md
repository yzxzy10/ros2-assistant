---
name: ros2-workflow-orchestration
description: Contracts and source templates for the exact 需求分析, ros2项目规划, and ros2代码生成 workflow tools used by the ROS2 learning orchestrator.
---

# ROS2 Workflow Orchestration

Use this skill only to support the main agent's routing rules. The main agent answers concepts, troubleshooting, exercises, learning-path questions, and follow-ups itself. Delivery-level plans and code must come from the named workflows unless the main agent has already exhausted the allowed retry and must use its labeled fallback.

## Exact tool names

1. `需求分析`
2. `ros2项目规划`
3. `ros2代码生成`

Never invent aliases. The packaged planning template has been normalized from its original internal name `ros项目规划` to `ros2项目规划` so it matches the main prompt.

## Invocation discipline

- Invoke a workflow only when the corresponding tool is actually available in the runtime.
- Pass one complete requirement, one unchanged requirement-data JSON string, or one node-spec JSON string as required by the main prompt.
- Never pass a complete project or node list to `ros2代码生成`.
- Parse marked blocks internally and never expose them or raw envelope JSON to the student.
- If a workflow tool is unavailable, fails, or returns an unparseable result, follow the main prompt's one-retry and explicit fallback-label rules. Do not claim that fallback output passed workflow checks.

## References and source templates

- Read `references/workflow-contracts.md` for input/output contracts and state invariants.
- Importable source templates are included at `workflows/requirements/template.yml`, `workflows/planning/template.yml`, and `workflows/codegen/template.yml` in the expert package.
- These YAML files are workflow definitions, not local Python executables. In a workflow-capable agent platform, import them and bind the three tools under the exact names above.
