//! state_op_map — GM JSON op path -> dispatcher tool mapping
//!
//! Corresponds to Python: rpg/state_op_tool_map.py (task 87 Phase 6)
//!
//! When a GM writes state through the old JSON op protocol
//! (`{"op":"set","path":"world.time","value":"X"}`), the chat handler
//! routes the op to the corresponding dispatcher tool via this mapping,
//! gaining unified audit + destructive checks.
//!
//! Design:
//! - One path maps to at most one tool
//! - Prefix matching for relationships.X / worldline.user_variables.Y etc.
//! - "append" ops map to add_* / pin_* tools
//! - "set" ops map to set_* tools

use serde_json::{json, Value};

/// Map a GM JSON op `(path, value, op_kind)` to a dispatcher tool call.
///
/// Returns `Some((tool_name, args))` or `None` (no corresponding tool; fall through
/// to the legacy path).
pub fn map_op_to_tool(path: &str, value: &Value, _op_kind: &str) -> Option<(String, Value)> {
    if path.is_empty() {
        return None;
    }

    // Helper: coerce a Value to a string (first element if array).
    let value_str = |v: &Value| -> String {
        match v {
            Value::String(s) => s.clone(),
            Value::Array(arr) => arr
                .first()
                .and_then(|e| e.as_str())
                .unwrap_or("")
                .to_owned(),
            Value::Null => String::new(),
            other => other.to_string(),
        }
    };

    // Helper: coerce to string, but for list-bucket paths take first element.
    let first_str = |v: &Value| -> String {
        match v {
            Value::Array(arr) => {
                if let Some(first) = arr.first() {
                    match first {
                        Value::String(s) => s.clone(),
                        Value::Null => String::new(),
                        other => other.to_string(),
                    }
                } else {
                    String::new()
                }
            }
            Value::String(s) => s.clone(),
            Value::Null => String::new(),
            other => other.to_string(),
        }
    };

    // ── world.* ─────────────────────────────────────────────
    if path == "world.time" {
        return Some((
            "set_world_time".into(),
            json!({"target": value_str(value)}),
        ));
    }

    if path == "world.known_events" {
        // Always append semantics; for arrays, take the first element
        // (caller should use expand_list_value_to_tool_calls for multi).
        let text = first_str(value);
        return Some(("add_world_event".into(), json!({"text": text})));
    }

    if let Some(key) = path.strip_prefix("world.") {
        // Exclude world.timeline and world.timeline.*
        if key != "timeline" && !key.starts_with("timeline.") && !key.contains('.') {
            return Some((
                "set_world_attribute".into(),
                json!({"key": key, "value": value_str(value)}),
            ));
        }
    }

    // ── player.* ────────────────────────────────────────────
    if path == "player.name" {
        return Some((
            "set_player_name".into(),
            json!({"name": value_str(value)}),
        ));
    }
    if path == "player.role" {
        return Some((
            "set_player_role".into(),
            json!({"role": value_str(value)}),
        ));
    }
    if path == "player.background" {
        return Some((
            "set_player_background".into(),
            json!({"background": value_str(value)}),
        ));
    }
    if path == "player.current_location" {
        return Some((
            "set_player_location".into(),
            json!({"location": value_str(value)}),
        ));
    }

    // ── relationships.X ─────────────────────────────────────
    if let Some(character) = path.strip_prefix("relationships.") {
        return Some((
            "set_relationship".into(),
            json!({"character": character, "status": value_str(value)}),
        ));
    }

    // ── memory.* ────────────────────────────────────────────
    if path == "memory.main_quest" {
        return Some((
            "set_main_quest".into(),
            json!({"text": value_str(value)}),
        ));
    }
    if path == "memory.current_objective" {
        return Some((
            "set_current_objective".into(),
            json!({"text": value_str(value)}),
        ));
    }
    if path == "memory.mode" {
        return Some((
            "set_memory_mode".into(),
            json!({"mode": value_str(value)}),
        ));
    }

    // memory list-bucket appends
    let bucket_tool = match path {
        "memory.facts" => Some("add_memory_fact"),
        "memory.resources" => Some("add_memory_resource"),
        "memory.abilities" => Some("add_memory_ability"),
        "memory.pinned" => Some("pin_memory"),
        "memory.notes" => Some("add_memory_note"),
        _ => None,
    };
    if let Some(tool_name) = bucket_tool {
        let text = first_str(value);
        return Some((tool_name.into(), json!({"text": text})));
    }

    // ── worldline.user_variables.X ──────────────────────────
    if let Some(key) = path.strip_prefix("worldline.user_variables.") {
        return Some((
            "set_user_variable".into(),
            json!({"key": key, "value": value_str(value)}),
        ));
    }

    // Other paths (permissions.* / history.* / schema_version / encounter.* / dice_log etc.)
    // have no corresponding tool -- should be caught by hard_forbidden or rules_managed.
    None
}

/// For list-valued append operations (e.g. `memory.facts=[A,B,C]`), expand into
/// multiple tool calls. Scalar values return a single call. Returns `[]` when
/// there is no mapping.
pub fn expand_list_value_to_tool_calls(
    path: &str,
    value: &Value,
    op_kind: &str,
    append: bool,
) -> Vec<(String, Value)> {
    let mut out = Vec::new();

    if let Value::Array(arr) = value {
        if !arr.is_empty() && (op_kind == "append" || append) {
            for v in arr {
                // For world.known_events, each element is mapped with op_kind="set";
                // for other list buckets, keep "append".
                let item_op = if path == "world.known_events" {
                    "set"
                } else {
                    "append"
                };
                if let Some(mapped) = map_op_to_tool(path, v, item_op) {
                    out.push(mapped);
                }
            }
            return out;
        }
    }

    // Scalar value or non-append: single call.
    if let Some(mapped) = map_op_to_tool(path, value, op_kind) {
        out.push(mapped);
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn test_world_time() {
        let (name, args) = map_op_to_tool("world.time", &json!("黄昏"), "set").unwrap();
        assert_eq!(name, "set_world_time");
        assert_eq!(args["target"], "黄昏");
    }

    #[test]
    fn test_world_known_events_scalar() {
        let (name, args) = map_op_to_tool("world.known_events", &json!("战争爆发"), "set").unwrap();
        assert_eq!(name, "add_world_event");
        assert_eq!(args["text"], "战争爆发");
    }

    #[test]
    fn test_world_known_events_array_takes_first() {
        let (name, args) =
            map_op_to_tool("world.known_events", &json!(["event1", "event2"]), "set").unwrap();
        assert_eq!(name, "add_world_event");
        assert_eq!(args["text"], "event1");
    }

    #[test]
    fn test_world_attribute_weather() {
        let (name, args) = map_op_to_tool("world.weather", &json!("暴风雨"), "set").unwrap();
        assert_eq!(name, "set_world_attribute");
        assert_eq!(args["key"], "weather");
        assert_eq!(args["value"], "暴风雨");
    }

    #[test]
    fn test_world_timeline_excluded() {
        assert!(map_op_to_tool("world.timeline", &json!("x"), "set").is_none());
        assert!(map_op_to_tool("world.timeline.0", &json!("x"), "set").is_none());
    }

    #[test]
    fn test_player_fields() {
        let (n, a) = map_op_to_tool("player.name", &json!("Alice"), "set").unwrap();
        assert_eq!(n, "set_player_name");
        assert_eq!(a["name"], "Alice");

        let (n, a) = map_op_to_tool("player.role", &json!("法师"), "set").unwrap();
        assert_eq!(n, "set_player_role");
        assert_eq!(a["role"], "法师");

        let (n, a) = map_op_to_tool("player.background", &json!("贵族"), "set").unwrap();
        assert_eq!(n, "set_player_background");
        assert_eq!(a["background"], "贵族");

        let (n, a) = map_op_to_tool("player.current_location", &json!("城堡"), "set").unwrap();
        assert_eq!(n, "set_player_location");
        assert_eq!(a["location"], "城堡");
    }

    #[test]
    fn test_relationships() {
        let (name, args) =
            map_op_to_tool("relationships.Lyra", &json!("友好"), "set").unwrap();
        assert_eq!(name, "set_relationship");
        assert_eq!(args["character"], "Lyra");
        assert_eq!(args["status"], "友好");
    }

    #[test]
    fn test_memory_scalars() {
        let (n, a) = map_op_to_tool("memory.main_quest", &json!("寻找圣剑"), "set").unwrap();
        assert_eq!(n, "set_main_quest");
        assert_eq!(a["text"], "寻找圣剑");

        let (n, a) = map_op_to_tool("memory.current_objective", &json!("到达村庄"), "set").unwrap();
        assert_eq!(n, "set_current_objective");
        assert_eq!(a["text"], "到达村庄");

        let (n, a) = map_op_to_tool("memory.mode", &json!("探索"), "set").unwrap();
        assert_eq!(n, "set_memory_mode");
        assert_eq!(a["mode"], "探索");
    }

    #[test]
    fn test_memory_list_buckets() {
        let cases = [
            ("memory.facts", "add_memory_fact"),
            ("memory.resources", "add_memory_resource"),
            ("memory.abilities", "add_memory_ability"),
            ("memory.pinned", "pin_memory"),
            ("memory.notes", "add_memory_note"),
        ];
        for (path, expected_tool) in &cases {
            let (name, args) = map_op_to_tool(path, &json!("test_value"), "set").unwrap();
            assert_eq!(name, *expected_tool, "path={path}");
            assert_eq!(args["text"], "test_value", "path={path}");
        }
    }

    #[test]
    fn test_worldline_user_variables() {
        let (name, args) =
            map_op_to_tool("worldline.user_variables.trust_level", &json!("high"), "set").unwrap();
        assert_eq!(name, "set_user_variable");
        assert_eq!(args["key"], "trust_level");
        assert_eq!(args["value"], "high");
    }

    #[test]
    fn test_no_mapping() {
        assert!(map_op_to_tool("permissions.gm_write", &json!(true), "set").is_none());
        assert!(map_op_to_tool("history.turns", &json!([]), "set").is_none());
        assert!(map_op_to_tool("schema_version", &json!(2), "set").is_none());
        assert!(map_op_to_tool("", &json!("x"), "set").is_none());
    }

    #[test]
    fn test_expand_list_append() {
        let calls = expand_list_value_to_tool_calls(
            "memory.facts",
            &json!(["fact1", "fact2", "fact3"]),
            "append",
            true,
        );
        assert_eq!(calls.len(), 3);
        assert_eq!(calls[0].0, "add_memory_fact");
        assert_eq!(calls[0].1["text"], "fact1");
        assert_eq!(calls[2].1["text"], "fact3");
    }

    #[test]
    fn test_expand_scalar() {
        let calls =
            expand_list_value_to_tool_calls("world.time", &json!("noon"), "set", false);
        assert_eq!(calls.len(), 1);
        assert_eq!(calls[0].0, "set_world_time");
    }

    #[test]
    fn test_expand_no_mapping() {
        let calls =
            expand_list_value_to_tool_calls("permissions.x", &json!("y"), "set", false);
        assert!(calls.is_empty());
    }

    #[test]
    fn test_null_value() {
        let (name, args) = map_op_to_tool("world.time", &Value::Null, "set").unwrap();
        assert_eq!(name, "set_world_time");
        assert_eq!(args["target"], "");
    }
}
