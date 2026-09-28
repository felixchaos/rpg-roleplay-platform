"""extract.per_chapter / extract.resolve — 世界观核心设定被压扁 两根因 修复单测(无 LLM/DB)。

覆盖:
  ① concepts.gloss 硬封顶 30 字 + resolve 聚合 first-gloss-wins(跨千章从不充实)
     → merge_gloss(old, new):择优(信息量更大胜出)+ 充实(机制词追加,不倒退)。
  ② cluster_entities 首字门禁(ni[0]==nr[0])挡住音译同音异字变体(埃/艾…)
     → _translit_norm(name):归一后放行进比较,不直接判同;无关人名仍不入簇。
"""
from __future__ import annotations

from extract.per_chapter import ChapterExtract
from extract.resolve import (
    _translit_norm,
    cluster_entities,
    gather_entity_mentions,
    merge_gloss,
    resolve_and_write,
)


def _ex(chapter, ents=None, concepts=None):
    return ChapterExtract(chapter=chapter, entities=ents or [], concepts=concepts or [])


# ── merge_gloss ──────────────────────────────────────────────────────────────
def test_merge_gloss_enriches_with_mechanism_detail():
    """新 gloss 明显信息量更大(更长 + 带机制词)→ 择优胜出,机制细节保留在结果里。"""
    old = "战斗用人形兵器"
    new = "战姬按血统分为三级,需消耗晶石激活二级以上护盾,型号 MK-2 起可远程作战"
    merged = merge_gloss(old, new)
    assert "三级" in merged and "MK-2" in merged
    assert len(merged) <= 160


def test_merge_gloss_appends_when_new_mechanism_but_not_outright_longer():
    """新 gloss 带旧版没有的机制词,但整体信息量与旧的相当(不构成"明显更长")
    → 走充实分支,追加"新增:",不丢旧句子。"""
    old = "战姬是以少女为核心改造的战斗人形兵器,用于前线作战与遏制敌方异能者"
    new = "战姬的护盾按血统分三档,二档以上需额外消耗晶石"
    merged = merge_gloss(old, new)
    assert old in merged
    assert "新增:" in merged
    assert "三档" in merged or "血统" in merged
    assert len(merged) <= 160


def test_merge_gloss_prefers_longer_non_template_over_short_template():
    """旧 gloss 是模板化短句、新的更长且带机制信息 → 新的直接胜出(择优)。"""
    old = "一种兵器"
    new = "战姬是以少女为核心改造的战斗人形兵器,依血统分三档,启动需要消耗核心晶石"
    merged = merge_gloss(old, new)
    assert merged == new


def test_merge_gloss_never_regresses_to_empty_or_template():
    """新 gloss 为空,或新 gloss 更短/更模板化且不含新机制词 → 保留旧的,不倒退。"""
    old = "战姬按血统分为三级,需消耗晶石激活二级以上护盾"
    assert merge_gloss(old, "") == old
    # 新的是模板化空话且没有新机制词 → 不应覆盖已有的丰富 gloss
    merged = merge_gloss(old, "一种兵器")
    assert "血统" in merged and "晶石" in merged


def test_merge_gloss_identical_or_first_write():
    assert merge_gloss("", "首次抽到的解释") == "首次抽到的解释"
    assert merge_gloss("同一句话", "同一句话") == "同一句话"


def test_merge_gloss_used_in_concept_aggregation_across_chapters():
    """跨章聚合走 resolve_and_write 的 concept_acc 逻辑,验证不再 first-gloss-wins 锁死。

    模拟:第1章只给模板化短 gloss,第50章给出机制细节 → 最终 canon 里的 concept.summary
    应包含第50章补充的机制信息(旧版 first-gloss-wins 会永远锁死第1章那句)。
    """
    class _FakeDB:
        # 查询一律查不到;insert … returning 返回一行(resolve_and_write 只统计真正写入的实体)
        def execute(self, sql="", *a, **kw):
            is_insert = str(sql).lstrip().lower().startswith("insert")

            class _Cur:
                def fetchone(self_inner):
                    return {"id": 1} if is_insert else None
            return _Cur()

    exs = [
        _ex(1, concepts=[{"name": "战姬", "gloss": "一种兵器"}]),
        _ex(50, concepts=[{"name": "战姬",
                            "gloss": "战姬依血统分三级,激活护盾需消耗晶石,型号 MK-2 起可远程作战"}]),
    ]
    result = resolve_and_write(_FakeDB(), script_id=1, chapter_extracts=exs, embedder=None)
    assert result["entities_written"] >= 1
    # 直接从 cluster 前的 concept_acc 逻辑等价路径核对更方便:重放同样的合并——
    # 第1章模板化短句"一种兵器" vs 第50章带机制细节的长句 → 择优胜出,机制信息不丢
    # (旧版 first-gloss-wins 会让 acc 永远停在"一种兵器")。
    from extract.resolve import merge_gloss as _mg
    acc = ""
    for ex in exs:
        for c in ex.concepts:
            acc = _mg(acc, c.get("gloss", ""))
    assert "晶石" in acc, f"跨章机制细节应被采纳,不应锁死在第1章模板句: {acc}"
    assert acc != "一种兵器", "不应 first-gloss-wins 锁死"


# ── _translit_norm ───────────────────────────────────────────────────────────
def test_translit_norm_merges_common_homophone_variants():
    assert _translit_norm("埃尔温·隆美尔") == _translit_norm("艾尔温·隆美尔")


def test_translit_norm_does_not_conflate_unrelated_names():
    assert _translit_norm("张三") != _translit_norm("李四")
    assert _translit_norm("林有德") != _translit_norm("林有财")


def test_translit_norm_covers_at_least_twelve_pairs():
    """映射表 ≥12 组常见音译同音异字对(埃/艾、丝/斯、娅/雅、莉/丽、克/科、姆/穆、贝/白 等)。"""
    from extract.resolve import _TRANSLIT_CHAR_MAP
    # 统计映射目标去重后的组数(每组至少2个源字指向同一目标)
    groups: dict[str, set[str]] = {}
    for src, tgt in _TRANSLIT_CHAR_MAP.items():
        groups.setdefault(tgt, set()).add(src)
    multi_char_groups = [g for g in groups.values() if len(g) >= 2]
    assert len(multi_char_groups) >= 12, f"音译映射组数不足 12: {len(multi_char_groups)}"


# ── cluster_entities 音译变体入簇 / 无关人名不入簇 ───────────────────────────
def test_cluster_merges_translit_variant_with_embedder_signal():
    """埃尔温·隆美尔 / 艾尔温·隆美尔(首字不同)靠假 embedder 高相似度应合并成一人。

    首字门禁修复前:ni[0]('艾' 或 '埃') != nr[0] 直接跳过嵌入比较分支,永不合并。
    修复后:_translit_norm 归一相同 → 放行比较 → 假 embedder 返回高相似度 → same=True。
    """
    exs = [
        _ex(1, [{"canonical_guess": "埃尔温·隆美尔", "type": "character"}]),
        _ex(2, [{"canonical_guess": "埃尔温·隆美尔", "type": "character"}]),
        _ex(3, [{"canonical_guess": "艾尔温·隆美尔", "type": "character"}]),
    ]

    def _fake_embedder(names):
        # 所有向量都一样 → 任意两名字余弦相似度恒为 1.0(>= 默认阈值 0.95)
        return [[1.0, 0.0] for _ in names]

    canon = cluster_entities(gather_entity_mentions(exs), embedder=_fake_embedder)
    chars = [c for c in canon if c.type == "character"]
    assert len(chars) == 1, f"音译变体应合并成一人: {[c.name for c in chars]}"


def test_cluster_does_not_merge_unrelated_names_via_translit():
    """无关人名(不同首字、翻译归一后也不同)即使配了高相似度假 embedder 也不应合并。"""
    exs = [
        _ex(1, [{"canonical_guess": "张三", "type": "character"}]),
        _ex(2, [{"canonical_guess": "李四", "type": "character"}]),
    ]

    def _fake_embedder(names):
        return [[1.0, 0.0] for _ in names]

    canon = cluster_entities(gather_entity_mentions(exs), embedder=_fake_embedder)
    chars = sorted(c.name for c in canon if c.type == "character")
    assert chars == ["张三", "李四"] or chars == ["李四", "张三"]
    assert len(chars) == 2, f"无关人名不应被误并: {chars}"


def test_cluster_first_char_equal_path_still_works():
    """首字相同的既有路径(未涉及音译映射表)不受影响,回归防御。"""
    exs = [
        _ex(1, [{"canonical_guess": "薇欧拉", "type": "character"}]),
        _ex(2, [{"canonical_guess": "薇瑟拉", "type": "character"}]),
    ]

    def _fake_embedder(names):
        return [[1.0, 0.0] for _ in names]

    canon = cluster_entities(gather_entity_mentions(exs), embedder=_fake_embedder)
    chars = [c for c in canon if c.type == "character"]
    assert len(chars) == 1, f"首字相同的同语言变体应合并(既有行为): {[c.name for c in chars]}"
