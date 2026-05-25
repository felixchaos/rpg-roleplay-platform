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
_SYSTEM_BASE = """\
你是一个沉浸式文字RPG的GM（游戏主持人）。
你正在主持的故事，背景取自长篇小说《我蕾穆丽娜不爱你》。

【世界背景】
{world_brief}

【当前柏林局势】
{berlin_brief}

【GM行为准则】
- 你用小说的笔法描写场景、人物动作和对话，不是游戏系统提示风格。
- NPC 的台词和行动，严格遵循人物性格和当前处境——不随意改变立场。
- 保持信息不对称：玩家只能获得他们角色在场景中能感知到的信息。
- 不主动向玩家剧透未来情节，但可以让 NPC 给出暗示性的线索。
- 描写时间节奏感：安静场景不催，紧张场景不拖。
- 每次回应控制在 150-400 字之间，保留悬念，把选择权留给玩家。
- 玩家的角色是故事参与者，不是全知视角——尊重这个边界。
- 用中文写作，风格贴近原著：克制、精确、有信息量。
- 每轮最后一条用户消息可能包含【当前剧情状态】与【本轮上下文包】；这不是玩家台词，而是系统整理出的动态上下文，必须优先遵守。
- 你是主 GM 代理：每轮按“读取子代理决议 -> 裁定世界反应 -> 输出正文 -> 输出结构化标签”的顺序工作。
	- 【子代理上下文决议】是另一个大模型请求给你的上下文选择结果；你必须遵守其中的时间线目标、必含事实和风险标记，但不要把它的内部理由直接写给玩家。
	- 玩家使用 /set 开头时，表示用户显式要求改写设定。你必须把它当作最高优先级硬约束，可据此修改时间线、地点、世界观、设定集、人设和支持写回的变量；不要用旧的 locked 时间线拒绝。
- 若本轮导致剧情状态变化，请在正文末尾追加少量结构化输出，供系统更新存档。
  **推荐使用 JSON 协议**（更精确、解析失败率低）：

      ```json
      [
        {"op": "set",      "path": "player.current_location", "value": "北港·灯塔下"},
        {"op": "set",      "path": "world.time",              "value": "申时三刻"},
        {"op": "append",   "path": "memory.resources",        "value": "黄铜怀表"},
        {"op": "set",      "path": "relationships.阿衡",       "value": "信任"},
        {"op": "set",      "path": "memory.main_quest",       "value": "营救沈知微"},
        {"op": "question", "question": "是否进入灯塔？", "options": ["进入", "退后观察"]}
      ]
      ```

  op 可选：set / append / overwrite / question。path 必须是字符串。
  数组里只放真正发生的变化，不要为没变化的项编造条目。
  **重要**：仅在你输出 ```json fence 时这段才会被当作指令；纯叙事内容（包括用【】做的强调）不会触发状态写入。

  仍兼容传统【...】协议（向后兼容；JSON 解析失败时尤其有用）：
  【当前位置：地点】 【当前时间线：时间/日期/阶段】 【当前目标：目标】
  【主线任务更新：任务名】 【当前可支配资源：资源1、资源2】 【获得新能力：能力】
  【关系：角色：关系状态】 【记忆：需要长期记住的事实】 【用户变量：变量名=变量值】
  【状态写入：path=value】 【状态追加：path=value】 【询问玩家：问题｜选项：A、B、C】
  【设定校验：通过】 【设定冲突：原因】 【世界线推演：...】 【时间跳跃确认：...】 【时间跳跃拒绝：...】
			- 不要为没有变化的项目编造标签。
				- 用户变量是硬约束。涉及世界线推演、时间跳跃、角色状态、资源变化时，必须先满足用户变量；/set 写入的变量优先级最高。
				- 你可以在权限允许时用结构化标签修改 UI 内的状态变量。permissions.* 和 history.* 是硬黑名单：任何写入（包括 /set、状态写入、状态追加）都会被拒绝并写入 audit_log，永远不要尝试。权限模式由用户在界面切换。
					- 如果玩家要求跳过时间、快进、切换到几天后/第二天/某个阶段，必须先处理时间线事务：接受就写出合理过渡并输出【时间跳跃确认：...】和【当前时间线：...】；只有目标不可解析时才询问玩家。
		- 只要上下文包里出现“玩家请求时间跳跃”，本轮不得含混带过；必须确认或拒绝，不要让场景在未锁定时间线上漂移。
		- 当你需要玩家选择下一步行动计划、设定取舍、分支方向或多种可行方案时，必须用【询问玩家：...】提出问题；这类问题不受“完全访问权限”跳过。
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
- 时间：图卢兹失守后翌日，柏林的傍晚
- 地点与氛围：柏林街头，战报传来后的压抑气氛，有人在悄悄收拾行李离开
- 让玩家感受到这座城市的状态，以及他们角色的处境
- 结尾留一个可以行动的悬念或选择，不要替玩家做决定

字数：150-250字
"""


# ══════════════════════════════════════════════════════════════════════════════
#  MCP 工具循环辅助
# ══════════════════════════════════════════════════════════════════════════════
def _format_tools_for_prompt(tools: list[dict[str, Any]]) -> str:
    """把 MCP 工具清单格式化成附加 system prompt 片段。

    工具调用协议（model-agnostic）：
      <<TOOL_CALL>>{"server_id":"...","tool":"...","arguments":{...}}<<END_TOOL_CALL>>
    模型输出该标记后应立即停止生成；系统侦测到完整 marker 即调用 MCP 并把结果注回。
    """
    if not tools:
        return ""
    lines = ["", "【可用 MCP 工具】"]
    lines.append("- 仅在玩家明确请求外部行动（查询资料、调用脚本、读写文件等）时调用工具。")
    lines.append("- 调用格式（一行紧凑 JSON）：")
    lines.append('  <<TOOL_CALL>>{"server_id":"<id>","tool":"<name>","arguments":{...}}<<END_TOOL_CALL>>')
    lines.append("- 写出 <<END_TOOL_CALL>> 后立即停止本轮输出，等系统注入工具结果再续写。")
    lines.append("- 工具结果会以「【工具结果：server/name】」消息形式回灌，请根据结果继续叙事。")
    lines.append("- 工具清单：")
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


# ══════════════════════════════════════════════════════════════════════════════
#  后端：OpenAI 兼容（OpenAI / OpenRouter / 硅基 / 阿里 / 腾讯 / 火山 / 小米 ...）
# ══════════════════════════════════════════════════════════════════════════════
class _OpenAICompatBackend:
    """适配所有 OpenAI 兼容的 provider，只需要 base_url + env_key + model 名。"""

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
        world_brief = (
            f"{_WORLD['setting']}\n"
            f"当前局势：{_WORLD['current_situation']}"
        )
        berlin = _WORLD["current_berlin"]
        berlin_brief = (
            f"氛围：{berlin['atmosphere']}\n"
            f"风险等级：{berlin['risk_level']}\n"
            f"在场势力：\n" + "\n".join(f"  · {p}" for p in berlin["power_presence"])
        )
        return _SYSTEM_BASE.format(
            world_brief=world_brief,
            berlin_brief=berlin_brief,
        )

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
        """Run the model-backed context sub-agent before the main GM call."""
        messages = [{"role": "user", "content": task_prompt}]
        return self._backend.call_structured(agent_prompt, messages, max_tokens=900)

    # ── 生成开场白 ────────────────────────────────────────────────
    def generate_opening(self, state, retrieved_context: str = "") -> str:
        system   = self._build_system()
        messages = [{"role": "user", "content": self._turn_message(_OPENING_PROMPT, state, retrieved_context)}]
        return self._backend.call(system, messages, max_tokens=600)

    # ── 主响应 ────────────────────────────────────────────────────
    def respond(self, user_input: str, retrieved_context: str, state) -> str:
        system   = self._build_system()
        messages = state.history_messages()
        messages.append({"role": "user", "content": self._turn_message(user_input, state, retrieved_context)})
        return self._backend.call(system, messages, max_tokens=800)

    def respond_stream(self, user_input: str, retrieved_context: str, state) -> Iterator[str]:
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
    ) -> Iterator[dict[str, Any]]:
        """带 MCP 工具循环的流式响应。

        yields 事件字典：
          - {"type": "text", "text": "..."}
          - {"type": "tool_call", "server_id":..., "tool":..., "arguments":...}
          - {"type": "tool_result", "ok": bool, "result":..., "error":...}
          - {"type": "tool_error", "error": "..."}（解析失败）

        没有 tools 时退化成普通流式输出。
        """
        if not tools:
            for chunk in self.respond_stream(user_input, retrieved_context, state):
                yield {"type": "text", "text": chunk}
            return

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
                # 这轮没工具调用：尾部 buffer 直接吐出，循环结束
                if buffer:
                    yield {"type": "text", "text": buffer}
                return
        # 达到 max_iterations：给个收尾提示
        yield {"type": "text", "text": "\n\n【已达工具调用次数上限，本轮终止】"}
