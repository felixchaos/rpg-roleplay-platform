"""
tavern_cards.py — SillyTavern V1/V2 角色卡 import/export 兼容

支持：
- 导入 V1 (扁平 JSON) 和 V2 (spec_v2 + data 三层) 格式
- 导入 PNG 嵌入卡：解析 tEXt chunk 的 "chara" 关键字（V2 也用 "ccv3" / "chara"）
- 导出本人 user_character_cards / user_personas 为 V2 JSON

字段映射（V2 data → user_character_cards）：
  name              → name
  description       → identity
  personality       → personality
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


def parse_png_card(blob: bytes) -> dict[str, Any]:
    """从 PNG 文件读 tEXt/zTXt chunk 中的 chara 数据。"""
    if not blob.startswith(PNG_SIGNATURE):
        raise ValueError("不是合法 PNG 文件")
    offset = 8
    text_chunks: dict[str, str] = {}
    while offset < len(blob):
        if offset + 8 > len(blob):
            break
        length = struct.unpack(">I", blob[offset:offset + 4])[0]
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
                    text_chunks[key.decode("latin-1")] = zlib.decompress(compressed).decode("utf-8", errors="replace")
            except Exception:
                continue
    # SillyTavern 通常用 key="chara" 或 "ccv3"
    for search_key in ("ccv3", "chara"):
        if search_key in text_chunks:
            return parse_card(text_chunks[search_key])
    raise ValueError("PNG 不包含 chara/ccv3 tEXt chunk")


# ── 映射到我方 user_character_cards ──────────────────────────────────
def tavern_to_user_card(card_v2: dict[str, Any]) -> dict[str, Any]:
    """V2 → user_character_cards.upsert_user_card() 的 payload。"""
    d = card_v2["data"]
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
        "identity": d.get("description", "")[:2000],
        "personality": d.get("personality", "")[:1500],
        "speech_style": "",  # tavern 没单独字段，留空
        "current_status": "",
        "secrets": "",
        "sample_dialogue": samples,
        "tags": d.get("tags") or [],
        "metadata": {
            "tavern_imported": True,
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


# ── 导出：user_character_cards → V2 JSON ─────────────────────────────
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
