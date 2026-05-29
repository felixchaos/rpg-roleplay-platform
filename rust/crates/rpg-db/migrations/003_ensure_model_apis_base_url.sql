-- v003 ensure_model_apis_base_url — 对应 Python v3。
-- OpenAI 兼容 provider 需要可配置 base_url。
-- model_apis 表在 Rust 端 001_init.sql 中未建,该 alter 在表存在时才生效。
-- 注:Rust 端目前未声明 model_apis 表(Python 端遗产),这里保留 ALTER 以便和老库兼容。

alter table model_apis add column if not exists base_url text not null default '';
