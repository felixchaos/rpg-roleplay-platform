//! knowledge —— RAG / embedding / retrieval。
//!
//! 完成度:
//! - `embedding`         **骨架**(`EmbeddingClient` trait + `EmbeddingJob` 状态机)
//! - `character_cards`   **主路径完整**(CRUD + Tavern V1/V2 import)
//! - `worldbook`         **主路径完整**(CRUD + `consult`)
//! - `memory`            **主路径完整**(CRUD,按 save_id+bucket 翻页)
//! - `retrieval`         **主路径完整**(chapter_facts + chunks BM25 + entity 向量召回)
//!
//! 对应 Python `rpg/platform_app/knowledge/` 子包。

pub mod character_cards;
pub mod embedding;
pub mod memory;
pub mod retrieval;
pub mod worldbook;

// TODO[Sonnet]: script_pack / script_overrides / session / worldline / context_runs

pub use character_cards::{
    delete_character_card, get_character_card, import_tavern_v2, list_character_cards,
    parse_tavern_card, set_character_card_enabled, tavern_v2_to_payload, upsert_character_card,
    CharacterCard, CharacterCardPayload,
};
pub use embedding::{
    embed_query, embed_script, embed_status, spawn_embed_script, EmbeddingClient,
    EmbeddingError, EmbeddingJobStatus, EmbeddingTaskType, VertexEmbeddingClient, BATCH_SIZE,
    EMBED_DIM, EMBED_MODEL, PER_CHUNK_CHAR_LIMIT,
};
pub use memory::{
    delete_memory, get_memory, list_memories, upsert_memory, MemoryItem, MemoryPayload,
};
pub use retrieval::{
    entity_search, entity_search_with_vec, list_chapter_facts, retrieve_runtime_context,
    retrieve_script_context, ChapterFactRow, EntityHit, RetrievalOptions,
};
pub use worldbook::{
    consult, delete_worldbook_entry, get_worldbook_entry, list_worldbook_entries,
    upsert_worldbook_entry, ConsultState, EntryHit, WorldbookEntry, WorldbookEntryPayload,
};
