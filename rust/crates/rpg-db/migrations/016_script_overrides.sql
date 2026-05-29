-- v016 script_overrides — 对应 Python v16。
-- 剧本 overrides 元数据 (signature tokens / suggestion rules /
-- phase inference / time_label_inference / known_names / known_locations 等)。
-- 从 modules/_script_overrides/<key>.json 迁移到 DB,为剧本分享 + 多用户隔离做准备。

create table if not exists script_overrides (
  script_id bigint primary key references scripts(id) on delete cascade,
  data jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);

create index if not exists idx_script_overrides_updated on script_overrides(updated_at);
