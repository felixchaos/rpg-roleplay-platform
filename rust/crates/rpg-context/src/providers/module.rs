//! Module providers — manifest.kind == "module_adventure" 时启用。
//! 绝不引用 ChapterFact / 小说锚点 / 小说检索。
//! 对应 Python: rpg/context_providers/module.py

use crate::error::ContextResult;
use crate::provider::{ContextProvider, ProviderServices};
use crate::types::{ContextContribution, Demand, Layer, Manifest};
use async_trait::async_trait;
use rpg_schemas::GameStateData;
use serde_json::{json, Value};

fn module_id_of(state_data: &GameStateData) -> Option<String> {
    if state_data.scene.module_id.is_empty() {
        None
    } else {
        Some(state_data.scene.module_id.clone())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::types::{Demand, Manifest};
    use rpg_schemas::GameStateData;

    fn module_manifest(providers: &[&str]) -> Manifest {
        Manifest {
            id: "test_mod".into(),
            kind: "module_adventure".into(),
            ruleset: "5e_compatible".into(),
            context_providers: providers.iter().map(|s| s.to_string()).collect(),
            ..Default::default()
        }
    }

    // ── Wave 9-A: Module providers applies() 单测 ───────────────────

    #[test]
    fn module_scene_applies_when_module_id_set() {
        let mut state = GameStateData::default();
        state.scene.module_id = "keep_001".to_string();
        let manifest = module_manifest(&["module_scene"]);
        let demand = Demand::default();
        assert!(
            ModuleSceneProvider.applies(&state, &manifest, &demand),
            "module_id 存在时 ModuleSceneProvider 应 applies=true"
        );
    }

    #[test]
    fn module_scene_does_not_apply_without_module_id() {
        let state = GameStateData::default(); // module_id = ""
        let manifest = module_manifest(&["module_scene"]);
        let demand = Demand::default();
        assert!(
            !ModuleSceneProvider.applies(&state, &manifest, &demand),
            "无 module_id 时 ModuleSceneProvider 应 applies=false"
        );
    }

    #[test]
    fn module_encounter_applies_when_module_id_set() {
        let mut state = GameStateData::default();
        state.scene.module_id = "dungeon_boss".to_string();
        let manifest = module_manifest(&["module_encounter"]);
        let demand = Demand::default();
        assert!(
            ModuleEncounterProvider.applies(&state, &manifest, &demand),
            "module_id 存在时 ModuleEncounterProvider 应 applies=true"
        );
    }

    #[test]
    fn module_worldbook_does_not_apply_without_provider_in_manifest() {
        let mut state = GameStateData::default();
        state.scene.module_id = "test_mod".to_string();
        // module_worldbook 不在 context_providers 里
        let manifest = module_manifest(&["module_scene"]);
        let demand = Demand::default();
        assert!(
            !ModuleWorldbookProvider.applies(&state, &manifest, &demand),
            "不在 context_providers 中时应 applies=false"
        );
    }
}

fn load_bundle(services: &ProviderServices, module_id: &str) -> Option<Value> {
    let loader = services.module_loader.as_ref()?;
    loader(module_id).ok()
}

// ── ModuleSceneProvider ──────────────────────────────────────────

pub struct ModuleSceneProvider;

#[async_trait]
impl ContextProvider for ModuleSceneProvider {
    fn id(&self) -> &'static str {
        "module_scene"
    }

    fn applies(&self, state_data: &GameStateData, manifest: &Manifest, _demand: &Demand) -> bool {
        if !manifest.context_providers.iter().any(|p| p == self.id()) {
            return false;
        }
        module_id_of(state_data).is_some()
    }

    async fn collect(
        &self,
        state_data: &GameStateData,
        _manifest: &Manifest,
        _demand: &Demand,
        services: &ProviderServices,
    ) -> ContextResult<ContextContribution> {
        let scene = serde_json::to_value(&state_data.scene).unwrap_or(Value::Null);
        let module_id = match module_id_of(state_data) {
            Some(id) => id,
            None => return Ok(ContextContribution::skipped(self.id(), "no module_id")),
        };
        let bundle = match load_bundle(services, &module_id) {
            Some(b) => b,
            None => {
                return Ok(ContextContribution::skipped(
                    self.id(),
                    format!("无法加载模组 {}", module_id),
                ));
            }
        };

        let rooms = bundle
            .get("rooms")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        let current_room_id = scene
            .get("location_id")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();
        let current_room = rooms
            .iter()
            .find(|r| {
                r.get("id")
                    .and_then(|v| v.as_str())
                    .map(|s| s == current_room_id)
                    .unwrap_or(false)
            })
            .cloned()
            .unwrap_or(Value::Null);

        let manifest_meta = bundle.get("manifest").cloned().unwrap_or(Value::Null);
        let title = manifest_meta
            .get("name_cn")
            .or_else(|| manifest_meta.get("name"))
            .and_then(|v| v.as_str())
            .unwrap_or(&module_id)
            .to_string();

        let mut lines: Vec<String> = Vec::new();
        lines.push(format!("【模组】{}（{}）", title, module_id));
        if let Some(t) = manifest_meta.get("tagline").and_then(|v| v.as_str()) {
            if !t.is_empty() {
                lines.push(format!("基调：{}", t));
            }
        }

        let room_name = current_room
            .get("name")
            .and_then(|v| v.as_str())
            .unwrap_or(if current_room_id.is_empty() {
                "未知"
            } else {
                &current_room_id
            })
            .to_string();
        lines.push(format!("\n【当前房间】{}", room_name));
        if let Some(desc) = current_room.get("description").and_then(|v| v.as_str()) {
            if !desc.is_empty() {
                lines.push(desc.to_string());
            }
        }

        let exits = current_room
            .get("exits")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        if !exits.is_empty() {
            lines.push("\n【可用出口】".to_string());
            for ex in &exits {
                let to = ex.get("to").and_then(|v| v.as_str()).unwrap_or("");
                let label = ex.get("label").and_then(|v| v.as_str()).unwrap_or("");
                let req = ex
                    .get("requires")
                    .and_then(|v| v.as_str())
                    .map(|r| format!("（需要：{}）", r))
                    .unwrap_or_default();
                lines.push(format!("  · → {}：{}{}", to, label, req));
            }
        }

        let clues = current_room
            .get("visible_clues")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        if !clues.is_empty() {
            lines.push("\n【可见线索】".to_string());
            for c in &clues {
                let text = c
                    .get("text")
                    .and_then(|v| v.as_str())
                    .unwrap_or_else(|| c.as_str().unwrap_or(""));
                lines.push(format!("  · {}", text));
            }
        }

        let checks = current_room
            .get("checks")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        if !checks.is_empty() {
            lines.push(
                "\n【可发起检定（玩家若主动尝试，GM 不可自行掷骰，必须经规则引擎）】".to_string(),
            );
            for chk in &checks {
                let kind = chk
                    .get("kind")
                    .and_then(|v| v.as_str())
                    .unwrap_or("skill_check");
                let skill = chk
                    .get("skill")
                    .or_else(|| chk.get("ability"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                let dc = chk.get("dc").cloned().unwrap_or(Value::Null);
                let fact = chk
                    .get("fact")
                    .or_else(|| chk.get("reveals"))
                    .and_then(|v| v.as_str())
                    .unwrap_or("");
                lines.push(format!("  · {} {} DC {} — {}", kind, skill, dc, fact));
            }
        }

        let hazards = current_room
            .get("hazards")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        if !hazards.is_empty() {
            lines.push("\n【环境危险】".to_string());
            for h in &hazards {
                let desc = h
                    .get("description")
                    .and_then(|v| v.as_str())
                    .or_else(|| h.get("id").and_then(|v| v.as_str()))
                    .unwrap_or("");
                lines.push(format!("  · {}", desc));
            }
        }

        let flags = scene.get("flags").and_then(|v| v.as_object()).cloned();
        if let Some(flags) = flags {
            let on_flags: Vec<&String> = flags
                .iter()
                .filter_map(|(k, v)| {
                    if v.as_bool().unwrap_or(false) {
                        Some(k)
                    } else {
                        None
                    }
                })
                .collect();
            if !on_flags.is_empty() {
                lines.push(format!(
                    "\n【场景标记】{}",
                    on_flags
                        .into_iter()
                        .map(|s| s.to_string())
                        .collect::<Vec<_>>()
                        .join(", ")
                ));
            }
        }
        let visited = scene
            .get("visited_rooms")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        if !visited.is_empty() {
            let names: Vec<String> = visited
                .iter()
                .filter_map(|v| v.as_str().map(String::from))
                .collect();
            lines.push(format!("\n【已访问房间】{}", names.join(", ")));
        }

        let text = lines.join("\n");
        let layer = Layer::new("module_scene", "当前模组场景", text.clone()).with_priority(90);
        let mut facts = vec![format!("模组『{}』当前房间：{}", title, room_name)];
        if !exits.is_empty() {
            let to_list: Vec<String> = exits
                .iter()
                .filter_map(|e| e.get("to").and_then(|v| v.as_str()).map(String::from))
                .collect();
            facts.push(format!("出口：{}", to_list.join(", ")));
        }
        let tokens = (text.chars().count() / 2) as u32;
        Ok(ContextContribution {
            provider_id: self.id().to_string(),
            kind: "module_scene".to_string(),
            priority: 90,
            facts,
            layers: vec![layer],
            retrieval_items: Vec::new(),
            warnings: Vec::new(),
            debug: json!({
                "module_id": module_id,
                "current_room": current_room_id,
                "exits": exits.iter().filter_map(|e| e.get("to").cloned()).collect::<Vec<_>>(),
                "checks_count": checks.len(),
            }),
            tokens_estimate: tokens,
            applied: true,
        })
    }
}

// ── ModuleEncounterProvider ──────────────────────────────────────

pub struct ModuleEncounterProvider;

#[async_trait]
impl ContextProvider for ModuleEncounterProvider {
    fn id(&self) -> &'static str {
        "module_encounter"
    }

    fn applies(&self, state_data: &GameStateData, manifest: &Manifest, _demand: &Demand) -> bool {
        if !manifest.context_providers.iter().any(|p| p == self.id()) {
            return false;
        }
        module_id_of(state_data).is_some()
    }

    async fn collect(
        &self,
        state_data: &GameStateData,
        _manifest: &Manifest,
        _demand: &Demand,
        services: &ProviderServices,
    ) -> ContextResult<ContextContribution> {
        let scene = serde_json::to_value(&state_data.scene).unwrap_or(Value::Null);
        let encounter = serde_json::to_value(&state_data.encounter).unwrap_or(Value::Null);
        let module_id = match module_id_of(state_data) {
            Some(id) => id,
            None => return Ok(ContextContribution::skipped(self.id(), "no module")),
        };
        let bundle = match load_bundle(services, &module_id) {
            Some(b) => b,
            None => {
                return Ok(ContextContribution::skipped(
                    self.id(),
                    format!("无法加载模组 {}", module_id),
                ));
            }
        };

        let mut lines: Vec<String> = Vec::new();
        let active = encounter.get("active").and_then(|v| v.as_bool()).unwrap_or(false);
        if active {
            lines.push("【战斗进行中】".to_string());
            let round = encounter.get("round").cloned().unwrap_or(Value::Null);
            let turn_index = encounter.get("turn_index").cloned().unwrap_or(Value::Null);
            lines.push(format!(
                "  · 第 {} 回合，turn_index={}",
                round, turn_index
            ));
            if let Some(combatants) = encounter.get("combatants").and_then(|v| v.as_array()) {
                for c in combatants {
                    let name = c.get("name").and_then(|v| v.as_str()).unwrap_or("");
                    let side = c.get("side").and_then(|v| v.as_str()).unwrap_or("");
                    let ac = c.get("ac").cloned().unwrap_or(Value::Null);
                    let mark = if c.get("defeated").and_then(|v| v.as_bool()).unwrap_or(false) {
                        "已倒下".to_string()
                    } else {
                        format!(
                            "HP {}/{}",
                            c.get("hp").cloned().unwrap_or(Value::Null),
                            c.get("max_hp").cloned().unwrap_or(Value::Null)
                        )
                    };
                    lines.push(format!("  · {} [{}] AC {} · {}", name, side, ac, mark));
                }
            }
            if let Some(init) = encounter.get("initiative_order").and_then(|v| v.as_array()) {
                if !init.is_empty() {
                    let parts: Vec<String> = init
                        .iter()
                        .map(|i| {
                            let name = i.get("name").and_then(|v| v.as_str()).unwrap_or("");
                            let init = i.get("init").cloned().unwrap_or(Value::Null);
                            format!("{}({})", name, init)
                        })
                        .collect();
                    lines.push(format!("  · 先攻：{}", parts.join(" > ")));
                }
            }
        }

        let encs = bundle
            .get("encounters")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();
        let loc_id = scene
            .get("location_id")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let rel: Vec<&Value> = encs
            .iter()
            .filter(|e| {
                e.get("location_id")
                    .and_then(|v| v.as_str())
                    .map(|s| s == loc_id)
                    .unwrap_or(false)
            })
            .collect();
        if !rel.is_empty() {
            lines.push("\n【本房间可能的预设遭遇】".to_string());
            for e in &rel {
                let id = e.get("id").and_then(|v| v.as_str()).unwrap_or("");
                let name = e.get("name").and_then(|v| v.as_str()).unwrap_or("");
                let desc = e.get("description").and_then(|v| v.as_str()).unwrap_or("");
                lines.push(format!("  · id={} — {} — {}", id, name, desc));
            }
        }
        if lines.is_empty() {
            return Ok(ContextContribution::skipped(self.id(), "无战斗 / 无预设遭遇"));
        }

        let text = lines.join("\n");
        let layer = Layer::new("module_encounter", "战斗 / 遭遇", text.clone()).with_priority(85);
        let mut facts: Vec<String> = Vec::new();
        if active {
            facts.push("战斗进行中 — GM 必须遵守规则引擎结果，禁止编造伤害/命中/HP。".to_string());
        }
        let preset_encounters: Vec<Value> = rel
            .iter()
            .filter_map(|e| e.get("id").cloned())
            .collect();
        let tokens = (text.chars().count() / 2) as u32;
        Ok(ContextContribution {
            provider_id: self.id().to_string(),
            kind: "module_encounter".to_string(),
            priority: 85,
            facts,
            layers: vec![layer],
            retrieval_items: Vec::new(),
            warnings: Vec::new(),
            debug: json!({
                "active": active,
                "preset_encounters": preset_encounters,
            }),
            tokens_estimate: tokens,
            applied: true,
        })
    }
}

// ── ModuleWorldbookProvider ──────────────────────────────────────

pub struct ModuleWorldbookProvider;

#[async_trait]
impl ContextProvider for ModuleWorldbookProvider {
    fn id(&self) -> &'static str {
        "module_worldbook"
    }

    fn applies(&self, state_data: &GameStateData, manifest: &Manifest, _demand: &Demand) -> bool {
        if !manifest.context_providers.iter().any(|p| p == self.id()) {
            return false;
        }
        module_id_of(state_data).is_some()
    }

    async fn collect(
        &self,
        state_data: &GameStateData,
        _manifest: &Manifest,
        _demand: &Demand,
        services: &ProviderServices,
    ) -> ContextResult<ContextContribution> {
        let module_id = match module_id_of(state_data) {
            Some(id) => id,
            None => return Ok(ContextContribution::skipped(self.id(), "no module")),
        };
        let bundle = match load_bundle(services, &module_id) {
            Some(b) => b,
            None => return Ok(ContextContribution::skipped(self.id(), "no module")),
        };
        let wb = bundle.get("worldbook").cloned().unwrap_or(Value::Null);
        if !wb.is_object() {
            return Ok(ContextContribution::skipped(self.id(), "no worldbook"));
        }
        let mut lines: Vec<String> = Vec::new();
        if let Some(s) = wb.get("setting").and_then(|v| v.as_str()) {
            if !s.is_empty() {
                lines.push(format!("【世界设定】{}", s));
            }
        }
        if let Some(factions) = wb.get("factions").and_then(|v| v.as_array()) {
            for fac in factions {
                let name = fac.get("name").and_then(|v| v.as_str()).unwrap_or("");
                let summary = fac.get("summary").and_then(|v| v.as_str()).unwrap_or("");
                lines.push(format!("\n【派系】{} — {}", name, summary));
            }
        }
        if let Some(themes) = wb.get("themes").and_then(|v| v.as_array()) {
            let s: Vec<String> = themes
                .iter()
                .filter_map(|v| v.as_str().map(String::from))
                .collect();
            if !s.is_empty() {
                lines.push(format!("\n【主题】{}", s.join(" / ")));
            }
        }
        if let Some(guides) = wb.get("tone_guide").and_then(|v| v.as_array()) {
            if !guides.is_empty() {
                lines.push("\n【GM 风格指引】".to_string());
                for g in guides {
                    if let Some(s) = g.as_str() {
                        lines.push(format!("  · {}", s));
                    }
                }
            }
        }
        if let Some(notice) = wb.get("rules_notice").and_then(|v| v.as_str()) {
            if !notice.is_empty() {
                lines.push(format!("\n【规则边界】{}", notice));
            }
        }

        let text = lines.join("\n");
        let layer = Layer::new("module_worldbook", "模组世界书", text.clone())
            .with_sticky(true)
            .with_priority(75);
        let factions_ids: Vec<Value> = wb
            .get("factions")
            .and_then(|v| v.as_array())
            .map(|arr| arr.iter().filter_map(|f| f.get("id").cloned()).collect())
            .unwrap_or_default();
        let tokens = (text.chars().count() / 2) as u32;
        Ok(ContextContribution {
            provider_id: self.id().to_string(),
            kind: "module_worldbook".to_string(),
            priority: 75,
            facts: Vec::new(),
            layers: vec![layer],
            retrieval_items: Vec::new(),
            warnings: Vec::new(),
            debug: json!({ "factions": factions_ids }),
            tokens_estimate: tokens,
            applied: true,
        })
    }
}
