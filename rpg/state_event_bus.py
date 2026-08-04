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
import os
import threading
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
# 安全: 单用户最多并发 SSE 订阅数。防止单用户开无限 SSE 连接吃光 fd/内存（DoS）。
MAX_SUBSCRIBERS_PER_USER = 10

# 线程安全: emit() 由 GM 工具循环在 *worker 线程* 调用(dispatcher 跑在 to_thread),
# 而 subscribe/unsubscribe/queue.get 在 *event-loop 线程*。两类线程并发读写
# _SUBSCRIBERS(set 迭代 vs discard/pop)→ RuntimeError: set changed size;
# 且 asyncio.Queue 非线程安全,跨线程 put_nowait 会损坏其 future 唤醒逻辑、丢事件。
# 用 _LOCK 串行化桶读写 + 把实际 put 经 call_soon_threadsafe 投递回 loop 线程。
_LOCK = threading.Lock()
_LOOP: asyncio.AbstractEventLoop | None = None
# 本进程唯一标识:emit 发布到 Redis 时带上,listener 收到自己 origin 的消息就跳过
# (本进程已在 emit 里本地直投过),避免重复投递。各 worker pid 不同即可区分。
_ORIGIN = f"p{os.getpid()}"


class TooManySubscribers(Exception):
    """订阅者超过 per-user 上限。"""


def subscribe(user_id: int) -> asyncio.Queue[StateEvent]:
    """SSE endpoint 调,拿一个新队列。endpoint 退出时务必 unsubscribe。

    超过 MAX_SUBSCRIBERS_PER_USER 上限时抛 TooManySubscribers, 路由层应
    返回 429 而不是继续累积。
    """
    global _LOOP
    try:
        # subscribe 总在 event-loop 线程的 SSE handler 里调 → 捕获该 loop 供 emit 跨线程投递
        _LOOP = asyncio.get_running_loop()
    except RuntimeError:
        pass
    with _LOCK:
        bucket = _SUBSCRIBERS[user_id]
        if len(bucket) >= MAX_SUBSCRIBERS_PER_USER:
            raise TooManySubscribers(
                f"user {user_id} 已达 SSE 订阅上限 ({MAX_SUBSCRIBERS_PER_USER}), "
                "请关掉旧标签页再重试"
            )
        q: asyncio.Queue[StateEvent] = asyncio.Queue(maxsize=_QUEUE_MAX)
        bucket.add(q)
    return q


def unsubscribe(user_id: int, q: asyncio.Queue[StateEvent]) -> None:
    with _LOCK:
        bucket = _SUBSCRIBERS.get(user_id)
        if bucket is None:
            return
        bucket.discard(q)
        if not bucket:
            _SUBSCRIBERS.pop(user_id, None)


def _deliver(q: asyncio.Queue[StateEvent], event: StateEvent) -> None:
    """在 event-loop 线程上执行的实际投递:满则丢最旧(背压)。"""
    try:
        q.put_nowait(event)
    except asyncio.QueueFull:
        try:
            q.get_nowait()
            q.put_nowait(event)
        except Exception:
            import logging
            logging.getLogger("rpg.state_event_bus").debug("_try_put 背压替换失败,丢弃事件")


def _local_emit(event: StateEvent) -> None:
    """投递给*本进程*该 user 的所有订阅者(可从任意线程调用)。
    队列满就丢最旧那条(背压),保证不阻塞调用方主路径。"""
    with _LOCK:
        queues = list(_SUBSCRIBERS.get(event.user_id, ()))  # 锁内取快照,避免迭代时被改
    loop = _LOOP
    for q in queues:
        if loop is not None and loop.is_running():
            # 跨线程安全:把 put 调度回 loop 线程执行
            try:
                loop.call_soon_threadsafe(_deliver, q, event)
            except RuntimeError:
                # loop 正在关闭(进程收尾):丢弃该事件即可,不让异常冒泡进 dispatcher
                pass
        else:
            _deliver(q, event)


def emit(user_id: int, topic: str, op: str, payload: dict[str, Any] | None = None) -> None:
    """非阻塞 push 给该 user 的所有订阅者(可从任意线程调用)。

    多 worker 水平扩展:优先发布到 Redis 频道,由各进程的 listener 统一投递给本进程订阅者
    (含发布进程自己)→ 订阅者落在哪个 worker 都能收到,根治跨 worker 丢事件。
    Redis 未配置 / 发布失败 → 回落进程内直接投递(单进程语义,本地开发不变)。
    """
    event = StateEvent(user_id=user_id, topic=topic, op=op, payload=payload or {})
    # 始终先本地直投:本进程订阅者立即收到,不依赖 Redis listener 是否已订阅完成
    # → 消除"启动/重连窗口里 publish 但本进程 listener 未就绪"导致的本进程丢事件。
    _local_emit(event)
    # 再跨进程广播:发布到 Redis 带 _ORIGIN;其它 worker 的 listener 投递,本进程
    # listener 收到自己 origin 的消息会跳过(上面已本地投过),不重复。
    try:
        import redis_bus
        if redis_bus.is_enabled():
            wire = json.dumps(
                {"origin": _ORIGIN, "user_id": user_id, "topic": topic, "op": op,
                 "payload": payload or {}, "ts": event.ts},
                ensure_ascii=False,
            )
            redis_bus.publish_event(wire)
    except Exception:
        import logging
        logging.getLogger("rpg.state_event_bus").warning(
            "Redis 跨进程投递失败 user_id=%s topic=%s", user_id, topic, exc_info=True
        )


async def redis_listener() -> None:
    """常驻协程(lifespan 启动):订阅 Redis 事件频道,把跨进程事件投递给本进程订阅者。
    断线自动重连。Redis 未配置则立即返回(纯进程内模式)。"""
    try:
        import redis_bus
        if not redis_bus.is_enabled():
            return
        import redis.asyncio as aioredis  # type: ignore
    except Exception as exc:
        import logging
        logging.getLogger("rpg.state_event_bus").info(
            "[state_event_bus] redis listener 未启用: %s", exc)
        return

    import logging
    _log = logging.getLogger("rpg.state_event_bus")
    url = redis_bus.redis_url()
    while True:
        client = None
        pubsub = None
        try:
            client = aioredis.from_url(url, decode_responses=True, health_check_interval=30)
            pubsub = client.pubsub()
            await pubsub.subscribe(redis_bus.EVENT_CHANNEL)
            _log.info("[state_event_bus] redis listener subscribed channel=%s", redis_bus.EVENT_CHANNEL)
            while True:
                # 用 get_message(timeout) 轮询而非 listen():空闲频道上 get_message 返回 None(不报错),
                # 而 listen() 会每隔 redis-py 默认 socket 读超时就抛 "Timeout reading from ...:6379" →
                # 旧实现据此每 3s 关连接重连(churn:刷屏日志 + 重连窗口内 publish 的跨进程事件丢失)。
                # 现在只有真断线才抛 → 落到外层 except 重连;空闲不再误判为故障。
                msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=1.0)
                if msg is None or msg.get("type") != "message":
                    continue
                try:
                    d = json.loads(msg["data"])
                    if d.get("origin") == _ORIGIN:
                        continue  # 自己 publish 的,本进程 emit 已本地投过,跳过免重复
                    ev = StateEvent(
                        user_id=int(d["user_id"]), topic=d["topic"], op=d["op"],
                        payload=d.get("payload") or {}, ts=float(d.get("ts") or time.time()),
                    )
                    _local_emit(ev)
                except Exception:
                    _log.exception("[state_event_bus] bad redis event payload")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log.warning("[state_event_bus] redis listener error, retry in 3s: %s", exc)
            await asyncio.sleep(3)
        finally:
            # 重连/取消前关掉旧连接,否则反复断线重连会泄漏 pubsub + 底层连接(fd)
            for _c in (pubsub, client):
                if _c is not None:
                    try:
                        await _c.aclose()
                    except Exception:
                        _log.debug("Redis 连接关闭失败(cleanup 路径,忽略)")


def subscriber_count(user_id: int) -> int:
    with _LOCK:
        return len(_SUBSCRIBERS.get(user_id, set()))


def reset_for_tests() -> None:
    global _LOOP
    with _LOCK:
        _SUBSCRIBERS.clear()
    _LOOP = None


__all__ = [
    "StateEvent",
    "subscribe",
    "unsubscribe",
    "emit",
    "subscriber_count",
    "reset_for_tests",
    "TooManySubscribers",
    "MAX_SUBSCRIBERS_PER_USER",
]
