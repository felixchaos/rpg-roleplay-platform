"""Chat pipeline phases (task #51).

把 app.py 里 /api/chat 内部的 stream() 拆出来,按 5 个 async-generator phase 串起来。
每个 phase 接收一个 PipelineContext + 必要参数,yield SSE event tuple
(event_name, data_dict),并在退出前把"留给下一个 phase"的产物写到 ctx 上。

ctx.early_return = True 表示这个 phase 已经发了 done/error,orchestrator 应当跳出。

这层只搬家,不改语义:SSE 事件名/payload/顺序/contextvar 设置/异常分支
都和原来 app.py inline 实现一致。
"""

from __future__ import annotations

from ._common import (
    PipelineContext,
    SSEEvent,
    _bridge_sync_generator_to_async,
    _gm_max_iters,
    _should_route_to_curator_clarify,
    _snippet_tool_result,
    _summarize_tool_args,
    _sync_active_entities_from_bundle,
    _uid_of,
    log,
)
from ._input_signals import (
    _CONTINUE_CORE_TEXTS,
    _CONTINUE_DIRECTIVE,
    _IMMERSIVE_OFF_PHRASES,
    _IMMERSIVE_ON_PHRASES,
    _SHORT_INPUT_CHARS,
    _SHORT_INPUT_DIRECTIVE,
    _immersive_request,
    _is_continue_request,
    _should_inject_short_input_directive,
)
from .context import run_context_agent, run_context_phase
from .directives import apply_player_directives_phase
from .gm import _POSTPROC_MODE, _narrator_slim, _recorder_unified, run_gm_phase
from .persist import persist_turn_phase
from .postproc import (
    _ACCEPTANCE_AB_MIN_INTERVAL,
    _ACCEPTANCE_BG_TASKS,
    _acceptance_ab_pref_enabled,
    _apply_gm_json_ops,
    _log_acceptance_ab,
    _run_anchor_reconcile,
    _run_post_gm_parallel,
)
from .rules import run_rules_phase
