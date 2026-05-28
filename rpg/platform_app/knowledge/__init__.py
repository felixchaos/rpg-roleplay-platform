from __future__ import annotations

# Public API — re-export all non-private symbols so that
# `from platform_app import knowledge as k; k.sync_script_knowledge(...)` works
# and patch.object(knowledge, "sync_script_knowledge", ...) still works.
from platform_app.knowledge._constants import CHUNK_CHARS, CHUNK_OVERLAP
from platform_app.knowledge.character_cards import (
    delete_character_card,
    get_character_card,
    list_chapter_facts,
    list_character_cards,
    set_character_card_enabled,
    upsert_character_card,
)
from platform_app.knowledge.context_runs import (
    list_context_runs,
    record_context_run,
    record_turn_messages,
    update_context_run_status,
)
from platform_app.knowledge.memory import list_memories
from platform_app.knowledge.retrieval import (
    retrieve_runtime_context,
    retrieve_script_context,
)
from platform_app.knowledge.session import (
    ensure_game_session,
    sync_script_knowledge,
)
from platform_app.knowledge.worldbook import list_worldbook_entries
from platform_app.knowledge.worldline import (
    list_worldline_variables,
    remove_worldline_variable,
    set_worldline_variable,
)

__all__ = [
    "CHUNK_CHARS",
    "CHUNK_OVERLAP",
    "ensure_game_session",
    "sync_script_knowledge",
    "set_worldline_variable",
    "remove_worldline_variable",
    "list_worldline_variables",
    "record_context_run",
    "update_context_run_status",
    "record_turn_messages",
    "list_context_runs",
    "list_memories",
    "retrieve_runtime_context",
    "retrieve_script_context",
    "list_chapter_facts",
    "list_character_cards",
    "get_character_card",
    "upsert_character_card",
    "delete_character_card",
    "set_character_card_enabled",
    "list_worldbook_entries",
]
