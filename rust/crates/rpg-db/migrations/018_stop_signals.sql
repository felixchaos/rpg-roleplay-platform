-- v018 stop_signals — Rust 端原生新增。
-- 跨进程取消信号表(对应 cluster.rs::request_stop)。

create table if not exists stop_signals (
  user_id bigint not null,
  run_id bigint not null,
  requested_at timestamptz not null default now(),
  primary key (user_id, run_id)
);
