"""
tests/test_tavern_cards_completeness.py — 角色卡 V2 spec 完整性 + 往返测试

纯单元测试，不依赖 DB / LLM。
"""
import base64
import json
import struct
import sys
import zlib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from platform_app.tavern_cards import (
    _normalize_v2,
    _v1_to_v2,
    parse_card,
    parse_png_card,
    tavern_to_user_card,
    user_card_to_tavern_v2,
    write_png_card,
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


# ── parse_card: 幂等性(移植自 tavern-card 库 0.1.1 修复) ──────────────
# 扁平 V1 卡归一化后会产出 spec="chara_card_v1" 且字段嵌套在 data 下（溯源标记）。
# 若再次喂给 parse_card，之前会被误当成扁平 V1 解析，导致
# write_png_card(parse_card(v1)) -> parse_png_card 这种自然往返直接炸掉。

def test_parse_card_idempotent_on_v1():
    """parse_card(parse_card(v1)) 必须等于 parse_card(v1) —— 归一化后的 V1
    输出（spec=chara_card_v1 + 嵌套 data）不能被重新路由进扁平 V1 分支。"""
    v1 = {"name": "Luna", "description": "d", "first_mes": "hi"}
    once = parse_card(v1)
    twice = parse_card(once)
    assert once == twice
    assert twice["data"]["name"] == "Luna"


def test_v1_card_png_roundtrip():
    """还原 write_png_card(parse_card(v1)) -> parse_png_card 的自然使用路径。"""
    v1 = {"name": "Luna", "description": "d", "first_mes": "hi"}
    card = parse_card(v1)
    blob = write_png_card(card)
    back = parse_png_card(blob)
    assert back["data"]["name"] == "Luna"
    assert back["data"]["description"] == "d"


def test_flat_v1_without_spec_key_routes_through_v1_to_v2():
    """真扁平 V1 卡（无 spec 键）的路由不受幂等分支影响，仍走 _v1_to_v2。"""
    from unittest import mock

    v1 = {"name": "Flat Hero", "description": "Brave warrior"}
    assert "spec" not in v1
    with mock.patch(
        "platform_app.tavern_cards._v1_to_v2", wraps=_v1_to_v2
    ) as spy_v1, mock.patch(
        "platform_app.tavern_cards._normalize_v2", wraps=_normalize_v2
    ) as spy_v2:
        result = parse_card(v1)
    spy_v1.assert_called_once()
    # _v1_to_v2 内部会再调用一次 _normalize_v2 来补全字段，属预期行为。
    assert spy_v2.call_count == 1
    assert result["data"]["name"] == "Flat Hero"


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


def test_tavern_to_user_card_splits_wpp_description_sections():
    v2 = minimal_v2()
    v2["data"]["description"] = '''
    [character("Lulu") {
      Age("18")
      Occupation("vineyard scout")
      Appearance("silver hair, travel-stained cloak")
      Personality("watchful but warm")
      Background("raised near the old chapel")
      Speech("short sentences, dry humor")
      Secret("keeps a broken signet ring")
    }]
    '''
    v2["data"]["personality"] = "Curious"
    result = parse_card(v2)
    user_card = tavern_to_user_card(result)

    assert "Age: 18" in user_card["identity"]
    assert "Occupation: vineyard scout" in user_card["identity"]
    assert "silver hair" in user_card["appearance"]
    assert "raised near the old chapel" in user_card["background"]
    assert "Curious" in user_card["personality"]
    assert "watchful but warm" in user_card["personality"]
    assert "dry humor" in user_card["speech_style"]
    assert "broken signet ring" in user_card["secrets"]
    assert user_card["metadata"]["tavern_structured_description"] is True
    assert "character(\"Lulu\")" in user_card["metadata"]["tavern_raw_description"]


def test_tavern_to_user_card_splits_colon_description_sections():
    v2 = minimal_v2()
    v2["data"]["description"] = """身份: 月神殿的外聘灵性顾问
外貌: 黑色短斗篷,银质月牙吊坠
背景: 曾参与神殿地基净化仪式
说话方式: 语速慢,会先观察对方
"""
    result = parse_card(v2)
    user_card = tavern_to_user_card(result)

    assert "月神殿" in user_card["identity"]
    assert "短斗篷" in user_card["appearance"]
    assert "地基净化" in user_card["background"]
    assert "先观察" in user_card["speech_style"]


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


# ── 平台原生卡导出:全量富文本不丢(行者反馈根因修复)──────────────────

def _native_card() -> dict:
    """模拟平台 UI 创建的原生卡(无 metadata.* 酒馆字段),富内容散在结构化列。"""
    return {
        "name": "莉穆雷",
        "identity": "第十六代魔王的余女",
        "background": "父亲是魔王,72岁前登基成为十七代魔王",
        "appearance": "银色长发,血红色瞳孔",
        "personality": "对外冷漠,标杆涟漪的国家领导者",
        "speech_style": "温柔,轻佻,偶尔的腹黑与幽默",
        "current_status": "魔界现任领袖",
        "secrets": "其实暗恋主角",
        "sample_dialogue": ["你来了。", "随我来。"],
        "tags": ["魔王", "二次元"],
        "metadata": {},
    }


def test_export_native_card_includes_all_rich_sections():
    """根因:旧实现 description 只取 identity,把 background/appearance/speech_style/
    current_status 全丢了。现在全量带标签拼进 description。"""
    desc = user_card_to_tavern_v2(_native_card())["data"]["description"]
    assert "第十六代魔王的余女" in desc   # identity
    assert "72岁前登基" in desc          # background
    assert "银色长发" in desc            # appearance
    assert "偶尔的腹黑" in desc          # speech_style
    assert "魔界现任领袖" in desc        # current_status
    # personality 走酒馆独立字段,不混进 description
    exported = user_card_to_tavern_v2(_native_card())
    assert exported["data"]["personality"] == "对外冷漠,标杆涟漪的国家领导者"
    # secrets 硬隔离:绝不进 AI 可见 description
    assert "其实暗恋主角" not in desc


def test_export_lossless_round_trip_via_stellatrix():
    """extensions.stellatrix 全量保存结构化字段(含 secrets),再 import 精确还原。"""
    card = _native_card()
    exported = user_card_to_tavern_v2(card)
    stx = exported["data"]["extensions"]["stellatrix"]
    assert stx["secrets"] == "其实暗恋主角"          # 仅在 extensions,不在 description
    assert stx["appearance"] == "银色长发,血红色瞳孔"

    reimported = tavern_to_user_card(parse_card(exported))
    for f in ("identity", "background", "appearance", "personality",
              "speech_style", "current_status", "secrets"):
        assert reimported[f] == card[f], f"{f} round-trip 丢失/变形"


def test_export_first_mes_falls_back_to_first_sample():
    """原生卡无 metadata.first_mes → 退回首条 sample_dialogue,避免酒馆开场白全空。"""
    exported = user_card_to_tavern_v2(_native_card())
    assert exported["data"]["first_mes"] == "你来了。"


def test_import_mes_example_reads_all_start_blocks():
    """旧实现首个 <START> 块后即 break,多组示例对话只留第一条。现读全部(≤4)。"""
    v2 = minimal_v2()
    v2["data"]["mes_example"] = (
        "<START>\n{{user}}: A\n{{char}}: 回答一\n"
        "<START>\n{{user}}: B\n{{char}}: 回答二\n"
        "<START>\n{{user}}: C\n{{char}}: 回答三"
    )
    joined = " ".join(tavern_to_user_card(parse_card(v2))["sample_dialogue"])
    assert "回答一" in joined and "回答二" in joined and "回答三" in joined
