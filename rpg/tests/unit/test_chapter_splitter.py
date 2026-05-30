from __future__ import annotations

from chapter_splitter import ChapterSplitter


def paragraph(seed: str, repeat: int = 35) -> str:
    return (seed + "这是用于模拟正文长度的段落，包含叙事、动作和对话，避免被误判为列表项。") * repeat


def test_standard_chinese_chapters() -> None:
    splitter = ChapterSplitter()
    text = "\n".join([
        "第1章 初遇",
        paragraph("少女在雨夜里推开门。"),
        "第二章 线索",
        paragraph("旧照片背后藏着新的线索。"),
        "第３章 追踪",
        paragraph("他们沿着河岸一路追踪。"),
    ])

    chapters, report = splitter.split_chapters_with_report(text)

    assert [chapter["title"] for chapter in chapters] == ["第1章 初遇", "第二章 线索", "第３章 追踪"]
    # Phase A.0: auto 路径走规则融合自适应切分(与 _split_auto 竞争取优)
    assert report["mode"] == "adaptive_fusion"
    assert report["problem_category"] == "ok"
    # 报告 v2 新字段
    assert report["rule_chosen"] is not None
    assert "score_breakdown" in report and report["score_breakdown"]
    assert report["gaps"] == []


def test_english_number_dot_and_custom_rules() -> None:
    splitter = ChapterSplitter()

    english, english_report = splitter.split_chapters_with_report(
        "Chapter 1 Arrival\n" + paragraph("Rain hit the window.", 15) + "\nChapter 2 Signal\n" + paragraph("The signal returned.", 15),
        split_rule="chapter_en",
    )
    assert [chapter["title"] for chapter in english] == ["Chapter 1 Arrival", "Chapter 2 Signal"]
    assert english_report["mode"] == "rule_chapter_en"

    numbered, numbered_report = splitter.split_chapters_with_report(
        "1. 开端\n" + paragraph("一行新的线索。", 15) + "\n2、推进\n" + paragraph("队伍继续前进。", 15),
        split_rule="number_dot",
    )
    assert [chapter["title"] for chapter in numbered] == ["1. 开端", "2、推进"]
    assert numbered_report["mode"] == "rule_number_dot"

    custom, custom_report = splitter.split_chapters_with_report(
        "卷一-第1章-风起\n" + paragraph("风从城市边缘吹来。", 15) + "\n卷一-第2章-雨落\n" + paragraph("雨落在旧桥上。", 15),
        split_rule="custom",
        custom_pattern="卷一-第*章",
    )
    assert [chapter["title"] for chapter in custom] == ["卷一-第1章-风起", "卷一-第2章-雨落"]
    assert custom_report["mode"] == "custom_pattern"


def test_numbered_sections_and_pagination() -> None:
    splitter = ChapterSplitter()
    sections = "\n".join([
        "异象旅馆 第一幕：来自异国的求助信号",
        "",
        "（1）",
        paragraph("少女的睫毛轻轻颤动。", 55),
        "",
        "（2）",
        paragraph("魔法屏障像水纹一样恢复。", 55),
        "",
        "（3）",
        paragraph("诺森把地图摊开。", 55),
    ])

    chapters, report = splitter.split_chapters_with_report(sections)

    assert len(chapters) == 3
    assert all("第一幕" in chapter["title"] for chapter in chapters)
    assert report["mode"] == "numbered_sections"

    pages = "\n".join([
        "异象旅馆(1)",
        paragraph("午后的阳光穿过图书馆顶层的彩绘玻璃。"),
        "异象旅馆(2)",
        paragraph("魔法屏障像水纹一样恢复。"),
        "异象旅馆(3)",
        paragraph("诺森把地图摊开。"),
    ])

    page_chapters, page_report = splitter.split_chapters_with_report(pages)

    assert [chapter["title"] for chapter in page_chapters] == ["异象旅馆(1)", "异象旅馆(2)", "异象旅馆(3)"]
    # Phase A.0: 分页式标题由规则融合的 paren_num 候选切出(同样 3 章、标题一致)
    assert page_report["mode"] in {"pagination_headings", "adaptive_fusion"}


def test_mixed_volume_chapter_generic_no_book_specific() -> None:
    """Phase A.0: 删除 REMULINA 书本特化后,混合卷-章标题仍由**通用**规则切分。

    这是 换书不退化 的回归保证:不靠任何书名/书本特化代码,
    通用 STRONG_CHAPTER_PATTERNS 就能识别 正卷－第X卷…第Y章 这类混合标题。
    """
    splitter = ChapterSplitter()
    text = "\n".join([
        "正卷－第一卷 火星十年，大战将起－第一章－虐待致死",
        paragraph("第一章正文。", 20),
        "正卷－第一卷 火星十年，大战将起－第二章－惊醒",
        paragraph("第二章正文。", 20),
        "第一卷 小结",
        paragraph("小结正文。", 20),
        "外卷－第一卷－相性百问－第一章－粉金主仆百问一",
        paragraph("百问正文。", 20),
    ])

    chapters, report = splitter.split_chapters_with_report(
        text,
        source_name="任意书名.txt",  # 不再依赖书名
        title="任意书名",
    )

    titles = [chapter["title"] for chapter in chapters]
    assert titles == [
        "正卷－第一卷 火星十年，大战将起－第一章－虐待致死",
        "正卷－第一卷 火星十年，大战将起－第二章－惊醒",
        "第一卷 小结",
        "外卷－第一卷－相性百问－第一章－粉金主仆百问一",
    ]
    # 不再有书本特化的 remulina_special 模式
    assert report["mode"] != "remulina_special"
    # 卷末"小结"应被作者非正文过滤标注(报告 v2)
    assert any("小结" in note["title"] for note in report.get("author_notes", []))


def test_fallback_report_for_unmarked_long_text() -> None:
    splitter = ChapterSplitter()
    chapters, report = splitter.split_chapters_with_report(paragraph("没有章节标题的长文本。", 180))

    assert chapters
    # 无标题长文 → 规则融合分数过低被否决,回落固定窗口
    assert report["mode"] == "fallback_window"
    assert report["problem_category"] == "no_heading_match"


def test_report_v2_fields_present() -> None:
    """报告 v2 必有的新字段(A0_ingestion §5)。"""
    splitter = ChapterSplitter()
    text = "\n".join([
        "第1章 开始", paragraph("正文一。"),
        "第2章 推进", paragraph("正文二。"),
        "第3章 卷末感言", "本卷完结，谢谢大家月票，明天新卷，抱歉这章短。",
    ])
    _, report = splitter.split_chapters_with_report(text)
    for key in ("rule_chosen", "rule_runnerup", "score_breakdown", "gaps",
                "cleaning", "author_notes", "weird_titles", "needs_review"):
        assert key in report, f"报告 v2 缺字段 {key}"
    assert isinstance(report["cleaning"], dict)
    assert any("感言" in n["title"] for n in report["author_notes"])


if __name__ == "__main__":
    test_standard_chinese_chapters()
    test_english_number_dot_and_custom_rules()
    test_numbered_sections_and_pagination()
    test_mixed_volume_chapter_generic_no_book_specific()
    test_fallback_report_for_unmarked_long_text()
    test_report_v2_fields_present()
