//! migrate.rs — `_migrate` 升级链 (v1 → v6)
//!
//! 对应 Python: `rpg/state/core.py::GameState._migrate`(~150 行 monolithic 版本)。
//!
//! Python 侧把所有兼容补全堆在一个 staticmethod 里(deep_update + setdefault +
//! 多个 namespace 兜底)。Rust 这边按 schema_version 升级粒度拆成 5 个独立函数
//! `migrate_v{N}_to_v{N+1}`,然后用 [`migrate`] 串成 v1→v6 升级链;再加一个
//! [`migrate_v6_final`] 跑各 namespace 的 backfill(memory legacy → items、
//! player.secrets → player_private.secrets、worldline.story_intent →
//! player_private.story_intent、player_character.hp 回填)。这些 backfill 在
//! Python 里同 `_migrate` 一锅煮,这里在最终版本 final pass 跑一次,语义等价。
//!
//! 调用约定:[`GameState::from_value`] 用 serde 反序列化拿到 typed
//! [`GameStateData`] 后,把原始 schema_version(0/默认 6) 传给本模块。本模块
//! 直接改 `&mut GameStateData`,最后无条件把 `schema_version` 置为
//! [`crate::state::CURRENT_SCHEMA_VERSION`]。
//!
//! ## 各版本改了什么字段
//! - v1→v2: `permissions.{mode,pending_writes,pending_questions,audit_log}` +
//!   `worldline.{user_variables,constraints,last_validation}` 兜底。
//! - v2→v3: `world.timeline.{anchor_state,current_phase,anchor_turn,
//!   anchor_source,pending_jump,last_transition,current_label}` 兜底。
//! - v3→v4: `memory.items = []`(task 74)。
//! - v4→v5: `ruleset` / `player_character` / `scene` / `encounter` / `dice_log`
//!   补 5E 默认骨架(task 5E)。
//! - v5→v6: `player_private` namespace(task 138)。
//!
//! ## 严格不动 schema
//! 全部用 typed struct + Map/Value 子树写入,不增字段。

use rpg_schemas::GameStateData;
use serde_json::{json, Value};

use crate::state::CURRENT_SCHEMA_VERSION;

/// 入口:把 `data` 从 `from_version` 升到 [`CURRENT_SCHEMA_VERSION`]。
///
/// `from_version == 0` 视作 v1(老存档没写 schema_version 也是这一类)。
pub fn migrate(data: &mut GameStateData, from_version: u64) -> Result<(), MigrateError> {
    let mut v = if from_version == 0 { 1 } else { from_version };
    while v < CURRENT_SCHEMA_VERSION {
        match v {
            1 => migrate_v1_to_v2(data)?,
            2 => migrate_v2_to_v3(data)?,
            3 => migrate_v3_to_v4(data)?,
            4 => migrate_v4_to_v5(data)?,
            5 => migrate_v5_to_v6(data)?,
            _ => return Err(MigrateError::UnknownVersion(v)),
        }
        v += 1;
    }
    // 最终 backfill pass(语义等价 Python `_migrate` 同函数内做的事)。
    migrate_v6_final(data)?;
    data.schema_version = CURRENT_SCHEMA_VERSION;
    Ok(())
}

#[derive(Debug, thiserror::Error)]
pub enum MigrateError {
    #[error("unknown schema version: {0}")]
    UnknownVersion(u64),
}

/// v1 → v2:permissions / worldline 兜底字段。
pub fn migrate_v1_to_v2(data: &mut GameStateData) -> Result<(), MigrateError> {
    // permissions —— typed struct 已经有 default,但旧存档可能整段没有,反序列
    // 化已经替我们填了 default。这里只是把 mode 在为空时归位。
    if data.permissions.mode.is_empty() {
        data.permissions.mode = "full_access".into();
    }
    // worldline.constraints 旧存档可能为空 vec —— 用 default。
    if data.worldline.constraints.is_empty() {
        data.worldline.constraints = default_worldline_constraints();
    }
    // last_validation status 老存档可能空。
    if data.worldline.last_validation.status.is_empty() {
        data.worldline.last_validation.status = "none".into();
    }
    data.schema_version = 2;
    Ok(())
}

/// v2 → v3:world.timeline 子树各字段兜底。
pub fn migrate_v2_to_v3(data: &mut GameStateData) -> Result<(), MigrateError> {
    let tl = &mut data.world.timeline;
    // current_label 缺时,用 world.time 兜底,anchor_source 标 migrated。
    if tl.current_label.is_empty() && !data.world.time.is_empty() {
        tl.current_label = data.world.time.clone();
        tl.anchor_source = "migrated".into();
    }
    if tl.anchor_state.is_empty() {
        tl.anchor_state = "locked".into();
    }
    // current_phase 通用底座默认空串(详见 Python 注释:不再硬编码柏林暗流篇)。
    // anchor_turn 兜底 turn。
    if tl.anchor_turn == 0 && data.turn > 0 {
        tl.anchor_turn = data.turn;
    }
    // pending_jump / last_transition 为 None 时不动 —— Python 用 setdefault(None)。
    data.schema_version = 3;
    Ok(())
}

/// v3 → v4:memory.items 默认 [](task 74)。
///
/// typed `Memory::items: Vec<Value>` 已经 default 空 vec;这里把状态推进版本号即可,
/// 真实的 legacy backfill 留到 [`migrate_v6_final`](和 Python 同位置)。
pub fn migrate_v3_to_v4(data: &mut GameStateData) -> Result<(), MigrateError> {
    // 显式触达,避免 typed 字段就算 default 也被认为"不知道升级到 v4 改了什么"。
    let _ = &data.memory.items;
    data.schema_version = 4;
    Ok(())
}

/// v4 → v5:ruleset / player_character / scene / encounter / dice_log 5E 骨架。
///
/// 字段全是 typed,反序列化时 `#[serde(default)]` 已经填 default。这里只是把
/// ruleset.id 在为空时兜回 "dnd5e"(对齐 DEFAULT_STATE)。
pub fn migrate_v4_to_v5(data: &mut GameStateData) -> Result<(), MigrateError> {
    if data.ruleset.id.is_empty() {
        data.ruleset.id = "dnd5e".into();
        data.ruleset.mode = "5e_compatible".into();
        data.ruleset.public_label = "5E compatible / 五版规则兼容".into();
    }
    data.schema_version = 5;
    Ok(())
}

/// v5 → v6:player_private namespace(task 138)。
pub fn migrate_v5_to_v6(data: &mut GameStateData) -> Result<(), MigrateError> {
    // typed PlayerPrivate 反序列化已经给 default;这里把 story_intent 之类的"必须
    // 是字符串"再确认一下(serde 已经强类型 String,直接信)。无字段需要补。
    let _ = &data.player_private;
    data.schema_version = 6;
    Ok(())
}

/// 最终 backfill pass:legacy memory → items / player.secrets → player_private /
/// worldline.user_variables.story_intent → player_private.story_intent /
/// player_character.hp 回填。
///
/// 对应 Python `_migrate` 后半段(memory backfill + secrets 迁移 + intent 迁移 +
/// hp 兜底)。
pub fn migrate_v6_final(data: &mut GameStateData) -> Result<(), MigrateError> {
    // 1) player.secrets(老 task 137 字段) → player_private.secrets
    //    player 没有专门 typed secrets 字段,旧存档把它塞在 player.extra 里。
    let old_secrets = data.player.extra.remove("secrets");
    if let Some(v) = old_secrets {
        push_legacy_secrets(&mut data.player_private.secrets, v);
    }

    // 2) worldline.user_variables.story_intent → player_private.story_intent
    //    只在目标 story_intent 仍空时迁,避免覆盖玩家本回合已写。
    if data.player_private.story_intent.is_empty() {
        if let Some(intent) = data.worldline.user_variables.get("story_intent") {
            let text = match intent {
                Value::String(s) => s.trim().to_string(),
                Value::Object(o) => o
                    .get("value")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .trim()
                    .to_string(),
                _ => String::new(),
            };
            if !text.is_empty() {
                data.player_private.story_intent = text;
            }
        }
    }

    // 3) player_character.hp 回填:max_hp 有值但 hp 没,hp = max_hp。
    if data.player_character.max_hp > 0 && data.player_character.hp == 0 {
        data.player_character.hp = data.player_character.max_hp;
    }

    // 4) memory.items 为空 & 任一 legacy bucket 有内容 → 整段转 MemoryItem 注入 items。
    //    source = legacy_migration_v1,turn = 0,kind = runtime_fact。
    if data.memory.items.is_empty() {
        let mut backfilled: Vec<Value> = Vec::new();
        for bucket in ["facts", "notes", "pinned", "abilities", "resources"] {
            let arr: &Vec<Value> = match bucket {
                "facts" => &data.memory.facts,
                "notes" => &data.memory.notes,
                "pinned" => &data.memory.pinned,
                "abilities" => &data.memory.abilities,
                "resources" => &data.memory.resources,
                _ => unreachable!(),
            };
            for raw in arr {
                let text = clean_item(raw);
                if text.is_empty() {
                    continue;
                }
                backfilled.push(json!({
                    "id": format!("mem_{}", short_token()),
                    "kind": "runtime_fact",
                    "text": text,
                    "source": "legacy_migration_v1",
                    "turn": 0,
                    "ts": chrono::Utc::now().to_rfc3339(),
                    "status": "active",
                    "legacy_bucket": bucket,
                }));
            }
        }
        if !backfilled.is_empty() {
            data.memory.items = backfilled;
        }
    }

    Ok(())
}

// ─── helpers ──────────────────────────────────────────────────────────────

fn default_worldline_constraints() -> Vec<String> {
    vec![
        "用户变量优先级高于世界线推演。".into(),
        "世界线推演必须先满足玩家设定,再外推局势。".into(),
        "若推演与用户变量冲突,必须报告冲突,不得写回为事实。".into(),
    ]
}

/// 把老 secrets(可能是 String 或 List[String]) 推到 player_private.secrets,
/// 已存在的去重。
fn push_legacy_secrets(target: &mut Vec<Value>, raw: Value) {
    match raw {
        Value::String(s) => {
            let t = s.trim();
            if !t.is_empty() && !contains_str(target, t) {
                target.push(Value::String(t.into()));
            }
        }
        Value::Array(arr) => {
            for v in arr {
                let s = match v {
                    Value::String(s) => s.trim().to_string(),
                    other => other.as_str().unwrap_or("").trim().to_string(),
                };
                if !s.is_empty() && !contains_str(target, &s) {
                    target.push(Value::String(s));
                }
            }
        }
        _ => {}
    }
}

fn contains_str(arr: &[Value], needle: &str) -> bool {
    arr.iter().any(|v| v.as_str() == Some(needle))
}

fn clean_item(raw: &Value) -> String {
    match raw {
        Value::String(s) => s.trim().to_string(),
        other => other.to_string().trim().trim_matches('"').to_string(),
    }
}

/// rand 短 token(对齐 Python `secrets.token_urlsafe(6)`)。
/// 不引入 rand crate;复用 chrono 纳秒 + hex。
fn short_token() -> String {
    let ns = chrono::Utc::now().timestamp_nanos_opt().unwrap_or(0) as u64;
    format!("{ns:x}")
}

// ─── tests ───────────────────────────────────────────────────────────────

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn from_json(v: Value) -> GameStateData {
        serde_json::from_value(v).unwrap()
    }

    #[test]
    fn v1_to_v2_fills_permissions_and_worldline_defaults() {
        let mut data = from_json(json!({
            "schema_version": 1,
            "permissions": {"mode": ""},
            "worldline": {"constraints": [], "last_validation": {"status": ""}}
        }));
        migrate_v1_to_v2(&mut data).unwrap();
        assert_eq!(data.permissions.mode, "full_access");
        assert!(!data.worldline.constraints.is_empty());
        assert_eq!(data.worldline.last_validation.status, "none");
        assert_eq!(data.schema_version, 2);
    }

    #[test]
    fn v2_to_v3_fills_timeline_from_world_time() {
        let mut data = from_json(json!({
            "schema_version": 2,
            "turn": 5,
            "world": {
                "time": "1934 年 4 月",
                "timeline": {"anchor_state": "", "current_label": "", "anchor_turn": 0}
            }
        }));
        migrate_v2_to_v3(&mut data).unwrap();
        assert_eq!(data.world.timeline.current_label, "1934 年 4 月");
        assert_eq!(data.world.timeline.anchor_source, "migrated");
        assert_eq!(data.world.timeline.anchor_state, "locked");
        assert_eq!(data.world.timeline.anchor_turn, 5);
        assert_eq!(data.schema_version, 3);
    }

    #[test]
    fn v3_to_v4_initializes_memory_items_empty() {
        let mut data = from_json(json!({
            "schema_version": 3,
            "memory": {"facts": ["foo"]}
        }));
        migrate_v3_to_v4(&mut data).unwrap();
        // items 仍是空(legacy backfill 在 v6_final 才做)。
        assert!(data.memory.items.is_empty());
        assert_eq!(data.schema_version, 4);
    }

    #[test]
    fn v4_to_v5_fills_ruleset_when_empty() {
        let mut data = from_json(json!({
            "schema_version": 4,
            "ruleset": {"id": "", "mode": "", "public_label": ""}
        }));
        migrate_v4_to_v5(&mut data).unwrap();
        assert_eq!(data.ruleset.id, "dnd5e");
        assert_eq!(data.ruleset.mode, "5e_compatible");
        assert!(data.ruleset.public_label.contains("5E"));
        assert_eq!(data.schema_version, 5);
    }

    #[test]
    fn v5_to_v6_player_private_default_exists() {
        let mut data = from_json(json!({
            "schema_version": 5
        }));
        // 反序列化已给 default,迁移后 namespace 仍在。
        migrate_v5_to_v6(&mut data).unwrap();
        assert_eq!(data.player_private.story_intent, "");
        assert!(data.player_private.secrets.is_empty());
        assert_eq!(data.schema_version, 6);
    }

    #[test]
    fn v6_final_migrates_player_secrets_to_player_private() {
        let mut data = from_json(json!({
            "schema_version": 6,
            "player": {"secrets": "我是穿越者"},
            "player_private": {"secrets": []}
        }));
        migrate_v6_final(&mut data).unwrap();
        assert_eq!(
            data.player_private.secrets,
            vec![json!("我是穿越者")]
        );
        // player.extra 里 secrets 已被移走。
        assert!(data.player.extra.get("secrets").is_none());
    }

    #[test]
    fn v6_final_migrates_story_intent_from_worldline() {
        let mut data = from_json(json!({
            "schema_version": 6,
            "worldline": {"user_variables": {"story_intent": "找到妹妹"}},
            "player_private": {"story_intent": ""}
        }));
        migrate_v6_final(&mut data).unwrap();
        assert_eq!(data.player_private.story_intent, "找到妹妹");
    }

    #[test]
    fn v6_final_backfills_memory_items_from_legacy_buckets() {
        let mut data = from_json(json!({
            "schema_version": 6,
            "memory": {
                "items": [],
                "facts": ["事实1", "事实2"],
                "notes": ["笔记"]
            }
        }));
        migrate_v6_final(&mut data).unwrap();
        assert_eq!(data.memory.items.len(), 3);
        for item in &data.memory.items {
            assert_eq!(item.get("source").and_then(|v| v.as_str()), Some("legacy_migration_v1"));
            assert_eq!(item.get("kind").and_then(|v| v.as_str()), Some("runtime_fact"));
            assert_eq!(item.get("status").and_then(|v| v.as_str()), Some("active"));
            assert_eq!(item.get("turn").and_then(|v| v.as_u64()), Some(0));
        }
    }

    #[test]
    fn v6_final_backfills_hp_from_max_hp() {
        let mut data = from_json(json!({
            "schema_version": 6,
            "player_character": {"max_hp": 30, "hp": 0}
        }));
        migrate_v6_final(&mut data).unwrap();
        assert_eq!(data.player_character.hp, 30);
    }

    #[test]
    fn full_chain_v1_to_v6_lands_on_current_version() {
        let mut data = from_json(json!({
            "schema_version": 1,
            "permissions": {"mode": ""},
            "world": {"time": "夏日", "timeline": {"current_label": ""}},
            "memory": {"facts": ["旧事实"], "items": []},
            "player": {"secrets": "老秘密"},
            "worldline": {"user_variables": {"story_intent": "救主"}}
        }));
        migrate(&mut data, 1).unwrap();
        assert_eq!(data.schema_version, CURRENT_SCHEMA_VERSION);
        // 各 backfill 都跑过
        assert_eq!(data.world.timeline.current_label, "夏日");
        assert!(!data.memory.items.is_empty());
        assert_eq!(data.player_private.story_intent, "救主");
        assert_eq!(
            data.player_private.secrets,
            vec![json!("老秘密")]
        );
    }

    #[test]
    fn migrate_from_zero_treats_as_v1() {
        let mut data = GameStateData::default();
        data.schema_version = 0;
        migrate(&mut data, 0).unwrap();
        assert_eq!(data.schema_version, CURRENT_SCHEMA_VERSION);
    }

    #[test]
    fn migrate_from_current_is_idempotent() {
        let mut data = GameStateData::default();
        let before = data.clone();
        migrate(&mut data, CURRENT_SCHEMA_VERSION).unwrap();
        assert_eq!(data.schema_version, CURRENT_SCHEMA_VERSION);
        // 默认状态没有 legacy 数据,backfill 不动它
        assert_eq!(data, before);
    }
}
