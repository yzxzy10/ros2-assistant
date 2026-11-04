# -*- coding: utf-8 -*-
"""任务进度事件：让「模型在想」和「已经卡住」在界面上可区分。

背景（学生真实反馈）：
  - "不知道它到底是在思考还是卡住"
  - 完整项目端到端 200 秒以上，中间没有任何反馈
所以进度不是装饰，是可用性的一部分：任何一次等待都不能超过 2 秒没有信号。

SSE 事件协议（data: {json}）：
  {"type":"progress","value":{
      "id":"analyze",              # 步骤标识，前端据此更新同一行
      "label":"需求分析",           # 展示名
      "state":"start|run|done|fail",
      "detail":"2/4 obstacle_detector_node",
      "elapsed":3.2,               # 该步骤已耗时
      "total_elapsed":12.4}}       # 整个请求已耗时
  {"type":"tick","value":{"elapsed":12.4,"label":"需求分析","detail":""}}

  tick 由心跳线程每 HEARTBEAT 秒发一次：即使上游模型 30 秒不吐字，
  前端也能看到计时器在走、当前步骤没变，从而判断"在跑，没死"。

设计约束：
  - 非流式调用（handle）没有 emitter，全部方法退化为空操作，零副作用；
  - 同一 id 可以嵌套（批量生成里每个节点都开 code 步），用深度计数避免重复上报；
  - 任何异常都不允许影响主链路：所有发送都吞掉异常。
"""
import threading
import time

HEARTBEAT = 2.0          # 心跳间隔（秒）


class Progress(object):
    """一次请求内的进度收集器。"""

    def __init__(self, emit=None):
        self._emit = emit
        self.t0 = time.time()
        self.current = None            # 当前步骤 {id,label,detail,t0}
        self._depth = {}               # id -> 嵌套层数
        self._steps = []               # 走过的步骤（按开始顺序，供最终摘要）
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._hb = None

    # ---------- 发送 ----------
    def _send(self, kind, payload):
        if self._emit is None:
            return
        try:
            self._emit((kind, payload))
        except Exception:              # noqa: BLE001  进度绝不能把主链路带崩
            pass

    def elapsed(self):
        return round(time.time() - self.t0, 1)

    # ---------- 心跳 ----------
    def start(self):
        if self._emit is None or self._hb is not None:
            return self
        self._hb = threading.Thread(target=self._beat, daemon=True)
        self._hb.start()
        return self

    def stop(self):
        self._stop.set()

    def _beat(self):
        while not self._stop.wait(HEARTBEAT):
            cur = self.current or {}
            self._send("tick", {
                "elapsed": self.elapsed(),
                "label": cur.get("label", ""),
                "detail": cur.get("detail", ""),
            })

    # ---------- 步骤 ----------
    def step_begin(self, sid, label, detail=""):
        with self._lock:
            depth = self._depth.get(sid, 0) + 1
            self._depth[sid] = depth
            if depth > 1:                       # 嵌套同一 id：只更新说明
                if self.current and self.current.get("id") == sid and detail:
                    self.current["detail"] = detail
                    self.note(detail)
                return
            cur = {"id": sid, "label": label, "detail": detail, "t0": time.time()}
            self.current = cur
            self._steps.append(cur)
        self._send("progress", {"id": sid, "label": label, "state": "start",
                                "detail": detail, "elapsed": 0.0,
                                "total_elapsed": self.elapsed()})

    def step_end(self, sid, state="done", detail=""):
        with self._lock:
            depth = self._depth.get(sid, 0) - 1
            self._depth[sid] = max(0, depth)
            if depth > 0:
                return
            cur = self.current if (self.current or {}).get("id") == sid else {}
            if self.current and self.current.get("id") == sid:
                self.current = None
        self._send("progress", {
            "id": sid,
            "label": cur.get("label", sid),
            "state": state,
            "detail": detail or cur.get("detail", ""),
            "elapsed": round(time.time() - cur.get("t0", time.time()), 1),
            "total_elapsed": self.elapsed()})

    def note(self, detail=None, label=None):
        """更新当前步骤的补充说明（进度百分比、正在处理的节点名）。"""
        cur = self.current
        if not cur:
            return
        with self._lock:
            if detail is not None:
                cur["detail"] = detail
            if label:
                cur["label"] = label
            snap = dict(cur)
        self._send("progress", {
            "id": snap["id"], "label": snap["label"], "state": "run",
            "detail": snap["detail"],
            "elapsed": round(time.time() - snap["t0"], 1),
            "total_elapsed": self.elapsed()})

    def step(self, sid, label, detail=""):
        return _StepCtx(self, sid, label, detail)

    # ---------- 最终摘要 ----------
    def snapshot(self):
        with self._lock:
            steps = [{"id": s["id"], "label": s["label"],
                      "elapsed": round(time.time() - s["t0"], 1)}
                     for s in self._steps]
        return {"elapsed": self.elapsed(), "steps": steps}


class _StepCtx(object):
    """with p.step("plan", "项目规划"): ...  异常时自动标记 fail。"""

    def __init__(self, p, sid, label, detail=""):
        self.p, self.sid, self.label, self.detail = p, sid, label, detail

    def __enter__(self):
        self.p.step_begin(self.sid, self.label, self.detail)
        return self.p

    def __exit__(self, et, ev, tb):
        self.p.step_end(self.sid, "fail" if et else "done")
        return False


# ---------- 线程本地：让编排层随手就能拿到当前请求的进度器 ----------

_LOCAL = threading.local()


def set_current(p):
    _LOCAL.p = p


def clear():
    _LOCAL.p = None


def current():
    return getattr(_LOCAL, "p", None)


def step(sid, label, detail=""):
    """便捷入口：没有进度器时返回空上下文，调用方不用写判断。"""
    p = current()
    if p is None:
        return _NullCtx()
    return p.step(sid, label, detail)


def note(detail=None, label=None):
    p = current()
    if p is not None:
        p.note(detail, label)


class _NullCtx(object):
    def __enter__(self):
        return None

    def __exit__(self, et, ev, tb):
        return False
