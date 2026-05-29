//! 静态 catalog JSON load + capability filter 测试。

use model_catalog::providers::{
    deepseek, openai, openrouter, tencent_hunyuan, xai, xiaomi_mimo,
};
use model_catalog::schema::{CatalogSource, ProviderId};

#[test]
fn openai_static_loads() {
    let v = openai::static_catalog().expect("openai");
    assert!(v.len() >= 4, "OpenAI 至少 4 个 model,实际 {}", v.len());
    assert!(v.iter().all(|m| m.provider == ProviderId::OpenAI));
    assert!(v.iter().all(|m| m.source == CatalogSource::StaticCatalog));
    assert!(v.iter().any(|m| m.id == "gpt-4o"));
}

#[test]
fn deepseek_static_loads() {
    let v = deepseek::static_catalog().expect("deepseek");
    assert!(v.len() >= 3);
    assert!(v.iter().any(|m| m.id == "deepseek-reasoner"));
    let r = v.iter().find(|m| m.id == "deepseek-reasoner").unwrap();
    assert!(r.capabilities.extended_thinking);
    assert!(!r.capabilities.tools);
}

#[test]
fn xai_static_loads() {
    let v = xai::static_catalog().expect("xai");
    assert!(v.len() >= 3);
    assert!(v.iter().any(|m| m.id.starts_with("grok-")));
}

#[test]
fn xiaomi_mimo_has_9_models_full_capability_coverage() {
    let v = xiaomi_mimo::static_catalog().expect("mimo");
    assert_eq!(v.len(), 9, "MiMo 必须 9 个 model,见 Wave 10 catalog 调研");
    // 必含的型号
    let ids: Vec<&str> = v.iter().map(|m| m.id.as_str()).collect();
    for needed in [
        "mimo-v2.5-pro",
        "mimo-v2-pro",
        "mimo-v2.5",
        "mimo-v2-omni",
        "mimo-v2-flash",
        "mimo-v2.5-tts",
    ] {
        assert!(ids.contains(&needed), "MiMo 缺 {}", needed);
    }
    // V2.5 Pro 1M ctx + extended_thinking
    let pro = v.iter().find(|m| m.id == "mimo-v2.5-pro").unwrap();
    assert_eq!(pro.context_window, Some(1_048_576));
    assert!(pro.capabilities.extended_thinking);
    assert!(pro.capabilities.web_search);
    assert!(!pro.capabilities.vision);
    // V2.5 omni 是 vision+audio
    let omni = v.iter().find(|m| m.id == "mimo-v2.5").unwrap();
    assert!(omni.capabilities.vision);
    assert!(omni.capabilities.audio);
    // V2 Pro 已宣布 deprecated_at
    let v2_pro = v.iter().find(|m| m.id == "mimo-v2-pro").unwrap();
    assert!(v2_pro.deprecated_at.is_some());
    // TTS 变体只能 audio
    let tts = v.iter().find(|m| m.id == "mimo-v2.5-tts").unwrap();
    assert!(tts.capabilities.audio);
    assert!(!tts.capabilities.tools);
}

#[test]
fn tencent_hunyuan_static_loads() {
    let v = tencent_hunyuan::static_catalog().expect("hunyuan");
    assert!(v.len() >= 5);
    assert!(v.iter().any(|m| m.id == "hunyuan-vision"));
    let vision = v.iter().find(|m| m.id == "hunyuan-vision").unwrap();
    assert!(vision.capabilities.vision);
}

#[test]
fn openrouter_fallback_static_loads() {
    let v = openrouter::static_catalog().expect("openrouter");
    assert!(!v.is_empty());
    // Wave 11-A 兜底 catalog 用 OpenRouterProxy
    assert!(v.iter().all(|m| m.source == CatalogSource::StaticCatalog));
    // 但 provider id 一律重写为 OpenRouter
    assert!(v.iter().all(|m| m.provider == ProviderId::OpenRouter));
}
