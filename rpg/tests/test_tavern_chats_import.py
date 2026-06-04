"""
tests/test_tavern_chats_import.py — 酒馆聊天记录 JSONL 解析单元测试

纯单元测试，不依赖 DB / LLM。
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from platform_app.tavern_chats import (
    _MAX_LINES,
    _MAX_MES_BYTES,
    chat_to_save_payload,
    parse_chat_jsonl,
)

# ── 辅助 ──────────────────────────────────────────────────────────────

def make_jsonl(header: dict, messages: list[dict]) -> str:
    lines = [json.dumps(header, ensure_ascii=False)]
    for m in messages:
        lines.append(json.dumps(m, ensure_ascii=False))
    return "\n".join(lines)


HEADER = {"user_name": "Alice", "character_name": "Luna", "create_date": "2025-01-01"}


# ── 正常解析 ──────────────────────────────────────────────────────────

def test_basic_round_trip():
    """一 user + 一 char 消息 → 一个 commit（gm_output 存 char 回复）。"""
    text = make_jsonl(HEADER, [
        {"name": "Alice", "is_user": True, "mes": "Hello!", "send_date": 1000},
        {"name": "Luna", "is_user": False, "mes": "Hi there!", "send_date": 1001},
    ])
    header, commits = parse_chat_jsonl(text)
    assert header["user_name"] == "Alice"
    assert header["character_name"] == "Luna"
    assert len(commits) == 1
    assert commits[0]["player_input"] == "Hello!"
    assert commits[0]["gm_output"] == "Hi there!"
    assert commits[0]["turn_index"] == 0
    assert commits[0]["metadata"]["tavern_imported"] is True


def test_multiple_rounds():
    """2 轮对话 → 2 个 commit。"""
    text = make_jsonl(HEADER, [
        {"name": "Alice", "is_user": True, "mes": "Q1"},
        {"name": "Luna", "is_user": False, "mes": "A1"},
        {"name": "Alice", "is_user": True, "mes": "Q2"},
        {"name": "Luna", "is_user": False, "mes": "A2"},
    ])
    _, commits = parse_chat_jsonl(text)
    assert len(commits) == 2
    assert commits[0]["player_input"] == "Q1"
    assert commits[0]["gm_output"] == "A1"
    assert commits[1]["player_input"] == "Q2"
    assert commits[1]["gm_output"] == "A2"
    assert commits[1]["turn_index"] == 1


def test_trailing_user_messages():
    """最后一批 user 消息（无 char 回复）作为独立 commit，gm_output 留空。"""
    text = make_jsonl(HEADER, [
        {"name": "Alice", "is_user": True, "mes": "Q1"},
        {"name": "Luna", "is_user": False, "mes": "A1"},
        {"name": "Alice", "is_user": True, "mes": "Final question"},
    ])
    _, commits = parse_chat_jsonl(text)
    assert len(commits) == 2
    assert commits[1]["player_input"] == "Final question"
    assert commits[1]["gm_output"] == ""


def test_consecutive_user_messages_merged():
    """连续多条 user 消息合并为一个 player_input（换行分隔）。"""
    text = make_jsonl(HEADER, [
        {"name": "Alice", "is_user": True, "mes": "Part 1"},
        {"name": "Alice", "is_user": True, "mes": "Part 2"},
        {"name": "Luna", "is_user": False, "mes": "Combined response"},
    ])
    _, commits = parse_chat_jsonl(text)
    assert len(commits) == 1
    assert "Part 1" in commits[0]["player_input"]
    assert "Part 2" in commits[0]["player_input"]


def test_char_only_no_user():
    """只有 char 回复（无 user 发言）→ player_input 空，gm_output 有值。"""
    text = make_jsonl(HEADER, [
        {"name": "Luna", "is_user": False, "mes": "Opening line with no user input"},
    ])
    _, commits = parse_chat_jsonl(text)
    assert len(commits) == 1
    assert commits[0]["player_input"] == ""
    assert commits[0]["gm_output"] == "Opening line with no user input"


def test_extra_field_preserved():
    """extra 字段保留在 metadata 中。"""
    text = make_jsonl(HEADER, [
        {"name": "Alice", "is_user": True, "mes": "Hi"},
        {"name": "Luna", "is_user": False, "mes": "Hello", "extra": {"token_count": 5}},
    ])
    _, commits = parse_chat_jsonl(text)
    assert commits[0]["metadata"]["extra"] == {"token_count": 5}


def test_send_date_preserved():
    """send_date 保留在 metadata 中。"""
    text = make_jsonl(HEADER, [
        {"name": "Alice", "is_user": True, "mes": "Hey", "send_date": 99999},
        {"name": "Luna", "is_user": False, "mes": "Yo", "send_date": 100000},
    ])
    _, commits = parse_chat_jsonl(text)
    assert commits[0]["metadata"]["send_date"] == 100000


def test_skip_empty_mes():
    """mes 为空的行跳过，不创建 commit。"""
    text = make_jsonl(HEADER, [
        {"name": "Alice", "is_user": True, "mes": ""},
        {"name": "Luna", "is_user": False, "mes": "Only real line"},
    ])
    _, commits = parse_chat_jsonl(text)
    # empty user mes skipped; char alone → 1 commit with empty player_input
    assert len(commits) == 1
    assert commits[0]["gm_output"] == "Only real line"


def test_default_header_values():
    """header 缺字段时用缺省值。"""
    text = make_jsonl({}, [
        {"name": "X", "is_user": False, "mes": "Hi"},
    ])
    header, _ = parse_chat_jsonl(text)
    assert header["user_name"] == "User"
    assert header["character_name"] == "Character"


# ── 错误处理 ──────────────────────────────────────────────────────────

def test_empty_text_raises():
    with pytest.raises(ValueError, match="为空"):
        parse_chat_jsonl("")


def test_blank_lines_only():
    with pytest.raises(ValueError, match="为空"):
        parse_chat_jsonl("   \n\n   ")


def test_invalid_json_line():
    with pytest.raises(ValueError, match="JSON 解析失败"):
        parse_chat_jsonl('{"user_name":"A"}\nNOT_JSON')


def test_no_valid_messages_raises():
    text = make_jsonl(HEADER, [
        {"name": "A", "is_user": True, "mes": ""},
        {"name": "B", "is_user": False, "mes": "  "},
    ])
    with pytest.raises(ValueError, match="不含有效消息"):
        parse_chat_jsonl(text)


def test_too_many_lines():
    header_line = json.dumps(HEADER)
    msg_line = json.dumps({"name": "X", "is_user": False, "mes": "x"})
    text = header_line + "\n" + "\n".join([msg_line] * (_MAX_LINES + 1))
    with pytest.raises(ValueError, match="超过上限"):
        parse_chat_jsonl(text)


def test_mes_truncated_at_limit():
    """超长 mes 截断到 _MAX_MES_BYTES 字节。"""
    big = "A" * (_MAX_MES_BYTES + 1000)
    text = make_jsonl(HEADER, [
        {"name": "Luna", "is_user": False, "mes": big},
    ])
    _, commits = parse_chat_jsonl(text)
    assert len(commits[0]["gm_output"]) <= _MAX_MES_BYTES


# ── chat_to_save_payload ──────────────────────────────────────────────

def test_save_payload_structure():
    text = make_jsonl(HEADER, [
        {"name": "Alice", "is_user": True, "mes": "Hi"},
        {"name": "Luna", "is_user": False, "mes": "Hey"},
    ])
    header, commits = parse_chat_jsonl(text)
    payload = chat_to_save_payload(header, commits)
    assert payload["export_version"] == 1
    assert "save" in payload
    assert isinstance(payload["commits"], list)
    assert len(payload["commits"]) == 1
    assert payload["save"]["state_snapshot"]["tavern_imported"] is True
    assert payload["save"]["state_snapshot"]["character_name"] == "Luna"


def test_save_payload_custom_title():
    text = make_jsonl(HEADER, [
        {"name": "Luna", "is_user": False, "mes": "Opening"},
    ])
    header, commits = parse_chat_jsonl(text)
    payload = chat_to_save_payload(header, commits, title="My Custom Title")
    assert payload["save"]["title"] == "My Custom Title"


def test_save_payload_default_title():
    text = make_jsonl(HEADER, [
        {"name": "Luna", "is_user": False, "mes": "Opening"},
    ])
    header, commits = parse_chat_jsonl(text)
    payload = chat_to_save_payload(header, commits)
    assert "Luna" in payload["save"]["title"]
