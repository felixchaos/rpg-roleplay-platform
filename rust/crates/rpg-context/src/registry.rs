//! Provider 注册表 + ContentPack manifest 解析 + 调度入口。
//! 对应 Python: rpg/context_providers/registry.py

use crate::provider::{ContextProvider, ProviderServices};
use crate::types::{ContextContribution, Demand, Manifest};
use rpg_schemas::GameStateData;
use serde_json::Value;
use std::collections::HashMap;
use std::sync::{Arc, LazyLock, RwLock};
use tracing::warn;

/// 全局 provider 注册表。
static REGISTRY: LazyLock<RwLock<HashMap<String, Arc<dyn ContextProvider>>>> =
    LazyLock::new(|| RwLock::new(HashMap::new()));

/// 注册一个 provider。重复 id 覆盖。
pub fn register_provider(provider: Arc<dyn ContextProvider>) {
    let id = provider.id();
    if id.is_empty() {
        panic!("provider 必须有非空 id");
    }
    REGISTRY
        .write()
        .expect("REGISTRY poisoned")
        .insert(id.to_string(), provider);
}

pub fn get_provider(provider_id: &str) -> Option<Arc<dyn ContextProvider>> {
    REGISTRY
        .read()
        .expect("REGISTRY poisoned")
        .get(provider_id)
        .cloned()
}

pub fn available_providers() -> Vec<String> {
    let guard = REGISTRY.read().expect("REGISTRY poisoned");
    let mut ids: Vec<String> = guard.keys().cloned().collect();
    ids.sort();
    ids
}

/// 注册所有内置 provider。调用一次即可。
///
/// 在 lib 顶层用 `Lazy` 或在应用启动时显式调用。
pub fn register_builtin_providers() {
    use crate::providers::*;
    register_provider(Arc::new(memory::MemoryProvider));
    register_provider(Arc::new(recent_chat::RecentChatProvider));
    register_provider(Arc::new(worldline::WorldlineProvider));
    register_provider(Arc::new(rules::RulesProvider));
    register_provider(Arc::new(module::ModuleSceneProvider));
    register_provider(Arc::new(module::ModuleEncounterProvider));
    register_provider(Arc::new(module::ModuleWorldbookProvider));
    register_provider(Arc::new(novel::NovelTimelineProvider));
    register_provider(Arc::new(novel::NovelRetrievalProvider));
    register_provider(Arc::new(novel::NovelCharactersProvider));
    register_provider(Arc::new(novel::NovelWorldbookProvider));
    register_provider(Arc::new(runtime_phase_digests::RuntimePhaseDigestProvider));
    register_provider(Arc::new(
        script_phase_anticipation::ScriptPhaseAnticipationProvider,
    ));
}

// ── 默认 Manifest ────────────────────────────────────────────────

pub fn default_novel_manifest() -> Manifest {
    Manifest {
        id: "__legacy_novel__".to_string(),
        kind: "novel_adaptation".to_string(),
        ruleset: "none".to_string(),
        context_providers: vec![
            "novel_timeline".into(),
            "novel_retrieval".into(),
            "novel_characters".into(),
            "novel_worldbook".into(),
            "memory".into(),
            "worldline".into(),
            "recent_chat".into(),
            "runtime_phase_digests".into(),
            "script_phase_anticipation".into(),
        ],
        retrieval_policy: serde_json::json!({
            "allow_script_retrieval": true,
            "allow_chapter_facts": true,
        }),
        gm_policy: serde_json::json!({
            "mode": "novel_gm",
            "must_obey_rules_result": false,
            "no_unverified_hard_state_write": false,
        }),
        extra: Default::default(),
    }
}

pub fn default_module_manifest() -> Manifest {
    Manifest {
        id: "__module_default__".to_string(),
        kind: "module_adventure".to_string(),
        ruleset: "5e_compatible".to_string(),
        context_providers: vec![
            "module_scene".into(),
            "module_encounter".into(),
            "module_worldbook".into(),
            "rules".into(),
            "memory".into(),
            "worldline".into(),
            "recent_chat".into(),
            "runtime_phase_digests".into(),
        ],
        retrieval_policy: serde_json::json!({
            "allow_script_retrieval": false,
            "allow_chapter_facts": false,
        }),
        gm_policy: serde_json::json!({
            "mode": "adventure_gm",
            "must_obey_rules_result": true,
            "no_unverified_hard_state_write": true,
        }),
        extra: Default::default(),
    }
}

pub fn default_freeform_manifest() -> Manifest {
    Manifest {
        id: "__freeform__".to_string(),
        kind: "freeform".to_string(),
        ruleset: "none".to_string(),
        context_providers: vec![
            "memory".into(),
            "worldline".into(),
            "recent_chat".into(),
            "runtime_phase_digests".into(),
        ],
        retrieval_policy: serde_json::json!({
            "allow_script_retrieval": false,
            "allow_chapter_facts": false,
        }),
        gm_policy: serde_json::json!({
            "mode": "freeform_gm",
            "must_obey_rules_result": false,
            "no_unverified_hard_state_write": false,
        }),
        extra: Default::default(),
    }
}

/// 根据当前 state 推断 active ContentPack manifest。
/// 对应 Python `resolve_content_pack(state, script_id)`。
pub fn resolve_content_pack(state_data: &GameStateData, script_id: Option<i64>) -> Manifest {
    // 1. state.content_pack 显式指定(存在 extra 里)
    if let Some(explicit) = state_data.extra.get("content_pack") {
        if explicit.is_object() {
            if let Some(providers) = explicit.get("context_providers") {
                if providers.as_array().map(|a| !a.is_empty()).unwrap_or(false) {
                    return normalize_manifest(explicit.clone());
                }
            }
        }
    }
    // 2. state.scene.module_manifest(模组开局写入,存在 scene.extra)
    let module_manifest = state_data.scene.extra
        .get("module_manifest")
        .cloned()
        .unwrap_or(Value::Null);
    let has_module = !state_data.scene.module_id.is_empty()
        || module_manifest.get("id").is_some_and(|v| !v.is_null());
    if has_module {
        let mut merged = default_module_manifest();
        if let Some(id) = module_manifest.get("id").and_then(|v| v.as_str()) {
            merged.id = id.to_string();
        } else if !state_data.scene.module_id.is_empty() {
            merged.id = state_data.scene.module_id.clone();
        }
        // TODO: 等 rpg-modules 把 _load_full_module_manifest 接上时再 merge full module.json
        return merged;
    }
    // 3. script_id 存在 → novel adaptation legacy 默认
    if let Some(sid) = script_id {
        let mut m = default_novel_manifest();
        m.id = format!("script:{}", sid);
        return m;
    }
    // 4. 老存档兼容:history 有内容也按 novel_adaptation 走
    if !state_data.history.is_empty() {
        let mut m = default_novel_manifest();
        m.id = "__legacy_save__".to_string();
        return m;
    }
    default_freeform_manifest()
}

/// 兜底补字段。对应 Python `_normalize_manifest`。
fn normalize_manifest(v: Value) -> Manifest {
    serde_json::from_value::<Manifest>(v).unwrap_or_default()
}

/// 按 manifest.context_providers 顺序运行每个 provider。
///
/// 对应 Python `run_providers(state, manifest, demand, services)`。
/// 返回 `(contributions, used_ids)`。任何 provider 异常都被吞掉。
pub async fn run_providers(
    state_data: &GameStateData,
    manifest: &Manifest,
    demand: &Demand,
    services: &ProviderServices,
) -> (Vec<ContextContribution>, Vec<String>) {
    // trait 直接吃 &GameStateData,免序列化
    let mut out: Vec<ContextContribution> = Vec::new();
    let mut used: Vec<String> = Vec::new();

    for pid in &manifest.context_providers {
        let provider = match get_provider(pid) {
            Some(p) => p,
            None => {
                let mut contrib = ContextContribution::skipped(pid.clone(), "未注册的 provider");
                contrib.warnings.push(format!("未注册的 provider: {}", pid));
                out.push(contrib);
                continue;
            }
        };
        if !provider.applies(state_data, manifest, demand) {
            out.push(ContextContribution::skipped(
                pid.clone(),
                "applies()=False",
            ));
            continue;
        }
        let result = provider.collect(state_data, manifest, demand, services).await;
        match result {
            Ok(mut contrib) => {
                // 保持 id 一致(防 provider 自己写错)
                contrib.provider_id = pid.clone();
                if contrib.applied {
                    used.push(pid.clone());
                }
                out.push(contrib);
            }
            Err(exc) => {
                warn!(provider = %pid, error = %exc, "provider failed");
                out.push(ContextContribution::failed(pid.clone(), exc));
            }
        }
    }

    (out, used)
}
