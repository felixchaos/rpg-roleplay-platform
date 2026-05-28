"""agents.gm.backends.vertex — Vertex AI (Gemini) backend."""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

BASE = Path(__file__).parent.parent.parent.parent  # rpg/agents/gm/backends/ → rpg/
SA_FILE = BASE / "vertex_sa.json"


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

        for _iteration in range(max_iterations):
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
        yield {"type": "text", "text": "\n\n【已达本轮工具调用上限 (限制为本次回复内的调用次数,下一条消息自动重置),本轮终止】"}

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
