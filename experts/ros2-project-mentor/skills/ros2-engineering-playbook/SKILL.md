---
name: ros2-engineering-playbook
description: ROS2 Humble/Jazzy engineering playbook for requirement analysis, project planning, C++/Python package generation, communication validation, simulation troubleshooting, and project-based teaching.
---

# ROS2 Engineering Playbook

Use this playbook to turn ROS2 requirements into verifiable robot projects and understandable learning material.

## Workflow

1. Identify the requested deliverable: plan, complete package, single node, bug fix, or explanation.
2. Establish only essential assumptions; record ROS2 distribution, language, platform and external interfaces.
3. Define the package/node/communication contract and acceptance checks before implementing a multi-file project.
4. Implement all files required by the chosen runtime path, including interface generation and build/install metadata.
5. Run available static checks, builds and tests. Distinguish actual execution evidence from suggested commands.
6. Deliver operating steps, observable verification and concise project-specific teaching.

## Reference Routing

- For architecture, requirement JSON, interface closure and acceptance: read `references/project-planning.md`.
- For rclcpp/rclpy, custom interfaces, packaging and validation: read `references/code-generation.md`.
- For diagnosis, simulation, safety and teaching: read `references/troubleshooting-teaching.md`.
- For local static validation of a generated rclpy node against its specification: run `scripts/check_python_node.py --code <path> --spec <json-path>`.

## Non-negotiable Checks

- Names, interfaces and dependencies agree across source, package metadata, build files and launch.
- Every internal subscription has a producer; external inputs are identified explicitly.
- Custom interfaces are generated before dependent nodes are built.
- No unverified claim of successful build, simulation, navigation, manipulation or hardware operation.
- Real robot control includes speed limits, stop behavior and simulation-first verification.
