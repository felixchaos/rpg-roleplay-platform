-- v021 scripts_and_chapters — Wave 5-A 后:退化为 idempotent 补丁。
--
-- 历史:Wave 2-A 时 v001 还在用 uuid 设计、不建 scripts/script_chapters,
-- 这里第一次把这两张表落地;v014/v015/v016 的外键此前在 fresh DB 上 dangling。
-- Wave 5-A 把 v001 整体对齐 Python init.py,scripts/script_chapters 已在 v001
-- 完整建出,本 migration 退化为兜底 alter,保持已有版本号不变。

-- ────────────────────────────────────────────────────────────────
-- scripts:确保扩展列(对 Python 老库可能缺这几个)。
-- ────────────────────────────────────────────────────────────────

alter table scripts add column if not exists chapter_count integer not null default 0;
alter table scripts add column if not exists word_count integer not null default 0;
alter table scripts add column if not exists import_report jsonb not null default '{}'::jsonb;

-- ────────────────────────────────────────────────────────────────
-- script_chapters:确保 unique(script_id, chapter_index) 行为存在(由 v001
-- 在表内联约束保证)。这里再加一个等价 unique index 作为冗余兜底。
-- ────────────────────────────────────────────────────────────────

create unique index if not exists uq_script_chapters_script_chapter
  on script_chapters(script_id, chapter_index);
create index if not exists idx_scripts_owner on scripts(owner_id, id desc);
create index if not exists idx_script_chapters_script_order
  on script_chapters(script_id, chapter_index);
