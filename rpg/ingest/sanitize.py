"""ingest/sanitize.py — 语料清洗 (Phase A.0 §4.a)。

逐条 port 自 books 项目 server/utils/sanitize.ts (本身 port 自 clean_novel_corpus.py)
的 AD_LINE_TESTS / INLINE_CLEANERS / sanitizeCorpusText,**新增乱码区块检测**。
取代 chapter_splitter._strip_pirate_promo (只有 8 条正则,弱)。

公开:
  sanitize_corpus(text) -> (cleaned_text, report)   # report: 删除统计,进总质量报告
  sanitize_corpus_text(text) -> cleaned_text         # 薄包装,drop-in 替换 _strip_pirate_promo

设计铁律:确定性清洗,不依赖 LLM。全角标点是合法中文,**故意不做全/半角转换**(转了会损坏正文)。
"""
from __future__ import annotations

import re

# ─── 整行判定为广告 → 删除整行 (port AD_LINE_TESTS) ──────────────────────────
AD_LINE_TESTS: list[re.Pattern] = [
    re.compile(r"https?://\S+", re.I),
    re.compile(r"www\.[A-Za-z0-9_.:/?=&%\-]+", re.I),
    re.compile(r"\b[a-zA-Z0-9\-]+\.(?:com|net|org|cc|xyz|top|vip|cn)\b", re.I),
    re.compile(r"搜书吧|sosdbot", re.I),
    re.compile(r"最新地址|最新域名|备用网址|永久地址|请收藏|回家地址|发布页"),
    re.compile(r"关注公众号|加入QQ群|加群|电报群|Telegram|tg群", re.I),
    re.compile(r"蔷薇后花园|黑沼泽俱乐部"),
    re.compile(r"AV片源|AVCAR|avcar", re.I),
    re.compile(r"广告"),
]

# ─── 行内删除匹配片段 (保留行本身) (port INLINE_CLEANERS) ────────────────────
INLINE_CLEANERS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"https?://\S+", re.I), ""),
    (re.compile(r"www\.[A-Za-z0-9_.:/?=&%\-]+", re.I), ""),
    (re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}", re.I), ""),
    (re.compile(r"\b[a-zA-Z0-9\-]+\.(?:com|net|org|cc|xyz|top|vip|cn)\b", re.I), ""),
    (re.compile(r"记住地[址阯]发布页|记住地[址阯]發[布佈]頁|記住地[址阯]發布頁"), ""),
    (re.compile(r"推广送金币|成人论坛加载中"), ""),
    (re.compile(r"温馨提示[:：][^\n]{0,160}"), ""),
    (re.compile(r"特别提示[:：][^\n]{0,180}"), ""),
    # 书库/看书网 inline tags: 【文字首发138看书网】【最新章节笔趣阁】
    (re.compile(r"【[^】]{0,40}(?:看书网|小说网|文学网|下载网|阅读网|电子书|首发|笔趣阁|顶点小说|17k|起点|纵横)[^】]{0,20}】"), ""),
    # //百度搜书名加XXX看最新章节//
    (re.compile(r"//[^/\n]{1,60}(?:看书|小说|最新章节)[^/\n]{0,40}//"), ""),
    # --网站名-- 嵌入句中: --138看书网--
    (re.compile(r"--[^-\n]{1,30}(?:看书网|小说|阅读)[^-\n]{0,20}--"), ""),
    # %网站名% 嵌入句中: %138看书网%
    (re.compile(r"%[^%\n]{1,30}(?:看书网|小说|阅读)[^%\n]{0,20}%"), ""),
]

# 控制字符 (保留 \n \t) — port 自 sanitizeCorpusText step 2
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# ─── 乱码区块检测 (新增,设计 §4.a "乱码区块") ────────────────────────────────
# 编码解错常见两种形态:
#   (a) U+FFFD replacement char (解码器塞的"�")
#   (b) mojibake — gbk 被当 latin-1 读出来的 Latin-1 补充区 / 私用区 / 拼音附加符
# 保守判定:整行才删,且要求长度足够 + 可疑字符密度高,避免误删含个别生僻字的正文。
_REPLACEMENT_CHAR = "�"
_SUSPECT_RANGES = (
    (0x00A0, 0x00FF),  # Latin-1 补充 (mojibake 高发)
    (0x0100, 0x024F),  # Latin Extended A/B
    (0xE000, 0xF8FF),  # 私用区
)


def _is_suspect_char(ch: str) -> bool:
    if ch == _REPLACEMENT_CHAR:
        return True
    cp = ord(ch)
    for lo, hi in _SUSPECT_RANGES:
        if lo <= cp <= hi:
            return True
    return False


def _is_garble_line(stripped: str) -> bool:
    """整行乱码判定 (保守)。"""
    if not stripped:
        return False
    # 任何含 >=2 个 replacement char 的行 → 乱码
    if stripped.count(_REPLACEMENT_CHAR) >= 2:
        return True
    non_space = [c for c in stripped if not c.isspace()]
    if len(non_space) < 6:
        # 太短不判乱码 (可能是合法短句/标点),除非全是可疑字符
        return bool(non_space) and all(_is_suspect_char(c) for c in non_space)
    suspect = sum(1 for c in non_space if _is_suspect_char(c))
    return suspect / len(non_space) > 0.4


def sanitize_corpus(text: str) -> tuple[str, dict]:
    """清洗语料,返回 (cleaned_text, report)。

    report = {removed_lines: int, by_category: {ad, garble, promo}, total_lines: int}
      ad     — 整行广告删除数
      garble — 整行乱码删除数
      promo  — 行内 promo 片段被替换的行数 (行保留)
    """
    if not text:
        return "", {"removed_lines": 0, "by_category": {"ad": 0, "garble": 0, "promo": 0}, "total_lines": 0}

    # 1. 统一换行 + 去 BOM
    text = text.replace("﻿", "")
    text = re.sub(r"\r\n?", "\n", text)
    # 2. 去控制字符 (保留 \n \t)
    text = _CONTROL_RE.sub("", text)

    ad = garble = promo = 0
    lines = text.split("\n")
    total_lines = len(lines)
    cleaned: list[str] = []
    for line in lines:
        stripped = line.strip()
        # 3. 整行广告 → 删
        if stripped and any(rx.search(stripped) for rx in AD_LINE_TESTS):
            ad += 1
            continue
        # 4. 整行乱码 → 删
        if _is_garble_line(stripped):
            garble += 1
            continue
        # 5. 行内 promo 替换 (行保留)
        new = stripped
        for rx, repl in INLINE_CLEANERS:
            new2 = rx.sub(repl, new)
            if new2 != new:
                new = new2
        if new != stripped:
            promo += 1
        cleaned.append(new.strip())

    text = "\n".join(cleaned)
    # 6. 压缩多余空行 (>2 连续 → 1 空行)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    report = {
        "removed_lines": ad + garble,
        "by_category": {"ad": ad, "garble": garble, "promo": promo},
        "total_lines": total_lines,
    }
    return text, report


def sanitize_corpus_text(text: str) -> str:
    """薄包装,只返清洗后文本。drop-in 替换 chapter_splitter._strip_pirate_promo。"""
    return sanitize_corpus(text)[0]
