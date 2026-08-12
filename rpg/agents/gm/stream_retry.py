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
import threading
import time
from collections.abc import Callable, Iterator
from typing import Any

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
    committed_flag: Any = None,
) -> Iterator[Any]:
    """包装一个同步流式生成器工厂,首个已提交项之前的可重试错误自动重建重放。

    factory: 每次(重)试调用一次,返回新的底层生成器(闭包自带 prompt/stop_event 等)。
    is_commit: 判定某个产出项是否「已提交」(之后失败不再重试)。
    emit_retry_notice: True 时每次重试前 yield {"type":"retry_notice", ...}
        (GM 事件流用;开场裸 chunk 流必须传 False,否则 dict 会混进正文)。
    committed_flag: 可选 threading.Event 形对象;首个已提交项出现时 .set()——
        供外层(跨渠道 fallback)读取「是否已提交」判定能否切换渠道。
    """
    attempt = 0
    while True:
        committed = False
        gen = factory()
        try:
            for item in gen:
                if not committed and is_commit(item):
                    committed = True
                    if committed_flag is not None:
                        try:
                            committed_flag.set()
                        except Exception:
                            pass
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


def stream_with_channel_fallback(
    primary_factory: Callable[[], Iterator[Any]],
    *,
    user_id: Any,
    primary_api_id: str,
    make_backup_factory: Callable[[str, str], Callable[[], Iterator[Any]]],
    stop_event: Any = None,
    is_commit: Callable[[Any], bool] = _gm_event_commits,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[Any]:
    """跨渠道 fallback 组合包装器(docs/design/channel_fallback_v0.md)。

    结构 = stream_with_pretoken_retry(主渠道) → 重试耗尽仍失败且【未提交任何事件】
    且分类 upstream/ratelimit 且 flag `channel_fallback` 开 → 解析用户自己的备用
    凭据渠道(严格 BYOK,跳过 degraded)→ yield {"type":"fallback_notice"} →
    stream_with_pretoken_retry(备用渠道)。每回合最多切换一次;备用轮再失败原样抛。

    与事件循环零耦合:调用方只需处理 fallback_notice 事件;bridge 之下工作,
    sync/async 两条后处理路径自动同享。make_backup_factory 由调用方提供
    (构造备用 GameMaster 属 chat_pipeline 职责,本模块不 import 上层)。
    """
    committed = threading.Event()
    try:
        yield from stream_with_pretoken_retry(
            primary_factory, stop_event=stop_event, is_commit=is_commit,
            sleep=sleep, committed_flag=committed,
        )
        return
    except GeneratorExit:
        raise  # 断连:向上传播,绝不切换
    except Exception:
        if committed.is_set():
            raise  # 已有正文/工具副作用:切换=换人续写+双重副作用,绝不做
        if stop_event is not None and getattr(stop_event, "is_set", lambda: False)():
            raise
        # 以下所有决策失败都必须回落到原始异常(bare raise 在 except 块内重抛原异常)
        candidate = None
        try:
            from core.feature_flags import feature_enabled
            if feature_enabled("channel_fallback", user_id):
                import sys as _sys

                from agents.provider_errors import (
                    classify_provider_error,  # noqa: F401 (口径同 _retryable_category)
                )
                _exc = _sys.exc_info()[1]
                if _retryable_category(_exc) is not None:
                    from core.channel_fallback import resolve_fallback_channel
                    candidate = resolve_fallback_channel(user_id, primary_api_id)
        except Exception:
            candidate = None
        if candidate is None:
            raise
        cand_api, cand_model = candidate
        log.info("[stream_retry] 主渠道 %s 重试耗尽,切换备用渠道 %s/%s (user=%s)",
                 primary_api_id, cand_api, cand_model, user_id)
        try:
            backup_factory = make_backup_factory(cand_api, cand_model)
        except Exception as _mk_err:
            log.warning("[stream_retry] 备用 GM 构造失败,回落原错误: %s", _mk_err)
            raise
        yield {
            "type": "fallback_notice",
            "from_api_id": primary_api_id,
            "api_id": cand_api,
            "model": cand_model,
        }
    # 备用轮(在 except 块外执行,避免二次异常报「在处理异常时又发生异常」链噪声)
    yield from stream_with_pretoken_retry(
        backup_factory, stop_event=stop_event, is_commit=is_commit, sleep=sleep,
    )
