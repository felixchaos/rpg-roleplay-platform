-- v011 user_runtime_db_backed — 对应 Python v11。
-- B2: runtime 元数据 DB 化。原 platform_data/runtime/user_{id}.json 的内容搬到这里。
-- 状态快照(state_snapshot)继续放在 runtime_checkouts;这里只放 user→当前激活 save 的指针。

create table if not exists user_runtime (
  user_id bigint primary key references users(id) on delete cascade,
  save_id bigint references game_saves(id) on delete set null,
  active_commit_id bigint,
  active_branch_node_id bigint,
  active_ref_id bigint,
  source_state_path text not null default '',
  runtime_state_path text not null default '',
  game_url text not null default '/',
  metadata jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

create index if not exists idx_user_runtime_save on user_runtime(save_id);
