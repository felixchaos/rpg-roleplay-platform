//! 小工具函数:权限标签、pending jump 警告。
//! 对应 Python: rpg/context_engine/helpers.py

use serde_json::Value;
use rpg_schemas::GameStateData;

/// 通用 pending_jump 警告。GM 运行契约的一部分,与 ContentPack 无关。
/// 对应 Python `_pending_jump_warning_text`。
pub fn pending_jump_warning_text(state_data: &GameStateData) -> String {
    let pending = match &state_data.world.timeline.pending_jump {
        Some(p) if p.is_object() => p.clone(),
        _ => return String::new(),
    };
    let pending_status = pending
        .get("status")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let is_awaiting = matches!(
        pending_status.as_str(),
        "awaiting_gm_confirmation" | "awaiting" | "pending_confirmation"
    );

    let from = pending.get("from").and_then(|v| v.as_str()).unwrap_or("");
    let to = pending.get("to").and_then(|v| v.as_str()).unwrap_or("");

    let mut lines = vec![
        format!("玩家请求时间跳跃：{} -> {}", from, to),
        format!(
            "pending 状态：{}",
            if pending_status.is_empty() {
                "未知"
            } else {
                pending_status.as_str()
            }
        ),
    ];

    if is_awaiting {
        lines.extend([
            "⚠ 本轮 anchor_state=pending_confirmation：禁止把玩家请求的未来时间/地点当作已发生的事实。".to_string(),
            "禁止输出『翌日…』『次日…』『转眼已是…』等任何把场景叙事推进到目标时间的措辞；".to_string(),
            "禁止输出标签【时间跳跃确认：…】【当前时间线：目标时间】【当前位置：新地点】【时间：目标时间】；".to_string(),
            "禁止给出『新时间/新地点』场景里的对话、动作、选项；".to_string(),
            "本轮只允许：① 给出冲突检查；② 列出风险/代价/前置条件；③ 输出【询问玩家：是否确认跳跃到 <目标时间>？】+ 1-3 个明确选项（确认 / 取消 / 修改目标）；".to_string(),
            "下一轮若玩家明确回复『确认』或 /confirm，再正式推进时间线和场景。".to_string(),
        ]);
    } else {
        lines.extend([
            "本轮必须先处理时间跳跃事务：默认尊重玩家的跳转/改线意图，".to_string(),
            "接受则写出过渡/落点并输出【时间跳跃确认：目标时间】和【当前时间线：目标时间】；".to_string(),
            "只有目标完全不可解析时才输出【询问玩家：...】。".to_string(),
            "在确认前，不要把玩家请求的未来时间当作已经发生；确认后才允许推进场景与更新位置/目标。".to_string(),
        ]);
    }
    lines.join("\n")
}

/// 权限模式归一化。对应 Python `_normalize_permission_mode`。
pub fn normalize_permission_mode(mode: &str) -> &'static str {
    let lower = mode.trim().to_lowercase();
    match lower.as_str() {
        "只读" | "只读模式" | "suggest" | "read" | "read_only" | "plan" => "read_only",
        "默认权限" | "default" => "default",
        "auto" | "自动审查" | "auto_review" | "review" => "auto_review",
        "完全访问权限" | "full" | "full_access" => "full_access",
        _ => "full_access",
    }
}

/// 权限标签。对应 Python `_permission_label`。
pub fn permission_label(mode: &str) -> &'static str {
    match normalize_permission_mode(mode) {
        "read_only" => "只读模式（仅叙事）",
        "default" => "默认权限",
        "auto_review" => "自动审查",
        _ => "完全访问权限",
    }
}
