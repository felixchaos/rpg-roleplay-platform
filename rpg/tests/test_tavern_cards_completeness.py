"""
tests/test_tavern_cards_completeness.py — 角色卡 V2 spec 完整性 + 往返测试

纯单元测试，不依赖 DB / LLM。
"""
import base64
import json
import sys
import struct
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from platform_app.tavern_cards import (
    parse_card,
    parse_png_card,
    write_png_card,
    _v1_to_v2,
    _normalize_v2,
    tavern_to_user_card,
    user_card_to_tavern_v2,
)


# ── 完整 V2 字段清单（spec_v2 规范） ─────────────────────────────────

V2_REQUIRED_FIELDS = [
    "name", "description", "personality", "scenario",
    "first_mes", "mes_example",
]
V2_NEW_FIELDS = [
    "creator_notes", "system_prompt", "post_history_instructions",
    "alternate_greetings", "tags", "creator", "character_version",
    "extensions",
]
# character_book 是 optional，允许 None
V2_ALL_DATA_FIELDS = V2_REQUIRED_FIELDS + V2_NEW_FIELDS + ["character_book"]


def minimal_v2(name="TestChar") -> dict:
    return {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": name,
            "description": "A test character",
            "personality": "Curious",
            "scenario": "A tavern",
            "first_mes": "Greetings, traveller.",
            "mes_example": "",
            "creator_notes": "Just a test",
            "system_prompt": "",
            "post_history_instructions": "",
            "alternate_greetings": ["Hello!", "Hey there!"],
            "tags": ["test", "sample"],
            "creator": "Tester",
            "character_version": "1.0",
            "extensions": {"depth_prompt": {"prompt": "Stay in character"}},
            "character_book": None,
        },
    }


# ── parse_card: V2 ────────────────────────────────────────────────────

def test_v2_all_fields_preserved():
    """parse_card 保留全部 V2 data 字段。"""
    v2 = minimal_v2()
    result = parse_card(v2)
    d = result["data"]
    for field in V2_ALL_DATA_FIELDS:
        assert field in d, f"缺少字段 data.{field}"


def test_v2_string_fields():
    v2 = minimal_v2()
    result = parse_card(v2)
    d = result["data"]
    for field in V2_REQUIRED_FIELDS + ["creator_notes", "system_prompt",
                                        "post_history_instructions", "creator", "character_version"]:
        assert isinstance(d[field], str), f"data.{field} 应为 str"


def test_v2_list_fields():
    v2 = minimal_v2()
    result = parse_card(v2)
    d = result["data"]
    assert isinstance(d["alternate_greetings"], list)
    assert isinstance(d["tags"], list)
    assert d["alternate_greetings"] == ["Hello!", "Hey there!"]


def test_v2_extensions_dict():
    v2 = minimal_v2()
    result = parse_card(v2)
    assert isinstance(result["data"]["extensions"], dict)
    assert result["data"]["extensions"]["depth_prompt"]["prompt"] == "Stay in character"


def test_v2_character_book_preserved():
    v2 = minimal_v2()
    v2["data"]["character_book"] = {
        "name": "Test Book",
        "entries": [{"keys": ["keyword"], "content": "lore text", "enabled": True, "insertion_order": 0}],
        "extensions": {},
    }
    result = parse_card(v2)
    assert result["data"]["character_book"]["name"] == "Test Book"
    assert len(result["data"]["character_book"]["entries"]) == 1


def test_v2_missing_name_raises():
    v2 = minimal_v2()
    v2["data"]["name"] = ""
    with pytest.raises(ValueError, match="缺少 name"):
        parse_card(v2)


# ── parse_card: V3 (treated same as V2) ──────────────────────────────

def test_v3_spec_accepted():
    """chara_card_v3 与 v2 路径相同。"""
    v3 = minimal_v2()
    v3["spec"] = "chara_card_v3"
    v3["spec_version"] = "3.0"
    result = parse_card(v3)
    assert result["data"]["name"] == "TestChar"
    assert result["spec"] == "chara_card_v3"


# ── parse_card: V1 ────────────────────────────────────────────────────

def test_v1_flat_normalized():
    v1 = {
        "name": "Flat Hero",
        "char_persona": "Brave warrior",
        "world_scenario": "Post-apocalypse",
        "char_greeting": "Battle cry!",
        "example_dialogue": "<START>\n{{user}}: Hi\n{{char}}: Hello",
        "tags": ["action"],
    }
    result = parse_card(v1)
    # V1 input is normalized to V2 structure but retains spec=chara_card_v1
    assert "chara_card_v" in result["spec"]
    assert result["data"]["name"] == "Flat Hero"
    assert result["data"]["description"] == "Brave warrior"
    assert result["data"]["scenario"] == "Post-apocalypse"
    assert result["data"]["first_mes"] == "Battle cry!"


def test_v1_char_name_fallback():
    v1 = {"char_name": "Hero V1", "description": "d"}
    result = parse_card(v1)
    assert result["data"]["name"] == "Hero V1"


def test_v1_missing_name_raises():
    with pytest.raises(ValueError, match="缺少 name"):
        parse_card({"description": "no name here"})


# ── parse_card: JSON string / base64 ─────────────────────────────────

def test_parse_json_string():
    v2_str = json.dumps(minimal_v2(), ensure_ascii=False)
    result = parse_card(v2_str)
    assert result["data"]["name"] == "TestChar"


def test_parse_base64():
    v2_str = json.dumps(minimal_v2(), ensure_ascii=False)
    b64 = base64.b64encode(v2_str.encode("utf-8")).decode("ascii")
    result = parse_card(b64)
    assert result["data"]["name"] == "TestChar"


def test_parse_bytes():
    v2_str = json.dumps(minimal_v2(), ensure_ascii=False).encode("utf-8")
    result = parse_card(v2_str)
    assert result["data"]["name"] == "TestChar"


# ── PNG round-trip ────────────────────────────────────────────────────

def test_write_and_parse_png():
    v2 = minimal_v2("PNGChar")
    png_bytes = write_png_card(v2)
    # Verify PNG signature
    assert png_bytes[:8] == b"\x89PNG\r\n\x1a\n"
    result = parse_png_card(png_bytes)
    assert result["data"]["name"] == "PNGChar"
    assert result["data"]["description"] == "A test character"


def test_write_png_preserves_extensions():
    v2 = minimal_v2()
    v2["data"]["extensions"]["custom_ns"] = {"key": "value"}
    png_bytes = write_png_card(v2)
    result = parse_png_card(png_bytes)
    assert result["data"]["extensions"]["custom_ns"]["key"] == "value"


def test_parse_png_missing_chunk_raises():
    # Minimal valid PNG with no tEXt chunk
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr_data = struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0)
    ihdr = struct.pack(">I", 13) + b"IHDR" + ihdr_data + struct.pack(">I", zlib.crc32(b"IHDR" + ihdr_data))
    iend = struct.pack(">I", 0) + b"IEND" + struct.pack(">I", zlib.crc32(b"IEND"))
    png = sig + ihdr + iend
    with pytest.raises(ValueError, match="chara"):
        parse_png_card(png)


def test_parse_png_too_large_raises():
    big_blob = b"\x89PNG\r\n\x1a\n" + b"\x00" * (10 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="大"):
        parse_png_card(big_blob)


# ── tavern_to_user_card ────────────────────────────────────────────────

def test_tavern_to_user_card_field_mapping():
    v2 = minimal_v2()
    v2["data"]["description"] = "A detailed identity description"
    v2["data"]["personality"] = "Bold and witty"
    v2["data"]["tags"] = ["fantasy", "npc"]
    v2["data"]["creator_notes"] = "Don't use in system prompt"
    result = parse_card(v2)
    user_card = tavern_to_user_card(result)

    assert user_card["name"] == "TestChar"
    assert "A detailed identity" in user_card["identity"]
    assert user_card["personality"] == "Bold and witty"
    assert "fantasy" in user_card["tags"]
    assert user_card["metadata"]["tavern_imported"] is True
    assert user_card["metadata"]["creator_notes"] == "Don't use in system prompt"


def test_tavern_to_user_card_mes_example_extraction():
    v2 = minimal_v2()
    v2["data"]["mes_example"] = "<START>\n{{user}}: What do you seek?\n{{char}}: Power and knowledge."
    result = parse_card(v2)
    user_card = tavern_to_user_card(result)
    assert any("Power and knowledge" in s for s in user_card["sample_dialogue"])


def test_tavern_to_user_card_metadata_has_all_v2_fields():
    v2 = minimal_v2()
    result = parse_card(v2)
    user_card = tavern_to_user_card(result)
    md = user_card["metadata"]
    for field in ["scenario", "first_mes", "alternate_greetings", "system_prompt",
                  "post_history_instructions", "creator", "character_version", "extensions", "character_book"]:
        assert field in md, f"metadata 缺少 {field}"


# ── user_card_to_tavern_v2 (export) ──────────────────────────────────

def test_export_round_trip():
    v2 = minimal_v2("ExportChar")
    parsed = parse_card(v2)
    user_card = tavern_to_user_card(parsed)
    exported = user_card_to_tavern_v2(user_card)
    assert exported["spec"] == "chara_card_v2"
    assert exported["data"]["name"] == "ExportChar"
    assert isinstance(exported["data"]["alternate_greetings"], list)
    assert isinstance(exported["data"]["extensions"], dict)


def test_export_produces_valid_parseable_card():
    v2 = minimal_v2("BackAndForth")
    parsed = parse_card(v2)
    user_card = tavern_to_user_card(parsed)
    exported = user_card_to_tavern_v2(user_card)
    # Must be parseable again
    re_parsed = parse_card(exported)
    assert re_parsed["data"]["name"] == "BackAndForth"
