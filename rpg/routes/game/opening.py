"""routes.game.opening —— /api/opening(开场流水线 · SSE)。

含 rail(贴原著)开场策略(_RAIL_OPENING_INSTRUCTION / _game_opening_policy)。
同步 generator → 异步 SSE 的桥接器收敛到权威 chat_pipeline._common._bridge_sync_generator_to_async
(强版:_Error wrapper 区分「产出项」与「异常」,消除弱版 `isinstance(item, Exception)` 把
generator 正常 yield 的 Exception 实例误判为错误的隐患;并支持 *args/**kwargs 透传)。本模块
仍以本名 re-export 该桥(routes.game.__init__ 依赖此名)。
"""
from __future__ import annotations

import asyncio
import threading
from typing import Any

from fastapi import Depends
from fastapi.responses import StreamingResponse

from chat_pipeline._common import _bridge_sync_generator_to_async
from routes._deps_fastapi import get_current_user
from state.parsers import _extract_trailing_markdown_options

from ._shared import _client_safe_error, _note_channel_health_failure, router

# ── 游戏流水线 · 开场策略(rail 知识只住游戏层,不进底层 GMAgent)──────────────
# 反馈#73:开了「贴原著」开局仍脱离原著、原著开篇人物(张杰/雇佣兵等)流失。原因:开场默认
# 提示词强制 150-250 字 + max_tokens=600,把已注入的原著开场压成极短原创、丢人物。
# 修复边界:底层 generate_opening 通用化(只收 prompt+预算);这里——游戏这条流水线——按
# steering_strength 决定 rail 时用"忠实重现原著开篇"的提示 + 更大预算。酒馆/编辑器不调此处,零影响。
_RAIL_OPENING_INSTRUCTION = """\
玩家选择了【贴原著】引导强度。请基于【本轮上下文包】中的「锚点章节原文 · 贴原著档」段,
忠实重现原著的开场场景,作为玩家进入故事的起点。

要求：
- 严格沿用原著开场的时间、地点、**登场人物与关键对话/事件**;原著开篇出现的配角、群像也要保留,
  不要只写主角、不要替换或省略原著开场出现的人物。
- 保留原著关键对白的原话或原意;原著开篇的冲突/相遇/转折不得跳过或一笔带过。
- 玩家角色按其角色卡/出生点切入这一场景(视角与切入点可由玩家身份决定),但不得让开场脱离原著走向。
- 以注入的原著正文为准,不要凭训练记忆自由另写一个开场。
- **只重现注入原文中【确实出现】的内容**:不要补充原文之外的剧情、结局、人物去向或晋升/封赏等
  发展(原文写到哪就到哪);状态写回(memory.facts 等)也只记原文确有的事,不要脑补。

**用户导演指令(最高优先级)**:若上下文出现 `【玩家给 GM 的高优先级引导指令】`,在原著开场框架内
尽量贴合该意图(场景细节/切入角度),但不得删改原著开场的主要人物与关键事件。

结尾留一个可行动的悬念或选择,不要替玩家做决定。
字数:500-900字(开场需足够篇幅容纳原著登场人物与场景,不要压缩成极短)。
"""


def _game_opening_policy(steering_strength: str) -> tuple[str | None, int]:
    """游戏流水线的开场策略。rail(贴原著)→(忠实重现提示, 1600);其它 →(None=底层默认开场, 600)。
    返回 (prompt 覆盖 | None, max_tokens)。底层据此生成,但不认识 rail 本身。"""
    if steering_strength == "rail":
        return (_RAIL_OPENING_INSTRUCTION, 1600)
    return (None, 600)


@router.post("/api/opening")
async def api_opening(
    api_user: dict[str, Any] | None = Depends(get_current_user),
) -> StreamingResponse:
    from app import (
        _active_script_id,
        _build_turn_context,
        _ensure_loaded,
        _get_gm,
        _payload_sse,
        _persist_runtime_checkpoint,
        _resolve_persist_target,
        _sse,
        platform_branches,
        platform_knowledge,
        retrieve_context,
    )
    state = _ensure_loaded(api_user)
    gm = _get_gm(api_user)

    async def stream():
        # task 121a: 4 阶段 stage 事件让前端能显示 thinking pill,避免 5-15s 无反馈
        yield _sse("stage", {"phase": "retrieving", "label": "翻阅剧本设定中…"})
        # 修(task 117):走 phase 算法路径 — 不硬编码"第一章"。
        script_id = _active_script_id(api_user)
        if script_id:
            world = state.data.get("world", {}) or {}
            player = state.data.get("player", {}) or {}
            memory = state.data.get("memory", {}) or {}
            events = world.get("known_events") or []
            query_parts = [
                str(player.get("current_location") or ""),
                str(world.get("time") or ""),
                str(memory.get("current_objective") or ""),
                *[str(e) for e in events[:2]],
            ]
            query = " ".join(p for p in query_parts if p).strip() or "开场"
        else:
            query = "开场"

        # P0-1: retrieve_context + build_context_bundle 包进 to_thread,不阻塞 event loop
        def _retrieve_and_build():
            _ctx = retrieve_context(
                query,
                state=state,
                user_id=api_user["id"] if api_user else None,
                script_id=script_id,
            )
            state.set_last_retrieval(_ctx)
            _, _save_id_for_ctx = _resolve_persist_target(api_user)
            _bundle = _build_turn_context(state, query, _ctx, script_id=script_id, save_id=_save_id_for_ctx)
            return _bundle

        yield _sse("stage", {"phase": "building_context", "label": "组装上下文…"})
        bundle = await asyncio.to_thread(_retrieve_and_build)
        # 游戏层决定开场策略:读该存档引导强度,rail→忠实重现原著开篇提示+大预算(#73)。
        _steering = "guided"
        try:
            _, _sid_steer = _resolve_persist_target(api_user)
            if _sid_steer:
                from platform_app.db import connect as _conn_steer
                with _conn_steer() as _db_steer:
                    _row_steer = _db_steer.execute(
                        "select worldline->>'steering_strength' as ss from game_sessions where save_id=%s",
                        (_sid_steer,),
                    ).fetchone()
                if _row_steer and _row_steer.get("ss"):
                    _steering = _row_steer["ss"]
        except Exception:
            _steering = "guided"
        _open_prompt, _open_tokens = _game_opening_policy(_steering)
        yield _sse("status", _payload_sse(api_user))
        yield _sse("stage", {"phase": "generating", "label": "GM 构思开场中…"})
        text = ""
        try:
            # P0-1: generate_opening_stream 是同步 generator,通过 bridge 异步化
            # stop_event 在 SSE 断开时由 bridge finally 设置,让 sync generator 提前退出
            _opening_stop = threading.Event()
            # 流式 ops 围栏抑制(与 chat 主循环同一 StreamFenceGuard):开场同样是
            # 「裸转发 + 落库前清洗」,半截 ```json 会漏给玩家。text 累积不受影响。
            from state import StreamFenceGuard
            _fence_guard = StreamFenceGuard()
            # 流式重试(与 chat 主循环同一包装器):首个非空 chunk 之前的 upstream/
            # ratelimit 失败静默重试(裸字符串流,不发 notice 事件防混入正文)。
            from agents.gm.stream_retry import opening_chunk_commits, stream_with_pretoken_retry

            def _opening_factory():
                return gm.generate_opening_stream(state, retrieved_context=bundle["prompt"], stop_event=_opening_stop,
                                                  prompt=_open_prompt, max_tokens=_open_tokens)

            async for chunk in _bridge_sync_generator_to_async(
                lambda: stream_with_pretoken_retry(
                    _opening_factory, is_commit=opening_chunk_commits,
                    emit_retry_notice=False, stop_event=_opening_stop,
                ),
                stop_event=_opening_stop,
            ):
                text += chunk
                _fence_fw = _fence_guard.feed(chunk)
                if _fence_fw:
                    yield _sse("token", {"text": _fence_fw})
            _fence_tail = _fence_guard.flush()
            if _fence_tail:
                yield _sse("token", {"text": _fence_tail})
            opening = text
            yield _sse("stage", {"phase": "done", "label": ""})
            opening_for_history, opening_options = _extract_trailing_markdown_options(opening)
            # 剥掉 ops JSON 围栏 / 工具元叙述 / 泄漏脚手架,得到给玩家看 + 落历史的干净正文。
            # 复用 chat 路径同一套 stripper(chat_pipeline 落库前也这么洗);结构化解析仍用含 ops
            # 的 opening_for_history(见下方 apply_structured_updates)。根因:开场原先直接存原文,
            # ```json ops 块漏给玩家(基准测出多档开场命中 leak)。
            from state import strip_json_state_ops, strip_leaked_scaffold, strip_meta_tool_preamble
            opening_visible = strip_leaked_scaffold(strip_meta_tool_preamble(strip_json_state_ops(opening_for_history)))
            state.data["history"].append({"role": "assistant", "content": opening_visible})
            # 让开场也走结构化解析,把【询问玩家】+JSON ops 解析进 pending_questions / state
            before_questions = len((state.data.get("permissions") or {}).get("pending_questions") or [])
            try:
                state.apply_structured_updates(opening_for_history)
            except Exception:
                import logging as _logging
                _logging.getLogger(__name__).warning("opening apply_structured_updates failed", exc_info=True)
            after_questions = len((state.data.get("permissions") or {}).get("pending_questions") or [])
            if opening_options and after_questions == before_questions:
                state.add_pending_question("你想怎么行动？", source="gm:opening_options", options=opening_options)
            state.save()
            try:
                persist_user_id, active_save_id = _resolve_persist_target(api_user)
                if api_user and persist_user_id and active_save_id:
                    _open_rt = platform_branches.record_runtime_turn(
                        "",
                        opening_visible,
                        user_id=api_user["id"],
                        state_data=state.data,
                    )
                    # 并发双开:开场已在别处生成(record_runtime_turn 检测活跃指针漂移已原子放弃)。
                    # 发冲突错误让前端刷新,跳过后续 session/messages 写入,不落半个开场。
                    if isinstance(_open_rt, dict) and _open_rt.get("conflict"):
                        yield _sse("error", {
                            "message": "开场已在别处生成,请刷新后重试。",
                            "kind": "save_conflict",
                        })
                        yield _sse("done", {"status": _payload_sse(api_user), "interrupted": True})
                        return
                    platform_knowledge.ensure_game_session(persist_user_id, active_save_id, state.data)
                    # 写入 messages 表:kb_native 存档 materialize() 从 messages 重建 history,
                    # 若不写,开场白在 messages 表缺失 → materialize 丢开场 → 前端只显示后续对话。
                    try:
                        platform_knowledge.record_turn_messages(
                            persist_user_id,
                            active_save_id,
                            state.data,
                            "",
                            opening_visible,
                        )
                    except Exception:
                        pass
                else:
                    _persist_runtime_checkpoint(state, api_user)
            except Exception:
                _persist_runtime_checkpoint(state, api_user)
            # 渠道健康门控:开场也走成功即清零(与主 chat 流程 persist_turn_phase 同口径)。
            try:
                import model_probe
                model_probe.note_channel_success(gm.api_id, user_id=(api_user or {}).get("id"))
            except Exception:
                pass
            yield _sse("done", {"status": _payload_sse(api_user)})
        except Exception as exc:
            _note_channel_health_failure(exc, gm.api_id, api_user)
            yield _sse("error", {"message": _client_safe_error(exc), "partial": text})
            yield _sse("done", {"interrupted": True, "status": _payload_sse(api_user)})

    return StreamingResponse(stream(), media_type="text/event-stream")
