//! schema 序列化往返测试 + 触发 ts-rs export(--features ts-rs 才真的写盘)。

use chrono::{TimeZone, Utc};
use model_catalog::schema::{
    CatalogSource, ModelCapabilities, ModelInfo, ProviderId,
};

fn sample() -> ModelInfo {
    ModelInfo {
        id: "gpt-test".to_string(),
        provider: ProviderId::OpenAI,
        display_name: "Test".to_string(),
        context_window: Some(128_000),
        max_output_tokens: Some(8_192),
        input_cost_per_million: Some(2.5),
        output_cost_per_million: Some(10.0),
        cache_write_cost_per_million: None,
        cache_read_cost_per_million: Some(1.25),
        capabilities: ModelCapabilities {
            streaming: true,
            tools: true,
            vision: true,
            audio: false,
            structured_output: true,
            extended_thinking: false,
            embedding: false,
            function_calling: true,
            prompt_caching: true,
            web_search: false,
            pdf_input: true,
        },
        unsupported_params: vec!["temperature".to_string()],
        deprecated_at: None,
        retiring_at: None,
        source: CatalogSource::LiveApi,
        last_updated: Utc.with_ymd_and_hms(2026, 5, 1, 0, 0, 0).unwrap(),
    }
}

#[test]
fn model_info_roundtrip() {
    let m = sample();
    let json = serde_json::to_string(&m).unwrap();
    let back: ModelInfo = serde_json::from_str(&json).unwrap();
    assert_eq!(m, back);
}

#[test]
fn provider_id_slug_unique() {
    let all = [
        ProviderId::OpenAI,
        ProviderId::Anthropic,
        ProviderId::GoogleAIStudio,
        ProviderId::AgentPlatform,
        ProviderId::OpenRouter,
        ProviderId::DeepSeek,
        ProviderId::XAi,
        ProviderId::XiaomiMimo,
        ProviderId::AlibabaQwen,
        ProviderId::TencentHunyuan,
    ];
    let slugs: std::collections::HashSet<&str> = all.iter().map(|p| p.slug()).collect();
    assert_eq!(slugs.len(), all.len(), "slug 必须唯一");
}

#[test]
fn catalog_source_serializes_as_pascal() {
    // serde 默认 enum 序列化 = "LiveApi";前端导出与之对齐。
    let s = serde_json::to_string(&CatalogSource::LiveApi).unwrap();
    assert_eq!(s, "\"LiveApi\"");
    let s = serde_json::to_string(&CatalogSource::OpenRouterProxy).unwrap();
    assert_eq!(s, "\"OpenRouterProxy\"");
}

#[cfg(feature = "ts-rs")]
#[test]
fn ts_export_triggers() {
    use ts_rs::TS;
    // 注:`#[ts(export)]` 注解的类型在 `cargo test` 运行任何 case 时会被 ts-rs
    // 自动写盘到 `export_to` 指定路径,这里只是显式 sanity check 一下函数链路可用。
    let cfg = ts_rs::Config::default();
    ModelInfo::export_all(&cfg).expect("ts-rs export ModelInfo + deps");
}
