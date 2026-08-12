"""幽灵空章根治回归测试。

因果链(见 project 审计,勿重复诊断):
  ① adaptive_split.py _DIVIDER_ONLY 提升逻辑把分隔符后第一条非空行(如"正文")
     提为标题,而该章正文为空 → 幽灵空章。
  ② chapter_splitter.py 丢弃条件是 `not title and not content`(AND),
     有标题无正文永不丢弃。
  ③ ingest/filters.py 两道启发式对"零正文"都够不到阈值,漏判。

本文件验证三处根修:标题被提升但正文为空的章不产出;分隔符+真标题+真正文的
正常用例不被误伤;filters 对零内容硬拦截。
"""
from __future__ import annotations

from chapter_splitter import ChapterSplitter
from ingest.adaptive_split import build_candidate_rules, split_by_heading_regex
from ingest.filters import filter_non_content


def _hr_rule():
    for rule in build_candidate_rules("------------\n正文\n"):
        if rule.id == "hr_divider":
            return rule
    raise AssertionError("hr_divider preset rule missing")


def _padded(text: str, repeat: int = 40) -> str:
    return (text + "，") * repeat


# ─── ① 剧本11 真实开头形态:分隔符 + 孤立"正文"行 + 分隔符 + 真标题 ──────────
def _script11_like_text() -> str:
    real_body = "他一睁眼发现自己躺在陌生的床榻上。" * 30
    return (
        "------------\n"
        "\n"
        "正文\n"
        "\n"
        "\n"
        "------------\n"
        "\n"
        f"第1章001穿越到了德……咦?\n"
        f"{real_body}\n"
    )


def test_adaptive_split_drops_promoted_empty_divider_chapter() -> None:
    text = _script11_like_text()
    rule = _hr_rule()
    chapters = split_by_heading_regex(text, rule.regex)
    # 不应产出正文为空的"正文"幽灵章
    assert all(c["content"].strip() for c in chapters), chapters
    assert not any(c["title"] == "正文" and not c["content"].strip() for c in chapters)
    # 第一章应是真标题,不是被提升的占位行
    assert chapters[0]["title"] == "第1章001穿越到了德……咦?"
    assert chapters[0]["chapter_number"] == 1
    assert "他一睁眼发现自己躺在陌生的床榻上" in chapters[0]["content"]


def test_chapter_splitter_end_to_end_no_ghost_chapter() -> None:
    splitter = ChapterSplitter()
    text = _script11_like_text() + (
        "\n------------\n\n第2章002继续冒险\n" + "后续正文继续发展的故事情节。" * 30 + "\n"
    )
    chapters, report = splitter.split_chapters_with_report(text)

    assert all(c["content"].strip() for c in chapters), chapters
    assert chapters[0]["title"] == "第1章001穿越到了德……咦?"
    # chapter_number 序号从 1 起,连续无空洞
    numbers = [c["chapter_number"] for c in chapters]
    assert numbers == list(range(1, len(numbers) + 1))
    assert not any(c.get("title") == "正文" for c in chapters)


# ─── ② 正常用例不误伤:分隔符 + 真标题 + 真正文 ────────────────────────────
def test_divider_with_real_title_and_content_not_dropped() -> None:
    real_body = "这是一段完整的正文内容，描述主角的冒险经历与心境变化。" * 20
    text = (
        "------------\n"
        "第5章 风起云涌\n"
        f"{real_body}\n"
        "------------\n"
        "第6章 剑指苍穹\n"
        f"{real_body}\n"
    )
    rule = _hr_rule()
    chapters = split_by_heading_regex(text, rule.regex)

    assert len(chapters) == 2
    assert chapters[0]["title"] == "第5章 风起云涌"
    assert chapters[1]["title"] == "第6章 剑指苍穹"
    assert all(c["content"].strip() for c in chapters)


def test_chapter_splitter_normal_divider_chapters_preserved() -> None:
    splitter = ChapterSplitter()
    real_body = "少年握紧了手中的长剑，望向远方的山脉。" * 25
    text = (
        "------------\n"
        "第1章 出发\n"
        f"{real_body}\n"
        "------------\n"
        "第2章 抵达\n"
        f"{real_body}\n"
    )
    chapters, _ = splitter.split_chapters_with_report(text)
    titles = [c["title"] for c in chapters]
    assert "第1章 出发" in titles
    assert "第2章 抵达" in titles
    assert all(c["content"].strip() for c in chapters)


# ─── ③ filters 零内容硬规则 ─────────────────────────────────────────────────
def test_filter_non_content_hard_rule_on_empty_body() -> None:
    chs = [
        {"title": "正文", "content": ""},
        {"title": "正文", "content": "   \n\n  "},  # 仅空白
        {"title": "第7章 正常", "content": "正常的一段章节正文内容。" * 10},
    ]
    filter_non_content(chs)
    assert chs[0]["is_author_note"] is True
    assert chs[0]["exclude_from_extraction"] is True
    assert chs[0]["_note_reason"] == "empty_content"
    assert chs[1]["is_author_note"] is True
    assert chs[1]["exclude_from_extraction"] is True
    # 有真实正文的章不受影响
    assert chs[2]["is_author_note"] is False
    assert chs[2]["exclude_from_extraction"] is False


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
    print("OK")
