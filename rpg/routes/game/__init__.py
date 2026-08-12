"""routes.game —— 游戏核心流程路由(/api/new · /api/opening · /api/chat* · /api/stop ·
/api/save · /api/message/edit · /api/acceptance/choice;SSE 热路径,包化)。

原单文件(1327 行)按资源族拆为子包;本 __init__ 是薄门面:import 全部子模块触发装配
(各子模块 `from ._shared import router` 后用 `@router.<verb>` 注册,共享同一 APIRouter
实例),再逐名 re-export 原模块的全部公开名(含 router / 全部 api_* 端点 / 下划线辅助与常量),
让 `from routes.game import router`(app.py)、`from routes.game import
_resolve_message_index_by_content`(chat_pipeline.gm)、`from routes.game import _client_safe_error /
_LAYER_CATEGORY / _CATEGORY_ORDER`(测试)与既有引用零改动。

── 2026-07-15 拆包说明(纯机械搬家,零行为变化)────────────────────────────
_shared.py  — 共享的单一 router 实例 + 跨族 helper(_log / _sanitize_payload /
              _client_safe_error(+_CLIENT_SAFE_RUNTIME_PREFIXES)/ _note_channel_health_failure)
new.py      — 新存档创建(/api/new,消费 _sanitize_payload)
opening.py  — 开场流水线(/api/opening,含 rail 开场策略 _RAIL_OPENING_INSTRUCTION /
              _game_opening_policy + 同步→异步桥 _bridge_sync_generator_to_async)
chat.py     — chat SSE 主路径(/api/chat)+ 上下文预估(/api/chat/estimate)+
              context breakdown(/api/chat/context-breakdown,含 _LAYER_CATEGORY /
              _CATEGORY_ORDER)+ 打断(/api/stop)
saves.py    — 存档写操作族:保存(/api/save)+ 消息编辑(/api/message/edit)+
              acceptance A/B 裁决(/api/acceptance/choice),含 _amend_history_message /
              _resolve_message_index_by_content
"""
from __future__ import annotations

from ._shared import (
    _CLIENT_SAFE_RUNTIME_PREFIXES,
    _client_safe_error,
    _log,
    _note_channel_health_failure,
    _sanitize_payload,
    router,
)
from .chat import (
    _CATEGORY_ORDER,
    _LAYER_CATEGORY,
    api_chat,
    api_chat_estimate,
    api_context_breakdown,
    api_stop,
)
from .new import api_new
from .opening import (
    _RAIL_OPENING_INSTRUCTION,
    _bridge_sync_generator_to_async,
    _game_opening_policy,
    api_opening,
)
from .saves import (
    _amend_history_message,
    _resolve_message_index_by_content,
    api_acceptance_choice,
    api_message_edit,
    api_save,
)

__all__ = ["router"]
