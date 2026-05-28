from __future__ import annotations

# ruff: noqa: F401
# Public API — re-export all non-private symbols so that
# `from platform_app import knowledge as k; k.sync_script_knowledge(...)` works
# and patch.object(knowledge, "sync_script_knowledge", ...) still works.
# Private helpers re-exported so import_pipeline can access `knowledge._ensure_book` etc.
from chapter_fact_indexer import (
    _known_concepts,
    _known_locations,
    _known_names,
    _load_summaries,
)
from context_engine.loaders import _load_characters, _load_world
from platform_app.knowledge._chunks import (
    _fact_from_chapter,
    _insert_chunk,
    _upsert_chapter_fact,
    _upsert_document,
)
from platform_app.knowledge._constants import CHUNK_CHARS, CHUNK_OVERLAP
from platform_app.knowledge._sync import _ensure_book
from platform_app.knowledge._utils import _chunk_text
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
