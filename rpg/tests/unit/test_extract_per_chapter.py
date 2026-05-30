"""Phase A Pass1 — extract.per_chapter 离线单测(mock LLM,锁 schema + 纪元铁律)。

真实 LLM 提取的 live 验证见提交说明(二战书 3 章 concepts 非空 + 纪元钉死)。
"""
from __future__ import annotations

import json

from extract.per_chapter import ChapterExtract, extract_chapter, to_chapter_facts_row


class _MockLLM:
    """返回预设 JSON 的假 LLM。"""

    def __init__(self, payload):
        self._payload = payload

    def complete_json(self, system, user, max_tokens=2000):
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


def test_era_pinned_even_if_model_hallucinates():
    # 模型乱填 era=1935 → 必须被强制回写种子纪元
    mock = _MockLLM({
        "story_time": {"label": "柏林战役", "era": "1935年"},
        "entities": [{"surface": "娅赛兰", "canonical_guess": "娅赛兰", "type": "character", "status": "linked"}],
        "concepts": [{"name": "神姬", "gloss": "力量体系"}],
        "events": [], "relationships": [], "confidence": 0.9,
    })
    ex = extract_chapter(mock, 5, "正文…", era="星历2930年代")
    assert ex.story_time["era"] == "星历2930年代", "纪元铁律失效"
    assert len(ex.concepts) == 1
    assert ex.entities[0]["type"] == "character"


def test_malformed_response_marked_not_ok():
    ex = extract_chapter(_MockLLM(ValueError("bad json")), 7, "正文", era="星历2930年代")
    assert ex.raw_ok is False
    assert ex.concepts == []


def test_to_chapter_facts_row_shape():
    ex = ChapterExtract(
        chapter=3,
        story_time={"label": "上午", "era": "星历2930年代"},
        entities=[{"canonical_guess": "薇瑟帝国", "type": "faction"},
                  {"canonical_guess": "柏林", "type": "location"}],
        concepts=[{"name": "制空权"}],
        events=[{"summary": "会战"}],
        confidence=0.8,
    )
    row = to_chapter_facts_row(ex, title="第3章")
    assert row["factions"][0]["canonical_guess"] == "薇瑟帝国"
    assert row["locations"][0]["canonical_guess"] == "柏林"
    assert row["concepts"][0]["name"] == "制空权"
    assert row["metadata"]["era"] == "星历2930年代"
    assert row["story_time_label"] == "上午"
    json.dumps(row, ensure_ascii=False)  # 可序列化


if __name__ == "__main__":
    test_era_pinned_even_if_model_hallucinates()
    test_malformed_response_marked_not_ok()
    test_to_chapter_facts_row_shape()
    print("OK")
