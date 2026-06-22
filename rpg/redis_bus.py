"""redis_bus.py — Redis 基础设施:跨进程事件总线 / 限流 / 并发信号量。

目标:让后端从"进程内状态、必须 workers=1"演进到"状态共享、可水平扩展 workers>1"。
进程内方案(state_event_bus 的 dict、auth 的限流 dict、import 的 threading.Semaphore)在
多 worker 下各自为政 → SSE 事件推不到别的 worker 的订阅者(丢事件)、限流按 worker 各算一份
(等效阈值 ×N)、并发信号量失控(总并发 = N×local)。本模块把这些状态挪到 Redis。

优雅降级铁律:REDIS_URL 未配置或 Redis 不可达时,所有接口返回"未启用"信号,调用方回落到
进程内行为。本地开发 / 无 Redis 环境下功能不变,只是退回单进程语义。
"""
from __future__ import annotations

import logging
import os
import threading
import time

log = logging.getLogger("rpg.redis_bus")

# 状态事件广播频道(单频道,payload 内带 user_id,各进程监听后按本地订阅者过滤)
EVENT_CHANNEL = "rpg:state_events"

_SYNC_CLIENT = None
_SYNC_RETRY_AT = 0.0  # time.monotonic();连接失败后下次允许重试的时间点
_SYNC_COOLDOWN = 30.0  # 失败后冷却 30s 再重连(避免每次调用都阻塞 ping 一次)
_INIT_LOCK = threading.Lock()  # 防止多线程(asyncio.to_thread)双初始化泄漏 ConnectionPool


def redis_url() -> str | None:
    return os.environ.get("REDIS_URL") or None


def is_enabled() -> bool:
    """是否配置了 Redis(仅看 env,不代表此刻可达)。"""
    return bool(redis_url())


def get_sync_client():
    """单例同步 redis 客户端。redis-py 连接池线程安全,可从 worker 线程调用。
    Redis 未配置 → None。连不上 → None 但**带冷却重连**:每 30s 重试一次,
    Redis 恢复后自愈(不再像之前那样首次失败就永久降级到死)。
    双检锁(double-checked locking)防止多线程(asyncio.to_thread)并发初始化泄漏 ConnectionPool。"""
    global _SYNC_CLIENT, _SYNC_RETRY_AT
    # 快速路径:已初始化时无锁直接返回(ConnectionPool 本身线程安全)
    if _SYNC_CLIENT is not None:
        return _SYNC_CLIENT
    url = redis_url()
    if not url:
        return None
    with _INIT_LOCK:
        # 锁内二次检查:其它线程可能刚完成初始化
        if _SYNC_CLIENT is not None:
            return _SYNC_CLIENT
        now = time.monotonic()
        if now < _SYNC_RETRY_AT:
            return None  # 冷却窗口内,暂不重连,直接走进程内降级
        try:
            import redis  # redis-py

            client = redis.Redis.from_url(
                url,
                socket_timeout=2,
                # 本机 Redis(localhost),0.5s 连接超时绰绰有余;Redis 抖动时降级路径不再阻塞
                # 调用方线程长达 2s(限流走登录路径,2s 阻塞放大「登录风暴」卡顿)。
                socket_connect_timeout=0.5,
                decode_responses=True,
                health_check_interval=30,
            )
            client.ping()
            _SYNC_CLIENT = client
            log.info("[redis] sync client connected")
        except Exception as exc:
            _SYNC_CLIENT = None
            _SYNC_RETRY_AT = time.monotonic() + _SYNC_COOLDOWN
            log.warning("[redis] unavailable, degrading to in-process (retry in %ds): %s",
                        int(_SYNC_COOLDOWN), exc)
    return _SYNC_CLIENT


# ── 事件总线 publish ────────────────────────────────────────────────────────

def publish_event(payload: str) -> bool:
    """把已序列化的事件 JSON 发布到 Redis 频道。成功 True;Redis 不可用 False(调用方本地投递)。"""
    cli = get_sync_client()
    if cli is None:
        return False
    try:
        cli.publish(EVENT_CHANNEL, payload)
        return True
    except Exception as exc:
        log.warning("[redis] publish failed: %s", exc)
        return False


# ── 限流(固定窗口原子计数)────────────────────────────────────────────────

# INCR + 条件 EXPIRE 必须原子:否则进程在 INCR 后、EXPIRE 前崩溃 → key 永久无 TTL
# → 计数永不重置 → 该 IP/用户限流键卡死,跨窗口累加可致过早/永久锁定。Lua 脚本在
# Redis 单线程下原子执行,消除崩溃窗口;额外 elseif 分支自愈任何已丢失 TTL 的历史卡死键。
_RATE_INCR_LUA = """
local c = redis.call('INCR', KEYS[1])
if c == 1 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
elseif redis.call('TTL', KEYS[1]) < 0 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return c
"""


def rate_incr(key: str, window_sec: int) -> int | None:
    """对 key 原子 +1,并原子地保证设置 TTL=window_sec。返回当前窗口内计数。
    Redis 不可用 → None,调用方回落进程内限流。

    用 Lua 脚本把 INCR 与 EXPIRE 合并为单次原子执行 —— 旧实现 incr 后单独 expire,
    两步之间崩溃会留下永不过期的计数键(限流卡死)。"""
    cli = get_sync_client()
    if cli is None:
        return None
    try:
        rkey = f"rpg:rl:{key}"
        cnt = cli.eval(_RATE_INCR_LUA, 1, rkey, window_sec)
        return int(cnt)
    except Exception as exc:
        log.warning("[redis] rate_incr failed: %s", exc)
        return None


def rate_reset(key: str) -> None:
    """成功登录等场景清零计数。Redis 不可用静默忽略。"""
    cli = get_sync_client()
    if cli is None:
        return
    try:
        cli.delete(f"rpg:rl:{key}")
    except Exception:
        pass


# ── 锁定键(登录失败锁定等,带 TTL 自动解锁)────────────────────────────────

def lock_set(name: str, ttl_sec: int) -> bool:
    """设置一个 TTL 锁定键。Redis 不可用 → False。"""
    cli = get_sync_client()
    if cli is None:
        return False
    try:
        cli.set(f"rpg:lock:{name}", "1", ex=ttl_sec)
        return True
    except Exception:
        return False


def lock_remaining(name: str) -> int | None:
    """剩余锁定秒数(>0=锁定中);0=未锁定;None=Redis 不可用(调用方回落进程内)。"""
    cli = get_sync_client()
    if cli is None:
        return None
    try:
        ttl = cli.ttl(f"rpg:lock:{name}")
        return ttl if (ttl and ttl > 0) else 0
    except Exception:
        return None


def lock_clear(name: str) -> None:
    cli = get_sync_client()
    if cli is None:
        return
    try:
        cli.delete(f"rpg:lock:{name}")
    except Exception:
        pass


# ── 跨进程并发信号量(令牌列表 + 阻塞 BLPOP)──────────────────────────────

# 信号量幂等初始化 Lua 脚本:
#   KEYS[1] = tokens 列表键
#   ARGV[1] = capacity (字符串)
# 仅当 tokens 列表不存在(或已空且从未被初始化过:用 EXISTS 判)时填入令牌。
# 不设 TTL:令牌是长期资源,TTL 过期会丢失令牌导致死锁;重启 Redis 时令牌自然消失,
# worker 重新 init 即可(EXISTS=0 → 重新 seed)。
# 原 SETNX 哨兵方案的问题:SETNX 成功但 rpush 崩溃 → 哨兵存在但令牌为空 → 永久死锁;
# 本 Lua 脚本以 tokens 列表是否存在为唯一来源,消除哨兵崩溃窗口。
_SEM_INIT_LUA = """
local exists = redis.call('EXISTS', KEYS[1])
if exists == 0 then
  local cap = tonumber(ARGV[1])
  if cap and cap > 0 then
    for i = 0, cap - 1 do
      redis.call('RPUSH', KEYS[1], tostring(i))
    end
  end
end
return redis.call('LLEN', KEYS[1])
"""


def sem_init(name: str, capacity: int) -> bool:
    """幂等初始化令牌池:仅当 tokens 列表不存在时原子填入 capacity 个令牌。
    不设 TTL(令牌是长期资源)。用 Lua 脚本保证原子性:不存在 → 填充;已存在 → no-op。
    Redis 不可用 → False。"""
    cli = get_sync_client()
    if cli is None:
        return False
    try:
        tokens_key = f"rpg:sem:{name}:tokens"
        cli.eval(_SEM_INIT_LUA, 1, tokens_key, str(capacity))
        return True
    except Exception as exc:
        log.warning("[redis] sem_init failed: %s", exc)
        return False


_BLOCKING_CLIENT = None


def _get_blocking_client():
    """[round-4-P1] 专供 BLPOP 等阻塞命令的独立 redis 客户端。

    get_sync_client() 为限流等非阻塞命令设了 socket_timeout=2,但该 socket 读超时
    会在 2s 后中断 BLPOP 的服务端等待(redis-py 把 socket.timeout 抛成 TimeoutError),
    导致 sem_acquire 恒在 2s 后返回 None → 永远回退进程内信号量,跨进程令牌池形同虚设
    (多 worker 下导入并发上限变 N×capacity)。故阻塞命令必须用不设 socket_timeout 的
    连接:由 BLPOP 自身的 timeout 参数(服务端)+ TCP keepalive 兜底死连接。"""
    global _BLOCKING_CLIENT
    if _BLOCKING_CLIENT is not None:
        return _BLOCKING_CLIENT
    url = redis_url()
    if not url:
        return None
    with _INIT_LOCK:
        if _BLOCKING_CLIENT is not None:
            return _BLOCKING_CLIENT
        try:
            import redis

            client = redis.Redis.from_url(
                url,
                socket_timeout=None,          # 阻塞命令不能设 socket 读超时
                socket_connect_timeout=0.5,
                socket_keepalive=True,        # 死连接由 keepalive 探测,不靠 socket_timeout
                decode_responses=True,
                health_check_interval=30,
            )
            client.ping()
            _BLOCKING_CLIENT = client
            log.info("[redis] blocking client connected")
        except Exception as exc:
            log.warning("[redis] blocking client connect failed: %s", exc)
            return None
    return _BLOCKING_CLIENT


def sem_acquire(name: str, timeout_sec: int = 300) -> str | None:
    """阻塞取一个令牌(BLPOP,跨进程)。返回令牌(release 时归还);超时/不可用返回 None。"""
    cli = _get_blocking_client()
    if cli is None:
        return None
    try:
        res = cli.blpop(f"rpg:sem:{name}:tokens", timeout=timeout_sec)
        if res is None:
            return None
        return res[1]  # (key, token)
    except Exception as exc:
        log.warning("[redis] sem_acquire failed: %s", exc)
        return None


def sem_release(name: str, token: str) -> None:
    """归还令牌。Redis 不可用静默忽略。"""
    cli = get_sync_client()
    if cli is None:
        return
    try:
        cli.rpush(f"rpg:sem:{name}:tokens", token)
    except Exception:
        pass


__all__ = [
    "EVENT_CHANNEL",
    "is_enabled",
    "get_sync_client",
    "publish_event",
    "rate_incr",
    "rate_reset",
    "sem_init",
    "sem_acquire",
    "sem_release",
]
