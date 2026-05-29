-- v012 import_jobs_kind_for_durable_sync — 对应 Python v12。
-- B5: 让 import_jobs 同时承载 full_pipeline 和 knowledge_sync 两类任务。

alter table import_jobs add column if not exists kind text not null default 'full_pipeline';

create index if not exists idx_import_jobs_kind_user on import_jobs(kind, user_id, status, created_at desc);
