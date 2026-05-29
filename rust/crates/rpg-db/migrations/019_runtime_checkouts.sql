-- v019 runtime_checkouts — Rust 端原生新增。
-- runtime_checkouts state 缓存时间戳表(对应 cluster.rs::is_state_stale)。

create table if not exists runtime_checkouts (
  save_id bigint primary key references game_saves(id) on delete cascade,
  user_id bigint not null references users(id) on delete cascade,
  worker_id text not null default '',
  updated_at timestamptz not null default now()
);

create index if not exists idx_runtime_checkouts_user on runtime_checkouts(user_id);
