-- v006 user_preferences — 对应 Python v6。
-- 用户 jsonb 偏好桶。

create table if not exists user_preferences (
  user_id bigint primary key references users(id) on delete cascade,
  preferences jsonb not null default '{}'::jsonb,
  updated_at timestamptz not null default now()
);
