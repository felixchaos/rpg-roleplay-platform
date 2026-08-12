"""extract.resolve — NPC 提取人名/语义错误 QA 修复离线单测(无 LLM/DB)。

覆盖:
  RC1 主角=叙事中心度融合分(治『红姑被标主角』)
  RC4 非人名(官职/封号/地名)误标 character 的降级闸
  RC9 aliases 运算符优先级 bug(规范名自指)
  RC2 中文敬称基名归并(苏玖姑娘↔苏玖)+ 保守不并单字基名
"""
from __future__ import annotations

from extract.per_chapter import ChapterExtract
from extract.resolve import (
    CanonEntity,
    _has_person_evidence,
    _looks_like_non_person,
    cluster_entities,
    compute_protagonist_importance,
    gather_entity_mentions,
)


def _ex(chapter, ents, events=None, rels=None):
    return ChapterExtract(chapter=chapter, entities=ents, events=events or [], relationships=rels or [])


# ── RC9 ────────────────────────────────────────────────────────────────────
def test_rc9_aliases_no_self_reference():
    exs = [
        _ex(1, [{"surface": "玉儿", "canonical_guess": "玉儿",
                 "aliases_in_chapter": ["小玉"], "type": "character"}]),
        _ex(2, [{"surface": "玉儿", "canonical_guess": "玉儿", "type": "character"}]),
    ]
    canon = cluster_entities(gather_entity_mentions(exs), embedder=None)
    c = [x for x in canon if x.type == "character"][0]
    assert c.name == "玉儿"
    assert "玉儿" not in c.aliases, f"规范名不应自指进 aliases: {c.aliases}"
    assert "小玉" in c.aliases


# ── RC2 ────────────────────────────────────────────────────────────────────
def test_rc2_honorific_base_merges():
    """『苏玖姑娘』与独立『苏玖』应经敬称基名(≥2字)合并成一人。"""
    exs = [
        _ex(1, [{"canonical_guess": "苏玖姑娘", "type": "character"}]),
        _ex(2, [{"canonical_guess": "苏玖", "type": "character"}]),
        _ex(3, [{"canonical_guess": "苏玖", "type": "character"}]),
    ]
    chars = [c for c in cluster_entities(gather_entity_mentions(exs), embedder=None)
             if c.type == "character"]
    assert len(chars) == 1, f"苏玖姑娘/苏玖 应合并: {[c.name for c in chars]}"


def test_rc2_single_char_base_not_merged():
    """『红姑』基名=红(单字)不归一 → 不与无关的『红绡』误并(保守)。"""
    exs = [
        _ex(1, [{"canonical_guess": "红姑", "type": "character"}]),
        _ex(2, [{"canonical_guess": "红绡", "type": "character"}]),
    ]
    chars = sorted(c.name for c in cluster_entities(gather_entity_mentions(exs), embedder=None)
                   if c.type == "character")
    assert chars == ["红姑", "红绡"], f"单字基名不应误并: {chars}"


# ── RC4 ────────────────────────────────────────────────────────────────────
def test_rc4_non_person_detection():
    for w in ("将军", "单于", "公主", "大人", "众人", "无忧宫", "未央宫"):
        assert _looks_like_non_person(w), f"{w} 应判为非人名"
    for w in ("金玉", "霍去病", "卫青", "李妍"):
        assert not _looks_like_non_person(w), f"{w} 是真人名,不应被判非人名"


def test_rc4_has_person_evidence_guard():
    # 名叫"将军"但有个人化 identity → 可能是外号,放过
    c1 = CanonEntity(logical_key="j", name="将军", type="character",
                     identity="镇北大营主帅", aliases=[])
    assert _has_person_evidence(c1)
    # 无 identity、别名只有自己 → 无佐证
    c2 = CanonEntity(logical_key="j", name="将军", type="character", identity="", aliases=["将军"])
    assert not _has_person_evidence(c2)
    # 有额外别名 → 有佐证
    c3 = CanonEntity(logical_key="j", name="将军", type="character", identity="", aliases=["卫青"])
    assert _has_person_evidence(c3)


# ── RC1 ────────────────────────────────────────────────────────────────────
def test_rc1_protagonist_span_beats_frequency():
    """红姑高频但窄跨度,玉儿频次略低但贯穿全书 + 关系/事件中心 → 玉儿 importance 最高。"""
    chars = [
        CanonEntity(logical_key="honggu", name="红姑", type="character", importance=20,
                    first_revealed_chapter=1, last_revealed_chapter=3, aliases=["红姑娘"]),
        CanonEntity(logical_key="yuer", name="玉儿", type="character", importance=14,
                    first_revealed_chapter=1, last_revealed_chapter=40, aliases=["金玉", "小玉"]),
        CanonEntity(logical_key="qubing", name="霍去病", type="character", importance=8,
                    first_revealed_chapter=10, last_revealed_chapter=35, aliases=[]),
    ]
    rels = [{"from": "玉儿", "to": "红姑"}, {"from": "玉儿", "to": "霍去病"},
            {"from": "霍去病", "to": "玉儿"}]
    events = [{"participants": ["玉儿", "红姑"]}, {"participants": ["玉儿", "霍去病"]},
              {"participants": ["玉儿"]}]
    exs = [_ex(1, [], events=events, rels=rels)]
    compute_protagonist_importance(chars, exs, title_text="大漠谣")
    by = {c.name: c.importance for c in chars}
    assert by["玉儿"] > by["红姑"], f"主角玉儿应高于贴身配角红姑: {by}"
    assert by["玉儿"] == max(by.values()), f"玉儿应排第一(rk=1): {by}"


def test_rc1_only_touches_characters():
    """faction/location/concept 的 importance 不被融合分改写(worldbook 阈值依赖它)。"""
    chars = [
        CanonEntity(logical_key="a", name="甲", type="character", importance=5,
                    first_revealed_chapter=1, last_revealed_chapter=2),
        CanonEntity(logical_key="f", name="匈奴", type="faction", importance=7,
                    first_revealed_chapter=1, last_revealed_chapter=9),
    ]
    compute_protagonist_importance(chars, [_ex(1, [])], title_text="")
    fac = [c for c in chars if c.type == "faction"][0]
    assert fac.importance == 7, "非 character 的 importance 不应被改"


if __name__ == "__main__":
    import sys

    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
