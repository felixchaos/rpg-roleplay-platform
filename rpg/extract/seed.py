"""extract/seed.py — Pass 0 作者种子 + LLM 自举词表(discover-then-link 的 discover)。

破除 bootstrap 死锁:逐章提取需要一个尚不存在的实体词表 → 先滑窗采样 LLM NER 发现词表。
纪元/世界线作者种子(避开"从散文推纪元/多线"无解难题)。设计 A_extraction.md §3。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from extract.llm import ExtractLLM


@dataclass
class ScriptSeed:
    era: str = ""                       # 纪元(钉死,治 1935)
    power_system: list[str] = field(default_factory=list)
    key_factions: list[str] = field(default_factory=list)
    worldlines: list[dict] = field(default_factory=list)  # [{id,label,arcs}]
    entity_vocab: list[str] = field(default_factory=list) # 自举发现的实体词表

    def to_json(self) -> dict:
        return {"era": self.era, "power_system": self.power_system,
                "key_factions": self.key_factions, "worldlines": self.worldlines,
                "entity_vocab": self.entity_vocab}


_NER_SYSTEM = (
    "你是小说实体发现器。读若干章节片段,**只输出一个 JSON 对象**(无解释):\n"
    '{"characters": ["人名"], "factions": ["势力/组织名"], "locations": ["地名"], '
    '"concepts": ["力量体系/设定/专有名词"], "era_hint": "若文中出现明确纪元/年代则填,否则空"}\n'
    "只抽**反复出现、像专有名词**的;别抽普通词。每类最多 30 个。"
)


def _sample_indices(total: int, k: int) -> list[int]:
    """均匀采样 k 个章节下标(确定性,无随机)。"""
    if total <= k:
        return list(range(total))
    step = total / k
    return sorted({int(i * step) for i in range(k)})


def _normalize_era(era: str) -> str:
    """归一化 era_hint:抽出最具体的年份/纪元锚,丢具体月日,便于多章共识。
    "1930年4月5日" / "1930年4月" / "1930年" / "1930 年代" → "1930"。
    保留非数字纪元如 "星历 2930 年代" 整字串。
    """
    import re as _re
    if not era:
        return ""
    m = _re.search(r"(\d{4})", era)
    if m:
        return m.group(1)
    return era.strip()[:30]


def bootstrap_vocab(llm: ExtractLLM, chapters: list[dict], *, sample: int = 12,
                    max_tokens: int = 1500) -> dict:
    """滑窗采样 LLM NER 聚合词表。chapters = [{title, content}, ...](已按序)。

    返回 {characters, factions, locations, concepts, era_hint}。
    """
    idxs = _sample_indices(len(chapters), sample)
    agg = {"characters": {}, "factions": {}, "locations": {}, "concepts": {}}
    era_hints: dict[str, int] = {}
    era_orig: dict[str, str] = {}  # 归一key → 原始最长写法
    for i in idxs:
        ch = chapters[i]
        body = (ch.get("content") or "")[:4000]
        if not body.strip():
            continue
        try:
            data = llm.complete_json(_NER_SYSTEM, f"【章节片段】\n{body}", max_tokens=max_tokens)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        for key in agg:
            for name in (data.get(key) or []):
                if isinstance(name, str) and name.strip():
                    agg[key][name.strip()] = agg[key].get(name.strip(), 0) + 1
        eh = (data.get("era_hint") or "").strip()
        if eh:
            ek = _normalize_era(eh)
            era_hints[ek] = era_hints.get(ek, 0) + 1
            # 保留最长原始写法(信息更多)
            if not era_orig.get(ek) or len(eh) > len(era_orig[ek]):
                era_orig[ek] = eh
    # 按频次排序取词表
    out = {k: [n for n, _ in sorted(v.items(), key=lambda x: -x[1])] for k, v in agg.items()}
    # 纪元共识门:至少 2 票或 25% 采样命中(取较小)。小样本(<8章)只要 1 票也接受
    # —— 防过严反伤"未知纪元"fallback。
    sorted_eras = sorted(era_hints.items(), key=lambda x: -x[1])
    if sorted_eras:
        threshold = 1 if len(idxs) < 8 else max(2, len(idxs) // 4)
        if sorted_eras[0][1] >= threshold:
            # 用归一 key 找回原始最长写法(保留"1930年4月5日"而不是裸"1930")
            out["era_hint"] = era_orig.get(sorted_eras[0][0]) or sorted_eras[0][0]
        else:
            out["era_hint"] = ""
    else:
        out["era_hint"] = ""
    return out


def build_seed(llm: ExtractLLM, chapters: list[dict], *, author_era: str = "",
               author_power_system: list[str] | None = None,
               author_worldlines: list[dict] | None = None, sample: int = 12) -> ScriptSeed:
    """组装 ScriptSeed:作者种子优先,LLM 自举补全词表 + 纪元提示(作者没填时用)。"""
    voc = bootstrap_vocab(llm, chapters, sample=sample)
    era = author_era or voc.get("era_hint") or ""
    entity_vocab = []
    for key in ("characters", "factions", "locations", "concepts"):
        entity_vocab.extend(voc.get(key, []))
    return ScriptSeed(
        era=era,
        power_system=list(author_power_system or voc.get("concepts", [])[:10]),
        key_factions=voc.get("factions", [])[:15],
        worldlines=list(author_worldlines or [{"id": "main", "label": "主线", "arcs": []}]),
        entity_vocab=entity_vocab[:120],
    )


def dumps(seed: ScriptSeed) -> str:
    return json.dumps(seed.to_json(), ensure_ascii=False, indent=2)
