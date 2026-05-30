"""Phase A.0 §4.b/c — ingest.filters 单元测试。"""
from __future__ import annotations

from ingest.filters import annotate_weird_titles, filter_non_content


def test_author_note_by_title() -> None:
    chs = [
        {"title": "第一卷 小结", "content": "本卷结束，谢谢月票，明天新卷。"},
        {"title": "请假通知", "content": "今天请假，明天补更，抱歉。"},
    ]
    filter_non_content(chs)
    assert all(c["is_author_note"] and c["exclude_from_extraction"] for c in chs)


def test_author_note_by_structure_normal_title() -> None:
    # 标题正常但正文是作者的话 → 结构信号命中
    chs = [{"title": "第190章 决战", "content": "作者：这章写得爽，大家记得投票哦，明天更新。"}]
    filter_non_content(chs)
    assert chs[0]["is_author_note"]


def test_normal_chapter_not_flagged() -> None:
    chs = [{"title": "第188章 柏林暗流", "content": "蕾穆丽娜走进会议室。「行动。」她说道。" + "正文。" * 200}]
    filter_non_content(chs)
    assert not chs[0]["is_author_note"]
    assert not chs[0]["exclude_from_extraction"]


def test_weird_meme_title_downgraded_with_descriptor() -> None:
    chs = [{"title": "第804章 说好的爆发推迟了（75）", "content": "蕾穆丽娜站在城墙上，远望敌军。"}]
    annotate_weird_titles(chs)
    assert chs[0]["title_confidence"] < 0.6
    assert chs[0]["content_descriptor"]


def test_normal_title_high_confidence_no_descriptor() -> None:
    chs = [{"title": "第5章 柏林会战", "content": "炮火连天，双方激战。"}]
    annotate_weird_titles(chs)
    assert chs[0]["title_confidence"] >= 0.6
    assert chs[0]["content_descriptor"] == ""


def test_embedder_injection_lowers_confidence_on_low_sim() -> None:
    # 注入一个"标题与正文不相关"的假嵌入器 → 触发嵌入信号降可信
    chs = [{"title": "第3章 神秘标题", "content": "完全无关的正文内容这里。"}]

    def fake_embedder(texts):
        # 标题向量 vs 正文向量 正交 → 相似度 0
        return [[1.0, 0.0] if k % 2 == 0 else [0.0, 1.0] for k in range(len(texts))]

    annotate_weird_titles(chs, embedder=fake_embedder, sim_threshold=0.5)
    assert chs[0]["title_confidence"] < 1.0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("OK")
