"""链路级验证对外入口（聚合层）：规划、启动、查询、取消。

模块分工：
- chain_msg    消息伪造模板 + 命令行工具函数
- chain_plan   从架构图算注入口 / 拓扑序断言点 / 下位机话题
- chain_script 生成容器内 bash（build → run → inject → hop → 收日志）
- chain_run    离线 mock 脚本、节点判定、状态结构
- chain_flow   标记解析、实时状态更新、结论与断点定位
- chain_exec   启动 docker/mock、流式读取、取消

红线：绿 = 那一跳真实收到过消息；红 = 真的没到；未执行 = 环境不支持。绝不造假。
"""
import json

from .chain_exec import cancel, start, status
from .chain_plan import plan

_IDLE = {
    "running": False, "finished": False, "cancelled": False,
    "stage": "idle", "ok": False, "reason": "尚未开始验证",
    "elapsed": 0.0, "backend": "", "distro": "", "image": "",
    "entries": [], "mcus": [], "skipped": [], "hops": [], "nodes": [],
}


def snapshot(sid, sess=None):
    """返回可 JSON 序列化的状态副本。

    不能直接把内部 dict 交给 json.dumps：后台线程正在往里写，
    序列化途中若结构被改动会抛 "dictionary changed size"。

    sess 是给「上次落盘结果」用的：要按当前图重算指纹，图改了旧结果作废。
    """
    st = status(sid, sess)
    if st is None:
        return dict(_IDLE)
    try:
        return json.loads(json.dumps(st, ensure_ascii=False, default=str))
    except Exception:                                   # noqa: BLE001
        return dict(_IDLE, reason="状态读取失败")


def busy(sid, sess=None):
    st = status(sid, sess)
    return bool(st and st.get("running"))


__all__ = ["plan", "start", "status", "cancel", "snapshot", "busy"]
