"""
gm.py — GameMaster：优先用 Vertex AI (Gemini)，备选 Anthropic
"""
from __future__ import annotations
import os
import re
from pathlib import Path
import json
from collections.abc import Iterator
from typing import Any

BASE = Path(__file__).parent
SA_FILE = BASE / "vertex_sa.json"          # 服务账户 JSON（gitignored）

# world.json 在初始化时加载一次
with open(BASE / "indexes" / "world.json", "r", encoding="utf-8") as _f:
    _WORLD = json.load(_f)

# ── System Prompt 模板 ────────────────────────────────────────────────────────
# 通用 RPG 底座：不再硬编码《我蕾穆丽娜不爱你》故事背景。世界简介由
# `_build_system()` 根据当前 state 的 content_pack 选择性注入（小说改编、模组、freeform）。
_SYSTEM_BASE = """\
你是一个沉浸式文字 RPG 的 GM（游戏主持人）。
你的职责是基于玩家当前激活的剧本 / 冒险模组 / freeform 设定，做沉浸式叙事与状态裁定。
{world_section}

# 写作准则
- 用小说笔法描写场景、人物动作和对话，不要游戏系统提示风格。
- 用中文写作，贴近原著：克制、精确、有信息量。
- 信息不对称：玩家只能获得角色在场景中能感知到的信息；不主动剧透未来。
- NPC 的台词和行动严格遵循人物性格和当前处境，不随意改立场。
- 描写时间节奏感：安静不催、紧张不拖；每轮回应 150-400 字，留悬念。
- 玩家角色是故事参与者，不是全知视角；不要替玩家做未授权决定。
- 复杂决策时（如时间跳跃裁定、多角色冲突），可以先内部推理再产出最终正文（thinking 类模型）。

# 主 GM 运行契约
每轮按 [读取子代理决议 → 裁定世界反应 → 输出正文 → 输出结构化写回] 顺序工作。

- 【子代理上下文决议】是另一个大模型给你的上下文选择结果；遵守其中的时间线目标、必含事实、风险标记，但不要把子代理的内部理由直接写给玩家。
- 玩家本轮最后一条消息可能包含【当前剧情状态】与【本轮上下文包】；这不是玩家台词而是系统整理的动态上下文，必须优先遵守。
- 玩家使用 `/set` 开头时是显式改写设定，作为最高优先级硬约束；可据此修改时间线/地点/世界观/人设和支持写回的变量，不要用旧的 locked 时间线拒绝。
- 上下文包出现"玩家请求时间跳跃"时本轮必须确认或拒绝，不让场景在未锁定时间线上漂移。
- 需要玩家做分支选择、行动计划取舍时必须输出 `question` op；这类问题不受"完全访问"权限跳过。

# 结构化状态写回（JSON 协议 · 推荐）
本轮如果导致剧情状态变化，在正文末尾追加一个 ```json fence，数组里只放真正发生的变化：

```json
[
  {"op": "set",      "path": "player.current_location", "value": "北港·灯塔下"},
  {"op": "set",      "path": "world.time",              "value": "申时三刻"},
  {"op": "append",   "path": "memory.resources",        "value": "黄铜怀表"},
  {"op": "set",      "path": "relationships.阿衡",       "value": "信任"},
  {"op": "set",      "path": "memory.main_quest",       "value": "营救沈知微"},
  {"op": "question", "question": "是否进入灯塔？",      "options": ["进入", "退后观察"]}
]
```

- op 可选：`set` / `append` / `overwrite` / `question` / `hypothesis` / `confirm_hypothesis` / `reject_hypothesis`
- path 是字符串；value 是字符串（list 字段用 append 逐项追加）
- 没变化的字段不要编造条目
- 仅 ```json fence 内的数组会被当作指令；纯叙事里的【...】不会触发写入
- **推测专用** `hypothesis`：你想假设/推测的内容用这个，不要写进 `memory.facts`。例：
  `{"op":"hypothesis","text":"斯雷因可能仍在监视宴会出口","characters":["斯雷因"]}`
  推测会单独存放，玩家或 GM 后续可用 `confirm_hypothesis`/`reject_hypothesis` 升级或弃用：
  `{"op":"confirm_hypothesis","id":"mem_xxxxxx"}` → 转 runtime_fact
  `{"op":"reject_hypothesis","id":"mem_xxxxxx"}` → 标 rejected

# 兼容协议（向后兼容 · JSON 失败时备用，新模型请优先用 JSON）
- `【状态写入：path=value】`、`【状态追加：path=value】`、`【询问玩家：问题｜选项：A、B、C】`
- 时间/位置专用：`【当前时间线：申时三刻】`、`【当前位置：北港·灯塔下】`
- 时间跳跃裁定：`【时间跳跃确认：目标】`、`【时间跳跃拒绝：原因】`
- 详细 schema 与字段类型见动态注入的【状态字段 schema】层。

# 硬约束（系统级，永远不能违反）
- `permissions.*` / `history.*` / `schema_version` / `created_at` 是写入黑名单，任何形式（包括 `/set`）都会被拒并记 audit_log。
- 用户变量（`worldline.user_variables.*`）是硬约束；时间线/资源/能力变化时必须先满足用户变量。
- pending_jump 待确认期间禁止把未来时间当成已发生（"翌日…""转眼已是…"等措辞、新地点新场景、新时间标签全部禁止）。

# 记忆优先级（高 → 低，冲突时高优先级胜）
本轮 prompt 里可能同时出现多种来源的信息。冲突时按下面顺序裁定，**不要把低优先级的内容当成已发生事实复述**：

1. **玩家硬设定**（`/set` 指令、玩家显式确认过的设定、`worldline.user_variables.*`）—— 最高权威，覆盖一切。
2. **当前存档状态**（【当前剧情状态】里的 player/world/memory/relationships/timeline）—— 本局已发生事实。
3. **原著/剧本事实**（角色卡、世界书、ChapterFact）—— 设定边界与人物逻辑的权威依据。
4. **检索参考**（【检索参考】层的 RAG 召回片段）—— **仅是候选材料，不是当前已发生事实**。可以作为人物口吻、地点描写、氛围基线参考，但不要直接当事实写入状态或叙事。
5. **推测/计划/草稿**（子代理的 `hypothesis`、GM 自己的猜想、未确认的 pending_change）—— **永远不能当事实叙事**。需要确认时用 `question` op 让玩家拍板，不要替玩家定调。

关键约束：检索参考里出现"原著里的某段对话/某个事件"不代表本局已经发生；要么放进 `runtime_fact` 写入状态后再叙事，要么作为人物背景隐含，**不要叙事成"刚才/上一次"**。

# 工具调用（如有 MCP 工具可用）
- Anthropic 等支持 native tool_use 的模型：通过 `tools` 参数直接发起调用，结果会作为 tool_result block 回灌。
- 其它模型：在正文中输出 `<<TOOL_CALL>>{"server_id":"...","tool":"...","arguments":{...}}<<END_TOOL_CALL>>`，写完 END marker 立即停止本轮输出。
- 工具结果回灌后基于结果继续叙事/写状态标签；不要重复已经叙述的内容。
"""

_DYNAMIC_CONTEXT = """\
【当前剧情状态】
{player_summary}

【本轮上下文包】
{retrieved_context}
{transmigrator_note}"""

_OPENING_PROMPT = """\
请为这位刚进入游戏的玩家生成一段开场描写。

描写要素：
- 时间与地点由当前剧本/模组的世界书或 state.world.time + player.current_location 决定，不要捏造与之冲突的场景
- 让玩家感受到当前世界的氛围，以及他们角色的处境
- 结尾留一个可以行动的悬念或选择，不要替玩家做决定

字数：150-250字
"""


# ══════════════════════════════════════════════════════════════════════════════
#  MCP 工具循环辅助
# ══════════════════════════════════════════════════════════════════════════════
def _anthropic_curator_tool_use(
    backend, agent_prompt: str, messages: list[dict], max_tokens: int,
) -> str:
    """task 68：用 native tool_use 跑 context curator，input_schema 强校验。

    定义一个 `select_context` 工具，input_schema 描述 curator 的 6 字段输出；
    模型必须以 tool_use block 返回（tool_choice 强制），SDK 校验合规。
    错误率比 re.search(r'\\{.*\\}') 抠 text JSON 低 5-10×。
    返回 dumped JSON 字符串（保持 curate_context 既有 -> str 契约）。
    """
    # task 79：Demand Ledger schema 替换原 6 字段 curator_plan。让 Anthropic
    # native tool_use 强校验所有字段，配合 context_agent.AGENT_PROMPT 同步升级。
    tool = {
        "name": "select_context",
        "description": "生成本轮 Demand Ledger：玩家意图、硬/软约束、候选动作、acceptance 验收标准、confidence 自评。",
        "input_schema": {
            "type": "object",
            "properties": {
                "intent": {"type": "string", "description": "玩家意图一句话"},
                "active_goal": {"type": "string", "description": "底层真实目标（不是字面）"},
                "hard_constraints": {"type": "array", "items": {"type": "string"}, "description": "必须满足的硬约束"},
                "soft_preferences": {"type": "array", "items": {"type": "string"}, "description": "希望满足的软偏好"},
                "target_entities": {"type": "array", "items": {"type": "string"}, "description": "涉及角色/势力名"},
                "target_location": {"type": "string", "description": "目标地点，无则空"},
                "target_time": {"type": "string", "description": "目标时间，无则空"},
                "timeline_target": {"type": "string", "description": "若请求跳时间的目标 label，无则空"},
                "retrieval_query": {"type": "string", "description": "检索短查询"},
                "retrieval_plan": {
                    "type": "object",
                    "properties": {
                        "must_include": {"type": "array", "items": {"type": "string"}, "description": "本轮必含事实"},
                        "should_include": {"type": "array", "items": {"type": "string"}, "description": "有助非必须的素材"},
                    },
                },
                "candidate_actions": {"type": "array", "items": {"type": "string"}, "description": "本轮 GM 可执行的 2-5 个候选动作"},
                "acceptance": {"type": "array", "items": {"type": "string"}, "description": "本轮成功的验收条件，每条可程序验证"},
                "risk_flags": {"type": "array", "items": {"type": "string"}, "description": "风险标记"},
                "confidence": {"type": "number", "description": "自评信心 0.0-1.0；<0.5 触发 clarifying_question"},
                "clarifying_question": {"type": "string", "description": "confidence 低时填封闭式问题 + 候选答案；否则空"},
                "reason": {"type": "string", "description": "为什么这样规划（不写给玩家）"},
            },
            "required": ["intent", "timeline_target", "retrieval_query", "risk_flags", "confidence", "reason"],
        },
    }
    resp = backend.client.messages.create(
        model=backend.model_name,
        max_tokens=max_tokens,
        temperature=0.1,
        system=agent_prompt,
        messages=messages,
        tools=[tool],
        tool_choice={"type": "tool", "name": "select_context"},
    )
    # capture usage 同 backend.call
    usage = getattr(resp, "usage", None)
    if usage:
        backend.last_usage = {
            "input_tokens": int(getattr(usage, "input_tokens", 0)),
            "output_tokens": int(getattr(usage, "output_tokens", 0)),
            "cached_input_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        }
        backend.last_usage["total_tokens"] = backend.last_usage["input_tokens"] + backend.last_usage["output_tokens"]
    for block in resp.content:
        if getattr(block, "type", None) == "tool_use" and block.name == "select_context":
            inp = block.input or {}
            return json.dumps(inp, ensure_ascii=False)
    # 没拿到 tool_use 块 → 返回最小合法 JSON 让 _parse_curator_json 不崩
    return json.dumps({
        "intent": "", "timeline_target": "", "retrieval_query": "",
        "must_include": [], "risk_flags": ["curator 未返回 tool_use"], "reason": "fallback",
    }, ensure_ascii=False)


def _format_tools_for_prompt(tools: list[dict[str, Any]]) -> str:
    """把 MCP 工具清单格式化成附加 system prompt 片段（text-marker fallback 路径用）。

    协议说明已经在 _SYSTEM_BASE 的「工具调用」段统一描述（task 67），这里只
    枚举本轮可用的工具清单。Anthropic / native tool_use 路径不调用这个函数，
    它专为 Vertex / OpenAI 兼容等还在用文本 marker 的 backend 服务。
    """
    if not tools:
        return ""
    lines = ["", "【本轮可用 MCP 工具清单】"]
    for t in tools[:40]:  # 防止 prompt 过长
        sid = t.get("server_id", "")
        name = t.get("name", "")
        desc = (t.get("description", "") or "").strip().replace("\n", " ")[:160]
        schema = t.get("schema") or {}
        props = schema.get("properties") or {}
        required = schema.get("required") or []
        arg_hint = ""
        if props:
            arg_hint = " · 参数: " + ", ".join(
                f"{k}{'*' if k in required else ''}" for k in list(props.keys())[:8]
            )
        lines.append(f"  · {sid}/{name}: {desc}{arg_hint}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════════════════════════
#  后端：Vertex AI (Gemini) — 使用新版 google-genai SDK (REST)
# ══════════════════════════════════════════════════════════════════════════════
class _VertexBackend:
    def __init__(self, model: str = "gemini-3.5-flash"):
        from google import genai
        from google.oauth2 import service_account

        if not SA_FILE.exists():
            raise FileNotFoundError(f"找不到服务账户文件：{SA_FILE}")

        with open(SA_FILE) as f:
            sa_info = json.load(f)

        credentials = service_account.Credentials.from_service_account_info(
            sa_info,
            scopes=["https://www.googleapis.com/auth/cloud-platform"],
        )
        self.client = genai.Client(
            vertexai=True,
            project=sa_info["project_id"],
            location="global",
            credentials=credentials,
        )
        self.model_name = model
        self._genai = genai
        print(f"[GM] Vertex AI (google-genai) · {model} @ global")

    def call(self, system: str, messages: list[dict], max_tokens: int) -> str:
        from google.genai import types

        contents = self._to_contents(messages, types)

        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max(max_tokens, 2048),  # thinking 模型需要足够 budget
            temperature=0.9,
            thinking_config=types.ThinkingConfig(thinking_budget=0),  # 禁用 thinking，纯生成
        )
        resp = self.client.models.generate_content(
            model=self.model_name,
            contents=contents,
            config=config,
        )
        self._capture_usage(resp)
        return resp.text.strip()

    def _capture_usage(self, resp) -> None:
        meta = getattr(resp, "usage_metadata", None)
        if not meta:
            return
        prompt = int(getattr(meta, "prompt_token_count", 0) or 0)
        candidates = int(getattr(meta, "candidates_token_count", 0) or 0)
        cached = int(getattr(meta, "cached_content_token_count", 0) or 0)
        thoughts = int(getattr(meta, "thoughts_token_count", 0) or 0)
        total = int(getattr(meta, "total_token_count", 0) or (prompt + candidates))
        self.last_usage = {
            "input_tokens": prompt,
            "output_tokens": candidates,
            "cached_input_tokens": cached,
            "reasoning_tokens": thoughts,
            "total_tokens": total,
        }

    def call_structured(self, system: str, messages: list[dict], max_tokens: int) -> str:
        from google.genai import types

        contents = self._to_contents(messages, types)
        config_kwargs = {
            "system_instruction": system,
            "max_output_tokens": max_tokens,
            "temperature": 0.1,
            "thinking_config": types.ThinkingConfig(thinking_budget=0),
        }
        try:
            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                **config_kwargs,
            )
        except TypeError:
            config = types.GenerateContentConfig(**config_kwargs)
        resp = self.client.models.generate_content(
            model=self.model_name,
            contents=contents,
            config=config,
        )
        return resp.text.strip()

    def stream(self, system: str, messages: list[dict], max_tokens: int) -> Iterator[str]:
        from google.genai import types

        contents = self._to_contents(messages, types)
        config = types.GenerateContentConfig(
            system_instruction=system,
            max_output_tokens=max(max_tokens, 2048),
            temperature=0.9,
            thinking_config=types.ThinkingConfig(thinking_budget=0),
        )
        for chunk in self.client.models.generate_content_stream(
            model=self.model_name,
            contents=contents,
            config=config,
        ):
            if getattr(chunk, "usage_metadata", None):
                self._capture_usage(chunk)
            text = getattr(chunk, "text", None)
            if text:
                yield text

    # task 70：Vertex 支持 native function_declarations
    supports_native_tools = True

    def stream_with_mcp_loop(
        self,
        system: str,
        messages: list[dict],
        mcp_tools: list[dict[str, Any]],
        max_iterations: int,
        max_tokens: int,
        mcp_call,
    ) -> Iterator[dict[str, Any]]:
        """Vertex (Gemini) native function calling MCP 循环。

        Gemini 的工具调用模型：
        - tools=[Tool(function_declarations=[FunctionDeclaration(...)])]
        - 流式时 chunk.candidates[0].content.parts[] 里可能有 text 或 function_call
        - 工具结果通过 types.Part.from_function_response(name=..., response=...)
          作为 user role 的 part 注回
        """
        from google.genai import types

        sep = "__"  # server_id 与 tool_name 分隔符
        fn_decls = []
        for t in mcp_tools[:40]:
            sid = str(t.get("server_id", ""))
            tname = str(t.get("name", ""))
            if not sid or not tname:
                continue
            safe_sid = re.sub(r"[^A-Za-z0-9_-]", "_", sid)
            safe_tname = re.sub(r"[^A-Za-z0-9_-]", "_", tname)
            full_name = f"{safe_sid}{sep}{safe_tname}"[:64]
            schema_raw = t.get("schema") or {"type": "object", "properties": {}}
            if not isinstance(schema_raw, dict):
                schema_raw = {"type": "object", "properties": {}}
            try:
                # Gemini 接受 OpenAPI 风格 schema dict 作为 parameters
                fn_decls.append(types.FunctionDeclaration(
                    name=full_name,
                    description=(t.get("description") or "")[:512],
                    parameters=schema_raw if schema_raw.get("type") == "object" else {"type": "object", "properties": {}},
                ))
            except Exception:
                # 个别字段不兼容时降级到无 schema 的工具
                fn_decls.append(types.FunctionDeclaration(
                    name=full_name,
                    description=(t.get("description") or "")[:512],
                ))

        if not fn_decls:
            for chunk in self.stream(system, messages, max_tokens=max_tokens):
                yield {"type": "text", "text": chunk}
            return

        tools_param = [types.Tool(function_declarations=fn_decls)]
        contents = self._to_contents(messages, types)

        for iteration in range(max_iterations):
            pending_calls: list[dict[str, Any]] = []
            current_text_parts: list[Any] = []
            current_text_str = ""

            config = types.GenerateContentConfig(
                system_instruction=system,
                max_output_tokens=max(max_tokens, 2048),
                temperature=0.9,
                tools=tools_param,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            )
            for chunk in self.client.models.generate_content_stream(
                model=self.model_name, contents=contents, config=config,
            ):
                if getattr(chunk, "usage_metadata", None):
                    self._capture_usage(chunk)
                # parts 走候选[0]
                cands = getattr(chunk, "candidates", None) or []
                if not cands:
                    continue
                content = getattr(cands[0], "content", None)
                if not content:
                    continue
                for part in (getattr(content, "parts", None) or []):
                    ptext = getattr(part, "text", None)
                    if ptext:
                        current_text_str += ptext
                        current_text_parts.append(types.Part.from_text(text=ptext))
                        yield {"type": "text", "text": ptext}
                    fc = getattr(part, "function_call", None)
                    if fc:
                        full_name = getattr(fc, "name", "") or ""
                        args_raw = getattr(fc, "args", None) or {}
                        try:
                            args = dict(args_raw)
                        except Exception:
                            args = {}
                        if sep in full_name:
                            server_id, _, tool_name = full_name.partition(sep)
                        else:
                            server_id, tool_name = "", full_name
                        # task 48 fix: Gemini 2.5 多轮 tool_use 需要把模型上一轮产生的
                        # thought_signature 跟 function_call 一起传回去,否则第 2 轮 API
                        # 返 400 "Function call is missing a thought_signature in functionCall parts"。
                        # 解决: 把整个 part 对象存下来 (含 thought_signature),装回 contents
                        # 时直接 append 原 part,而不是用 name+args 重建。
                        pending_calls.append({
                            "name": full_name, "server_id": server_id,
                            "tool_name": tool_name, "arguments": args,
                            "raw_part": part,  # 保留原 part,含 thought_signature
                        })
                        yield {
                            "type": "tool_call", "server_id": server_id,
                            "tool": tool_name, "arguments": args,
                        }

            if not pending_calls:
                return
            # 把 model 回合（文本 + function_call parts）作为 model role 装回 contents
            model_parts: list[Any] = []
            if current_text_str:
                model_parts.append(types.Part.from_text(text=current_text_str))
            for pc in pending_calls:
                # task 48 fix: 优先直接用 SDK 返回的原 part (它含 thought_signature)。
                # raw_part 不可用时降级到重建 (老 SDK / 离线测试场景)。
                raw_part = pc.get("raw_part")
                if raw_part is not None:
                    model_parts.append(raw_part)
                else:
                    try:
                        fc_part = types.Part.from_function_call(name=pc["name"], args=pc["arguments"])
                    except Exception:
                        fc_part = types.Part(function_call=types.FunctionCall(name=pc["name"], args=pc["arguments"]))
                    model_parts.append(fc_part)
            contents.append(types.Content(role="model", parts=model_parts))

            # 顺序 dispatch，把每个 function_response part 收成 user role 一次性 append
            result_parts: list[Any] = []
            for pc in pending_calls:
                try:
                    result = mcp_call(pc["server_id"], pc["tool_name"], pc["arguments"])
                except Exception as exc:
                    result = {"ok": False, "error": f"call_tool 异常: {exc}"}
                yield {
                    "type": "tool_result", "ok": bool(result.get("ok")),
                    "result": result.get("result"), "error": result.get("error"),
                }
                # Gemini 要求 response 是 dict
                response_dict = result if isinstance(result, dict) else {"result": str(result)[:2000]}
                # 截断防爆
                try:
                    response_dict = json.loads(json.dumps(response_dict, ensure_ascii=False)[:2000])
                except Exception:
                    response_dict = {"result_truncated": str(response_dict)[:2000]}
                result_parts.append(types.Part.from_function_response(
                    name=pc["name"], response=response_dict,
                ))
            contents.append(types.Content(role="user", parts=result_parts))
        yield {"type": "text", "text": "\n\n【已达工具调用次数上限，本轮终止】"}

    @staticmethod
    def _to_contents(messages: list[dict], types):
        contents = []
        for msg in messages:
            role = "user" if msg["role"] == "user" else "model"
            contents.append(
                types.Content(
                    role=role,
                    parts=[types.Part.from_text(text=msg["content"])],
                )
            )
        return contents


# ══════════════════════════════════════════════════════════════════════════════
#  后端：Anthropic (备用)
# ══════════════════════════════════════════════════════════════════════════════
class _AnthropicBackend:
    # task 57 (2026-05-25): 默认改为当前 Sonnet（最新平衡型）；
    # Opus 4.7 是 frontier 但成本 5×，留给用户显式选。
    def __init__(self, model: str = "claude-sonnet-4-6", user_id: int | None = None):
        from anthropic import Anthropic
        from platform_app.user_credentials import resolve_api_key
        result = resolve_api_key(user_id, "anthropic", env_fallback="ANTHROPIC_API_KEY")
        key = result.get("key") or os.environ.get("EMBED_API_KEY")
        if not key:
            raise ValueError("找不到 Anthropic API Key（用户未配置且无环境变量）")
        self.client = Anthropic(api_key=key)
        self.model_name = model
        self.last_usage: dict[str, int] = {}
        print(f"[GM] Anthropic · {self.model_name} (key from {result.get('source', 'env')})")

    def call(self, system: str, messages: list[dict], max_tokens: int) -> str:
        resp = self.client.messages.create(
            model=self.model_name,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        )
        usage = getattr(resp, "usage", None)
        if usage:
            self.last_usage = {
                "input_tokens": int(getattr(usage, "input_tokens", 0)),
                "output_tokens": int(getattr(usage, "output_tokens", 0)),
                "cached_input_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
            }
            self.last_usage["total_tokens"] = self.last_usage["input_tokens"] + self.last_usage["output_tokens"]
        return resp.content[0].text.strip()

    def call_structured(self, system: str, messages: list[dict], max_tokens: int) -> str:
        resp = self.client.messages.create(
            model=self.model_name,
            max_tokens=max_tokens,
            temperature=0.1,
            system=system + "\n\n你必须只返回合法 JSON，不能包含 Markdown 代码围栏或解释文字。",
            messages=messages,
        )
        return resp.content[0].text.strip()

    def stream(self, system: str, messages: list[dict], max_tokens: int) -> Iterator[str]:
        with self.client.messages.stream(
            model=self.model_name,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
        ) as stream:
            for text in stream.text_stream:
                yield text
            # stream 结束后从 final_message 抽 usage
            try:
                final = stream.get_final_message()
                usage = getattr(final, "usage", None)
                if usage:
                    self.last_usage = {
                        "input_tokens": int(getattr(usage, "input_tokens", 0)),
                        "output_tokens": int(getattr(usage, "output_tokens", 0)),
                        "cached_input_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
                    }
                    self.last_usage["total_tokens"] = self.last_usage["input_tokens"] + self.last_usage["output_tokens"]
            except Exception:
                pass

    # task 66：native tool_use 流式 — 替代文本协议 <<TOOL_CALL>>。
    # 错误率比 text marker 低 5-10×，input_schema 校验直接由 Anthropic 做。
    supports_native_tools = True

    def stream_with_tools_native(
        self,
        system: str,
        messages: list[dict],
        anthropic_tools: list[dict],
        max_tokens: int,
    ) -> Iterator[dict[str, Any]]:
        """流式 + native tool_use。yields:
          - {"type": "text", "text": "..."}
          - {"type": "tool_use_block", "id": "...", "name": "...", "input": {...}}
          - {"type": "stop", "stop_reason": "end_turn"|"tool_use"|...}
        每个 tool_use_block 完整产生后才 yield（input JSON 已合并完）。
        """
        current_block: dict[str, Any] | None = None
        partial_json_buf = ""
        stop_reason: str | None = None
        with self.client.messages.stream(
            model=self.model_name,
            max_tokens=max_tokens,
            system=system,
            messages=messages,
            tools=anthropic_tools,
            tool_choice={"type": "auto"},
        ) as stream:
            for event in stream:
                et = getattr(event, "type", None)
                if et == "content_block_start":
                    block = getattr(event, "content_block", None)
                    bt = getattr(block, "type", None)
                    if bt == "tool_use":
                        current_block = {
                            "id": getattr(block, "id", ""),
                            "name": getattr(block, "name", ""),
                        }
                        partial_json_buf = ""
                elif et == "content_block_delta":
                    delta = getattr(event, "delta", None)
                    dt = getattr(delta, "type", None)
                    if dt == "text_delta":
                        text = getattr(delta, "text", "") or ""
                        if text:
                            yield {"type": "text", "text": text}
                    elif dt == "input_json_delta":
                        partial_json_buf += getattr(delta, "partial_json", "") or ""
                elif et == "content_block_stop":
                    if current_block is not None:
                        try:
                            parsed = json.loads(partial_json_buf or "{}")
                            if not isinstance(parsed, dict):
                                parsed = {}
                        except Exception:
                            parsed = {}
                        yield {
                            "type": "tool_use_block",
                            "id": current_block["id"],
                            "name": current_block["name"],
                            "input": parsed,
                        }
                        current_block = None
                        partial_json_buf = ""
                elif et == "message_delta":
                    delta = getattr(event, "delta", None)
                    if delta:
                        sr = getattr(delta, "stop_reason", None)
                        if sr:
                            stop_reason = sr
            # capture usage
            try:
                final = stream.get_final_message()
                usage = getattr(final, "usage", None)
                if usage:
                    self.last_usage = {
                        "input_tokens": int(getattr(usage, "input_tokens", 0)),
                        "output_tokens": int(getattr(usage, "output_tokens", 0)),
                        "cached_input_tokens": int(getattr(usage, "cache_read_input_tokens", 0) or 0),
                    }
                    self.last_usage["total_tokens"] = self.last_usage["input_tokens"] + self.last_usage["output_tokens"]
            except Exception:
                pass
        yield {"type": "stop", "stop_reason": stop_reason or "end_turn"}

    def stream_with_mcp_loop(
        self,
        system: str,
        messages: list[dict],
        mcp_tools: list[dict[str, Any]],
        max_iterations: int,
        max_tokens: int,
        mcp_call,
    ) -> Iterator[dict[str, Any]]:
        """task 66：完整的 native tool_use MCP 循环（Anthropic 路径）。

        每个 backend 拥有自己的 loop，封装该 provider 的：
        - 工具列表 → 原生格式
        - 流式 event → 统一事件
        - assistant + tool_result 消息装回历史的具体形态
        """
        sep = "__"  # server_id 与 tool_name 分隔符
        # MCP → Anthropic tools
        anthropic_tools = []
        for t in mcp_tools[:40]:
            sid = str(t.get("server_id", ""))
            tname = str(t.get("name", ""))
            if not sid or not tname:
                continue
            safe_sid = re.sub(r"[^A-Za-z0-9_-]", "_", sid)
            safe_tname = re.sub(r"[^A-Za-z0-9_-]", "_", tname)
            full_name = f"{safe_sid}{sep}{safe_tname}"[:64]
            schema = t.get("schema") or {"type": "object", "properties": {}}
            if not isinstance(schema, dict):
                schema = {"type": "object", "properties": {}}
            if schema.get("type") != "object":
                schema = {"type": "object", "properties": schema.get("properties", {})}
            anthropic_tools.append({
                "name": full_name,
                "description": (t.get("description") or "")[:512],
                "input_schema": schema,
            })
        if not anthropic_tools:
            for chunk in self.stream(system, messages, max_tokens=max_tokens):
                yield {"type": "text", "text": chunk}
            return

        for iteration in range(max_iterations):
            pending_uses: list[dict[str, Any]] = []
            accumulated_blocks: list[dict[str, Any]] = []
            current_text = ""
            for ev in self.stream_with_tools_native(
                system, messages, anthropic_tools, max_tokens=max_tokens,
            ):
                et = ev.get("type")
                if et == "text":
                    text = ev.get("text", "")
                    if text:
                        current_text += text
                        yield {"type": "text", "text": text}
                elif et == "tool_use_block":
                    full_name = ev.get("name", "")
                    if sep in full_name:
                        server_id, _, tool_name = full_name.partition(sep)
                    else:
                        server_id, tool_name = "", full_name
                    arguments = ev.get("input") or {}
                    tu_id = ev.get("id", "")
                    pending_uses.append({
                        "id": tu_id, "server_id": server_id,
                        "tool_name": tool_name, "arguments": arguments,
                    })
                    accumulated_blocks.append({
                        "type": "tool_use", "id": tu_id,
                        "name": full_name, "input": arguments,
                    })
                    yield {
                        "type": "tool_call", "server_id": server_id,
                        "tool": tool_name, "arguments": arguments,
                    }
                elif et == "stop":
                    break
            if not pending_uses:
                return
            assistant_content: list[dict[str, Any]] = []
            if current_text:
                assistant_content.append({"type": "text", "text": current_text})
            assistant_content.extend(accumulated_blocks)
            messages.append({"role": "assistant", "content": assistant_content})
            tool_result_blocks: list[dict[str, Any]] = []
            for use in pending_uses:
                try:
                    result = mcp_call(use["server_id"], use["tool_name"], use["arguments"])
                except Exception as exc:
                    result = {"ok": False, "error": f"call_tool 异常: {exc}"}
                yield {
                    "type": "tool_result", "ok": bool(result.get("ok")),
                    "result": result.get("result"), "error": result.get("error"),
                }
                truncated = json.dumps(result, ensure_ascii=False)[:2000]
                tool_result_blocks.append({
                    "type": "tool_result", "tool_use_id": use["id"],
                    "content": truncated, "is_error": not bool(result.get("ok")),
                })
            messages.append({"role": "user", "content": tool_result_blocks})
        yield {"type": "text", "text": "\n\n【已达工具调用次数上限，本轮终止】"}


# ══════════════════════════════════════════════════════════════════════════════
#  后端：OpenAI 兼容（OpenAI / OpenRouter / 硅基 / 阿里 / 腾讯 / 火山 / 小米 ...）
# ══════════════════════════════════════════════════════════════════════════════
class _OpenAICompatBackend:
    """适配所有 OpenAI 兼容的 provider，只需要 base_url + env_key + model 名。"""

    # task 71：升 native tools，但 provider 兼容度不一（OpenAI/DeepSeek/豆包/
    # 智谱/Kimi/通义 都支持；SiliconFlow/OpenRouter 看模型；本地 ollama 通常
    # 不支持）。第一次调用 try/except，捕获到不支持时自动降级到 text marker
    # 协议（GameMaster.respond_stream_with_tools 会兜底）。
    supports_native_tools = True

    # 类级状态：记录已经验证过不支持 native tools 的 (api_id, model) 组合，
    # 同一进程内之后直接走 text marker 不再重试
    _unsupported_combos: set[tuple[str, str]] = set()

    def __init__(self, model: str, base_url: str, env_key: str, display_kind: str = "openai_compat",
                 user_id: int | None = None, api_id: str | None = None):
        from openai import OpenAI
        from platform_app.user_credentials import resolve_api_key
        result = resolve_api_key(user_id, api_id or display_kind, env_fallback=env_key)
        key = result.get("key")
        if not key:
            raise ValueError(f"找不到 {api_id or display_kind} 的 API Key（用户未配置且无环境变量 {env_key}）")
        # 用户覆盖了 base_url 的话优先用用户的
        effective_base = result.get("base_url_override") or base_url
        kwargs: dict[str, Any] = {"api_key": key}
        if effective_base:
            kwargs["base_url"] = effective_base
        self.client = OpenAI(**kwargs)
        self.model_name = model
        self.kind = display_kind
        self.api_id = api_id or display_kind
        self.last_usage: dict[str, int] = {}
        print(f"[GM] {display_kind} · {model} (base={effective_base or 'default'}, key from {result.get('source')})")

    def _to_messages(self, system: str, messages: list[dict]) -> list[dict]:
        out = []
        if system:
            out.append({"role": "system", "content": system})
        out.extend(messages)
        return out

    def call(self, system: str, messages: list[dict], max_tokens: int) -> str:
        resp = self.client.chat.completions.create(
            model=self.model_name,
            messages=self._to_messages(system, messages),
            max_tokens=max_tokens,
            temperature=0.9,
        )
        self._capture_usage(resp)
        return (resp.choices[0].message.content or "").strip()

    def _capture_usage(self, resp) -> None:
        usage = getattr(resp, "usage", None)
        if not usage:
            return
        # OpenAI 格式：prompt_tokens / completion_tokens / total_tokens
        # 部分 provider 还会带 prompt_tokens_details.cached_tokens
        cached = 0
        details = getattr(usage, "prompt_tokens_details", None)
        if details:
            cached = int(getattr(details, "cached_tokens", 0) or 0)
        reasoning = 0
        comp_details = getattr(usage, "completion_tokens_details", None)
        if comp_details:
            reasoning = int(getattr(comp_details, "reasoning_tokens", 0) or 0)
        self.last_usage = {
            "input_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
            "output_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
            "cached_input_tokens": cached,
            "reasoning_tokens": reasoning,
            "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        }

    def call_structured(self, system: str, messages: list[dict], max_tokens: int) -> str:
        sys_text = (system or "") + "\n\n你必须只返回合法 JSON，不能包含 Markdown 代码围栏或解释文字。"
        resp = self.client.chat.completions.create(
            model=self.model_name,
            messages=self._to_messages(sys_text, messages),
            max_tokens=max_tokens,
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        return (resp.choices[0].message.content or "").strip()

    def stream(self, system: str, messages: list[dict], max_tokens: int) -> Iterator[str]:
        stream = self.client.chat.completions.create(
            model=self.model_name,
            messages=self._to_messages(system, messages),
            max_tokens=max_tokens,
            temperature=0.9,
            stream=True,
            stream_options={"include_usage": True},  # 末尾 chunk 带 usage
        )
        for chunk in stream:
            # 末尾 usage chunk 的 choices 可能为空
            try:
                if getattr(chunk, "usage", None):
                    self._capture_usage(chunk)
                if chunk.choices:
                    delta = chunk.choices[0].delta.content
                    if delta:
                        yield delta
            except Exception:
                continue

    def stream_with_mcp_loop(
        self,
        system: str,
        messages: list[dict],
        mcp_tools: list[dict[str, Any]],
        max_iterations: int,
        max_tokens: int,
        mcp_call,
    ) -> Iterator[dict[str, Any]]:
        """task 71：OpenAI 兼容 native function calling MCP 循环，带 fallback。

        OpenAI tools schema：
          tools=[{"type":"function","function":{"name":..., "description":..., "parameters":<jsonschema>}}]

        流式中 chunk.choices[0].delta.tool_calls[] 是 list of:
          { index: 0, id: "...", type: "function", function: {name?, arguments?} }
        arguments 是分片字符串，按 index 拼到完整 JSON。
        finish_reason == 'tool_calls' 时表示模型选择调工具，dispatch 后继续。

        Provider 不支持 tools 参数时（HTTP 400 / response 异常）→ 标记
        (api_id, model) 为 unsupported，本进程后续直接走 text marker fallback。
        """
        combo_key = (self.api_id, self.model_name)
        if combo_key in self._unsupported_combos:
            # 已知该 provider/model 不支持 tools → 立即降级到 text marker
            yield from _openai_text_marker_loop(self, system, messages, mcp_tools, max_iterations, max_tokens, mcp_call)
            return

        sep = "__"
        openai_tools = []
        for t in mcp_tools[:40]:
            sid = str(t.get("server_id", ""))
            tname = str(t.get("name", ""))
            if not sid or not tname:
                continue
            safe_sid = re.sub(r"[^A-Za-z0-9_-]", "_", sid)
            safe_tname = re.sub(r"[^A-Za-z0-9_-]", "_", tname)
            full_name = f"{safe_sid}{sep}{safe_tname}"[:64]
            schema_raw = t.get("schema") or {"type": "object", "properties": {}}
            if not isinstance(schema_raw, dict):
                schema_raw = {"type": "object", "properties": {}}
            if schema_raw.get("type") != "object":
                schema_raw = {"type": "object", "properties": schema_raw.get("properties", {})}
            openai_tools.append({
                "type": "function",
                "function": {
                    "name": full_name,
                    "description": (t.get("description") or "")[:512],
                    "parameters": schema_raw,
                },
            })
        if not openai_tools:
            for chunk in self.stream(system, messages, max_tokens=max_tokens):
                yield {"type": "text", "text": chunk}
            return

        oai_messages = self._to_messages(system, messages)

        first_attempt = True
        for iteration in range(max_iterations):
            tool_calls_buf: dict[int, dict[str, Any]] = {}  # index → {id, name, arguments}
            current_text = ""
            finish_reason: str | None = None
            try:
                stream = self.client.chat.completions.create(
                    model=self.model_name,
                    messages=oai_messages,
                    max_tokens=max_tokens,
                    temperature=0.9,
                    tools=openai_tools,
                    tool_choice="auto",
                    stream=True,
                    stream_options={"include_usage": True},
                )
                for chunk in stream:
                    try:
                        if getattr(chunk, "usage", None):
                            self._capture_usage(chunk)
                        if not chunk.choices:
                            continue
                        choice = chunk.choices[0]
                        delta = getattr(choice, "delta", None)
                        if delta:
                            ctext = getattr(delta, "content", None)
                            if ctext:
                                current_text += ctext
                                yield {"type": "text", "text": ctext}
                            tcs = getattr(delta, "tool_calls", None) or []
                            for tc in tcs:
                                idx = getattr(tc, "index", 0) or 0
                                buf = tool_calls_buf.setdefault(idx, {"id": "", "name": "", "arguments": ""})
                                if getattr(tc, "id", None):
                                    buf["id"] = tc.id
                                fn = getattr(tc, "function", None)
                                if fn:
                                    if getattr(fn, "name", None):
                                        buf["name"] = fn.name
                                    args_delta = getattr(fn, "arguments", None)
                                    if args_delta:
                                        buf["arguments"] += args_delta
                        fr = getattr(choice, "finish_reason", None)
                        if fr:
                            finish_reason = fr
                    except Exception:
                        continue
            except Exception as exc:
                # tools 不支持？标记并降级（只在第一次尝试时降级，避免循环中途异常被当成"不支持"）
                if first_attempt:
                    print(f"[gm] {self.api_id}/{self.model_name} native tools failed: {exc} → text marker fallback")
                    self._unsupported_combos.add(combo_key)
                    yield from _openai_text_marker_loop(self, system, messages, mcp_tools, max_iterations, max_tokens, mcp_call)
                    return
                # 后续 iteration 异常：let it bubble
                raise
            first_attempt = False

            if not tool_calls_buf:
                # 没有 tool_calls → 本轮结束
                return

            # 装回 assistant 消息（含 tool_calls）
            assistant_msg: dict[str, Any] = {
                "role": "assistant",
                "content": current_text or None,
                "tool_calls": [
                    {
                        "id": buf["id"] or f"call_{idx}",
                        "type": "function",
                        "function": {"name": buf["name"], "arguments": buf["arguments"] or "{}"},
                    }
                    for idx, buf in sorted(tool_calls_buf.items())
                ],
            }
            oai_messages.append(assistant_msg)

            # dispatch + 装 tool result（OpenAI 用 role=tool, tool_call_id=...）
            for idx in sorted(tool_calls_buf.keys()):
                buf = tool_calls_buf[idx]
                full_name = buf["name"] or ""
                if sep in full_name:
                    server_id, _, tool_name = full_name.partition(sep)
                else:
                    server_id, tool_name = "", full_name
                try:
                    args = json.loads(buf["arguments"] or "{}")
                    if not isinstance(args, dict):
                        args = {}
                except Exception:
                    args = {}
                yield {
                    "type": "tool_call", "server_id": server_id,
                    "tool": tool_name, "arguments": args,
                }
                try:
                    result = mcp_call(server_id, tool_name, args)
                except Exception as exc:
                    result = {"ok": False, "error": f"call_tool 异常: {exc}"}
                yield {
                    "type": "tool_result", "ok": bool(result.get("ok")),
                    "result": result.get("result"), "error": result.get("error"),
                }
                truncated = json.dumps(result, ensure_ascii=False)[:2000]
                oai_messages.append({
                    "role": "tool",
                    "tool_call_id": buf["id"] or f"call_{idx}",
                    "content": truncated,
                })
        yield {"type": "text", "text": "\n\n【已达工具调用次数上限，本轮终止】"}


def _openai_text_marker_loop(
    backend, system, messages, mcp_tools, max_iterations, max_tokens, mcp_call,
) -> Iterator[dict[str, Any]]:
    """task 71：不支持 native tools 的 OpenAI 兼容 provider 用 text marker。

    复用主循环的 <<TOOL_CALL>>{json}<<END_TOOL_CALL>> 协议——直接调
    GameMaster.respond_stream_with_tools 内联那段逻辑会循环依赖；这里把它
    单独抽出，让 backend 自己跑 text marker 路径。

    本函数 yields 同样的 text/tool_call/tool_result 事件，与 native 路径
    interchangeable。
    """
    system_with_tools = system + _format_tools_for_prompt(mcp_tools)
    START = "<<TOOL_CALL>>"
    END = "<<END_TOOL_CALL>>"
    tail_keep = max(len(START), len(END)) - 1
    accumulated_text = ""

    for iteration in range(max_iterations):
        buffer = ""
        in_tool = False
        tool_invoked = False
        for chunk in backend.stream(system_with_tools, messages, max_tokens=max_tokens):
            buffer += chunk
            while True:
                if not in_tool:
                    start_idx = buffer.find(START)
                    if start_idx < 0:
                        if len(buffer) > tail_keep:
                            emit = buffer[:-tail_keep]
                            buffer = buffer[-tail_keep:]
                            if emit:
                                accumulated_text += emit
                                yield {"type": "text", "text": emit}
                        break
                    pre = buffer[:start_idx]
                    if pre:
                        accumulated_text += pre
                        yield {"type": "text", "text": pre}
                    buffer = buffer[start_idx + len(START):]
                    in_tool = True
                    continue
                end_idx = buffer.find(END)
                if end_idx < 0:
                    break
                tool_json_raw = buffer[:end_idx]
                buffer = buffer[end_idx + len(END):]
                in_tool = False
                tool_invoked = True
                try:
                    tool_data = json.loads(tool_json_raw.strip())
                    server_id = str(tool_data.get("server_id", ""))
                    tool_name = str(tool_data.get("tool", ""))
                    arguments = tool_data.get("arguments") or {}
                    if not isinstance(arguments, dict):
                        arguments = {}
                except Exception as exc:
                    yield {"type": "tool_error", "error": f"工具调用 JSON 解析失败: {exc}", "raw": tool_json_raw[:200]}
                    messages.append({"role": "assistant", "content": accumulated_text + START + tool_json_raw + END})
                    messages.append({"role": "user", "content": "【系统】上一条工具调用 JSON 解析失败，请重新生成或放弃工具调用。"})
                    accumulated_text = ""
                    break
                yield {"type": "tool_call", "server_id": server_id, "tool": tool_name, "arguments": arguments}
                try:
                    result = mcp_call(server_id, tool_name, arguments)
                except Exception as exc:
                    result = {"ok": False, "error": f"call_tool 异常: {exc}"}
                yield {
                    "type": "tool_result", "ok": bool(result.get("ok")),
                    "result": result.get("result"), "error": result.get("error"),
                }
                assistant_msg = accumulated_text + START + tool_json_raw + END
                messages.append({"role": "assistant", "content": assistant_msg})
                truncated_result = json.dumps(result, ensure_ascii=False)[:2000]
                messages.append({
                    "role": "user",
                    "content": (
                        f"【工具结果：{server_id}/{tool_name}】\n{truncated_result}\n\n"
                        f"请基于工具结果继续本轮回应（不要重复正文，可继续描写或追加状态标签）。"
                    ),
                })
                accumulated_text = ""
                break
            if tool_invoked:
                break
        if not tool_invoked:
            if in_tool:
                yield {"type": "tool_error", "error": "工具调用未闭合", "raw": buffer[:200]}
                messages.append({"role": "assistant", "content": accumulated_text + START + buffer})
                messages.append({"role": "user", "content": "【系统】上一条工具调用未闭合，请重新输出完整 marker 或放弃调用。"})
                accumulated_text = ""
                continue
            if buffer:
                yield {"type": "text", "text": buffer}
            return
    yield {"type": "text", "text": "\n\n【已达工具调用次数上限，本轮终止】"}


# ══════════════════════════════════════════════════════════════════════════════
#  GameMaster：统一接口
# ══════════════════════════════════════════════════════════════════════════════
class GameMaster:
    def __init__(self, model: str = "gemini-3.5-flash", api_id: str = "vertex_ai", user_id: int | None = None):
        """
        api_id: provider id from model_registry.py.
        model: provider-native real model name.
        user_id: 当前用户 ID，用于按用户隔离取 API key。本地未登录 + RPG_REQUIRE_AUTH!=1 时回退环境变量。
        """
        from model_registry import load_model_catalog, find_api
        catalog = load_model_catalog()
        api = find_api(catalog, api_id)
        kind = (api or {}).get("kind", api_id)
        self.api_id = api_id
        self.user_id = user_id

        if kind == "anthropic":
            self._backend = _AnthropicBackend(model=model, user_id=user_id)
        elif kind == "vertex_ai":
            # Vertex 用 service account JSON，不走 user_credentials（暂时保持原逻辑）
            self._backend = _VertexBackend(model=model)
        elif kind in {"openai", "openai_compat"}:
            base_url = (api or {}).get("base_url") or ""
            env_key = (api or {}).get("credential_env") or "OPENAI_API_KEY"
            self._backend = _OpenAICompatBackend(
                model=model, base_url=base_url, env_key=env_key,
                display_kind=api_id, user_id=user_id, api_id=api_id,
            )
        else:
            if SA_FILE.exists():
                self._backend = _VertexBackend(model=model)
            else:
                print(f"[GM] 未知 kind={kind}，降级到 Anthropic")
                self._backend = _AnthropicBackend(user_id=user_id)

    # ── 构建 system prompt ────────────────────────────────────────
    def _build_system(self) -> str:
        """组装通用 system prompt。

        world_section 来源：
        - 如果绑定了《我蕾穆丽娜不爱你》兼容老存档（is_default_novel=True），注入 _WORLD（柏林宇宙）
        - 模组 / 其它剧本 / freeform：不在 system prompt 注入特定世界硬编码；
          世界 / 房间 / 时间线由 context_providers / world book / 模组 manifest 在动态上下文层提供。
        """
        world_section = self._world_section_for_active_content()
        # _SYSTEM_BASE intentionally contains literal JSON examples such as
        # {"op": "set", ...}.  Do not run the whole prompt through str.format(),
        # because those braces are prompt text, not Python placeholders.
        return _SYSTEM_BASE.replace("{world_section}", world_section)

    def _world_section_for_active_content(self) -> str:
        """根据当前 state 的 content_pack 返回一段『世界 / 模组背景』。
        默认 Berlin 老存档仍读 indexes/world.json；新剧本 / 模组 / freeform 不注入硬编码。
        """
        state = getattr(self, "_active_state", None)
        is_default_novel = False
        try:
            if state is not None:
                from context_providers import resolve_content_pack
                manifest = resolve_content_pack(state) or {}
                kind = manifest.get("kind") or ""
                mid = str(manifest.get("id") or "")
                # 默认 Berlin novel 标识：legacy save 或 __legacy_novel__
                if kind == "novel_adaptation" and (
                    mid in ("__legacy_novel__", "__legacy_save__") or mid.startswith("script:")
                ):
                    # 仅当 state 含柏林 token（防止误注入到其他剧本）
                    data = getattr(state, "data", {}) or {}
                    world_time = str((data.get("world") or {}).get("time") or "")
                    location = str((data.get("player") or {}).get("current_location") or "")
                    if any(tok in (world_time + location) for tok in (
                        "柏林", "图卢兹", "哈布斯堡", "蛇信", "薇瑟", "扎兹巴鲁姆", "蕾穆丽娜",
                    )):
                        is_default_novel = True
        except Exception:
            is_default_novel = False
        if not is_default_novel:
            return ""  # 通用底座：world 信息走 context_providers / dynamic context
        try:
            world_brief = (
                f"{_WORLD['setting']}\n"
                f"当前局势：{_WORLD['current_situation']}"
            )
            berlin = _WORLD.get("current_berlin") or {}
            parts: list[str] = []
            if world_brief.strip():
                parts.append("# 世界背景\n" + world_brief)
            if berlin:
                berlin_brief = (
                    f"氛围：{berlin.get('atmosphere','')}\n"
                    f"风险等级：{berlin.get('risk_level','')}\n"
                    f"在场势力：\n" + "\n".join(f"  · {p}" for p in (berlin.get('power_presence') or []))
                )
                parts.append("# 当前柏林局势\n" + berlin_brief)
            return "\n\n".join(parts)
        except Exception:
            return ""

    def _dynamic_context(self, player_summary: str, retrieved_context: str) -> str:
        # 穿越者专属附注
        is_transmigrator = "穿越者" in player_summary
        if is_transmigrator:
            transmigrator_note = """
【穿越者特殊规则】
- 玩家角色是来自另一个世界的穿越者，读过这个世界的原著小说，对部分剧情走向有超前认知——但穿越已经改变了部分支线，不确定哪些还准。
- 她拥有魔力∞，但用法尚未摸清，不是"随时能解决一切"，而是"潜力巨大但控制未知"。
- 外表：白发红瞳少女，在这个世界会引发旁人注目或误判。
- 她偶尔会对NPC说出让对方摸不着头脑的话（因为她知道原著内容）。
- GM要体现信息不对称的趣味：她知道一些别人不知道的，但她也有很多书里没写到的盲区。
- 不要让她"一眼看穿一切"——读者视角和亲历者视角是不同的。"""
        else:
            transmigrator_note = ""

        return _DYNAMIC_CONTEXT.format(
            player_summary=player_summary,
            retrieved_context=retrieved_context or "（本轮无额外召回）",
            transmigrator_note=transmigrator_note,
        )

    def _turn_message(self, user_input: str, state, retrieved_context: str) -> str:
        return (
            f"{self._dynamic_context(state.short_summary(), retrieved_context)}\n\n"
            f"【玩家本轮输入】\n{user_input}"
        )

    def curate_context(self, agent_prompt: str, task_prompt: str) -> str:
        """Run the model-backed context sub-agent before the main GM call.

        task 68：Anthropic 用 native tool_use（input_schema 强校验），
        消除原 _parse_curator_json 的 re.search(r'\\{.*\\}') 兜底脆性。
        Vertex / OpenAI compat 继续走各自的 JSON mode（response_mime_type /
        response_format=json_object），那两条路径已经够稳。
        """
        messages = [{"role": "user", "content": task_prompt}]
        backend = self._backend
        # Anthropic backend 走 native tool_use
        if isinstance(backend, _AnthropicBackend):
            try:
                return _anthropic_curator_tool_use(
                    backend, agent_prompt, messages, max_tokens=900,
                )
            except Exception as exc:
                print(f"[curator] native tool_use 失败，降级到文本 JSON：{exc}")
                # fallback to text JSON
        return backend.call_structured(agent_prompt, messages, max_tokens=900)

    # ── 生成开场白 ────────────────────────────────────────────────
    def generate_opening(self, state, retrieved_context: str = "") -> str:
        self._active_state = state
        system   = self._build_system()
        messages = [{"role": "user", "content": self._turn_message(_OPENING_PROMPT, state, retrieved_context)}]
        return self._backend.call(system, messages, max_tokens=600)

    # ── 主响应 ────────────────────────────────────────────────────
    def respond(self, user_input: str, retrieved_context: str, state) -> str:
        self._active_state = state
        system   = self._build_system()
        messages = state.history_messages()
        messages.append({"role": "user", "content": self._turn_message(user_input, state, retrieved_context)})
        return self._backend.call(system, messages, max_tokens=800)

    def respond_stream(self, user_input: str, retrieved_context: str, state) -> Iterator[str]:
        self._active_state = state
        system   = self._build_system()
        messages = state.history_messages()
        messages.append({"role": "user", "content": self._turn_message(user_input, state, retrieved_context)})
        yield from self._backend.stream(system, messages, max_tokens=800)

    # ── 主响应（带 MCP 工具循环） ─────────────────────────────────
    def respond_stream_with_tools(
        self,
        user_input: str,
        retrieved_context: str,
        state,
        tools: list[dict[str, Any]] | None = None,
        max_iterations: int = 3,
        max_tokens: int = 800,
        tool_call_router: Any = None,
    ) -> Iterator[dict[str, Any]]:
        """带 MCP 工具循环的流式响应。

        yields 事件字典：
          - {"type": "text", "text": "..."}
          - {"type": "tool_call", "server_id":..., "tool":..., "arguments":...}
          - {"type": "tool_result", "ok": bool, "result":..., "error":...}
          - {"type": "tool_error", "error": "..."}（解析失败）

        没有 tools 时退化成普通流式输出。

        task 66：backend 支持 native tool_use（Anthropic）时走 native 路径，
        否则用文本 marker 兜底。
        """
        if not tools:
            for chunk in self.respond_stream(user_input, retrieved_context, state):
                yield {"type": "text", "text": chunk}
            return

        # task 66：native tool_use 分支
        if getattr(self._backend, "supports_native_tools", False):
            yield from self._respond_stream_native_tools(
                user_input, retrieved_context, state, tools,
                max_iterations, max_tokens, tool_call_router=tool_call_router,
            )
            return

        self._active_state = state
        system = self._build_system() + _format_tools_for_prompt(tools)
        messages = state.history_messages()
        messages.append({
            "role": "user",
            "content": self._turn_message(user_input, state, retrieved_context),
        })

        START = "<<TOOL_CALL>>"
        END = "<<END_TOOL_CALL>>"
        tail_keep = max(len(START), len(END)) - 1
        accumulated_text = ""  # 用于把 assistant 回合拼回 messages

        for iteration in range(max_iterations):
            buffer = ""
            in_tool = False
            tool_invoked = False
            for chunk in self._backend.stream(system, messages, max_tokens=max_tokens):
                buffer += chunk
                while True:
                    if not in_tool:
                        start_idx = buffer.find(START)
                        if start_idx < 0:
                            # 没看到 start，但保留尾部 tail_keep 字符以防 marker 被切断
                            if len(buffer) > tail_keep:
                                emit = buffer[:-tail_keep]
                                buffer = buffer[-tail_keep:]
                                if emit:
                                    accumulated_text += emit
                                    yield {"type": "text", "text": emit}
                            break
                        # 看到 start：把前面的正文吐出
                        pre = buffer[:start_idx]
                        if pre:
                            accumulated_text += pre
                            yield {"type": "text", "text": pre}
                        buffer = buffer[start_idx + len(START):]
                        in_tool = True
                        continue
                    # in_tool: 找 END
                    end_idx = buffer.find(END)
                    if end_idx < 0:
                        # 等更多 chunk
                        break
                    tool_json_raw = buffer[:end_idx]
                    buffer = buffer[end_idx + len(END):]
                    in_tool = False
                    tool_invoked = True
                    try:
                        tool_data = json.loads(tool_json_raw.strip())
                        server_id = str(tool_data.get("server_id", ""))
                        tool_name = str(tool_data.get("tool", ""))
                        arguments = tool_data.get("arguments") or {}
                        if not isinstance(arguments, dict):
                            arguments = {}
                    except Exception as exc:
                        yield {
                            "type": "tool_error",
                            "error": f"工具调用 JSON 解析失败: {exc}",
                            "raw": tool_json_raw[:200],
                        }
                        # 失败也插回一条 user 消息，让 GM 自纠
                        messages.append({"role": "assistant", "content": accumulated_text + START + tool_json_raw + END})
                        messages.append({"role": "user", "content": "【系统】上一条工具调用 JSON 解析失败，请重新生成或放弃工具调用。"})
                        accumulated_text = ""
                        break
                    yield {
                        "type": "tool_call",
                        "server_id": server_id,
                        "tool": tool_name,
                        "arguments": arguments,
                    }
                    try:
                        from mcp_broker import call_tool as _mcp_call_tool
                        result = _mcp_call_tool(server_id, tool_name, arguments)
                    except Exception as exc:
                        result = {"ok": False, "error": f"call_tool 异常: {exc}"}
                    yield {
                        "type": "tool_result",
                        "ok": bool(result.get("ok")),
                        "result": result.get("result"),
                        "error": result.get("error"),
                    }
                    # 把"前缀正文 + 工具调用块"作为 assistant 写回，工具结果作为 user 写回，准备续生成
                    assistant_msg = accumulated_text + START + tool_json_raw + END
                    messages.append({"role": "assistant", "content": assistant_msg})
                    truncated_result = json.dumps(result, ensure_ascii=False)[:2000]
                    messages.append({
                        "role": "user",
                        "content": (
                            f"【工具结果：{server_id}/{tool_name}】\n{truncated_result}\n\n"
                            f"请基于工具结果继续本轮回应（不要重复正文，可继续描写或追加状态标签）。"
                        ),
                    })
                    accumulated_text = ""
                    break  # 跳出 while，开始下一轮 backend.stream
                if tool_invoked:
                    break
            if not tool_invoked:
                # task 61：在 fall-through 前先检查"开了 TOOL_CALL 但没收到 END"
                # 这是 LLM 输出 <<TOOL_CALL>>{...} 但忘 <<END_TOOL_CALL>> 的常见错误。
                # 之前的 buffer（含未闭合的 JSON 片段）会被当成 text 吐给用户，
                # LLM 完全不知道工具没真调用 → 以为"成功"继续叙事 → 状态彻底乱。
                if in_tool:
                    yield {
                        "type": "tool_error",
                        "error": "工具调用未闭合：找到 <<TOOL_CALL>> 但流结束前没有 <<END_TOOL_CALL>>。重新生成时请把 marker 写完整。",
                        "raw": buffer[:200],
                    }
                    # 把不完整片段塞回 messages，告诉模型重试
                    messages.append({"role": "assistant", "content": accumulated_text + START + buffer})
                    messages.append({
                        "role": "user",
                        "content": (
                            "【系统】上一条工具调用未闭合（缺 <<END_TOOL_CALL>> 结束 marker）。"
                            "请重新输出完整的 <<TOOL_CALL>>{\"server_id\":\"...\",\"tool\":\"...\",\"arguments\":{...}}<<END_TOOL_CALL>>，"
                            "或放弃工具调用直接续写叙事。"
                        ),
                    })
                    accumulated_text = ""
                    continue  # 进下一轮 iteration 让 LLM 重试
                # 正常 fall-through：buffer 直接吐出（无任何 marker）
                if buffer:
                    yield {"type": "text", "text": buffer}
                return
        # 达到 max_iterations：给个收尾提示
        yield {"type": "text", "text": "\n\n【已达工具调用次数上限，本轮终止】"}

    # ── 主响应（带 MCP 工具循环 · native tool_use 路径） ───────────
    def _respond_stream_native_tools(
        self,
        user_input: str,
        retrieved_context: str,
        state,
        tools: list[dict[str, Any]],
        max_iterations: int,
        max_tokens: int,
        tool_call_router: Any = None,
    ) -> Iterator[dict[str, Any]]:
        """task 66/70/71：用 backend 的 native tool_use / function calling API
        跑 MCP 循环。

        优点（相对 text marker 协议）：
        - input_schema 由 SDK/provider 校验，错误率降 5-10×
        - 不需要在正文里塞 <<TOOL_CALL>> marker → 节省 tokens
        - 不会有"marker 被切断 / 未闭合"问题（task 61 修的就是这类）

        每个 backend 拥有自己的 stream_with_mcp_loop()，封装：
        - MCP tool 列表 → 该 provider 原生格式
        - 流式 event 解析（content blocks / parts / delta.tool_calls 各家不同）
        - assistant + tool_result 消息装回历史的 provider-specific 格式
        本方法只是 dispatcher。
        """
        self._active_state = state
        system = self._build_system()
        messages = state.history_messages()
        messages.append({
            "role": "user",
            "content": self._turn_message(user_input, state, retrieved_context),
        })
        # 没有 tools 时退化
        if not tools:
            for chunk in self._backend.stream(system, messages, max_tokens=max_tokens):
                yield {"type": "text", "text": chunk}
            return
        # task 87 Phase 5: tool_call_router 默认是 mcp_broker.call_tool,
        # 但 chat handler 可以传入 unified router (识别 dispatcher 工具 + MCP 工具)
        if tool_call_router is None:
            from mcp_broker import call_tool as _mcp_call_tool
            tool_call_router = _mcp_call_tool
        yield from self._backend.stream_with_mcp_loop(
            system, messages, tools, max_iterations, max_tokens, mcp_call=tool_call_router,
        )
