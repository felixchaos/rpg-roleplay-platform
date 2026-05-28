"""
state_event_bus.py — task 69: 进程内 state-event 广播总线。

Dispatcher 在工具执行成功后调 emit(),订阅者(SSE endpoint per user)
就能把事件 push 给前端,前端转 CustomEvent("rpg-{topic}-updated") 触发
现有页面 reload — 无需手动刷新。

设计要点:
  · 进程内,不跨进程 (用 redis/postgres LISTEN 是后续优化)。
  · 按 user_id 分桶,跨用户互不可见 (安全)。
  · 订阅者拿 asyncio.Queue,非阻塞 push。
  · 超过 ttl 没人消费就丢 (避免泄漏)。
"""
from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any


@dataclass
class StateEvent:
    user_id: int
    topic: str  # 例: "saves", "cards", "personas", "permissions", "scripts"
    op: str  # 例: "created", "deleted", "updated", "activated", "renamed"
    payload: dict[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)

    def to_sse_data(self) -> str:
        return json.dumps(
            {
                "topic": self.topic,
                "op": self.op,
                "payload": self.payload,
                "ts": self.ts,
            },
            ensure_ascii=False,
        )


# user_id → set of subscriber queues
_SUBSCRIBERS: dict[int, set[asyncio.Queue[StateEvent]]] = defaultdict(set)
_QUEUE_MAX = 64


def subscribe(user_id: int) -> asyncio.Queue[StateEvent]:
    """SSE endpoint 调,拿一个新队列。endpoint 退出时务必 unsubscribe。"""
    q: asyncio.Queue[StateEvent] = asyncio.Queue(maxsize=_QUEUE_MAX)
    _SUBSCRIBERS[user_id].add(q)
    return q


def unsubscribe(user_id: int, q: asyncio.Queue[StateEvent]) -> None:
    bucket = _SUBSCRIBERS.get(user_id)
    if bucket is None:
        return
    bucket.discard(q)
    if not bucket:
        _SUBSCRIBERS.pop(user_id, None)


def emit(user_id: int, topic: str, op: str, payload: dict[str, Any] | None = None) -> None:
    """非阻塞 push 给该 user 的所有订阅者。
    队列满就丢最旧那条(背压),保证不阻塞 dispatcher 主路径。
    """
    event = StateEvent(user_id=user_id, topic=topic, op=op, payload=payload or {})
    for q in list(_SUBSCRIBERS.get(user_id, ())):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            try:
                q.get_nowait()
                q.put_nowait(event)
            except Exception:
                pass


def subscriber_count(user_id: int) -> int:
    return len(_SUBSCRIBERS.get(user_id, set()))


def reset_for_tests() -> None:
    _SUBSCRIBERS.clear()


__all__ = [
    "StateEvent",
    "subscribe",
    "unsubscribe",
    "emit",
    "subscriber_count",
    "reset_for_tests",
]
