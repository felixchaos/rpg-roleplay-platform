"""agents.gm.stream_retry — 流式 LLM 调用的「首 token 前」自动重试包装器。

韧性战役核心缺口(生产实况:opencode.ai 网关连环 502,玩家三分钟撞 30 次「生成失败」;
而三个 backend 的 _MAX_RETRIES 只包了从不被调用的非流式 call()——「重试代码在≠生效」)。

设计(保守、确定性):
- 只在【任何已提交事件发出之前】的失败可重试。已提交 = 正文 token / tool_call /
  tool_result(工具可能已执行,重试会双重副作用);纯 reasoning / 状态类事件不算提交
  (重试后思考流重启,视觉可接受,无副作用)。
- 只对 classify_provider_error 分类为 upstream(5xx/网关)/ratelimit(429) 的错误重试;
  balance/auth/context/model_unavailable/feature_unsupported 重试无意义,原样抛。
- 最多 MAX_RETRIES 次,线性退避(attempt * BACKOFF_BASE_SEC);每次重试先 yield 一个
  {"type": "retry_notice"} 事件,调用方转成 SSE 告知玩家「自动重试中」,不再干等。
- stop_event 已置位(玩家停止/断连)不重试。

同步生成器包装同步生成器(在 _bridge_sync_generator_to_async 之下工作),对 bridge/
事件循环零侵入;chat 主流与开场流共用(开场是裸字符串 chunk,用 is_commit 参数适配)。
"""
from __future__ import annotations

import logging
import time
from typing import Any, Callable, Iterator

log = logging.getLogger(__name__)

MAX_RETRIES = 2
BACKOFF_BASE_SEC = 1.5

# GM 事件流的「已提交」判定:这些事件到达 = 对话已有不可重放的外部效果/玩家可见正文。
_COMMIT_EVENT_TYPES = frozenset({"text", "tool_call", "tool_result", "tool_error"})


def _gm_event_commits(event: Any) -> bool:
    """GM respond_stream_with_tools 的事件是否算「已提交」。"""
    if not isinstance(event, dict):
        return True  # 未知形态保守视为已提交,不重试
    etype = event.get("type")
    if etype == "text":
        return bool(event.get("text"))
    return etype in _COMMIT_EVENT_TYPES


def opening_chunk_commits(chunk: Any) -> bool:
    """开场流(裸字符串 chunk)的提交判定:任何非空字符串即已提交。"""
    return bool(chunk)


def _retryable_category(exc: Exception) -> str | None:
    """可重试则返回分类名(upstream/ratelimit),否则 None。分类失败保守不重试。"""
    try:
        from agents.provider_errors import classify_provider_error
        known = classify_provider_error(exc)
    except Exception:
        return None
    if known and known[0] in ("upstream", "ratelimit"):
        return known[0]
    return None


def stream_with_pretoken_retry(
    factory: Callable[[], Iterator[Any]],
    *,
    is_commit: Callable[[Any], bool] = _gm_event_commits,
    emit_retry_notice: bool = True,
    stop_event: Any = None,
    max_retries: int = MAX_RETRIES,
    backoff_base_sec: float = BACKOFF_BASE_SEC,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[Any]:
    """包装一个同步流式生成器工厂,首个已提交项之前的可重试错误自动重建重放。

    factory: 每次(重)试调用一次,返回新的底层生成器(闭包自带 prompt/stop_event 等)。
    is_commit: 判定某个产出项是否「已提交」(之后失败不再重试)。
    emit_retry_notice: True 时每次重试前 yield {"type":"retry_notice", ...}
        (GM 事件流用;开场裸 chunk 流必须传 False,否则 dict 会混进正文)。
    """
    attempt = 0
    while True:
        committed = False
        gen = factory()
        try:
            for item in gen:
                if not committed and is_commit(item):
                    committed = True
                yield item
            return
        except GeneratorExit:
            raise  # 客户端断开:向上传播,绝不重试
        except Exception as exc:
            if committed or attempt >= max_retries:
                raise
            if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
                raise  # 玩家已停止,别背着他重试
            category = _retryable_category(exc)
            if category is None:
                raise
            attempt += 1
            log.info("[stream_retry] 上游 %s 失败(首token前),自动重试 %d/%d: %s",
                     category, attempt, max_retries, type(exc).__name__)
            if emit_retry_notice:
                yield {
                    "type": "retry_notice",
                    "attempt": attempt,
                    "max_retries": max_retries,
                    "category": category,
                }
            sleep(backoff_base_sec * attempt)
        finally:
            try:
                gen.close()
            except Exception:
                pass
