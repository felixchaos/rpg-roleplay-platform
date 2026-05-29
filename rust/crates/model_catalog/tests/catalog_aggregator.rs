//! ModelCatalog 聚合 + cache + override 测试。

use std::time::Duration;

use model_catalog::catalog::ModelCatalog;
use model_catalog::schema::ProviderId;
use wiremock::matchers::{method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

#[tokio::test]
async fn preload_static_includes_all_6_providers() {
    let cat = ModelCatalog::default();
    cat.preload_static().expect("preload");
    let all = cat.list_all().await;
    // 至少含 OpenAI / DeepSeek / xAI / MiMo(9) / Hunyuan / OpenRouter
    assert!(all.len() >= 4 + 3 + 3 + 9 + 5 + 3);
    // 6 家都有数据
    for p in [
        ProviderId::OpenAI,
        ProviderId::DeepSeek,
        ProviderId::XAi,
        ProviderId::XiaomiMimo,
        ProviderId::TencentHunyuan,
        ProviderId::OpenRouter,
    ] {
        assert!(all.iter().any(|m| m.provider == p), "缺 provider {:?}", p);
    }
}

#[tokio::test]
async fn get_by_id_after_preload() {
    let cat = ModelCatalog::default();
    cat.preload_static().expect("preload");
    let _ = cat.list_all().await;
    let m = cat.get("mimo-v2.5-pro").expect("mimo pro 应在 cache");
    assert_eq!(m.provider, ProviderId::XiaomiMimo);
    assert!(cat.get("nonexistent-model-id-zzz").is_none());
}

#[tokio::test]
async fn override_base_url_redirects_live_fetch() {
    // 起一个 mock server,把 DeepSeek 的 base_url 改指过去。
    let server = MockServer::start().await;
    Mock::given(method("GET"))
        .and(path("/models"))
        .respond_with(ResponseTemplate::new(200).set_body_json(serde_json::json!({
            "data": [{"id": "deepseek-chat-mock", "object": "model"}]
        })))
        .mount(&server)
        .await;

    let cat = ModelCatalog::new(Duration::from_secs(60));
    cat.set_base_url_override(ProviderId::DeepSeek, server.uri());
    cat.refresh(ProviderId::DeepSeek).await.expect("refresh");
    let list = cat
        .list_provider(ProviderId::DeepSeek)
        .await
        .expect("list");
    assert!(list.iter().any(|m| m.id == "deepseek-chat-mock"));
}

#[tokio::test]
async fn refresh_live_failure_falls_back_to_static() {
    // 改指到一个永远拒绝的地址 → live 必失败 → 应降级 static catalog。
    let cat = ModelCatalog::new(Duration::from_secs(60));
    cat.set_base_url_override(
        ProviderId::DeepSeek,
        // 127.0.0.1:1 端口几乎一定 connection refused
        "http://127.0.0.1:1".to_string(),
    );
    cat.refresh(ProviderId::DeepSeek)
        .await
        .expect("应该降级 static,不返回 Err");
    let list = cat
        .list_provider(ProviderId::DeepSeek)
        .await
        .expect("list");
    assert!(list.iter().any(|m| m.id == "deepseek-chat"));
}
