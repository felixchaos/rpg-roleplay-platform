"""state — 游戏状态 (按职责拆分子模块,GameState 整体在 core).

外部 import 方式不变:
    from state import GameState, SAVE_FILE, strip_json_state_ops
    from state import DEFAULT_STATE, _gm_is_asking_for_time_confirm, _split_items  # tests
"""
from state.core import (
    CURRENT_SCHEMA_VERSION,
    DEFAULT_STATE,
    MAX_HISTORY_TURNS,
    SAVE_FILE,
    GameState,
)
from state.extractors import (
    _extract_explicit_time_updates,
    _extract_location_override,
    _extract_player_time_directives,
    _extract_set_assignments,
    _extract_set_directive,
    _extract_set_time_targets,
    _extract_time_matches,
)
from state.json_ops import _extract_json_state_ops, strip_json_state_ops, strip_meta_tool_preamble
from state.labels import _risk_label, _validation_label
from state.parsers import (
    _clean_item,
    _parse_assignment,
    _parse_question,
    _split_items,
    _split_label,
    _split_relation,
)
from state.path_ops import (
    _clean_path,
    _get_path,
    _module_scene_active,
    _set_path,
    _write_path_allowed,
    _write_path_hard_forbidden,
    _write_path_kind,
    _write_path_module_managed,
    _write_path_rules_managed,
)
from state.permissions import _normalize_permission_mode, _permission_label
from state.time_ops import (
    _clean_time_value,
    _format_pending_timeline,
    _gm_is_asking_for_time_confirm,
    _looks_like_time_value,
    _phase_for_time,
)
from state.utils import _deep_update, _hit_score, _latest_assistant_text, _player_action_text

__all__ = [
    "GameState",
    "SAVE_FILE",
    "DEFAULT_STATE",
    "MAX_HISTORY_TURNS",
    "CURRENT_SCHEMA_VERSION",
    "strip_json_state_ops",
    "strip_meta_tool_preamble",
    "_extract_json_state_ops",
    "_clean_item",
    "_split_label",
    "_split_items",
    "_split_relation",
    "_parse_assignment",
    "_parse_question",
    "_extract_player_time_directives",
    "_extract_set_directive",
    "_extract_set_assignments",
    "_extract_location_override",
    "_extract_set_time_targets",
    "_extract_explicit_time_updates",
    "_extract_time_matches",
    "_gm_is_asking_for_time_confirm",
    "_clean_time_value",
    "_looks_like_time_value",
    "_format_pending_timeline",
    "_phase_for_time",
    "_normalize_permission_mode",
    "_permission_label",
    "_risk_label",
    "_validation_label",
    "_clean_path",
    "_write_path_hard_forbidden",
    "_write_path_rules_managed",
    "_write_path_module_managed",
    "_module_scene_active",
    "_write_path_allowed",
    "_write_path_kind",
    "_set_path",
    "_get_path",
    "_deep_update",
    "_latest_assistant_text",
    "_hit_score",
    "_player_action_text",
]
