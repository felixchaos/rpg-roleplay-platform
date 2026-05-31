"""extract/per_chapter.py — Pass 1 逐章固定-schema 三元组提取。

discover-then-link 的 link:带 已发现词表 + 钉死纪元种子 读每章 → 固定 schema JSON。
直接修"concepts 98% 空"(关键词匹配空,LLM 强制填字段);纪元钉死 → 不再幻觉 1935。
设计 docs/design/A_extraction.md §4。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from extract.llm import ExtractLLM

# 每章输出 JSON schema(给模型看的契约)
_SCHEMA_HINT = """{
  "chapter_summary": "本章主线 1-3 句话浓缩(>=30 字 <=150 字),含核心冲突/转折/谁做了什么。绝不照抄原文,绝不堆细节。",
  "story_time": {"label": "本章故事时间(短语)", "relative_marker": "相对上章的时序线索", "era": "<纪元,必须照抄给定纪元,严禁改写>"},
  "entities": [{"surface": "文中称呼", "full_name": "本人最完整的正式名(欧美名 = 名+姓全套,如 Mulelia Zazbarum;若文中已知则填写,否则同 surface)", "canonical_guess": "规范名(优先匹配已知实体)", "aliases_in_chapter": ["本章用到的其他称呼/昵称/半名/译名(如 ['Mulelia','小蕾'])"], "type": "character|faction|location|item", "status": "linked|proposed", "evidence": "≤20字依据"}],
  "events": [{"summary": "事件一句话", "participants": ["实体名"], "location": "地点", "importance": 0-100, "causal_refs": ["前置事件描述"]}],
  "relationships": [{"from": "实体A", "to": "实体B", "kind": "敌对|盟友|上下级|亲属|...", "evidence": "≤20字"}],
  "concepts": [{"name": "概念/设定/力量体系名", "gloss": "≤30字解释", "evidence": "≤20字"}],
  "confidence": 0.0-1.0
}"""


@dataclass
class ChapterExtract:
    chapter: int
    chapter_summary: str = ""
    story_time: dict = field(default_factory=dict)
    entities: list = field(default_factory=list)
    events: list = field(default_factory=list)
    relationships: list = field(default_factory=list)
    concepts: list = field(default_factory=list)
    confidence: float = 0.0
    raw_ok: bool = True


def build_system(era: str, power_system: list[str] | None = None) -> str:
    ps = ("、".join(power_system)) if power_system else "(未提供,自行从文中发现)"
    return (
        "你是小说世界观结构化提取器。读一章正文,**只输出一个 JSON 对象**(无任何解释/前后语)。\n"
        f"【纪元铁律】本作纪元固定为:「{era}」。story_time.era 字段必须**原样照抄**此纪元,"
        "**绝对禁止**根据剧情(如二战、年份数字)推断或改写成别的纪元(如 1935、1940)。违反即错误。\n"
        f"【力量体系参考】{ps}(文中出现就抽进 concepts,可发现新的)。\n"
        "【提取要求】entities 优先匹配下方已知实体词表(status=linked),文中新出现的标 proposed;"
        "concepts 必须尽量抽全(力量体系/组织设定/专有名词/世界规则),不要留空;"
        "events 给本章局部 importance(0-100),不要做跨章全局排序。\n"
        "【欧美人名铁律】凡角色为欧美名(包含字母或音译,如 Mulelia/林菲尔德/伊莎贝拉·路德维希):full_name **必须** 是"
        "正式的全套姓+名(若本章用 'Mulelia' 但作者之前已揭示她叫 'Mulelia Zazbarum',则 full_name 写完整全名);"
        "本章里出现的所有别称(昵称/半名/敬称/外号/译名)塞进 aliases_in_chapter。**严禁** 把全名和昵称当作两个实体输出。\n"
        "严格按此 schema 输出:\n" + _SCHEMA_HINT
    )


def build_user(chapter_text: str, *, known_entities: list[str] | None = None,
               prev_summary: str = "", title_descriptor: str = "") -> str:
    parts = []
    if known_entities:
        parts.append("【已知实体词表(优先 link)】" + "、".join(known_entities[:80]))
    if prev_summary:
        parts.append("【上一章梗概(仅供时序连续,勿照抄)】" + prev_summary[:200])
    if title_descriptor:
        parts.append("【本章内容提示】" + title_descriptor)
    # 控制长度(便宜模型上下文 + 成本):截到 ~6000 字
    body = chapter_text.strip()
    if len(body) > 6000:
        body = body[:6000] + "…(后略)"
    parts.append("【本章正文】\n" + body)
    return "\n\n".join(parts)


def extract_chapter(llm: ExtractLLM, chapter_num: int, chapter_text: str, *, era: str,
                    power_system: list[str] | None = None, known_entities: list[str] | None = None,
                    prev_summary: str = "", title_descriptor: str = "",
                    max_tokens: int = 3500) -> ChapterExtract:
    # 默认 3500:schema 新增 chapter_summary + entity 的 full_name/aliases_in_chapter 字段
    # 后,实测 haiku 单章输出 ~2400-2800 tokens(原 2000 会被截在 events 中段导致 JSON 解析失败)
    system = build_system(era, power_system)
    user = build_user(chapter_text, known_entities=known_entities,
                      prev_summary=prev_summary, title_descriptor=title_descriptor)
    try:
        data = llm.complete_json(system, user, max_tokens=max_tokens)
    except Exception:
        return ChapterExtract(chapter=chapter_num, raw_ok=False)
    if not isinstance(data, dict):
        return ChapterExtract(chapter=chapter_num, raw_ok=False)
    st = data.get("story_time") or {}
    # 纪元铁律兜底:即使模型乱填,也强制回写种子纪元
    if isinstance(st, dict):
        st["era"] = era
    return ChapterExtract(
        chapter=chapter_num,
        chapter_summary=str(data.get("chapter_summary") or "")[:400],
        story_time=st if isinstance(st, dict) else {"era": era},
        entities=[e for e in (data.get("entities") or []) if isinstance(e, dict)],
        events=[e for e in (data.get("events") or []) if isinstance(e, dict)],
        relationships=[r for r in (data.get("relationships") or []) if isinstance(r, dict)],
        concepts=[c for c in (data.get("concepts") or []) if isinstance(c, dict)],
        confidence=float(data.get("confidence") or 0.0),
    )


def to_chapter_facts_row(ex: ChapterExtract, *, title: str = "") -> dict[str, Any]:
    """转成现有 chapter_facts 表的列形状(复用表,值来自 LLM 三元组而非关键词)。"""
    return {
        "chapter": ex.chapter,
        "title": title,
        "story_time_label": (ex.story_time or {}).get("label", ""),
        "story_phase": "",
        "characters": [e for e in ex.entities if e.get("type") == "character"],
        "locations": [e for e in ex.entities if e.get("type") == "location"],
        "factions": [e for e in ex.entities if e.get("type") == "faction"],
        "concepts": ex.concepts,
        "items": [e for e in ex.entities if e.get("type") == "item"],
        "relationships": ex.relationships,
        "events": ex.events,
        "confidence": ex.confidence,
        "metadata": {"era": (ex.story_time or {}).get("era", ""), "extractor": "llm_pass1"},
    }


def dumps(ex: ChapterExtract) -> str:
    return json.dumps(to_chapter_facts_row(ex), ensure_ascii=False, indent=2)
