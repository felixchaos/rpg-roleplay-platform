//! Wave 11-B native client 测试 — Anthropic / GoogleAIStudio / AgentPlatform / DashScope。

use chrono::NaiveDate;
use model_catalog::providers::{
    agent_platform, alibaba_dashscope, anthropic, google_ai_studio,
};
use model_catalog::schema::{CatalogError, CatalogSource, ProviderId};
use wiremock::matchers::{header, method, path, query_param};
use wiremock::{Mock, MockServer, ResponseTemplate};

// ============================================================================
// Anthropic
// ============================================================================

#[test]
fn anthropic_static_catalog_has_main_models() {
    let v = anthropic::static_catalog().expect("static");
    assert!(v.iter().any(|m| m.id == "claude-opus-4-7"));
    assert!(v.iter().any(|m| m.id == "claude-sonnet-4-7"));
    assert!(v.iter().any(|m| m.id == "claude-haiku-4-5"));
    assert!(v.iter().any(|m| m.id == "claude-sonnet-4-5"));
    // claude-sonnet-4 应携带退休日期 2026-06-15
    let s4 = v.iter().find(|m| m.id == "claude-sonnet-4").expect("s4");
    assert_eq!(
        s4.retiring_at,
        Some(NaiveDate::from_ymd_opt(2026, 6, 15).unwrap())
    );
    for m in &v {
        assert_eq!(m.provider, ProviderId::Anthropic);
        assert_eq!(m.source, CatalogSource::StaticCatalog);
    }
}

#[tokio::test]
async fn anthropic_fetch_models_requires_api_key() {
    let client = reqwest::Client::new();
    let err = anthropic::fetch_models(&client, None, None).await.unwrap_err();
    matches!(err, CatalogError::MissingApiKey { provider: ProviderId::Anthropic, .. });
}

#[tokio::test]
async fn anthropic_fetch_parses_capabilities_and_pagination() {
    let server = MockServer::start().await;
    // 第一页 has_more=true,last_id="m1"
    Mock::given(method("GET"))
        .and(path("/models"))
        .and(header("x-api-key", "sk-test"))
        .and(header("anthropic-version", anthropic::API_VERSION))
        .and(query_param("limit", "100"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "data": [{
                "id": "claude-sonnet-4-5",
                "type": "model",
                "display_name": "Claude Sonnet 4.5",
                "deprecated_at": null,
                "retiring_at": "2026-06-15",
                "capabilities": {
                    "thinking": true, "batch": true, "structured_outputs": true,
                    "image_input": true, "pdf_input": true, "tool_use": true,
                    "prompt_caching": true, "web_search": true
                }
            }],
            "has_more": true,
            "first_id": "claude-sonnet-4-5",
            "last_id": "m1"
        })))
        .up_to_n_times(1)
        .mount(&server)
        .await;
    // 第二页 has_more=false
    Mock::given(method("GET"))
        .and(path("/models"))
        .and(query_param("after_id", "m1"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "data": [{
                "id": "claude-haiku-4-5",
                "type": "model",
                "display_name": "Claude Haiku 4.5",
                "capabilities": {"tool_use": true, "image_input": true}
            }],
            "has_more": false
        })))
        .mount(&server)
        .await;

    let client = reqwest::Client::new();
    let models = anthropic::fetch_models(&client, Some("sk-test"), Some(&server.uri()))
        .await
        .expect("fetch");
    assert_eq!(models.len(), 2);
    let s = &models[0];
    assert_eq!(s.id, "claude-sonnet-4-5");
    assert!(s.capabilities.extended_thinking);
    assert!(s.capabilities.tools);
    assert!(s.capabilities.vision);
    assert!(s.capabilities.pdf_input);
    assert!(s.capabilities.prompt_caching);
    assert!(s.capabilities.web_search);
    assert_eq!(
        s.retiring_at,
        Some(NaiveDate::from_ymd_opt(2026, 6, 15).unwrap())
    );
    assert_eq!(s.source, CatalogSource::LiveApi);

    let h = &models[1];
    assert!(h.capabilities.tools);
    assert!(h.capabilities.vision);
    // 未列在 caps 的 → false
    assert!(!h.capabilities.extended_thinking);
}

// ============================================================================
// Google AI Studio
// ============================================================================

#[test]
fn google_ai_studio_static_loads() {
    let v = google_ai_studio::static_catalog().expect("static");
    assert!(v.iter().any(|m| m.id == "gemini-2.5-pro"));
    assert!(v.iter().any(|m| m.id == "gemini-2.5-flash"));
    assert!(v.iter().any(|m| m.id == "text-embedding-004"));
    for m in &v {
        assert_eq!(m.provider, ProviderId::GoogleAIStudio);
    }
}

#[tokio::test]
async fn google_ai_studio_requires_api_key() {
    let client = reqwest::Client::new();
    let err = google_ai_studio::fetch_models(&client, None, None)
        .await
        .unwrap_err();
    matches!(err, CatalogError::MissingApiKey { provider: ProviderId::GoogleAIStudio, .. });
}

#[tokio::test]
async fn google_ai_studio_fetch_parses_token_limits_and_pagination() {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/models"))
        .and(header("x-goog-api-key", "ai-studio-key"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "models": [{
                "name": "models/gemini-2.5-pro",
                "displayName": "Gemini 2.5 Pro",
                "inputTokenLimit": 2097152,
                "outputTokenLimit": 65536,
                "supportedGenerationMethods": ["generateContent", "countTokens"],
                "thinking": true
            }, {
                "name": "models/text-embedding-004",
                "displayName": "Text Embedding 004",
                "inputTokenLimit": 2048,
                "supportedGenerationMethods": ["embedContent"]
            }],
            "nextPageToken": ""
        })))
        .mount(&server)
        .await;
    let client = reqwest::Client::new();
    let models = google_ai_studio::fetch_models(&client, Some("ai-studio-key"), Some(&server.uri()))
        .await
        .expect("fetch");
    assert_eq!(models.len(), 2);
    let pro = &models[0];
    assert_eq!(pro.id, "gemini-2.5-pro");
    assert_eq!(pro.context_window, Some(2097152));
    assert_eq!(pro.max_output_tokens, Some(65536));
    assert!(pro.capabilities.extended_thinking);
    assert!(pro.capabilities.tools);
    let emb = &models[1];
    assert_eq!(emb.id, "text-embedding-004");
    assert!(emb.capabilities.embedding);
    assert!(!emb.capabilities.tools);
}

// ============================================================================
// Agent Platform
// ============================================================================

#[test]
fn agent_platform_static_loads() {
    let v = agent_platform::static_catalog().expect("static");
    assert!(!v.is_empty());
    for m in &v {
        assert_eq!(m.provider, ProviderId::AgentPlatform);
    }
}

#[test]
fn agent_platform_display_name_and_aliases() {
    assert_eq!(agent_platform::DISPLAY_NAME, "Agent Platform");
    assert!(agent_platform::ALIASES.contains(&"Vertex AI"));
    assert!(agent_platform::ALIASES.contains(&"Gemini Enterprise"));
}

#[test]
fn agent_platform_from_env_reports_missing_sa() {
    // 隔离测试时 env 可能被并发改:仅断言无 env 时返回 MissingApiKey 或同等 InvalidConfig。
    let prev = std::env::var(agent_platform::SA_PATH_ENV).ok();
    std::env::remove_var(agent_platform::SA_PATH_ENV);
    let err = agent_platform::AgentPlatformConfig::from_env("google").unwrap_err();
    match err {
        CatalogError::MissingApiKey { env, .. } => {
            assert_eq!(env, agent_platform::SA_PATH_ENV)
        }
        other => panic!("unexpected err: {:?}", other),
    }
    if let Some(p) = prev {
        std::env::set_var(agent_platform::SA_PATH_ENV, p);
    }
}

#[tokio::test]
async fn agent_platform_fetch_reports_invalid_sa_file() {
    let client = reqwest::Client::new();
    let cfg = agent_platform::AgentPlatformConfig {
        service_account_path: std::path::PathBuf::from("/nonexistent/sa.json"),
        region: "us-central1".to_string(),
        publisher: "google".to_string(),
        base_url_override: None,
    };
    let err = agent_platform::fetch_models(&client, &cfg).await.unwrap_err();
    match err {
        CatalogError::InvalidConfig { provider, .. } => {
            assert_eq!(provider, ProviderId::AgentPlatform)
        }
        other => panic!("unexpected err: {:?}", other),
    }
}

// ============================================================================
// DashScope
// ============================================================================

#[test]
fn dashscope_static_loads_with_six_models() {
    let v = alibaba_dashscope::static_catalog().expect("static");
    assert!(v.len() >= 6, "expected ≥6 Qwen models, got {}", v.len());
    assert!(v.iter().any(|m| m.id == "qwen3-max"));
    assert!(v.iter().any(|m| m.id == "qwen-plus"));
    assert!(v.iter().any(|m| m.id == "qwen-turbo"));
    assert!(v.iter().any(|m| m.id == "qwq-32b-preview"));
    assert!(v.iter().any(|m| m.id == "qwen-vl-max"));
    for m in &v {
        assert_eq!(m.provider, ProviderId::AlibabaQwen);
    }
}

#[tokio::test]
async fn dashscope_fetch_returns_static_catalog() {
    // DashScope 无 /models 端点,fetch_models 实际等同 static_catalog
    let client = reqwest::Client::new();
    let v = alibaba_dashscope::fetch_models(&client, Some("sk-fake"))
        .await
        .expect("fetch");
    assert!(v.iter().any(|m| m.id == "qwen3-max"));
}

#[test]
fn dashscope_native_endpoint_is_correct() {
    assert_eq!(
        alibaba_dashscope::NATIVE_GENERATION_ENDPOINT,
        "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"
    );
}
