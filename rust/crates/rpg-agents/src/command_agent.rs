//! command_agent — /set 命令的 LLM 工具调用解析。
//!
//! 对应 Python: `rpg/agents/command_agent.py`
//!
//! 公开接口:
//! ```ignore
//! let agent = CommandAgent::new(llm);
//! let calls = agent.run(CommandInput { set_text, ... }, &state).await?;
//! // calls: Vec<{ "name": str, "input": dict }>
//! ```

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::common::{
    extract_json_block, AgentResult, ChatMessage, GameState, SharedLlm, ToolSchema,
};

const SYSTEM_PROMPT: &str = include_str!("prompts/command_agent.txt");

const DEFAULT_MAX_TOKENS: usize = 800;

#[derive(Debug, Clone)]
pub struct CommandInput {
    pub set_text: String,
    pub user_id: Option<i64>,
    pub model_override: Option<String>,
    pub api_id_override: Option<String>,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct CommandOutput {
    /// 每个元素形如 {"name": str, "input": {...}}
    pub tool_calls: Vec<Value>,
}

pub struct CommandAgent {
    llm: SharedLlm,
    max_tokens: usize,
}

impl CommandAgent {
    pub fn new(llm: SharedLlm) -> Self {
        Self {
            llm,
            max_tokens: DEFAULT_MAX_TOKENS,
        }
    }

    pub async fn run(
        &self,
        input: CommandInput,
        state: &GameState,
    ) -> AgentResult<CommandOutput> {
        if input.set_text.trim().is_empty() {
            return Ok(CommandOutput::default());
        }
        let user_prompt = build_user_prompt(&input.set_text, &state.data);
        let messages = vec![ChatMessage::user(user_prompt)];

        // 优先 native tool_use 路径(Anthropic / Vertex 支持)。
        if self.llm.supports_native_tools() {
            let tools = command_tool_schemas();
            match self
                .llm
                .call_with_tools(SYSTEM_PROMPT, &messages, &tools, self.max_tokens)
                .await
            {
                Ok(resp) => {
                    if !resp.tool_calls.is_empty() {
                        let calls: Vec<Value> = resp
                            .tool_calls
                            .into_iter()
                            .map(|tc| {
                                json!({
                                    "name": tc.name,
                                    "input": tc.input,
                                })
                            })
                            .collect();
                        return Ok(CommandOutput { tool_calls: calls });
                    }
                    // tool_calls 空 → 回退到文本路径解析 resp.text。
                    if !resp.text.is_empty() {
                        let parsed = parse_tool_call_json_array(&resp.text);
                        if !parsed.is_empty() {
                            return Ok(CommandOutput { tool_calls: parsed });
                        }
                    }
                }
                Err(e) => {
                    tracing::warn!(
                        "[command_agent] native tool_use 失败,回退 JSON mode: {e}"
                    );
                    // 继续 fallthrough 到 JSON mode
                }
            }
        }

        // Fallback: JSON mode 文本解析。
        let raw = match self
            .llm
            .call_structured(SYSTEM_PROMPT, &messages, self.max_tokens)
            .await
        {
            Ok(t) => t,
            Err(e) => {
                tracing::warn!("[command_agent] call_structured failed: {e}");
                return Ok(CommandOutput::default());
            }
        };

        Ok(CommandOutput {
            tool_calls: parse_tool_call_json_array(&raw),
        })
    }
}

/// /set 命令支持的工具集合。与 Python `COMMAND_TOOLS` 对齐。
fn command_tool_schemas() -> Vec<ToolSchema> {
    vec![
        ToolSchema {
            name: "set_state_path".into(),
            description: "把某个 state 路径设为给定值。优先用于结构化字段(player.name / world.time 等)。".into(),
            input_schema: json!({
                "type": "object",
                "required": ["path", "value"],
                "properties": {
                    "path": {"type": "string", "description": "dot 路径,如 player.name / memory.main_quest"},
                    "value": {"description": "任意 JSON 值"},
                },
            }),
        },
        ToolSchema {
            name: "delete_state_path".into(),
            description: "删除某个 state 路径(关系 / 资源 / 自定义字段)。".into(),
            input_schema: json!({
                "type": "object",
                "required": ["path"],
                "properties": {
                    "path": {"type": "string"},
                },
            }),
        },
        ToolSchema {
            name: "append_state_path".into(),
            description: "向数组路径 append 元素。".into(),
            input_schema: json!({
                "type": "object",
                "required": ["path", "value"],
                "properties": {
                    "path": {"type": "string"},
                    "value": {"description": "任意 JSON 值"},
                },
            }),
        },
        ToolSchema {
            name: "set_relationship".into(),
            description: "更新关系图节点(player ↔ NPC):亲密度 / 标签。".into(),
            input_schema: json!({
                "type": "object",
                "required": ["entity", "value"],
                "properties": {
                    "entity": {"type": "string", "description": "对方名字 / id"},
                    "value": {"description": "新关系状态(string 或 object)"},
                },
            }),
        },
        ToolSchema {
            name: "add_memory_item".into(),
            description: "在 memory.items 加一条事件 / 物品 / 信念。".into(),
            input_schema: json!({
                "type": "object",
                "required": ["kind", "content"],
                "properties": {
                    "kind": {"type": "string", "enum": ["event", "item", "belief", "quest"]},
                    "content": {"type": "string"},
                },
            }),
        },
    ]
}

fn build_user_prompt(set_text: &str, state_data: &Value) -> String {
    let p = state_data.get("player").cloned().unwrap_or(Value::Null);
    let rels = state_data.get("relationships").cloned().unwrap_or(Value::Null);
    let m = state_data.get("memory").cloned().unwrap_or(Value::Null);
    let w = state_data.get("world").cloned().unwrap_or(Value::Null);

    let g = |v: &Value, k: &str| -> String {
        v.get(k).and_then(|x| x.as_str()).unwrap_or("(空)").to_string()
    };

    let rels_text = rels
        .as_object()
        .map(|o| {
            o.iter()
                .take(8)
                .map(|(k, v)| format!("  - {k}: {v}"))
                .collect::<Vec<_>>()
                .join("\n")
        })
        .unwrap_or_default();

    format!(
        "## 当前状态快照\n\
         - player.name = {}\n\
         - player.role = {}\n\
         - player.current_location = {}\n\
         - world.time = {}\n\
         - memory.main_quest = {}\n\
         - memory.current_objective = {}\n\
         - relationships:\n{}\n\
         \n## /set 命令文本\n{}",
        g(&p, "name"),
        g(&p, "role"),
        g(&p, "current_location"),
        g(&w, "time"),
        g(&m, "main_quest"),
        g(&m, "current_objective"),
        rels_text,
        set_text,
    )
}

/// 解析 LLM 返回的 [{"name":..., "input":{...}}, ...]。
fn parse_tool_call_json_array(text: &str) -> Vec<Value> {
    let Ok(blk) = extract_json_block(text) else {
        return vec![];
    };
    let parsed: Value = match serde_json::from_str(blk) {
        Ok(v) => v,
        Err(_) => return vec![],
    };
    coerce_calls(&parsed)
}

fn coerce_calls(parsed: &Value) -> Vec<Value> {
    match parsed {
        Value::Array(arr) => arr
            .iter()
            .filter(|v| {
                v.is_object()
                    && v.get("name").is_some()
                    && v.get("input").map(|i| i.is_object()).unwrap_or(false)
            })
            .cloned()
            .collect(),
        Value::Object(obj) => {
            // {"tool_calls": [...]} / {"calls": [...]} / 单 call
            for k in ["tool_calls", "calls", "tools"] {
                if let Some(Value::Array(arr)) = obj.get(k) {
                    return coerce_calls(&Value::Array(arr.clone()));
                }
            }
            if obj.contains_key("name") && obj.contains_key("input") {
                vec![Value::Object(obj.clone())]
            } else {
                vec![]
            }
        }
        _ => vec![],
    }
}
