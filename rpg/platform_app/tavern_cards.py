"""
tavern_cards.py — SillyTavern V1/V2 角色卡 import/export 兼容

支持：
- 导入 V1 (扁平 JSON) 和 V2 (spec_v2 + data 三层) 格式
- 导入 PNG 嵌入卡：解析 tEXt chunk 的 "chara" 关键字（V2 也用 "ccv3" / "chara"）
- 导出本人 PC 卡 / persona 为 V2 JSON(v28: 均落 character_cards 表,card_type 区分)

字段映射（V2 data → character_cards card_type='pc'）：
  name              → name
  description       → 结构化拆分到 identity/background/appearance/personality/
                      speech_style/current_status/secrets（缩进大纲 / 扁平 colon / W++）;
                      原文始终留存 metadata.tavern_raw_description。
                      拆不开的自由文本可经 ai_split opt-in 用 LLM 兜底（apply_llm_structure，
                      走平台统一 usage 管理）。
  personality       → personality（与 description 拆出的性格合并）
  scenario          → metadata.scenario
  first_mes         → metadata.first_mes
  mes_example       → 取首段对话进 sample_dialogue[0]
  creator_notes     → metadata.creator_notes（不入 prompt）
  system_prompt     → metadata.system_prompt
  alternate_greetings → metadata.alternate_greetings
  tags              → tags
  creator           → metadata.creator
  character_version → metadata.character_version
  extensions        → metadata.extensions
  character_book    → metadata.character_book（保留原结构，后续可接入世界书表）
"""
from __future__ import annotations

import base64
import binascii
import json
import re
import struct
import zlib
from typing import Any

# 标签 → 字段映射。同时覆盖两类 label：
#   · 叶子标签（W++ 的 Age/Occupation、扁平 colon 的「身份/外貌」）
#   · 段落标题（中文人设模板的「基本信息/背景故事/家庭背景/NSFW」等大段落头）
# 段落标题命中时，整段（含缩进子字段）归属该字段，避免「全堆进 identity」。
_LABEL_ALIASES: dict[str, tuple[str, ...]] = {
    "identity": (
        # 叶子
        "identity", "role", "occupation", "job", "class", "race", "species", "gender",
        "age", "身份", "身份设定", "职业", "职位", "种族", "物种", "性别", "年龄", "定位",
        # 段落标题
        "基本信息", "基础信息", "基本资料", "基础资料", "基本資料", "角色信息", "人物信息",
        "人物资料", "角色资料", "个人信息", "基本设定", "profile", "basic info", "basics",
        "basic information",
    ),
    "background": (
        "background", "backstory", "history", "lore", "past", "origin", "relationship",
        "relationships", "family", "goal", "goals",
        "背景", "经历", "过去", "来历", "身世", "关系", "设定",
        "背景故事", "家庭", "家庭背景", "家世", "家族", "社交关系", "人际关系", "社会关系",
        "关系网", "社会地位", "地位", "身份地位", "人生目标", "目标", "理想", "抱负",
        "能力技能", "技能", "能力", "特长", "专长", "经历背景",
    ),
    "appearance": (
        "appearance", "looks", "look", "body", "clothing", "outfit", "features",
        "外貌", "外观", "长相", "体型", "衣着", "服装", "特征",
        "形象", "容貌", "身材", "衣着风格", "着装", "穿着", "服饰", "外形",
    ),
    "personality": (
        "personality", "mind", "traits", "temperament", "likes", "dislikes", "quirks",
        "性格", "人格", "个性", "喜好", "厌恶", "特点",
        "性格特点", "性格特征", "情绪", "情绪表现", "喜好厌恶", "好恶", "工作行为", "行为",
        "行为习惯", "生活习惯", "习惯", "缺点弱点", "缺点", "弱点", "优点", "爱好",
        # NSFW 偏好/硬限归 personality:GM 可见,才能尊重取向并避开禁忌底线。
        # 不放 secrets——secrets 被硬隔离、绝不进 GM 上下文(见 context_engine 安全边界)。
        "nsfw", "性相关特征", "性相关", "性癖", "性癖好", "性设定", "性偏好",
        "禁忌底线", "禁忌",
    ),
    "speech_style": (
        "speech", "speaking style", "speech style", "dialogue style", "voice", "tone",
        "口癖", "说话方式", "语气", "语调", "台词风格", "谈吐", "言谈",
    ),
    "current_status": (
        "current status", "status", "state", "situation", "当前状态", "状态", "处境",
        "现状", "近况", "目前状态",
    ),
    "secrets": (
        # 仅"玩家知道但 GM/NPC 不应知道"的剧情秘密。NSFW 偏好不归这里(见 personality)。
        "secret", "secrets", "hidden", "private", "秘密", "隐秘", "隐藏设定", "私密",
    ),
}

# 未识别的段落标题，整段归入该字段（lore 兜底），绝不污染 identity/appearance。
_DEFAULT_SECTION_FIELD = "background"


def _label_field(label: str) -> str | None:
    normalized = re.sub(r"[\s_\-:：]+", " ", str(label or "").strip().lower()).strip()
    compact = normalized.replace(" ", "")
    for field, aliases in _LABEL_ALIASES.items():
        for alias in aliases:
            alias_norm = re.sub(r"[\s_\-:：]+", " ", alias.lower()).strip()
            if normalized == alias_norm or compact == alias_norm.replace(" ", ""):
                return field
    return None


def _append_section(out: dict[str, list[str]], field: str, label: str, value: str) -> None:
    text = str(value or "").strip()
    if not text:
        return
    clean_label = re.sub(r"\s+", " ", str(label or "").strip())
    entry = f"{clean_label}: {text}" if clean_label else text
    if entry not in out[field]:
        out[field].append(entry)


# 缩进式大纲 / 扁平 colon 通用的 label 行：可选缩进 + 可选列表符 + label + 冒号 + 值
_OUTLINE_LABEL_RE = re.compile(r"^([ \t]*)(?:[-*]\s*)?([^:：\n]{1,40})[:：][ \t]*(.*)$")


def _split_outline_sections(text: str) -> list[tuple[str, str]]:
    """把缩进式大纲（或扁平 label 列表）切成「顶层 (label, 正文)」。

    顶层标题的正文包含其行内值 + 所有更深缩进的子行，因此嵌套子字段会粘在
    各自段落上，而不会泄漏进下一个被识别的 label——这正是「全堆进 identity」
    bug 的根因修复。
    """
    rows: list[tuple[int | None, str | None, str]] = []  # (indent, label, text)
    for line in str(text or "").splitlines():
        if not line.strip():
            rows.append((None, None, ""))  # 空行标记，保留段内分段
            continue
        m = _OUTLINE_LABEL_RE.match(line)
        indent = len(line) - len(line.lstrip(" \t"))
        if m:
            rows.append((indent, m.group(2).strip(), m.group(3).strip()))
        else:
            rows.append((indent, None, line.strip()))

    label_indents = [r[0] for r in rows if r[1] is not None and r[0] is not None]
    if not label_indents:
        return []
    top = min(label_indents)

    sections: list[tuple[str, str]] = []
    cur_label: str | None = None
    cur_lines: list[str] = []

    def _flush() -> None:
        if cur_label is not None:
            sections.append((cur_label, "\n".join(cur_lines).strip()))

    for indent, label, txt in rows:
        is_top = label is not None and indent is not None and indent <= top
        if is_top:
            _flush()
            cur_label = label
            cur_lines = [txt] if txt else []
        elif cur_label is not None:
            if label is not None:
                cur_lines.append(f"{label}: {txt}" if txt else f"{label}:")
            elif txt:
                cur_lines.append(txt)
            else:
                cur_lines.append("")  # 段内空行，维持可读分段
    _flush()
    return sections


def _extract_outline(text: str) -> dict[str, str]:
    """缩进大纲 / 扁平 colon 风格 → 我方字段。

    顶层段落标题命中字段则整段归属；未识别段落整段折进 background（lore 兜底），
    不污染其它字段。仅当至少 2 个顶层段落命中已知字段时才认为「结构化成功」。
    """
    sections = _split_outline_sections(text)
    if not sections:
        return {}

    buckets: dict[str, list[tuple[str, str]]] = {field: [] for field in _LABEL_ALIASES}
    mapped = 0
    unknown: list[tuple[str, str]] = []
    for label, content in sections:
        if not label:
            continue
        field = _label_field(label)
        if field:
            mapped += 1
            buckets[field].append((label, content))
        else:
            unknown.append((label, content))

    if mapped < 2:
        return {}

    # 未识别段落整段折进 background，绝不污染 identity/appearance/personality。
    for label, content in unknown:
        if content:
            buckets[_DEFAULT_SECTION_FIELD].append((label, content))

    out: dict[str, str] = {}
    for field, items in buckets.items():
        items = [(lab, body) for lab, body in items if (body or lab)]
        if not items:
            continue
        if len(items) == 1:
            lab, body = items[0]
            out[field] = (body or lab).strip()
        else:
            # 一个字段聚合多个段落 → 保留段落标题作小标题，便于阅读
            blocks = [f"{lab}:\n{body}" if body else f"{lab}" for lab, body in items]
            out[field] = "\n\n".join(blocks).strip()
    return out


def _extract_structured_description(description: str) -> dict[str, str]:
    """Split dense SillyTavern/W++ description text into our card fields.

    Many Tavern cards put all profile details into data.description, so a direct
    description -> identity mapping leaves users with one huge field. This keeps
    ordinary prose untouched and only splits when at least two known labels are
    found.
    """
    text = str(description or "").strip()
    if not text:
        return {}

    buckets: dict[str, list[str]] = {field: [] for field in _LABEL_ALIASES}

    # W++ style: Personality("..."), Appearance("..."), Background("...").
    pair_re = re.compile(
        r"([\w\u4e00-\u9fff][\w\u4e00-\u9fff\s/&.+-]{0,40})\s*\(\s*[\"“](.*?)[\"”]\s*\)",
        re.S,
    )
    matched = 0
    for label, value in pair_re.findall(text):
        field = _label_field(label)
        if not field:
            continue
        matched += 1
        _append_section(buckets, field, label, value)

    # W++ 命中 ≥2 → 用 W++ 结果。
    if matched >= 2:
        return {
            field: "\n".join(parts).strip()
            for field, parts in buckets.items()
            if parts
        }

    # 否则按缩进大纲 / 扁平 colon 拆分（覆盖中文人设模板的大段落结构）。
    return _extract_outline(text)


def _join_text(*parts: str, limit: int) -> str:
    seen: set[str] = set()
    out: list[str] = []
    for part in parts:
        text = str(part or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text)
    return "\n\n".join(out)[:limit]


# ── 解析 ──────────────────────────────────────────────────────────────
def parse_card(data: dict[str, Any] | str | bytes) -> dict[str, Any]:
    """统一入口：吃 dict / JSON 字符串 / base64 字符串，返回 V2 形态 dict。"""
    if isinstance(data, (bytes, bytearray)):
        text = data.decode("utf-8", errors="replace")
        return parse_card(text)
    if isinstance(data, str):
        # 可能是裸 JSON 或 base64
        stripped = data.strip()
        if stripped.startswith("{"):
            return parse_card(json.loads(stripped))
        try:
            decoded = base64.b64decode(stripped, validate=True).decode("utf-8")
            return parse_card(json.loads(decoded))
        except (binascii.Error, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ValueError(f"无法解析角色卡：既不是 JSON 也不是 base64({exc})") from exc
    if not isinstance(data, dict):
        raise ValueError(f"不支持的角色卡类型：{type(data)}")
    # 是 V2 还是 V1？
    if data.get("spec") == "chara_card_v2" or data.get("spec") == "chara_card_v3":
        return _normalize_v2(data)
    return _v1_to_v2(data)


def _normalize_v2(card: dict[str, Any]) -> dict[str, Any]:
    """确保 V2 结构完整，补缺失字段。"""
    d = dict(card.get("data") or {})
    out = {
        "spec": card.get("spec") or "chara_card_v2",
        "spec_version": card.get("spec_version") or "2.0",
        "data": {
            "name": str(d.get("name") or "").strip(),
            "description": str(d.get("description") or ""),
            "personality": str(d.get("personality") or ""),
            "scenario": str(d.get("scenario") or ""),
            "first_mes": str(d.get("first_mes") or ""),
            "mes_example": str(d.get("mes_example") or ""),
            "creator_notes": str(d.get("creator_notes") or ""),
            "system_prompt": str(d.get("system_prompt") or ""),
            "post_history_instructions": str(d.get("post_history_instructions") or ""),
            "alternate_greetings": list(d.get("alternate_greetings") or []),
            "tags": list(d.get("tags") or []),
            "creator": str(d.get("creator") or ""),
            "character_version": str(d.get("character_version") or ""),
            "extensions": dict(d.get("extensions") or {}),
            "character_book": d.get("character_book"),
        },
    }
    if not out["data"]["name"]:
        raise ValueError("角色卡缺少 name")
    return out


def _v1_to_v2(card: dict[str, Any]) -> dict[str, Any]:
    """V1 扁平 → V2 标准化。"""
    name = (card.get("name") or card.get("char_name") or "").strip()
    if not name:
        raise ValueError("V1 角色卡缺少 name")
    return _normalize_v2({
        "spec": "chara_card_v1",
        "spec_version": "1.0",
        "data": {
            "name": name,
            "description": card.get("description", "") or card.get("char_persona", ""),
            "personality": card.get("personality", ""),
            "scenario": card.get("scenario", "") or card.get("world_scenario", ""),
            "first_mes": card.get("first_mes", "") or card.get("char_greeting", ""),
            "mes_example": card.get("mes_example", "") or card.get("example_dialogue", ""),
            "creator": card.get("creator", ""),
            "character_version": card.get("character_version", "1.0"),
            "tags": card.get("tags", []) or [],
        },
    })


# ── PNG tEXt chunk 解析 ──────────────────────────────────────────────
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"

# 安全上限：zlib 解压后单个 chunk 最多 4MB；总 chunk 长度 8MB；防止压缩炸弹 OOM。
_MAX_PNG_BYTES = 10 * 1024 * 1024
_MAX_ZTXT_DECOMPRESSED = 4 * 1024 * 1024
_MAX_CHUNK_LENGTH = 8 * 1024 * 1024


def _safe_zlib_decompress(compressed: bytes, max_size: int) -> bytes:
    """流式解压并在累计字节超限时立刻终止，防止 zlib 炸弹。"""
    decomp = zlib.decompressobj()
    out = bytearray()
    # 切块喂入，每喂一段就检查累计大小
    chunk_size = 65536
    pos = 0
    while pos < len(compressed):
        out += decomp.decompress(compressed[pos:pos + chunk_size], max_size - len(out))
        if len(out) >= max_size and decomp.unconsumed_tail:
            raise ValueError(f"zTXt 解压超过上限 {max_size} 字节（疑似 zlib 炸弹）")
        pos += chunk_size
    out += decomp.flush()
    if len(out) > max_size:
        raise ValueError(f"zTXt 解压超过上限 {max_size} 字节")
    return bytes(out)


def parse_png_card(blob: bytes) -> dict[str, Any]:
    """从 PNG 文件读 tEXt/zTXt chunk 中的 chara 数据。

    硬限：blob ≤ 10MB；单 chunk length ≤ 8MB；zTXt 解压后 ≤ 4MB。
    超限直接 ValueError，避免 worker OOM。
    """
    if not blob.startswith(PNG_SIGNATURE):
        raise ValueError("不是合法 PNG 文件")
    if len(blob) > _MAX_PNG_BYTES:
        raise ValueError(f"PNG 文件过大（最大 {_MAX_PNG_BYTES // (1024*1024)}MB）")
    offset = 8
    text_chunks: dict[str, str] = {}
    while offset < len(blob):
        if offset + 8 > len(blob):
            break
        length = struct.unpack(">I", blob[offset:offset + 4])[0]
        if length > _MAX_CHUNK_LENGTH:
            raise ValueError(f"PNG chunk 长度超过上限 {_MAX_CHUNK_LENGTH}")
        chunk_type = blob[offset + 4:offset + 8].decode("ascii", errors="replace")
        body = blob[offset + 8:offset + 8 + length]
        offset += 12 + length  # 4 type + length + 4 CRC
        if chunk_type == "IEND":
            break
        if chunk_type in ("tEXt", "zTXt"):
            try:
                if chunk_type == "tEXt":
                    key, _, value = body.partition(b"\x00")
                    text_chunks[key.decode("latin-1")] = value.decode("utf-8", errors="replace")
                else:  # zTXt：压缩文本
                    key, _, rest = body.partition(b"\x00")
                    # rest[0] 是 compression method（0=deflate），rest[1:] 是压缩数据
                    compressed = rest[1:] if len(rest) > 1 else b""
                    raw = _safe_zlib_decompress(compressed, _MAX_ZTXT_DECOMPRESSED)
                    text_chunks[key.decode("latin-1")] = raw.decode("utf-8", errors="replace")
            except ValueError:
                # 解压炸弹/异常长度等：上抛让调用方拒绝整个文件
                raise
            except Exception:
                continue
    # SillyTavern 通常用 key="chara" 或 "ccv3"
    for search_key in ("ccv3", "chara"):
        if search_key in text_chunks:
            return parse_card(text_chunks[search_key])
    raise ValueError("PNG 不包含 chara/ccv3 tEXt chunk")


# ── 映射到我方 PC 卡(character_cards, card_type='pc')───────────────
def tavern_to_user_card(card_v2: dict[str, Any]) -> dict[str, Any]:
    """V2 → user_cards.upsert_user_card() 的 payload(v28: 落 character_cards 表 card_type='pc')。"""
    d = card_v2["data"]
    raw_description = d.get("description", "")
    structured = _extract_structured_description(raw_description)
    # mes_example 切第一条对话作为 sample_dialogue
    samples: list[str] = []
    for chunk in re.split(r"<START>|---", d.get("mes_example", "")):
        chunk = chunk.strip()
        if not chunk:
            continue
        # 提取 {{char}}: 后的内容
        for line in chunk.splitlines():
            line = line.strip()
            if not line:
                continue
            m = re.match(r"\{\{char\}\}:\s*(.+)", line)
            if m:
                samples.append(m.group(1).strip())
                if len(samples) >= 4:
                    break
        if samples:
            break

    return {
        "name": d["name"],
        "identity": (structured.get("identity") if structured else raw_description)[:2000],
        "background": structured.get("background", "")[:2000],
        "appearance": structured.get("appearance", "")[:2000],
        "personality": _join_text(d.get("personality", ""), structured.get("personality", ""), limit=1500),
        "speech_style": structured.get("speech_style", "")[:1500],
        "current_status": structured.get("current_status", "")[:1500],
        "secrets": structured.get("secrets", "")[:1500],
        "sample_dialogue": samples,
        "tags": d.get("tags") or [],
        "metadata": {
            "tavern_imported": True,
            "tavern_structured_description": bool(structured),
            # 始终保留原文:供「AI 整理字段」兜底 / 用户对照核查 / 重新解析。
            "tavern_raw_description": raw_description[:8000],
            "scenario": d.get("scenario", ""),
            "first_mes": d.get("first_mes", ""),
            "alternate_greetings": d.get("alternate_greetings", []),
            "creator_notes": d.get("creator_notes", ""),
            "system_prompt": d.get("system_prompt", ""),
            "post_history_instructions": d.get("post_history_instructions", ""),
            "creator": d.get("creator", ""),
            "character_version": d.get("character_version", ""),
            "extensions": d.get("extensions") or {},
            "character_book": d.get("character_book"),
            "spec": card_v2.get("spec"),
            "spec_version": card_v2.get("spec_version"),
        },
    }


# ── LLM 兜底:确定性规则拆不开的自由文本 description,挂 LLM 拆字段 ──────
#    仅在调用方显式 opt-in(ai_split)时触发,走平台统一 usage 管理:
#    call_agent_json 在 user_id + agent_kind 下自动 record_usage,不会赊账。
_LLM_SPLIT_FIELDS = (
    "identity", "background", "appearance",
    "personality", "speech_style", "current_status", "secrets",
)


def llm_structure_description(
    raw_description: str,
    user_id: int | None,
    *,
    save_id: int | None = None,
    api_id_override: str | None = None,
    model_override: str | None = None,
) -> dict[str, str]:
    """把自由文本角色档案用 LLM 拆进我方字段。失败 / 无 user / 空文本 → {}。

    只做语义归类与适度精简,不新增设定;走平台便宜模型(默认 gemini-3.5-flash),
    usage 由 call_agent_json 自动入账(agent_kind='card_import')。

    模型解析:override(本次导入选的) > user_preferences['card_import.*'] >
    'agent.*' 通配 > 用户首个可用模型 > 便宜默认 gemini-3.5-flash。
    """
    text = str(raw_description or "").strip()
    if not text or not user_id:
        return {}

    from core.logging import get_logger

    log = get_logger(__name__)
    try:
        from agents._harness import call_agent_json, resolve_api_and_model
    except Exception as exc:  # pragma: no cover - harness 不可用时静默降级
        log.warning(f"[card-import] LLM 拆分不可用: {exc}")
        return {}

    api_id, model = resolve_api_and_model(
        user_id,
        api_pref_key="card_import.api_id",
        model_pref_key="card_import.model_real_name",
        default_api="vertex_ai",
        default_model="gemini-3.5-flash",
        api_id_override=api_id_override or None,
        model_override=model_override or None,
    )
    schema = {
        "name": "emit_card_fields",
        "description": "把角色卡自由文本档案按语义拆进结构化字段",
        "input_schema": {
            "type": "object",
            "properties": {
                "identity": {"type": "string", "description": "身份/职业/年龄/性别等基本定位,简短一两行"},
                "background": {"type": "string", "description": "背景故事/经历/家庭/社交关系/社会地位/人生目标/能力技能"},
                "appearance": {"type": "string", "description": "外貌/体型/衣着风格"},
                "personality": {"type": "string", "description": "性格/情绪/喜好厌恶/习惯/优缺点;以及 NSFW 取向/性癖/禁忌底线等亲密偏好与硬限(GM 需可见才能尊重)"},
                "speech_style": {"type": "string", "description": "说话方式/语气/口癖;没有则空字符串"},
                "current_status": {"type": "string", "description": "当前状态/近况;没有则空字符串"},
                "secrets": {"type": "string", "description": "仅剧情秘密/隐藏设定(玩家知道但 GM/NPC 不应知道的);不要放 NSFW 偏好;没有则空字符串"},
            },
            "required": ["identity", "background", "appearance", "personality"],
        },
    }
    system = (
        "你是角色卡字段整理器。把用户给的角色档案原文,按语义归类拆进给定字段。规则:"
        "1) 只做归类与适度精简,绝不新增或编造任何设定;"
        "2) 原文是什么语言就用什么语言输出;"
        "3) 找不到对应内容的字段填空字符串;"
        "4) 必须且只能通过调用 emit_card_fields 工具输出结果。"
    )
    user = f"角色档案原文:\n\n{text[:8000]}"
    try:
        out, _usage = call_agent_json(
            api_id, model, system, user, user_id,
            tool_schema=schema, max_tokens=2000,
            agent_kind="card_import", save_id=save_id,
        )
        data = json.loads(out)
    except Exception as exc:
        log.warning(f"[card-import] LLM 拆分失败({api_id}/{model}): {exc}")
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        f: str(data.get(f) or "").strip()
        for f in _LLM_SPLIT_FIELDS
        if str(data.get(f) or "").strip()
    }


def apply_llm_structure(
    payload: dict[str, Any],
    user_id: int | None,
    *,
    save_id: int | None = None,
    api_id_override: str | None = None,
    model_override: str | None = None,
) -> tuple[dict[str, Any], bool]:
    """对已确定性解析的 user-card payload 跑 LLM 兜底拆分,非空字段覆盖回 payload。

    同步函数(call_agent_json 同步);异步路由里请用 asyncio.to_thread 包裹。
    api_id_override/model_override:本次导入用户在弹窗里选的模型(为空则跟随配置)。
    返回 (payload, used)。
    """
    md = payload.get("metadata") or {}
    raw = md.get("tavern_raw_description") or payload.get("identity") or ""
    fields = llm_structure_description(
        raw, user_id, save_id=save_id,
        api_id_override=api_id_override, model_override=model_override,
    )
    if not fields:
        return payload, False
    for key, val in fields.items():
        if val:
            payload[key] = val[:2000]
    payload.setdefault("metadata", {})
    payload["metadata"]["llm_structured_description"] = True
    payload["metadata"]["tavern_structured_description"] = True
    return payload, True


# ── 导出:PC 卡(character_cards card_type='pc') → V2 JSON ────────────
def write_png_card(v2_card: dict[str, Any], template_png: bytes | None = None) -> bytes:
    """把 V2 卡 JSON 嵌入 PNG 的 tEXt chara chunk。

    template_png: 可选 PNG 文件作底图；省略则生成一张 1x1 透明 PNG。
    """
    if template_png and template_png.startswith(PNG_SIGNATURE):
        png = template_png
    else:
        # 生成最小 1x1 透明 PNG
        png = _minimal_png()

    json_str = json.dumps(v2_card, ensure_ascii=False)
    chara_b64 = base64.b64encode(json_str.encode("utf-8"))
    chunk_data = b"chara" + b"\x00" + chara_b64
    text_chunk = (
        struct.pack(">I", len(chunk_data))
        + b"tEXt"
        + chunk_data
        + struct.pack(">I", zlib.crc32(b"tEXt" + chunk_data))
    )
    # 插到 IEND chunk 之前
    iend_pos = png.rfind(b"IEND")
    if iend_pos < 4:
        raise ValueError("template_png 没有 IEND chunk")
    # IEND chunk 起点（length 字段在 type 前 4 字节）
    insert_at = iend_pos - 4
    return png[:insert_at] + text_chunk + png[insert_at:]


def _minimal_png() -> bytes:
    """生成 1x1 透明 PNG，作为没传 template 时的默认底。"""
    sig = PNG_SIGNATURE
    # IHDR: 1x1, 8bit, RGBA
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr_data))
    # IDAT: 单像素透明（zlib 压缩 \x00 + 4 字节 RGBA）
    raw = b"\x00\x00\x00\x00\x00"  # filter byte + RGBA
    compressed = zlib.compress(raw)
    idat = struct.pack(">I", len(compressed)) + b"IDAT" + compressed + struct.pack(">I", zlib.crc32(b"IDAT" + compressed))
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", zlib.crc32(b"IEND"))
    return sig + ihdr + idat + iend


def user_card_to_tavern_v2(card: dict[str, Any]) -> dict[str, Any]:
    """反向：本人卡 → V2 JSON 标准格式，可下载给酒馆用。"""
    md = card.get("metadata") or {}
    samples = card.get("sample_dialogue") or []
    # 合成 mes_example（SillyTavern 习惯）
    mes_example = ""
    if samples:
        sample_blocks = []
        for s in samples[:4]:
            sample_blocks.append(f"<START>\n{{{{user}}}}: \n{{{{char}}}}: {s}")
        mes_example = "\n".join(sample_blocks)

    return {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": card.get("name", ""),
            "description": card.get("identity", "") or card.get("appearance", ""),
            "personality": card.get("personality", ""),
            "scenario": md.get("scenario", ""),
            "first_mes": md.get("first_mes", ""),
            "mes_example": md.get("mes_example") or mes_example,
            "creator_notes": md.get("creator_notes", ""),
            "system_prompt": md.get("system_prompt", ""),
            "post_history_instructions": md.get("post_history_instructions", ""),
            "alternate_greetings": md.get("alternate_greetings", []),
            "tags": card.get("tags") or [],
            "creator": md.get("creator", ""),
            "character_version": md.get("character_version", "1.0"),
            "extensions": md.get("extensions") or {},
            "character_book": md.get("character_book"),
        },
    }
