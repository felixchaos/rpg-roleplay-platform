-- v022 branches_extended_columns — Wave 5-A 重写:基表已由 v001 建好,本 migration
-- 退化为"对 Python 共享库的渐进升级补丁",对 Rust fresh DB 走完 v001 后这里几乎
-- 全部 no-op(if not exists 兜底)。
--
-- 历史:Wave 2-D 时 v001 还在用 uuid 设计,基表全缺;v022 不得不补 game_saves /
-- branch_nodes / branch_commits / branch_refs / runtime_checkouts 的 create + alter。
-- Wave 5-A 把 v001 整体对齐 Python init.py,所有基表与扩展列都已在 v001 落地,
-- 这里只保留 idempotent alter 语句,留给"老 Python DB 没跑过这些 alter"的兼容场景。
--
-- 对照 Python 来源:
--   - branch_commits.digested_in_phase / digest_at:init.py L150..L156
--   - branch_commits.state_snapshot:init.py L663
--   - branch_refs.is_active:init.py L251(基表定义)
--   - runtime_checkouts 5 列:Python migrations.py v1 + init.py L667..L671
--   - game_saves 扩展列:init.py L664..L666

-- ────────────────────────────────────────────────────────────────
-- branch_nodes / branch_commits / branch_refs 扩展列
-- ────────────────────────────────────────────────────────────────

alter table branch_nodes add column if not exists summary text not null default '';

alter table branch_commits add column if not exists state_snapshot jsonb not null default '{}'::jsonb;
alter table branch_commits add column if not exists digested_in_phase integer;
alter table branch_commits add column if not exists digest_at timestamptz;
alter table branch_commits add column if not exists row_version bigint not null default 1;
alter table branch_commits add column if not exists public_id uuid not null default gen_random_uuid();
create unique index if not exists idx_branch_commits_public_id on branch_commits(public_id);

alter table branch_refs add column if not exists row_version bigint not null default 1;
alter table branch_refs add column if not exists public_id uuid not null default gen_random_uuid();
create unique index if not exists idx_branch_refs_public_id on branch_refs(public_id);

-- ────────────────────────────────────────────────────────────────
-- game_saves 扩展列
-- ────────────────────────────────────────────────────────────────

alter table game_saves add column if not exists active_branch_ref_id bigint;
alter table game_saves add column if not exists active_commit_id bigint;
alter table game_saves add column if not exists state_snapshot jsonb not null default '{}'::jsonb;
alter table game_saves add column if not exists active_phase_index integer not null default 0;

-- ────────────────────────────────────────────────────────────────
-- runtime_checkouts 扩展列(Python migrations.py v1 + init.py)
-- ────────────────────────────────────────────────────────────────

alter table runtime_checkouts add column if not exists ref_id bigint references branch_refs(id) on delete set null;
alter table runtime_checkouts add column if not exists commit_id bigint references branch_commits(id) on delete set null;
alter table runtime_checkouts add column if not exists runtime_state_path text not null default '';
alter table runtime_checkouts add column if not exists state_snapshot jsonb not null default '{}'::jsonb;
alter table runtime_checkouts add column if not exists snapshot_hash text not null default '';
alter table runtime_checkouts add column if not exists dirty boolean not null default false;
alter table runtime_checkouts add column if not exists turn_at_commit integer not null default 0;
alter table runtime_checkouts add column if not exists turn_runtime integer not null default 0;

-- ────────────────────────────────────────────────────────────────
-- 索引(对齐 Python init.py L680..L689)
-- ────────────────────────────────────────────────────────────────

create index if not exists idx_saves_user on game_saves(user_id, updated_at desc, id desc);
create index if not exists idx_saves_user_script on game_saves(user_id, script_id, id desc);
create index if not exists idx_branch_save on branch_nodes(save_id, id);
create index if not exists idx_branch_parent on branch_nodes(parent_id);
create index if not exists idx_branch_save_turn on branch_nodes(save_id, turn_index, id);
create index if not exists idx_branch_commits_save on branch_commits(save_id, id);
create index if not exists idx_branch_commits_parent on branch_commits(parent_id);
create index if not exists idx_branch_commits_save_turn on branch_commits(save_id, turn_index, id);
create index if not exists idx_branch_refs_save on branch_refs(save_id, id);
create index if not exists idx_branch_refs_target on branch_refs(target_commit_id);
