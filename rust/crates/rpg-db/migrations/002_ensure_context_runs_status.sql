-- v002 ensure_context_runs_status — 对应 rpg/platform_app/db/migrations.py 的 v2。
-- 为 context_runs 补全 status / error / duration_ms / started_at 列。
-- 这些列在 Rust 端 001_init.sql 的 context_runs 表中尚未声明,所以仍需该 migration。

alter table context_runs add column if not exists status text not null default 'done';
alter table context_runs add column if not exists error text not null default '';
alter table context_runs add column if not exists duration_ms integer not null default 0;
alter table context_runs add column if not exists started_at timestamptz not null default now();
