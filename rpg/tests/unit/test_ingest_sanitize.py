"""Phase A.0 §4.a — ingest.sanitize 单元测试。"""
from __future__ import annotations

from ingest.sanitize import sanitize_corpus, sanitize_corpus_text


def test_removes_ad_lines() -> None:
    text = "正文一句。\n请收藏 www.sosdbot.com 最新地址\n关注公众号领福利\n正文二句。"
    clean, rep = sanitize_corpus(text)
    assert "sosdbot" not in clean
    assert "请收藏" not in clean
    assert "关注公众号" not in clean
    assert "正文一句。" in clean and "正文二句。" in clean
    assert rep["by_category"]["ad"] >= 2


def test_removes_inline_promo_keeps_line() -> None:
    text = "她转身说道。【最新章节笔趣阁】我们走吧。"
    clean, rep = sanitize_corpus(text)
    assert "笔趣阁" not in clean
    assert "她转身说道" in clean and "我们走吧" in clean
    assert rep["by_category"]["promo"] >= 1


def test_removes_garble_lines_keeps_text() -> None:
    text = "正文内容这一行是好的。\næˆ‘æ˜¯ä¹±ç æµ‹è¯•è¡Œè¿žç»­ä¹±ç \n下一行也是好的正文。"
    clean, rep = sanitize_corpus(text)
    assert rep["by_category"]["garble"] >= 1
    assert "正文内容这一行是好的。" in clean
    assert "下一行也是好的正文。" in clean


def test_preserves_legit_chinese_punctuation() -> None:
    # 全角标点是合法中文,绝不能转换/删除
    text = "「你好。」她说，「今天天气不错！」"
    clean = sanitize_corpus_text(text)
    assert clean == "「你好。」她说，「今天天气不错！」"


def test_compresses_blank_lines() -> None:
    text = "第一段。\n\n\n\n第二段。"
    clean = sanitize_corpus_text(text)
    assert "\n\n\n" not in clean


def test_empty_input() -> None:
    clean, rep = sanitize_corpus("")
    assert clean == ""
    assert rep["removed_lines"] == 0


if __name__ == "__main__":
    test_removes_ad_lines()
    test_removes_inline_promo_keeps_line()
    test_removes_garble_lines_keeps_text()
    test_preserves_legit_chinese_punctuation()
    test_compresses_blank_lines()
    test_empty_input()
    print("OK")
