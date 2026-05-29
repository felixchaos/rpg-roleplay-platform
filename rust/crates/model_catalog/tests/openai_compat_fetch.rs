//! 用 wiremock 模拟 /models 端点,验证 openai_compat::fetch_models 行为。

use model_catalog::providers::openai_compat::{fetch_models, OpenAICompatConfig};
use model_catalog::schema::{CatalogSource, ProviderId};
use wiremock::matchers::{header, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

fn make_config(server: &MockServer, provider: ProviderId) -> OpenAICompatConfig {
    OpenAICompatConfig {
        provider_id: provider,
        base_url: server.uri(),
        api_key_env: "TEST_API_KEY",
        extra_headers: vec![("X-Title".to_string(), "test".to_string())],
        models_endpoint: Some("/models".to_string()),
        static_models_path: None,
    }
}

#[tokio::test]
async fn fetch_basic_openai_list() {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/models"))
        .and(header("authorization", "Bearer sk-test"))
        .and(header("x-title", "test"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "object": "list",
            "data": [
                {"id": "gpt-4o", "object": "model"},
                {"id": "o3-mini", "object": "model"}
            ]
        })))
        .mount(&server)
        .await;

    let client = reqwest::Client::new();
    let cfg = make_config(&server, ProviderId::OpenAI);
    let models = fetch_models(&client, &cfg, Some("sk-test")).await.unwrap();
    assert_eq!(models.len(), 2);
    assert_eq!(models[0].id, "gpt-4o");
    assert_eq!(models[0].source, CatalogSource::LiveApi);
    assert!(models[0].capabilities.streaming);
}

#[tokio::test]
async fn fetch_openrouter_rich_catalog_parses_pricing_and_capabilities() {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/models"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "data": [
                {
                    "id": "anthropic/claude-opus-4-7",
                    "name": "Claude Opus 4.7",
                    "context_length": 200000,
                    "pricing": {
                        "prompt": "0.000015",
                        "completion": "0.000075",
                        "input_cache_read": "0.0000015",
                        "input_cache_write": "0.00001875"
                    },
                    "supported_parameters": ["tools", "tool_choice", "response_format", "reasoning"],
                    "architecture": {
                        "input_modalities": ["text", "image", "file"],
                        "output_modalities": ["text"]
                    },
                    "top_provider": {
                        "context_length": 200000,
                        "max_completion_tokens": 32768
                    }
                },
                {
                    "id": "google/gemini-2.5-pro",
                    "name": "Gemini 2.5 Pro",
                    "context_length": 2097152,
                    "pricing": {"prompt": "0.00000125", "completion": "0.00001"},
                    "supported_parameters": ["tools", "web_search_options"],
                    "architecture": {
                        "input_modalities": ["text", "image", "audio"],
                        "output_modalities": ["text"]
                    }
                }
            ]
        })))
        .mount(&server)
        .await;

    let client = reqwest::Client::new();
    let cfg = make_config(&server, ProviderId::OpenRouter);
    let models = fetch_models(&client, &cfg, None).await.unwrap();
    assert_eq!(models.len(), 2);

    let opus = &models[0];
    assert_eq!(opus.id, "anthropic/claude-opus-4-7");
    assert_eq!(opus.display_name, "Claude Opus 4.7");
    assert_eq!(opus.context_window, Some(200_000));
    assert_eq!(opus.max_output_tokens, Some(32_768));
    // 价格转 per-million USD
    assert!((opus.input_cost_per_million.unwrap() - 15.0).abs() < 1e-6);
    assert!((opus.output_cost_per_million.unwrap() - 75.0).abs() < 1e-6);
    assert!((opus.cache_read_cost_per_million.unwrap() - 1.5).abs() < 1e-6);
    assert!((opus.cache_write_cost_per_million.unwrap() - 18.75).abs() < 1e-6);
    assert!(opus.capabilities.tools);
    assert!(opus.capabilities.function_calling);
    assert!(opus.capabilities.structured_output);
    assert!(opus.capabilities.extended_thinking);
    assert!(opus.capabilities.vision);
    assert!(opus.capabilities.pdf_input);
    assert_eq!(opus.source, CatalogSource::OpenRouterProxy);

    let gemini = &models[1];
    assert!(gemini.capabilities.audio);
    assert!(gemini.capabilities.web_search);
}

#[tokio::test]
async fn fetch_500_propagates_error() {
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/models"))
        .respond_with(ResponseTemplate::new(500))
        .mount(&server)
        .await;

    let client = reqwest::Client::new();
    let cfg = make_config(&server, ProviderId::DeepSeek);
    let r = fetch_models(&client, &cfg, Some("x")).await;
    assert!(r.is_err());
}

#[tokio::test]
async fn fetch_no_live_endpoint_errors() {
    let server = MockServer::start().await;
    let mut cfg = make_config(&server, ProviderId::XiaomiMimo);
    cfg.models_endpoint = None;
    let client = reqwest::Client::new();
    let r = fetch_models(&client, &cfg, None).await;
    assert!(matches!(
        r,
        Err(model_catalog::schema::CatalogError::NoLiveEndpoint { .. })
    ));
}
