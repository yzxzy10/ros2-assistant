#!/usr/bin/env python3
"""Statically check a small rclpy node against its planning specification."""

import argparse
import ast
import json
import sys
from pathlib import Path


def load_spec(path):
    if not path:
        return {}
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if "node" in data and isinstance(data["node"], dict):
        data = data["node"]
    return data


def topic_name(value):
    return "/" + str(value or "").strip().lstrip("/")


def message_tail(value):
    return str(value or "").replace(".", "/").split("/")[-1]


def expected_topics(spec, key):
    result = {}
    for item in spec.get(key, []) or []:
        if isinstance(item, str):
            result[topic_name(item)] = ""
        elif isinstance(item, dict):
            name = topic_name(item.get("name") or item.get("topic"))
            if name != "/":
                result[name] = message_tail(item.get("msg_type") or item.get("message_type"))
    return result


def dotted_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = dotted_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def check(source, spec):
    errors = []
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"E001 line {exc.lineno}: Python syntax error: {exc.msg}"]

    imports = set()
    imported_types = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.asname or alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                local = alias.asname or alias.name
                imports.add(local)
                imported_types[local] = alias.name

    if "rclpy" not in imports:
        errors.append("E101: missing 'import rclpy'")

    node_classes = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and any(dotted_name(base).split(".")[-1] == "Node" for base in node.bases):
            node_classes.append(node)
    if not node_classes:
        errors.append("E102: no class inherits from rclpy.node.Node")
        return errors
    if "Node" not in imports:
        errors.append("E103: use 'from rclpy.node import Node'")

    cls = node_classes[0]
    expected_class = spec.get("class_name")
    if expected_class and cls.name != expected_class:
        errors.append(f"E104: class '{cls.name}' does not match '{expected_class}'")

    init_method = next((n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"), None)
    if init_method is None:
        errors.append("E105: node class has no __init__")
    else:
        expected_node = spec.get("node_name")
        for call in ast.walk(init_method):
            if not isinstance(call, ast.Call):
                continue
            is_super_init = (
                isinstance(call.func, ast.Attribute)
                and call.func.attr == "__init__"
                and isinstance(call.func.value, ast.Call)
                and isinstance(call.func.value.func, ast.Name)
                and call.func.value.func.id == "super"
            )
            if is_super_init and call.args:
                if isinstance(call.args[0], ast.Constant) and expected_node and call.args[0].value != expected_node:
                    errors.append(f"E106: node name '{call.args[0].value}' does not match '{expected_node}'")
                break
        else:
            errors.append("E107: missing super().__init__(node_name)")

    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    call_names = [dotted_name(n.func) for n in calls]
    for required in ("rclpy.init", "rclpy.spin", "rclpy.shutdown"):
        if required not in call_names:
            errors.append(f"E108: missing {required}()")
    if not any(isinstance(n, ast.FunctionDef) and n.name == "main" for n in ast.walk(tree)):
        errors.append("E109: missing main()")

    actual = {"publish_topics": {}, "subscribe_topics": {}}
    for call in calls:
        method = dotted_name(call.func).split(".")[-1]
        key = "publish_topics" if method == "create_publisher" else "subscribe_topics" if method == "create_subscription" else None
        if not key:
            continue
        required_args = 3 if key == "publish_topics" else 4
        if len(call.args) != required_args:
            errors.append(f"E110 line {call.lineno}: {method} expects {required_args} positional arguments")
        if len(call.args) > 1 and isinstance(call.args[1], ast.Constant):
            actual[key][topic_name(call.args[1].value)] = message_tail(dotted_name(call.args[0]))

    for key, create_name in (("publish_topics", "publisher"), ("subscribe_topics", "subscription")):
        for name, msg_type in expected_topics(spec, key).items():
            if name not in actual[key]:
                errors.append(f"E111: required {create_name} '{name}' is missing")
            elif msg_type and actual[key][name] and msg_type != actual[key][name]:
                errors.append(f"E112: '{name}' uses '{actual[key][name]}', expected '{msg_type}'")

    for call in calls:
        if dotted_name(call.func).split(".")[-1] != "create_subscription" or len(call.args) < 3:
            continue
        callback = dotted_name(call.args[2]).split(".")[-1]
        target = next((n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == callback), None)
        if target is None:
            errors.append(f"E113 line {call.lineno}: callback '{callback}' is not defined")
        elif len(target.args.args) != 2:
            errors.append(f"E114 line {target.lineno}: subscription callback must accept (self, msg)")

    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code", required=True, help="Path to the generated Python node")
    parser.add_argument("--spec", help="Path to a JSON node specification")
    args = parser.parse_args()
    source = Path(args.code).read_text(encoding="utf-8")
    spec = load_spec(args.spec)
    errors = check(source, spec)
    report = {"passed": not errors, "errors": errors, "note": "Static checks only; colcon build and runtime tests are still required."}
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if not errors else 1


if __name__ == "__main__":
    sys.exit(main())
