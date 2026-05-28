"""context_engine — 上下文构建引擎 (按职责拆分)."""
from context_engine.core import build_context_bundle, _format_history, _recent_text

from context_engine.layers import (
    _state_schema_layer,
    _fact_groups_layer,
    _candidate_actions_layer,
    _active_hypotheses_layer,
    _write_results_layer,
    _timeline_layer,
    _safe_timeline_filter,
    _worldline_layer,
)

from context_engine.loaders import (
    _safe_load_chars,
    _load_characters,
    _load_characters_db,
    _load_worldbook_db,
    _load_world,
)

from context_engine.formatters import (
    _player_card,
    _active_character_cards,
    _active_worldbook,
    _worldbook_entries,
    _wb,
    _format_card,
    _strip_card_text,
    _strip_worldbook_text,
)

from context_engine.rules_text import (
    _story_rules,
    _agent_runtime_rules,
    _context_agent_decision,
    _context_agent_debug,
)

from context_engine.helpers import (
    _neutralize_state_write_tags,
    _pending_jump_warning_text,
    _normalize_permission_mode,
    _permission_label,
)

from context_engine._constants import MAX_LAYER_CHARS
from context_engine._utils import _layer, _trim, _preview, _estimate_tokens, _cache_plan

__all__ = [
    "build_context_bundle",
    "_format_history",
    "_recent_text",
    # layers
    "_state_schema_layer",
    "_fact_groups_layer",
    "_candidate_actions_layer",
    "_active_hypotheses_layer",
    "_write_results_layer",
    "_timeline_layer",
    "_safe_timeline_filter",
    "_worldline_layer",
    # loaders
    "_safe_load_chars",
    "_load_characters",
    "_load_characters_db",
    "_load_worldbook_db",
    "_load_world",
    # formatters
    "_player_card",
    "_active_character_cards",
    "_active_worldbook",
    "_worldbook_entries",
    "_wb",
    "_format_card",
    "_strip_card_text",
    "_strip_worldbook_text",
    # rules_text
    "_story_rules",
    "_agent_runtime_rules",
    "_context_agent_decision",
    "_context_agent_debug",
    # helpers
    "_neutralize_state_write_tags",
    "_pending_jump_warning_text",
    "_normalize_permission_mode",
    "_permission_label",
    # constants & utils
    "MAX_LAYER_CHARS",
    "_layer",
    "_trim",
    "_preview",
    "_estimate_tokens",
    "_cache_plan",
]
