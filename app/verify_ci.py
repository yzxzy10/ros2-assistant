# -*- coding: utf-8 -*-
"""编译沙箱真实验证入口（CI / 本地 Docker 均可运行）。

在真实 ROS 2 容器里跑一遍 sandbox.verify()：生成 ament_python 包
→ colcon build → 起节点 → ros2 node list，产出 L1「启动」层证据，
证明「生成的代码真的能编译、真的能起来」。

本地用法（app/ 目录下，需已装 ROS2 Jazzy + colcon）：
    ROS2_VERIFY_BACKEND=local python3 -X utf8 verify_ci.py

本地 Docker 用法（先 build -f Dockerfile.verify 出 ros2-tutor:jazzy）：
    docker run --rm -v "$(pwd)/..:/app" -w /app/app \
        ros2-tutor:jazzy python3 -X utf8 verify_ci.py

退出码：0 = 验证通过；1 = 验证失败。
"""
import json
import os
import sys
import time

# 保证能从任意目录运行：把本脚本所在目录（app/）放进 sys.path，
# 这样 `from app import sandbox` 能命中内层 app 包。
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import sandbox          # noqa: E402
from app import versions         # noqa: E402


# 一个最小 rclpy 发布者节点：足以证明「能编译、能起、能注册」。
# 不依赖任何 LLM 密钥，CI 里零配置即可跑。
SAMPLE_CODE = '''\
import rclpy
from rclpy.node import Node
from std_msgs.msg import String


class Talker(Node):
    def __init__(self):
        super().__init__("talker")
        self.pub = self.create_publisher(String, "chatter", 10)
        self.timer = self.create_timer(0.5, self.on_timer)

    def on_timer(self):
        msg = String()
        msg.data = "hello ros2"
        self.pub.publish(msg)


def main(args=None):
    rclpy.init(args=args)
    node = Talker()
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        node.destroy_node()
        rclpy.shutdown()


if __name__ == "__main__":
    main()
'''


def load_cfg():
    """优先读 config.json，读不到则用最小配置（distro=jazzy, backend=auto）。"""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
    if os.path.isfile(p):
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    return {"ros": {"distro": "jazzy",
                    "verify": {"enabled": True, "backend": "auto"}}}


def main():
    cfg = load_cfg()
    spec = {"node_name": "talker_demo"}
    result = sandbox.verify(cfg, SAMPLE_CODE, spec)

    # 补上证据元信息：时间戳、版本、CI 上下文，答辩时可追溯。
    result["evidence"] = {
        "app_version": versions.APP_VERSION,
        "generated_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ci": bool(os.environ.get("CI")),
        "run_id": os.environ.get("GITHUB_RUN_ID", ""),
        "run_number": os.environ.get("GITHUB_RUN_NUMBER", ""),
        "sha": os.environ.get("GITHUB_SHA", ""),
    }

    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "verify_result.json"), "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    with open(os.path.join(here, "verify.log"), "w", encoding="utf-8") as f:
        f.write(result.get("log", "") or "")

    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("ok"):
        print("\nEVIDENCE_OK: L1 启动验证在真实容器内跑通"
              "（colcon build + 节点注册）")
        return 0
    print("\nEVIDENCE_FAIL: stage=%s reason=%s"
          % (result.get("stage"), result.get("reason")))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
