-- v013 import_jobs_single_active_per_script — 对应 Python v13。
-- B5 加固:同 (user_id, script_id, kind) 在 pending/running 状态下只能有一行。
-- 配合 _schedule_knowledge_sync 的 INSERT ... ON CONFLICT DO NOTHING 做原子去重 +
-- _run_sync_job 的 UPDATE ... RETURNING 做原子领取,避免多进程重复跑同一任务。
-- heartbeat 用于回收死掉的 worker 占用的任务(守护进程巡检超时 running 用)。

create unique index if not exists uq_import_jobs_active_per_script
on import_jobs(user_id, script_id, kind)
where status in ('pending', 'running');

alter table import_jobs add column if not exists heartbeat_at timestamptz;

create index if not exists idx_import_jobs_heartbeat on import_jobs(status, heartbeat_at) where status = 'running';
