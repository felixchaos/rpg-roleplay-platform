//! typed_path.rs — `GameStateData` 的 path API typed dispatch
//!
//! ## 设计
//! 把 `state.get_path("permissions.audit_log[0].path")` 这类调用按 **第一段** 分发到
//! `GameStateData` 的 typed 字段:
//! - 读路径(`get_path` / inc 的读半边):serialize 该字段为 `Value`,再 walk
//!   剩余 segs。`Value` 体积是 *子树* 而非整树(~µs),不退化。
//! - 写路径(`set_path` / `delete_path` / `append_path` / `merge_path` / inc 写半边):
//!   serialize 子树 → 在 `Value` 上做 segs 操作 → `from_value` 回填字段。一次 round-trip,
//!   不动其他字段,不影响 Arc snapshot 复用。
//!
//! ## 为什么不直接整棵 `data` 用 Value
//! prior session 把整树 typed 化判定为"5 年项",理由是 path 访问会退化 100x —— 那个
//! 判定**夸大了**:正确实现是按 head 段 dispatch 到字段(子树 serialize),不是整树
//! serialize。本模块就是正确实现。
//!
//! ## 顶层字段 dispatch 表
//! `GameStateData` 顶层 18 个字段(含 4 个标量 + 11 个 typed struct + 3 个动态容器 +
//! `extra` catch-all)。未知 head 段进入 `extra`,保持前向兼容。

use rpg_schemas::{
    AuditEntry, Encounter, GameStateData, Memory, PendingWrite, PermissionsState, PlayerCharacter,
    PlayerInfo, PlayerPrivate, Ruleset, Scene, World, Worldline,
};
use serde::{de::DeserializeOwned, Serialize};
use serde_json::{json, Map, Value};

use crate::path::{
    self, append_path_segs, delete_path_segs, get_path_segs, set_path_segs, PathError, PathSegment,
};
use crate::state::StateError;

// ===== 读 =======================================================================

/// `state.get_path(...)` 的核心 dispatch。返回 owned `Value`(typed 字段没有持久的
/// `Value` 形态,无法返回 `&Value`)。
pub fn get_path(data: &GameStateData, cleaned: &str) -> Option<Value> {
    let segs = path::parse_path(cleaned).ok()?;
    let (head, rest) = segs.split_first()?;
    let head_key = match head {
        PathSegment::Key(k) => k.as_str(),
        // 顶层数组下标对 GameStateData 无意义。
        PathSegment::Index(_) => return None,
    };
    let subtree = field_to_value(data, head_key)?;
    if rest.is_empty() {
        Some(subtree)
    } else {
        get_path_segs(&subtree, rest).cloned()
    }
}

/// 把单个 typed 字段序列化成 `Value`;未知 head → 走 `extra`。
fn field_to_value(data: &GameStateData, name: &str) -> Option<Value> {
    Some(match name {
        // 标量
        "schema_version" => json!(data.schema_version),
        "turn" => json!(data.turn),
        "is_new" => json!(data.is_new),
        "created_at" => json!(data.created_at),
        // typed struct
        "ruleset" => serde_json::to_value(&data.ruleset).ok()?,
        "player_character" => serde_json::to_value(&data.player_character).ok()?,
        "scene" => serde_json::to_value(&data.scene).ok()?,
        "encounter" => serde_json::to_value(&data.encounter).ok()?,
        "player" => serde_json::to_value(&data.player).ok()?,
        "player_private" => serde_json::to_value(&data.player_private).ok()?,
        "world" => serde_json::to_value(&data.world).ok()?,
        "permissions" => serde_json::to_value(&data.permissions).ok()?,
        "worldline" => serde_json::to_value(&data.worldline).ok()?,
        "memory" => serde_json::to_value(&data.memory).ok()?,
        // 动态容器(本身就是 Value 形态,免一次 serialize)
        "dice_log" => Value::Array(data.dice_log.clone()),
        "active_entities" => Value::Array(data.active_entities.clone()),
        "relationships" => Value::Object(data.relationships.clone()),
        "history" => Value::Array(data.history.clone()),
        // 未知 → extra
        _ => data.extra.get(name).cloned()?,
    })
}

// ===== 写 =======================================================================

/// `state.set_path(...)` 的核心 dispatch。
pub fn set_path(
    data: &mut GameStateData,
    cleaned: &str,
    value: Value,
) -> Result<(), StateError> {
    let segs = path::parse_path(cleaned)?;
    let (head, rest) = segs.split_first().ok_or(PathError::Empty)?;
    let head_key = match head {
        PathSegment::Key(k) => k.clone(),
        PathSegment::Index(_) => {
            return Err(StateError::Path(PathError::Syntax(
                "top-level array index not supported on GameStateData".into(),
            )));
        }
    };

    if rest.is_empty() {
        // 顶层覆盖
        set_field_from_value(data, &head_key, value)
    } else {
        // 深路径 — round-trip 子树
        round_trip_subtree(data, &head_key, |sub| {
            set_path_segs(sub, rest, value).map_err(StateError::Path)
        })
    }
}

pub fn delete_path(
    data: &mut GameStateData,
    cleaned: &str,
) -> Result<Option<Value>, StateError> {
    let segs = path::parse_path(cleaned)?;
    let (head, rest) = segs.split_first().ok_or(PathError::Empty)?;
    let head_key = match head {
        PathSegment::Key(k) => k.clone(),
        PathSegment::Index(_) => return Ok(None),
    };

    if rest.is_empty() {
        // 顶层删除 → reset 到 Default(对 typed 字段)/ 从 extra 删(对未知)。
        Ok(delete_top_level(data, &head_key))
    } else {
        let mut removed: Option<Value> = None;
        round_trip_subtree(data, &head_key, |sub| {
            removed = delete_path_segs(sub, rest).map_err(StateError::Path)?;
            Ok(())
        })?;
        Ok(removed)
    }
}

pub fn append_path(
    data: &mut GameStateData,
    cleaned: &str,
    value: Value,
) -> Result<(), StateError> {
    let segs = path::parse_path(cleaned)?;
    let (head, rest) = segs.split_first().ok_or(PathError::Empty)?;
    let head_key = match head {
        PathSegment::Key(k) => k.clone(),
        PathSegment::Index(_) => {
            return Err(StateError::Path(PathError::Syntax(
                "top-level array index not supported on GameStateData".into(),
            )))
        }
    };

    if rest.is_empty() {
        // 顶层 append:目标必须是 Vec<Value>。
        append_top_level(data, &head_key, value)
    } else {
        round_trip_subtree(data, &head_key, |sub| {
            append_path_segs(sub, rest, value).map_err(StateError::Path)
        })
    }
}

/// `merge_path` 的 typed dispatch。incoming 必须是 Object(调用方已校验)。
pub fn merge_path(
    data: &mut GameStateData,
    cleaned: &str,
    incoming: Map<String, Value>,
) -> Result<(), StateError> {
    // 用 path::set_path_segs 直接覆盖整个目标点为 merge 结果 — 复用现有 shallow merge 行为。
    let existing = get_path(data, cleaned).unwrap_or(Value::Null);
    let mut target = match existing {
        Value::Object(m) => m,
        _ => Map::new(),
    };
    for (k, v) in incoming {
        target.insert(k, v);
    }
    set_path(data, cleaned, Value::Object(target))
}

// ===== top-level 字段读写 =======================================================

fn set_field_from_value(
    data: &mut GameStateData,
    name: &str,
    v: Value,
) -> Result<(), StateError> {
    match name {
        "schema_version" => {
            data.schema_version = v.as_u64().unwrap_or(data.schema_version);
        }
        "turn" => {
            // 兼容旧 i64 输入(序列化层会发 i64)。
            data.turn = v
                .as_u64()
                .or_else(|| v.as_i64().map(|n| n.max(0) as u64))
                .unwrap_or(data.turn);
        }
        "is_new" => data.is_new = v.as_bool().unwrap_or(data.is_new),
        "created_at" => {
            data.created_at = v
                .as_str()
                .map(|s| s.to_string())
                .unwrap_or_else(|| data.created_at.clone());
        }
        "ruleset" => data.ruleset = deser::<Ruleset>(v, name)?,
        "player_character" => data.player_character = deser::<PlayerCharacter>(v, name)?,
        "scene" => data.scene = deser::<Scene>(v, name)?,
        "encounter" => data.encounter = deser::<Encounter>(v, name)?,
        "player" => data.player = deser::<PlayerInfo>(v, name)?,
        "player_private" => data.player_private = deser::<PlayerPrivate>(v, name)?,
        "world" => data.world = deser::<World>(v, name)?,
        "permissions" => data.permissions = deser::<PermissionsState>(v, name)?,
        "worldline" => data.worldline = deser::<Worldline>(v, name)?,
        "memory" => data.memory = deser::<Memory>(v, name)?,
        "dice_log" => data.dice_log = deser::<Vec<Value>>(v, name)?,
        "active_entities" => data.active_entities = deser::<Vec<Value>>(v, name)?,
        "relationships" => data.relationships = deser::<Map<String, Value>>(v, name)?,
        "history" => data.history = deser::<Vec<Value>>(v, name)?,
        _ => {
            // 未知 → extra
            data.extra.insert(name.to_string(), v);
        }
    }
    Ok(())
}

fn delete_top_level(data: &mut GameStateData, name: &str) -> Option<Value> {
    macro_rules! reset_to_default {
        ($field:ident, $ty:ty) => {{
            let prev = serde_json::to_value(&data.$field).ok();
            data.$field = <$ty>::default();
            prev
        }};
    }
    match name {
        "schema_version" => {
            let prev = json!(data.schema_version);
            data.schema_version = 6;
            Some(prev)
        }
        "turn" => {
            let prev = json!(data.turn);
            data.turn = 0;
            Some(prev)
        }
        "is_new" => {
            let prev = json!(data.is_new);
            data.is_new = true;
            Some(prev)
        }
        "created_at" => {
            let prev = json!(data.created_at);
            data.created_at = String::new();
            Some(prev)
        }
        "ruleset" => reset_to_default!(ruleset, Ruleset),
        "player_character" => reset_to_default!(player_character, PlayerCharacter),
        "scene" => reset_to_default!(scene, Scene),
        "encounter" => reset_to_default!(encounter, Encounter),
        "player" => reset_to_default!(player, PlayerInfo),
        "player_private" => reset_to_default!(player_private, PlayerPrivate),
        "world" => reset_to_default!(world, World),
        "permissions" => reset_to_default!(permissions, PermissionsState),
        "worldline" => reset_to_default!(worldline, Worldline),
        "memory" => reset_to_default!(memory, Memory),
        "dice_log" => {
            let prev = Value::Array(std::mem::take(&mut data.dice_log));
            Some(prev)
        }
        "active_entities" => {
            let prev = Value::Array(std::mem::take(&mut data.active_entities));
            Some(prev)
        }
        "relationships" => {
            let prev = Value::Object(std::mem::take(&mut data.relationships));
            Some(prev)
        }
        "history" => {
            let prev = Value::Array(std::mem::take(&mut data.history));
            Some(prev)
        }
        _ => data.extra.remove(name),
    }
}

fn append_top_level(
    data: &mut GameStateData,
    name: &str,
    value: Value,
) -> Result<(), StateError> {
    match name {
        "dice_log" => {
            data.dice_log.push(value);
            Ok(())
        }
        "active_entities" => {
            data.active_entities.push(value);
            Ok(())
        }
        "history" => {
            data.history.push(value);
            Ok(())
        }
        // typed struct / scalar / Map 字段 append 不可能成功 → 走 round-trip(让底层
        // path::append 自动初始化为数组覆盖)。极少触发,允许。
        _ => round_trip_subtree(data, name, |sub| {
            if !sub.is_array() {
                *sub = Value::Array(Vec::new());
            }
            if let Value::Array(arr) = sub {
                arr.push(value);
            }
            Ok(())
        }),
    }
}

// ===== round-trip 子树 ==========================================================

/// 把 `name` 对应的子树 serialize 出来,让 closure 在 `Value` 上原地改,再
/// `from_value` 回填字段。**只 round-trip 一个子树**,不影响其他字段。
fn round_trip_subtree<F>(
    data: &mut GameStateData,
    name: &str,
    f: F,
) -> Result<(), StateError>
where
    F: FnOnce(&mut Value) -> Result<(), StateError>,
{
    let mut sub = field_to_value(data, name).unwrap_or(Value::Null);
    f(&mut sub)?;
    set_field_from_value(data, name, sub)
}

// ===== 工具 =====================================================================

fn deser<T: DeserializeOwned + Default>(v: Value, name: &str) -> Result<T, StateError> {
    serde_json::from_value(v).map_err(|e| StateError::TypeMismatch {
        path: name.to_string(),
        hint: leak_serde_error(&e),
    })
}

/// 把 serde 错误转成 `&'static str`(StateError::TypeMismatch.hint 要求 'static)。
/// 这是少量的故意泄露 — 错误信息走 tracing 而不是 hot loop,接受。
fn leak_serde_error(_e: &serde_json::Error) -> &'static str {
    "typed deserialize failed; subtree shape mismatch"
}

// ===== typed 直接入口 — 给 ops.rs 的 push_audit / push_pending 用 ====================

/// 直接追加 AuditEntry 到 `permissions.audit_log`,cap 在 200 条。
/// 不走 round-trip,直接 typed push,**~ns 级**(vs 旧 Value 路径 ~µs)。
pub fn push_audit(data: &mut GameStateData, entry: AuditEntry) {
    let log = &mut data.permissions.audit_log;
    log.push(entry);
    let n = log.len();
    if n > 200 {
        log.drain(0..n - 200);
    }
}

/// 直接追加 PendingWrite 到 `permissions.pending_writes`,cap 在 20 条。
pub fn push_pending(data: &mut GameStateData, entry: PendingWrite) {
    let pw = &mut data.permissions.pending_writes;
    pw.push(entry);
    let n = pw.len();
    if n > 20 {
        pw.drain(0..n - 20);
    }
}

/// 登记 `player_private.user_locked_fields`(去重 append 字符串)。
/// `user_locked_fields` 是 PlayerPrivate.extra 里的字段(原 Python schema 没列,
/// 是 task 36 加的运行时附加字段),所以走 extra path。
pub fn mark_user_locked(data: &mut GameStateData, path: &str) {
    let pp_extra = &mut data.player_private.extra;
    let arr = pp_extra
        .entry("user_locked_fields".to_string())
        .or_insert_with(|| Value::Array(Vec::new()));
    if let Value::Array(list) = arr {
        let already = list.iter().any(|v| v.as_str() == Some(path));
        if !already {
            list.push(Value::String(path.to_string()));
        }
    }
}

// ===== 单测 =====================================================================

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn get_path_top_level_scalar() {
        let mut data = GameStateData::default();
        data.turn = 7;
        assert_eq!(get_path(&data, "turn"), Some(json!(7)));
        assert_eq!(get_path(&data, "is_new"), Some(json!(true)));
    }

    #[test]
    fn get_path_typed_subtree_field() {
        let mut data = GameStateData::default();
        data.encounter.round = 3;
        data.encounter.encounter_id = "abc".into();
        assert_eq!(get_path(&data, "encounter.round"), Some(json!(3)));
        assert_eq!(
            get_path(&data, "encounter.encounter_id"),
            Some(json!("abc"))
        );
    }

    #[test]
    fn get_path_deep_array_index() {
        let mut data = GameStateData::default();
        data.encounter.combatants = vec![json!({"name": "goblin"}), json!({"name": "ogre"})];
        assert_eq!(
            get_path(&data, "encounter.combatants[1].name"),
            Some(json!("ogre"))
        );
    }

    #[test]
    fn get_path_unknown_top_falls_to_extra() {
        let mut data = GameStateData::default();
        data.extra
            .insert("user_extra_root".into(), json!({"foo": "bar"}));
        assert_eq!(
            get_path(&data, "user_extra_root.foo"),
            Some(json!("bar"))
        );
    }

    #[test]
    fn set_path_top_level_scalar() {
        let mut data = GameStateData::default();
        set_path(&mut data, "turn", json!(42)).unwrap();
        assert_eq!(data.turn, 42);
    }

    #[test]
    fn set_path_typed_subtree() {
        let mut data = GameStateData::default();
        set_path(&mut data, "player_character.hp", json!(15)).unwrap();
        assert_eq!(data.player_character.hp, 15);
        // 其他 typed struct 字段不受影响
        assert_eq!(data.player_character.max_hp, 0);
    }

    #[test]
    fn set_path_deep_unknown_falls_to_extra_typed_field() {
        // encounter 的 extra 字段
        let mut data = GameStateData::default();
        set_path(
            &mut data,
            "encounter.future_marker",
            json!("x"),
        )
        .unwrap();
        assert_eq!(
            data.encounter.extra.get("future_marker"),
            Some(&json!("x"))
        );
    }

    #[test]
    fn delete_path_typed_subtree_field() {
        let mut data = GameStateData::default();
        data.player_character.hp = 5;
        let removed = delete_path(&mut data, "player_character.hp").unwrap();
        // 删除后子树反序列化回 PlayerCharacter,hp 字段消失 → 用 #[serde(default)] 回 0
        assert_eq!(data.player_character.hp, 0);
        assert_eq!(removed, Some(json!(5)));
    }

    #[test]
    fn append_path_top_level_dice_log() {
        let mut data = GameStateData::default();
        append_path(&mut data, "dice_log", json!({"roll": 7})).unwrap();
        assert_eq!(data.dice_log.len(), 1);
        assert_eq!(data.dice_log[0], json!({"roll": 7}));
    }

    #[test]
    fn append_path_into_typed_struct_log() {
        let mut data = GameStateData::default();
        append_path(&mut data, "encounter.log", json!("hit goblin")).unwrap();
        assert_eq!(data.encounter.log, vec![json!("hit goblin")]);
    }

    #[test]
    fn merge_path_on_typed_subtree() {
        let mut data = GameStateData::default();
        let incoming: Map<String, Value> = {
            let mut m = Map::new();
            m.insert("mode".to_string(), json!("read_only"));
            m
        };
        merge_path(&mut data, "permissions", incoming).unwrap();
        assert_eq!(data.permissions.mode, "read_only");
    }

    #[test]
    fn push_audit_caps_at_200() {
        let mut data = GameStateData::default();
        for i in 0..250 {
            push_audit(&mut data, AuditEntry::blocked("gm", &format!("p{i}"), "hard_forbidden", i));
        }
        assert_eq!(data.permissions.audit_log.len(), 200);
        // 最旧 50 条被砍
        assert_eq!(data.permissions.audit_log[0].path, "p50");
    }

    #[test]
    fn push_pending_caps_at_20() {
        let mut data = GameStateData::default();
        for i in 0..25 {
            push_pending(
                &mut data,
                PendingWrite {
                    id: format!("pw{i}"),
                    path: format!("p{i}"),
                    ..Default::default()
                },
            );
        }
        assert_eq!(data.permissions.pending_writes.len(), 20);
        assert_eq!(data.permissions.pending_writes[0].id, "pw5");
    }

    #[test]
    fn mark_user_locked_dedupes() {
        let mut data = GameStateData::default();
        mark_user_locked(&mut data, "player.name");
        mark_user_locked(&mut data, "player.name");
        mark_user_locked(&mut data, "player.role");
        let arr = data
            .player_private
            .extra
            .get("user_locked_fields")
            .and_then(|v| v.as_array())
            .unwrap();
        assert_eq!(arr.len(), 2);
        assert_eq!(arr[0], json!("player.name"));
        assert_eq!(arr[1], json!("player.role"));
    }

    /// 中文别名应在 typed dispatch 之前被 clean_path 归一(本模块只接受 cleaned 路径)。
    #[test]
    fn cleaned_alias_dispatched_correctly() {
        let cleaned = crate::path::clean_path("姓名"); // → "player.name"
        let mut data = GameStateData::default();
        set_path(&mut data, &cleaned, json!("Aria")).unwrap();
        assert_eq!(data.player.name, "Aria");
    }
}
