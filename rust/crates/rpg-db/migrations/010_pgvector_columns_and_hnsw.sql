-- v010 pgvector_columns_and_hnsw — 对应 Python v10。
-- 仅当 vector 扩展已启用时建 vector 列 + HNSW;否则保持 jsonb fallback。
-- 注:Rust 端 001_init.sql 已无条件启用 pgvector + 建好 document_chunks.embedding 和
--     memories.embedding(vector(768)) + HNSW 索引。这里的 embedding_vec 是 Python 时代
--     的兼容字段名,与 Rust 主表字段并行存在,Python 侧老库可用。保留 migration 保持序号连续。

do $$
begin
  if exists (select 1 from pg_extension where extname = 'vector') then
    execute 'alter table document_chunks add column if not exists embedding_vec vector(768)';
    execute 'alter table memories add column if not exists embedding_vec vector(768)';
    execute 'create index if not exists idx_doc_chunks_embedding_hnsw on document_chunks using hnsw (embedding_vec vector_cosine_ops)';
    execute 'create index if not exists idx_memories_embedding_hnsw on memories using hnsw (embedding_vec vector_cosine_ops)';
  end if;
end $$;
