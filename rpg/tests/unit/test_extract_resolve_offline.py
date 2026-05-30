"""Phase A Pass2 — extract.resolve 离线单测(无 LLM/DB,锁消歧聚合逻辑)。"""
from __future__ import annotations

from extract.per_chapter import ChapterExtract
from extract.resolve import _slug, cluster_entities, gather_entity_mentions


def _ex(chapter, ents, concepts=None):
    return ChapterExtract(chapter=chapter, entities=ents, concepts=concepts or [])


def test_gather_counts_and_first_chapter():
    exs = [
        _ex(3, [{"canonical_guess": "林有德", "type": "character"}]),
        _ex(1, [{"surface": "林君", "canonical_guess": "林有德", "type": "character"},
                {"canonical_guess": "奥匈帝国", "type": "faction"}]),
    ]
    m = gather_entity_mentions(exs)
    assert m[("林有德", "character")]["count"] == 2
    assert m[("林有德", "character")]["first_chapter"] == 1  # 取最早
    assert "林君" in m[("林有德", "character")]["surfaces"]


def test_cluster_no_embedder_exact_merge_keeps_type_separate():
    exs = [
        _ex(1, [{"canonical_guess": "光", "type": "character"},
                {"canonical_guess": "光", "type": "location"}]),  # 同名不同 type 不合并
        _ex(2, [{"canonical_guess": "光", "type": "character"}]),
    ]
    m = gather_entity_mentions(exs)
    canon = cluster_entities(m, embedder=None)
    chars = [c for c in canon if c.type == "character"]
    locs = [c for c in canon if c.type == "location"]
    assert len(chars) == 1 and chars[0].importance == 2
    assert len(locs) == 1
    # logical_key 撞名 → 加 type 后缀去重
    keys = [c.logical_key for c in canon]
    assert len(set(keys)) == len(keys)


def test_cluster_with_embedder_merges_near_duplicates():
    exs = [
        _ex(1, [{"canonical_guess": "薇瑟帝国", "type": "faction"}]),
        _ex(2, [{"canonical_guess": "薇瑟", "type": "faction"}]),  # 近义
        _ex(3, [{"canonical_guess": "地联", "type": "faction"}]),
    ]
    m = gather_entity_mentions(exs)

    def fake_embedder(names):
        # 薇瑟帝国/薇瑟 → 近(高 cos);地联 → 远
        table = {"薇瑟帝国": [1.0, 0.0, 0.0], "薇瑟": [0.98, 0.02, 0.0], "地联": [0.0, 0.0, 1.0]}
        return [table.get(n, [0.0, 0.0, 0.0]) for n in names]

    canon = cluster_entities(m, embedder=fake_embedder, sim_threshold=0.9)
    factions = [c for c in canon if c.type == "faction"]
    # 薇瑟帝国 与 薇瑟 合并 → 2 个簇(薇瑟系 + 地联)
    assert len(factions) == 2
    weise = next(c for c in factions if "薇瑟" in c.name)
    assert "薇瑟" in weise.aliases or "薇瑟帝国" in (weise.aliases + [weise.name])


def test_slug():
    assert _slug("夏莉·德里尔")
    assert _slug("  a b ") == "a_b"


if __name__ == "__main__":
    for n, f in list(globals().items()):
        if n.startswith("test_") and callable(f):
            f()
    print("OK")


def test_cluster_does_not_merge_distinct_names_even_if_embeddings_close():
    """回归 W1: 旧 0.86 阈值把 14 个不同角色并成 1。不同人名(非子串)即使嵌入接近也不合并。"""
    from extract.resolve import gather_entity_mentions, cluster_entities
    from extract.per_chapter import ChapterExtract
    names = ["林有德", "茜茜", "千寻", "薇欧拉", "薇欧拉小姐", "特蕾西亚", "狐狸"]
    exs = [ChapterExtract(chapter=1, entities=[{"canonical_guess": n, "type": "character"} for n in names])]
    m = gather_entity_mentions(exs)

    def close_embedder(ns):  # 全部高度相似(模拟中文人名嵌入坍缩)
        return [[1.0, 0.01 * i] for i in range(len(ns))]

    canon = cluster_entities(m, embedder=close_embedder, sim_threshold=0.9)
    # 仅 薇欧拉/薇欧拉小姐(子串)合并 → 6;其余 5 个不同人保留,不因嵌入接近坍缩
    assert len(canon) == 6, [c.name for c in canon]
    assert len([c for c in canon if c.type == "character"]) == 6
