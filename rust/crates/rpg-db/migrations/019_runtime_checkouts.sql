-- v019 runtime_checkouts — Wave 5-A 后:no-op 占位。
--
-- 历史:此 migration 曾用 `save_id PRIMARY KEY` 的简化版建 runtime_checkouts;
-- 与 Python(bigserial id + unique(user_id,save_id))不兼容。Wave 5-A 将
-- runtime_checkouts 完整版迁回 v001(对齐 Python),v019 不再需要建表。
--
-- 保留版本号 + 文件以维持 schema_migrations 编号连续性(已部署环境的 v019
-- 行不能丢)。新库走完 v001 后,这里只是个 idempotent no-op,确保 v022 的
-- alter 在老 Python 库上仍能命中正确的列形态。

-- 让 SQL 文件非空(满足 migrations_non_empty_sql 测试),且对 fresh DB 完全无害。
-- 等价于"如果 runtime_checkouts 存在就什么都不做",并确保它至少有 save_id 列。
alter table runtime_checkouts add column if not exists save_id bigint;
