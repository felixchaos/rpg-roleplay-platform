"""context_engine — 上下文构建引擎 (按职责拆分)."""
from context_engine._constants import MAX_LAYER_CHARS
from context_engine._utils import _cache_plan, _estimate_tokens, _layer, _preview, _trim
from context_engine.core import _format_history, _recent_text, build_context_bundle
from context_engine.formatters import (
    _active_character_cards,
    _active_worldbook,
    _format_card,
    _player_card,
    _strip_card_text,
    _strip_worldbook_text,
    _wb,
    _worldbook_entries,
)
from context_engine.helpers import (
    _neutralize_state_write_tags,
    _normalize_permission_mode,
    _pending_jump_warning_text,
    _permission_label,
)
from context_engine.layers import (
    _active_hypotheses_layer,
    _candidate_actions_layer,
    _fact_groups_layer,
    _safe_timeline_filter,
    _state_schema_layer,
    _timeline_layer,
    _worldline_layer,
    _write_results_layer,
)
from context_engine.loaders import (
    _load_characters,
    _load_characters_db,
    _load_world,
    _load_worldbook_db,
    _safe_load_chars,
)
from context_engine.rules_text import (
    _agent_runtime_rules,
    _context_agent_debug,
    _context_agent_decision,
    _story_rules,
)

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
