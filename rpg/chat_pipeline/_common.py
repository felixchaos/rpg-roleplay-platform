"""chat_pipeline 共享基础层(拆包 task):日志、类型、通用小工具、PipelineContext、
sync→async 桥接。纯机械搬家自原 chat_pipeline.py,行为零变化。"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from threading import Event
from typing import Any

from core.logging import get_logger
from state import GameState

log = get_logger("chat_pipeline")


# 酒馆 v2(R3/B4):tool_call/tool_result 作为 SSE 转发给前端做"可折叠后台工具流"。
# 为避免淹没沉浸 + 控制 SSE 体积:args 摘要 ≤200 字符,result 片段 ≤300 字符。
def _summarize_tool_args(args: Any, limit: int = 200) -> str:
    try:
        s = json.dumps(args, ensure_ascii=False, default=str)
    except Exception:
        s = str(args)
    return s if len(s) <= limit else s[:limit] + "…"


def _snippet_tool_result(result: Any, limit: int = 300) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        s = result
    else:
        try:
            s = json.dumps(result, ensure_ascii=False, default=str)
        except Exception:
            s = str(result)
    return s if len(s) <= limit else s[:limit] + "…"


def _gm_max_iters() -> int:
    """GM 单轮工具调用上限。原 8 太紧:世界线收束后一轮常需
    update_state → list_pending_anchors → mark_anchor_satisfied → set_question → 写正文,
    8 轮经常没串完就被「已达工具上限」硬截,浪费整轮 token。默认提到 16,可用
    RPG_GM_MAX_ITERS 调。GM 不再需要工具时会自然停,调高只给上限不强制多调。"""
    try:
        return max(4, int(os.environ.get("RPG_GM_MAX_ITERS", "16")))
    except (TypeError, ValueError):
        return 16


def _uid_of(api_user: dict | None) -> int | None:
    try:
        return int(api_user["id"]) if api_user and api_user.get("id") is not None else None
    except (TypeError, ValueError, KeyError):
        return None


def _should_route_to_curator_clarify(confidence: float, threshold: float, clarify: str) -> bool:
    """Only interrupt the GM when the curator is actually below confidence threshold."""
    return bool((clarify or "").strip()) and float(confidence) < float(threshold)


# ---------------------------------------------------------------------------
# Pipeline context: 在 phase 之间传递的可变状态
# ---------------------------------------------------------------------------


def _sync_active_entities_from_bundle(state, bundle) -> None:
    """把 context bundle 算出的 npc_cards / player_card 同步到 state.active_entities。

    小说剧本不走 rules_engine enter_room (那条路径才填 active_entities),
    所以前端 "当前在场" 面板永远是空。这里在每轮 GM context 注入后,把:
      · player_card.name → 玩家自己 (always 在场,第一位)
      · npc_cards.items[*].name → 当前轮 GM 上下文里的 NPC (anchor 强制注入 +
        grep 命中,都在 npc_cards layer 里)
    写回 state.active_entities,前端 PanelCharacters 自然能渲染。

    幂等:每轮重写一次,以 npc_cards 当前结果为准。
    """
    if not state or not bundle:
        return
    layers = (bundle.get("debug") or {}).get("layers") or []
    active: list[dict] = []
    # [#82] 「当前在场」只放【本场景真出现】的 NPC。npc_cards layer 是 RAG 检索结果
    # (anchor 注入 + grep 命中 + 章节可见),是"潜在相关"而非"真在场";长篇剧本进度靠后时,
    # 章节可见的 NPC 多达数百,全灌进来 → 面板「大量无关后期 NPC 卡」(反馈 #82)。
    # 判据:NPC 名字出现在最近叙事(上一条 GM 正文 + 最近一条玩家输入)里才算在场;
    # 否则只是上下文相关、不进在场面板。本同步在出本回合正文前跑,故"最近"=上一回合场景。
    _recent = ""
    _seen_r: set[str] = set()
    for _h in reversed(state.data.get("history") or []):
        _r = _h.get("role"); _ct = str(_h.get("content") or "")
        if _r in ("assistant", "user") and _r not in _seen_r and _ct:
            _recent += "\n" + _ct
            _seen_r.add(_r)
        if len(_seen_r) >= 2:
            break
    # 玩家始终第一位
    p = (state.data.get("player") or {})
    if p.get("name"):
        # 玩家游戏内头像 = 所选角色卡(PC卡)的 avatar_path(绝非账户头像)。
        # 老存档 player state 没存头像 → 用 source_id 一次性回查所选卡并写回,后续轮免查。
        _player_avatar = p.get("avatar_path") or ""
        if not _player_avatar and p.get("source_id"):
            try:
                from platform_app.db import connect as _connect
                with _connect() as _db:
                    _r = _db.execute(
                        "select avatar_path from character_cards where id = %s",
                        (int(p.get("source_id") or 0),),
                    ).fetchone()
                _player_avatar = ((_r.get("avatar_path") if _r else "") or "")
                if _player_avatar:
                    p["avatar_path"] = _player_avatar  # 写回 runtime player state,下轮免查
            except Exception:
                _player_avatar = ""
        active.append({
            "id": "player",
            "name": p["name"],
            "kind": "player",
            "disposition": "self",
            "source": "player",
            "card_id": "",
            "avatar_path": _player_avatar,
        })
    for lyr in layers:
        if lyr.get("id") != "npc_cards":
            continue
        for it in (lyr.get("items") or []):
            nm = (it.get("name") or "").strip()
            if not nm or nm == p.get("name"):
                continue
            # [#82] 只保留本场景真出现(名字在最近叙事里命中)的 NPC,滤掉仅被检索到的潜在相关项。
            if nm not in _recent:
                continue
            active.append({
                "id": f"npc:{nm}",
                "name": nm,
                "kind": "npc",
                "disposition": (it.get("disposition") or "neutral"),
                "source": (it.get("_source") or "context_inject"),
                "card_id": nm,  # 用 name 做 card_id,前端可点开看卡
                "identity": it.get("identity") or "",
                "avatar_path": it.get("avatar_path") or "",
            })
    state.data["active_entities"] = active


@dataclass
class PipelineContext:
    """phases 之间共享的可变 state。

    每个 phase 读它需要的字段,把产物写回。orchestrator(api_chat)只
    检查 early_return 来决定要不要短路。
    """

    # 入参 (orchestrator 填好)
    api_user: dict[str, Any] | None
    state: GameState
    gm: Any                                       # GameMaster
    sub_gm: Any                                   # GameMaster (sub)
    message_for_model: str
    run_id: int
    stop_event: Event
    chat_start_time: float

    # phase 间结果
    directive_updates: list[str] = field(default_factory=list)
    early_persist_user_id: int | None = None
    early_active_save_id: int | None = None
    persist_user_id: int | None = None
    active_save_id: int | None = None
    context_run_id: int | None = None
    agent_result: dict[str, Any] | None = None
    bundle: dict[str, Any] | None = None
    ctx_text: str = ""
    response: str = ""

    # 流程控制
    early_return: bool = False
    tavern_character_set: bool = False  # Phase 4 酒馆角色卡工具成功(first_mes 可能为空,非 error)


# 类型别名:phase generator 产物
SSEEvent = tuple[str, dict[str, Any]]


async def _bridge_sync_generator_to_async(
    gen_factory: Callable[[], Any],
    *args: Any,
    stop_event=None,
    **kwargs: Any,
) -> AsyncIterator[dict[str, Any]]:
    """把同步 generator 桥接成 async iterator,中途 LLM 调用不阻塞 event loop。

    gen_factory: 无参 callable 返回 sync generator。
                 若有额外位置/关键字参数,透传给 gen_factory(*args, **kwargs)。
                 推荐用 lambda 包装好后不传 args/kwargs。
    stop_event:  threading.Event;SSE 断开时由 bridge finally 设置,
                 让 sync generator 内部循环提前 break。未传时内部新建。

    实现:
    1. 在 ThreadPool 里跑 sync generator
    2. thread 内每 yield 一个 item,用 loop.call_soon_threadsafe 投到 asyncio.Queue
    3. async 端 await queue.get() 拿 item;SENTINEL 表示 generator 结束
    4. thread 异常通过 _Error wrapper 传回 async 端再抛
    5. finally 设置 stop_event,通知 sync 端早退

    用于 context_agent.run_context_agent 这种同步 generator + 内部阻塞调用
    (curator LLM 调用通过 ThreadPoolExecutor 等结果),让 chat_pipeline 的
    event loop 在 LLM 等待期间仍可调度其它协程。
    """
    import threading as _threading
    if stop_event is None:
        stop_event = _threading.Event()
    loop = asyncio.get_running_loop()
    aqueue: asyncio.Queue = asyncio.Queue()
    SENTINEL = object()

    class _Error:
        __slots__ = ("exc",)
        def __init__(self, exc: BaseException) -> None:
            self.exc = exc

    def _run_in_thread() -> None:
        try:
            for item in gen_factory(*args, **kwargs):
                if stop_event.is_set():
                    break
                loop.call_soon_threadsafe(aqueue.put_nowait, item)
        except BaseException as exc:  # noqa: BLE001
            loop.call_soon_threadsafe(aqueue.put_nowait, _Error(exc))
        finally:
            loop.call_soon_threadsafe(aqueue.put_nowait, SENTINEL)

    # 用 asyncio.to_thread 跑 wrapper,task 在 generator 结束/异常后自然完成
    runner = asyncio.create_task(asyncio.to_thread(_run_in_thread))
    try:
        while True:
            item = await aqueue.get()
            if item is SENTINEL:
                break
            if isinstance(item, _Error):
                raise item.exc
            yield item
    finally:
        # SSE 断开 / 异常 / 正常完成:通知 sync 端早退
        stop_event.set()
        try:
            await runner
        except Exception:
            pass
