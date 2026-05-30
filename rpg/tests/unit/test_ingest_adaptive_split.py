"""Phase A.0 §3 — ingest.adaptive_split 单元测试。"""
from __future__ import annotations

from ingest.adaptive_split import (
    _cn_to_int,
    adaptive_split,
    build_candidate_rules,
    extract_seq,
    split_by_heading_regex,
    structural_score,
)


def _book(n: int, chapter_chars: int = 1200) -> str:
    return "\n".join(f"第{i}章 标题{i}\n" + "正文内容。" * (chapter_chars // 5) for i in range(1, n + 1))


def test_cn_number_parsing() -> None:
    assert _cn_to_int("3") == 3
    assert _cn_to_int("１８８") == 188
    assert _cn_to_int("一百零八") == 108
    assert _cn_to_int("二十") == 20
    assert _cn_to_int("三千五百") == 3500
    assert _cn_to_int("零") == 0


def test_extract_seq_takes_first_ordinal() -> None:
    assert extract_seq("第188章 柏林") == 188
    assert extract_seq("804说好的爆发推迟了（75）") == 804
    assert extract_seq("没有数字的标题") is None


def test_normal_book_splits_cleanly() -> None:
    chapters, report = adaptive_split(_book(20))
    assert len(chapters) == 20
    assert report["gaps"] == []
    assert report["rule_chosen"]["score"] > 0.6


def test_gap_detection_on_missing_chapter() -> None:
    text = "\n".join(f"第{i}章 标题\n" + "正文。" * 200 for i in [1, 2, 3, 4, 6, 7, 8])
    _, report = adaptive_split(text)
    assert any(g["expected_index"] == 5 for g in report["gaps"])


def test_derived_rule_fires_on_custom_format() -> None:
    custom = "\n".join(f"◇{i}◇ 第{i}节\n" + "正文。" * 150 for i in range(1, 12))
    rules = build_candidate_rules(custom)
    assert any(r.kind == "derived" for r in rules)


def test_score_ranks_correct_split_over_glued() -> None:
    good = _book(20)
    good_chapters = split_by_heading_regex(good, build_candidate_rules(good)[0].regex)
    good_score, _ = structural_score(good_chapters, good)
    glued = [{"title": "第1章", "content": good, "chapter_number": 1}]
    glued_score, _ = structural_score(glued, good)
    assert good_score > glued_score


def test_no_recovery_on_uniform_sizes() -> None:
    # 均匀章长里某章略大不应被误判离群拆碎(回归 fuse bug)
    chapters, _ = adaptive_split(_book(12))
    assert len(chapters) == 12
    assert all(c["content"].strip() for c in chapters)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("OK")
