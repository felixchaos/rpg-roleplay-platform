"""Chapter recognition and TXT splitting rules shared by script imports.

This module merges the rule sets previously used in MuMuAINovel and
Stellatrix/books.  It deliberately stays dependency-free so it can be reused
by platform imports, background indexing, and future worker deployments.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from re import Pattern
from statistics import mean, stdev

NUMBER_TOKEN = r"零一二三四五六七八九十百千万〇两\d０-９"


@dataclass
class SplitPattern:
    pattern: Pattern[str]
    label: str


class ChapterSplitter:
    """Rule-first TXT chapter splitter with diagnostics."""

    SECTION_MARKER_PATTERN = re.compile(rf"^[（(]\s*([{NUMBER_TOKEN}]{{1,8}})\s*[）)]$")
    PAGINATION_HEADING_PATTERN = re.compile(r"^(.{1,60}?)[（(]\s*([0-9０-９]{1,5})\s*[）)]$")
    ACT_HEADING_PATTERN = re.compile(
        rf"^(?:.{{0,80}}?\s+)?(第[{NUMBER_TOKEN}]+[幕卷部集篇](?:\s*[：:、.．\-—]\s*.+|.+)?)$"
    )
    VOLUME_PATTERN = re.compile(rf"^(.{{0,30}}第[{NUMBER_TOKEN}]+卷.*)$")

    STRONG_CHAPTER_PATTERNS = [
        re.compile(rf"^第[{NUMBER_TOKEN}]+(?:[章回卷集部篇幕场].*|节(?:$|[\s　:：、.．\-—]).*)$"),
        re.compile(r"^(?:楔子|引子|序[章言曲]?|后记|尾声|终章|完本感言|番外)(?:$|[\s　:：、.．\-—].*)$"),
        re.compile(r"^#{1,3}\s+\S.+$"),
        re.compile(r"^(?:chapter|chap\.|part)\s*[0-9０-９ivxlcdm]+.*$", re.IGNORECASE),
        re.compile(r"^(?:prologue|epilogue).*$", re.IGNORECASE),
        re.compile(rf"^(?:正卷|外卷)[－-](?:第|正)?[{NUMBER_TOKEN}]+卷.*?(?:第[{NUMBER_TOKEN}]+[章节]|第[{NUMBER_TOKEN}]+节|尾章).*$"),
    ]

    RULE_PATTERNS: dict[str, SplitPattern] = {
        "chapter_cn": SplitPattern(
            re.compile(rf"^(.{{0,30}}(?:第[{NUMBER_TOKEN}]+[章节集回]|[序楔]章|楔子|引[子言]|前言|番外).*)$", re.MULTILINE),
            "中文章节",
        ),
        "corpus": SplitPattern(
            re.compile(
                rf"^(.{{0,40}}(?:第[{NUMBER_TOKEN}]+[章节集回卷]|第[{NUMBER_TOKEN}]+部|[序楔终]章|楔子|引[子言]|前言|正文|番外|外传|大结局).*)$",
                re.MULTILINE,
            ),
            "语料章节",
        ),
        "chapter_en": SplitPattern(re.compile(r"^(Chapter\s+[0-9０-９]+.*)$", re.IGNORECASE | re.MULTILINE), "英文章节"),
        "number_dot": SplitPattern(re.compile(r"^([0-9０-９]+[.、]\s*.*)$", re.MULTILINE), "数字点号"),
        "paren_num": SplitPattern(re.compile(rf"^(.{{0,10}}[（(]\s*[{NUMBER_TOKEN}]+\s*[)）].*)$", re.MULTILINE), "括号编号"),
    }

    SPLIT_MODE_LABELS = {
        "empty": "空文本",
        "custom_pattern": "自定义规则",
        "numbered_sections": "篇章小节",
        "pagination_headings": "分页标题",
        "adaptive_fusion": "规则融合自适应",
        "rule_chapter_cn": "中文章节规则",
        "rule_corpus": "语料章节规则",
        "rule_chapter_en": "英文章节规则",
        "rule_number_dot": "数字点号规则",
        "rule_paren_num": "括号编号规则",
        "strong_headings": "标准章节标题",
        "weak_headings": "弱标题推断",
        "fallback_window": "固定窗口兜底",
        "quality_fallback_window": "质量兜底窗口",
    }

    # (Phase A.0) 已删除 REMULINA_* 书本特化硬编码 — 反模式。
    # auto 切分改走 ingest.adaptive_split 规则融合自适应,换任意网文不退化。

    def decode_bytes(self, content: bytes) -> tuple[str, str]:
        for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk", "big5"):
            try:
                return content.decode(encoding), encoding
            except UnicodeDecodeError:
                continue
        return content.decode("utf-8", errors="ignore"), "utf-8(ignore)"

    def _normalize_encoding(self, text: str) -> str:
        """\u7f16\u7801/\u6362\u884c/\u5168\u89d2\u7a7a\u683c\u5f52\u4e00 (\u4e0d\u542b\u5e7f\u544a\u6e05\u6d17)\u3002\u5e7f\u544a\u6e05\u6d17\u4ea4 ingest.sanitize\u3002"""
        normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\ufeff", "")
        normalized = normalized.replace("\u3000", "  ")
        normalized = re.sub(r"[ \t]+\n", "\n", normalized)
        normalized = re.sub(r"\n{4,}", "\n\n\n", normalized)
        return normalized

    def _strip_pirate_promo(self, text: str) -> str:
        """(Phase A.0) \u59d4\u6258 ingest.sanitize \u2014 \u53d6\u4ee3\u539f 8 \u6761\u5f31\u6b63\u5219,\u65b0\u7248\u542b\u4e71\u7801\u68c0\u6d4b\u3002"""
        from ingest.sanitize import sanitize_corpus_text
        return sanitize_corpus_text(text)

    def clean_text(self, text: str) -> str:
        """\u7f16\u7801\u5f52\u4e00 + \u5e7f\u544a/\u4e71\u7801\u6e05\u6d17 (text-only \u5165\u53e3)\u3002\u5e26\u62a5\u544a\u7684\u6e05\u6d17\u89c1 split_chapters_with_report\u3002"""
        from ingest.sanitize import sanitize_corpus_text
        return sanitize_corpus_text(self._normalize_encoding(text)).strip()

    def split_chapters(
        self,
        text: str,
        *,
        split_rule: str = "auto",
        custom_pattern: str = "",
        source_name: str = "",
        title: str = "",
    ) -> list[dict]:
        chapters, _ = self.split_chapters_with_report(
            text,
            split_rule=split_rule,
            custom_pattern=custom_pattern,
            source_name=source_name,
            title=title,
        )
        return chapters

    def split_chapters_with_report(
        self,
        text: str,
        *,
        split_rule: str = "auto",
        custom_pattern: str = "",
        source_name: str = "",
        title: str = "",
    ) -> tuple[list[dict], dict]:
        # Phase A.0: 编码归一 → 广告/乱码清洗(带报告) → 规则融合切分 → 三道过滤 → 报告 v2
        normalized = self._normalize_encoding(text)
        from ingest.sanitize import sanitize_corpus
        cleaned, cleaning_report = sanitize_corpus(normalized)
        cleaned = cleaned.strip()
        chapters, split_mode, adaptive_report = self._split_chapters_internal(
            cleaned,
            split_rule=(split_rule or "auto").strip() or "auto",
            custom_pattern=custom_pattern or "",
            source_name=source_name or "",
            title=title or "",
        )
        chapters = self._post_process_chapters(chapters)
        # 三道过滤之 作者非正文 + 怪标题 (广告/乱码已在 sanitize_corpus 完成)
        from ingest.filters import annotate_weird_titles, filter_non_content
        filter_non_content(chapters)
        annotate_weird_titles(chapters)
        report = self._build_split_report(chapters=chapters, split_mode=split_mode, source_text=cleaned)
        report["split_rule"] = split_rule or "auto"
        self._augment_report_v2(report, chapters, cleaning_report, adaptive_report)
        return chapters, report

    def _split_chapters_internal(
        self,
        text: str,
        *,
        split_rule: str,
        custom_pattern: str,
        source_name: str,  # noqa: ARG002 — 保留 API 兼容(旧 REMULINA 用,现不用)
        title: str,  # noqa: ARG002
    ) -> tuple[list[dict], str, dict | None]:
        """返回 (chapters, split_mode, adaptive_report|None)。

        用户显式选规则 (custom / 预设) → 走该规则;否则 auto → 规则融合自适应切分。
        """
        if not text.strip():
            return [], "empty", None

        # 用户显式自定义规则
        if split_rule == "custom":
            pattern = self.build_custom_pattern(custom_pattern)
            if pattern:
                chapters = self._flatten_volumes(self._split_with_volumes(text, pattern))
                if chapters:
                    return chapters, "custom_pattern", None

        # 用户显式选了某条预设规则
        if split_rule in self.RULE_PATTERNS:
            chapters = self._flatten_volumes(self._split_with_volumes(text, self.RULE_PATTERNS[split_rule].pattern))
            processed = self._post_process_chapters(chapters)
            if processed and self._has_reasonable_chapter_quality(processed):
                return processed, f"rule_{split_rule}", None

        # auto → 规则融合自适应切分 与 既有 _split_auto 检测器(分页/篇章小节/弱标题/
        # 固定窗口)**按结构性评分竞争取优**。既保留 _split_auto 的通用能力(换书不退化),
        # 又用 adaptive 的候选库+评分+漏切回收补强。两者都跑(import 一次性,成本可接受)。
        from ingest.adaptive_split import adaptive_split, structural_score

        adaptive_chapters, adaptive_report = adaptive_split(text)
        adaptive_score = float((adaptive_report.get("rule_chosen") or {}).get("score", 0.0))
        adaptive_ok = (
            len(adaptive_chapters) > 1
            and adaptive_score >= 0.5
            and self._has_reasonable_chapter_quality(adaptive_chapters)
        )

        auto_chapters, auto_mode = self._split_auto(text)
        auto_score = structural_score(auto_chapters, text)[0] if len(auto_chapters) > 1 else 0.0
        auto_ok = bool(auto_chapters) and self._has_reasonable_chapter_quality(auto_chapters)

        # adaptive 多章且分数不低于 auto → 采纳 adaptive;否则若 auto 可靠用 auto
        if adaptive_ok and adaptive_score >= auto_score:
            return adaptive_chapters, "adaptive_fusion", adaptive_report
        if auto_ok:
            return auto_chapters, auto_mode, adaptive_report
        if adaptive_ok:
            return adaptive_chapters, "adaptive_fusion", adaptive_report
        # 都不可靠 → 用 auto 的兜底结果(通常 fallback_window)
        return (auto_chapters or adaptive_chapters), (auto_mode if auto_chapters else "adaptive_fusion"), adaptive_report

    def _split_auto(self, text: str) -> tuple[list[dict], str]:
        lines = text.split("\n")
        strong_heading_indexes: list[int] = []
        weak_heading_indexes: list[int] = []

        for idx, line in enumerate(lines):
            stripped = line.strip()
            if not stripped:
                continue
            if self._is_strong_heading(stripped):
                strong_heading_indexes.append(idx)
            elif self._is_weak_heading(lines, idx):
                weak_heading_indexes.append(idx)

        section_heading_indexes = self._collect_numbered_section_headings(lines)
        if section_heading_indexes and (
            len(strong_heading_indexes) < 2 or len(section_heading_indexes) >= len(strong_heading_indexes) * 2
        ):
            section_chapters = self._split_numbered_sections(lines, section_heading_indexes)
            if section_chapters:
                return section_chapters, "numbered_sections"

        if len(strong_heading_indexes) < 2:
            pagination_heading_indexes = self._collect_pagination_headings(lines)
            if pagination_heading_indexes:
                pagination_chapters = self._split_standard_headings(lines, pagination_heading_indexes)
                if pagination_chapters:
                    return pagination_chapters, "pagination_headings"

        has_strong_mode = len(strong_heading_indexes) >= 2
        heading_indexes = sorted(set(strong_heading_indexes if has_strong_mode else strong_heading_indexes + weak_heading_indexes))
        if not heading_indexes:
            return self._fallback_split(text), "fallback_window"

        chapters = self._split_standard_headings(lines, heading_indexes)
        processed = self._post_process_chapters(chapters)
        if processed and self._has_reasonable_chapter_quality(processed):
            return processed, "strong_headings" if has_strong_mode else "weak_headings"

        return self._fallback_split(text), "quality_fallback_window"

    def _split_standard_headings(self, lines: list[str], heading_indexes: list[int]) -> list[dict]:
        chapters: list[dict] = []
        chapter_no = 1
        first_heading = heading_indexes[0]
        if first_heading > 0:
            preface = "\n".join(lines[:first_heading]).strip()
            if len(preface) >= 200:
                chapters.append({"title": "前言", "content": preface, "chapter_number": chapter_no})
                chapter_no += 1

        for i, start_idx in enumerate(heading_indexes):
            end_idx = heading_indexes[i + 1] if i + 1 < len(heading_indexes) else len(lines)
            title = lines[start_idx].strip()[:200] or f"第{chapter_no}章"
            body = "\n".join(lines[start_idx + 1 : end_idx]).strip()
            if not body and i + 1 < len(heading_indexes):
                body = lines[start_idx + 1].strip() if start_idx + 1 < len(lines) else ""
            chapters.append({"title": title, "content": body, "chapter_number": chapter_no})
            chapter_no += 1
        return [chapter for chapter in chapters if chapter["title"] or chapter["content"]]

    def _split_with_volumes(self, text: str, pattern: Pattern[str]) -> list[dict]:
        lines = text.split("\n")
        volume_markers = []
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if self.VOLUME_PATTERN.match(stripped) and not pattern.match(stripped):
                volume_markers.append({"title": stripped, "line_idx": idx})

        if not volume_markers:
            return [{"title": "", "chapters": self._split_by_pattern(text, pattern)}]

        volumes = []
        if volume_markers[0]["line_idx"] > 0:
            pre_content = "\n".join(lines[: volume_markers[0]["line_idx"]]).strip()
            if pre_content:
                pre_chapters = self._split_by_pattern(pre_content, pattern)
                if pre_chapters:
                    volumes.append({"title": "", "chapters": pre_chapters})

        for index, marker in enumerate(volume_markers):
            start = marker["line_idx"] + 1
            end = volume_markers[index + 1]["line_idx"] if index + 1 < len(volume_markers) else len(lines)
            section = "\n".join(lines[start:end])
            volumes.append({"title": marker["title"], "chapters": self._split_by_pattern(section, pattern)})
        return volumes

    def _split_by_pattern(self, text: str, pattern: Pattern[str]) -> list[dict]:
        chapters = self._line_split(text, pattern)
        if len(chapters) <= 1 and len(text) > 500:
            position_chapters = self._position_split(text, pattern)
            if len(position_chapters) > len(chapters):
                return position_chapters
        return chapters

    def _line_split(self, text: str, pattern: Pattern[str]) -> list[dict]:
        lines = text.split("\n")
        chapters: list[dict] = []
        current_title = ""
        current_lines: list[str] = []

        for line in lines:
            trimmed = line.strip()
            match = pattern.match(trimmed)
            if match:
                if current_title or current_lines:
                    chapters.append(
                        {
                            "title": current_title or ("序章" if not chapters else f"第{len(chapters) + 1}章"),
                            "content": "\n".join(current_lines).strip(),
                            "chapter_number": len(chapters) + 1,
                        }
                    )
                current_title = (match.group(1) if match.groups() else trimmed).strip()
                current_lines = []
            else:
                current_lines.append(line)

        if current_title or current_lines:
            chapters.append(
                {
                    "title": current_title or ("序章" if not chapters else f"第{len(chapters) + 1}章"),
                    "content": "\n".join(current_lines).strip(),
                    "chapter_number": len(chapters) + 1,
                }
            )
        return [chapter for chapter in chapters if chapter["content"]]

    def _position_split(self, text: str, pattern: Pattern[str]) -> list[dict]:
        matches = []
        for match in pattern.finditer(text):
            line_start = text.rfind("\n", 0, match.start()) + 1
            line_end = text.find("\n", match.start())
            if line_end == -1:
                line_end = len(text)
            line = text[line_start:line_end].strip()
            if line:
                matches.append({"index": line_start, "content_start": line_end + 1, "title": line})
        if not matches:
            return []

        chapters: list[dict] = []
        if matches[0]["index"] > 0:
            preface = text[: matches[0]["index"]].strip()
            if preface:
                chapters.append({"title": "序章", "content": preface, "chapter_number": 1})

        for index, marker in enumerate(matches):
            end = matches[index + 1]["index"] if index + 1 < len(matches) else len(text)
            body = text[marker["content_start"] : end].strip()
            if body:
                chapters.append({"title": marker["title"], "content": body, "chapter_number": len(chapters) + 1})
        return chapters

    def _flatten_volumes(self, volumes: list[dict]) -> list[dict]:
        chapters = []
        for volume in volumes:
            volume_title = str(volume.get("title") or "")
            for chapter in volume.get("chapters") or []:
                item = dict(chapter)
                item["volume_title"] = volume_title
                item["chapter_number"] = len(chapters) + 1
                chapters.append(item)
        return chapters

    def _collect_pagination_headings(self, lines: list[str]) -> list[int]:
        candidates: list[tuple[int, str, int]] = []
        for idx, line in enumerate(lines):
            match = self.PAGINATION_HEADING_PATTERN.match(line.strip())
            if not match:
                continue
            title = match.group(1).strip()
            page_no = self._to_int(match.group(2))
            if not title or page_no <= 0:
                continue
            if len(title) > 40 or re.search(r"[。！？!?；;]", title):
                continue
            next_idx = self._next_nonempty_line_index(lines, idx + 1)
            if next_idx is None or len(lines[next_idx].strip()) < 20:
                continue
            candidates.append((idx, title, page_no))
        if len(candidates) < 3:
            return []

        title_counts: dict[str, int] = {}
        for _, title, _ in candidates:
            title_counts[title] = title_counts.get(title, 0) + 1
        dominant_title, dominant_count = max(title_counts.items(), key=lambda item: item[1])
        if dominant_count < max(3, int(len(candidates) * 0.6)):
            return []
        filtered = [(idx, page_no) for idx, title, page_no in candidates if title == dominant_title]
        pages = sorted(set(page_no for _, page_no in filtered))
        if len(pages) < 3 or pages[0] != 1:
            return []
        if max(pages) - min(pages) + 1 > len(pages) + 2:
            return []
        return [idx for idx, _ in filtered]

    def _collect_numbered_section_headings(self, lines: list[str]) -> list[int]:
        candidates = [idx for idx in range(len(lines)) if self._is_numbered_section_heading(lines, idx)]
        if len(candidates) < 2:
            return []
        section_lengths = []
        for index, start in enumerate(candidates):
            next_idx = candidates[index + 1] if index + 1 < len(candidates) else len(lines)
            end = self._trim_trailing_act_heading(lines, start + 1, next_idx)
            section_text = "\n".join(lines[start + 1 : end]).strip()
            if section_text:
                section_lengths.append(len(section_text))
        if len(section_lengths) < 2:
            return []
        long_sections = sum(length >= 500 for length in section_lengths)
        if long_sections < max(2, len(section_lengths) // 2):
            return []
        return candidates

    def _is_numbered_section_heading(self, lines: list[str], idx: int) -> bool:
        line = lines[idx].strip()
        if not self.SECTION_MARKER_PATTERN.match(line):
            return False
        next_idx = self._next_nonempty_line_index(lines, idx + 1)
        if next_idx is None:
            return False
        next_line = lines[next_idx].strip()
        if len(next_line) < 20 or self._is_strong_heading(next_line):
            return False
        return True

    def _split_numbered_sections(self, lines: list[str], heading_indexes: list[int]) -> list[dict]:
        chapters: list[dict] = []
        chapter_no = 1
        current_act_title: str | None = None
        scan_from = 0

        first_heading = heading_indexes[0]
        if first_heading > 0:
            preface_lines = []
            for idx in range(first_heading):
                stripped = lines[idx].strip()
                act_title = self._extract_act_heading(stripped)
                if act_title:
                    current_act_title = act_title
                elif stripped:
                    preface_lines.append(lines[idx])
            preface = "\n".join(preface_lines).strip()
            if len(preface) >= 200:
                chapters.append({"title": "前言", "content": preface, "chapter_number": chapter_no})
                chapter_no += 1
            scan_from = first_heading + 1

        for index, start_idx in enumerate(heading_indexes):
            for idx in range(scan_from, start_idx):
                act_title = self._extract_act_heading(lines[idx].strip())
                if act_title:
                    current_act_title = act_title

            marker = re.sub(r"\s+", "", lines[start_idx].strip())
            title = f"{current_act_title}{marker}" if current_act_title else marker
            raw_end = heading_indexes[index + 1] if index + 1 < len(heading_indexes) else len(lines)
            end = self._trim_trailing_act_heading(lines, start_idx + 1, raw_end)
            body = "\n".join(lines[start_idx + 1 : end]).strip()
            if body:
                normalized_title = title[:200] or f"第{chapter_no}章"
                if chapters:
                    previous = chapters[-1]
                    if previous.get("title") == normalized_title and self._compact(previous.get("content")) == self._compact(body):
                        scan_from = start_idx + 1
                        continue
                chapters.append({"title": normalized_title, "content": body, "chapter_number": chapter_no})
                chapter_no += 1
            scan_from = start_idx + 1
        return chapters

    def _extract_act_heading(self, line: str) -> str | None:
        if not line or len(line) > 120:
            return None
        if re.search(r"[。！？!?；;]", line):
            return None
        match = self.ACT_HEADING_PATTERN.match(line)
        return match.group(1).strip() if match else None

    def _next_nonempty_line_index(self, lines: list[str], start_idx: int) -> int | None:
        for idx in range(start_idx, len(lines)):
            if lines[idx].strip():
                return idx
        return None

    def _trim_trailing_act_heading(self, lines: list[str], start_idx: int, end_idx: int) -> int:
        idx = end_idx - 1
        while idx >= start_idx and not lines[idx].strip():
            idx -= 1
        if idx >= start_idx and self._extract_act_heading(lines[idx].strip()):
            return idx
        return end_idx

    def _is_strong_heading(self, line: str) -> bool:
        if len(line) > 120:
            return False
        if len(line) > 80 and re.search(r"[。！？!?；;]", line):
            return False
        return any(pattern.match(line) for pattern in self.STRONG_CHAPTER_PATTERNS)

    def _is_weak_heading(self, lines: list[str], idx: int) -> bool:
        line = lines[idx].strip()
        if not line or len(line) > 25:
            return False
        if re.search(r"[，。！？；：,.!?;:]", line):
            return False
        if line.startswith(("“", "‘", '"', "'", "「", "『", "（", "(", "《")):
            return False
        if line.endswith(("”", "’", '"', "'", "」", "』", "）", ")", "》")):
            return False
        prev_blank = idx == 0 or not lines[idx - 1].strip()
        next_blank = idx == len(lines) - 1 or not lines[idx + 1].strip()
        return prev_blank and next_blank

    def _post_process_chapters(self, chapters: list[dict]) -> list[dict]:
        cleaned: list[dict] = []
        for chapter in chapters:
            title = str(chapter.get("title") or "").strip()[:200]
            content = str(chapter.get("content") or "").strip()
            volume_title = str(chapter.get("volume_title") or "").strip()
            if not title and not content:
                continue
            if content and len(content) > 50000:
                for idx, sub_chapter in enumerate(self._fallback_split(content, min_window=6000, max_window=9000), start=1):
                    cleaned.append(
                        {
                            "title": f"{title or '章节'}（{idx}）"[:200],
                            "content": sub_chapter["content"],
                            "chapter_number": len(cleaned) + 1,
                            "volume_title": volume_title,
                        }
                    )
                continue
            if cleaned:
                previous = cleaned[-1]
                if previous.get("title") == title and self._compact(previous.get("content")) == self._compact(content):
                    continue
            cleaned.append({"title": title or f"第{len(cleaned) + 1}章", "content": content, "chapter_number": len(cleaned) + 1, "volume_title": volume_title})
        for idx, chapter in enumerate(cleaned, start=1):
            chapter["chapter_number"] = idx
        return cleaned

    def _has_reasonable_chapter_quality(self, chapters: list[dict]) -> bool:
        if not chapters:
            return False
        if len(chapters) <= 2:
            return True
        lengths = [len((chapter.get("content") or "").strip()) for chapter in chapters]
        if sum(lengths) < 3000:
            return True
        nonempty = [length for length in lengths if length > 0]
        if len(nonempty) < max(2, len(chapters) // 2):
            return False
        tiny_count = sum(length < 200 for length in nonempty)
        if tiny_count / len(nonempty) > 0.45:
            return False
        median = sorted(nonempty)[len(nonempty) // 2]
        return not (len(chapters) >= 8 and median < 350)

    def _fallback_split(self, text: str, min_window: int = 3000, max_window: int = 5000) -> list[dict]:
        chapters: list[dict] = []
        start = 0
        boundary_punctuation = "。！？!?\n"
        while start < len(text):
            ideal_end = min(start + max_window, len(text))
            if ideal_end >= len(text):
                end = len(text)
            else:
                search_from = min(start + min_window, len(text))
                segment = text[search_from:ideal_end]
                offset = max(segment.rfind(ch) for ch in boundary_punctuation)
                end = search_from + offset + 1 if offset >= 0 else ideal_end
            chunk = text[start:end].strip()
            if chunk:
                chapters.append({"title": f"第{len(chapters) + 1}章", "content": chunk, "chapter_number": len(chapters) + 1})
            start = end
        return chapters

    def _build_split_report(self, *, chapters: list[dict], split_mode: str, source_text: str) -> dict:
        lengths = [len((chapter.get("content") or "").strip()) for chapter in chapters]
        chapter_count = len(chapters)
        total_words = sum(lengths)
        average_words = int(total_words / chapter_count) if chapter_count else 0
        min_words = min(lengths) if lengths else 0
        max_words = max(lengths) if lengths else 0
        size_cv = self._coefficient_of_variation(lengths)
        dialogue_ratio = self._dialogue_line_ratio(source_text[:50000])
        heading_density = self._heading_candidate_density(source_text[:50000])
        short_numbers = [
            int(chapter.get("chapter_number") or idx)
            for idx, chapter in enumerate(chapters, start=1)
            if len((chapter.get("content") or "").strip()) < 300
        ]
        long_numbers = [
            int(chapter.get("chapter_number") or idx)
            for idx, chapter in enumerate(chapters, start=1)
            if len((chapter.get("content") or "").strip()) > 12000
        ]

        confidence = {
            "numbered_sections": 0.86,
            "strong_headings": 0.88,
            "pagination_headings": 0.78,
            "adaptive_fusion": 0.88,
            "custom_pattern": 0.72,
            "rule_chapter_cn": 0.82,
            "rule_corpus": 0.8,
            "rule_chapter_en": 0.82,
            "rule_number_dot": 0.74,
            "rule_paren_num": 0.72,
            "weak_headings": 0.58,
            "fallback_window": 0.38,
            "quality_fallback_window": 0.34,
            "empty": 0.0,
        }.get(split_mode, 0.5)
        reasons: list[str] = []
        if split_mode in {"fallback_window", "quality_fallback_window"}:
            reasons.append("未找到可靠章节标题，已按固定字数窗口兜底切分")
        if split_mode == "weak_headings":
            reasons.append("仅识别到弱标题，建议人工确认章节边界")
        if split_mode == "pagination_headings":
            reasons.append("检测到分页式标题，已按同名连续页码切分")
        if split_mode == "numbered_sections":
            reasons.append("检测到篇章标题下的独立小节编号，已按小节编号切分")
        if split_mode == "adaptive_fusion":
            reasons.append("规则融合自适应切分：候选规则结构性评分选优 + 离群块漏切回收")
        if split_mode.startswith("rule_") or split_mode == "custom_pattern":
            reasons.append("按用户选择的旧项目规则切分")

        if chapter_count <= 1 and len(source_text) > 5000:
            confidence -= 0.25
            reasons.append("长文本只识别到一个章节，可能存在漏切")
        if short_numbers:
            confidence -= min(0.2, len(short_numbers) / max(1, chapter_count) * 0.25)
            reasons.append(f"有 {len(short_numbers)} 个章节短于300字，建议检查是否误切")
        if long_numbers:
            confidence -= min(0.16, len(long_numbers) / max(1, chapter_count) * 0.2)
            reasons.append(f"有 {len(long_numbers)} 个章节超过12000字，建议检查是否漏切")
        if chapter_count >= 3 and min_words > 0 and max_words / max(1, min_words) >= 8:
            confidence -= 0.12
            reasons.append("章节长度差异较大，可能存在边界异常")
        if not chapters and source_text.strip():
            reasons.append("文本存在内容，但未能识别有效章节")

        problem = self._classify_split_problem(
            split_mode=split_mode,
            source_text=source_text,
            chapter_count=chapter_count,
            average_words=average_words,
            max_words=max_words,
            short_count=len(short_numbers),
            long_count=len(long_numbers),
            size_cv=size_cv,
        )
        problem_reason = self._problem_reason(problem)
        if problem_reason and problem_reason not in reasons:
            reasons.append(problem_reason)
        return {
            "mode": split_mode,
            "mode_label": self.SPLIT_MODE_LABELS.get(split_mode, split_mode),
            "confidence": round(max(0.0, min(0.99, confidence)), 2),
            "chapter_count": chapter_count,
            "total_words": total_words,
            "average_words": average_words,
            "min_words": min_words,
            "max_words": max_words,
            "size_cv": round(size_cv, 3),
            "dialogue_ratio": round(dialogue_ratio, 4),
            "heading_density": round(heading_density, 4),
            "problem_category": problem,
            "problem_label": self._problem_label(problem),
            "short_chapter_count": len(short_numbers),
            "long_chapter_count": len(long_numbers),
            "abnormal_chapter_numbers": sorted(set(short_numbers + long_numbers))[:80],
            "reasons": reasons,
        }

    def _augment_report_v2(
        self,
        report: dict,
        chapters: list[dict],
        cleaning_report: dict,
        adaptive_report: dict | None,
    ) -> None:
        """报告 v2:把规则融合 + 清洗 + 三道过滤的结果并进报告 (A0_ingestion §5)。

        不静默删:author_notes / weird_titles / gaps 全部上报,供复核 UI 逐条裁决。
        """
        ar = adaptive_report or {}
        report["rule_chosen"] = ar.get("rule_chosen")
        report["rule_runnerup"] = ar.get("rule_runnerup")
        report["score_breakdown"] = ar.get("score_breakdown", {})
        gaps = ar.get("gaps", []) or []
        report["gaps"] = gaps
        report["cleaning"] = cleaning_report or {}

        author_notes = [
            {
                "chapter_index": c.get("chapter_number"),
                "title": c.get("title", ""),
                "reason": c.get("_note_reason", ""),
            }
            for c in chapters
            if c.get("is_author_note")
        ]
        weird_titles = [
            {
                "chapter_index": c.get("chapter_number"),
                "title": c.get("title", ""),
                "title_confidence": c.get("title_confidence", 1.0),
                "content_descriptor": c.get("content_descriptor", ""),
            }
            for c in chapters
            if float(c.get("title_confidence", 1.0)) < 0.6
        ]
        report["author_notes"] = author_notes
        report["weird_titles"] = weird_titles

        n = max(1, len(chapters))
        unrecovered = [g for g in gaps if not g.get("recovered")]
        report["needs_review"] = bool(
            unrecovered
            or len(author_notes) / n > 0.3
            or len(weird_titles) / n > 0.2
        )

        # 清理章节里的内部临时字段(_note_reason),保留入库列字段
        for c in chapters:
            c.pop("_note_reason", None)

    def _classify_split_problem(
        self,
        *,
        split_mode: str,
        source_text: str,
        chapter_count: int,
        average_words: int,
        max_words: int,
        short_count: int,
        long_count: int,
        size_cv: float,
    ) -> str:
        text_length = len(source_text or "")
        if split_mode == "empty":
            return "empty"
        if chapter_count <= 0 and text_length > 0:
            return "no_chapters"
        if split_mode == "fallback_window":
            return "no_heading_match"
        if split_mode == "quality_fallback_window":
            return "fallback_used"
        if chapter_count == 1 and max_words > 30000:
            return "single_huge_chapter"
        if chapter_count < 5 and text_length > 100000:
            return "heading_too_sparse"
        if average_words < 500 and chapter_count > 10:
            return "heading_too_dense"
        if short_count > 0 and chapter_count > 0 and short_count / chapter_count > 0.2:
            return "many_tiny_chapters"
        if long_count > 0 and chapter_count > 0 and long_count / chapter_count > 0.2:
            return "many_huge_chapters"
        if size_cv > 2.0:
            return "high_variance"
        return "ok"

    def _problem_label(self, problem: str) -> str:
        return {
            "ok": "未发现明显异常",
            "empty": "空文本",
            "no_chapters": "未识别章节",
            "no_heading_match": "未匹配标题",
            "fallback_used": "质量兜底",
            "single_huge_chapter": "单章过大",
            "heading_too_sparse": "标题过稀",
            "heading_too_dense": "标题过密",
            "many_tiny_chapters": "短章过多",
            "many_huge_chapters": "长章过多",
            "high_variance": "长度波动大",
        }.get(problem, problem)

    def _problem_reason(self, problem: str) -> str:
        return {
            "no_chapters": "未能切出有效章节，需要人工设置章节边界",
            "no_heading_match": "没有匹配到稳定章节标题，当前切分仅适合预览和人工修正",
            "fallback_used": "规则切分质量不足，已转入兜底切分",
            "single_huge_chapter": "长文本只形成一个超大章节，疑似漏切",
            "heading_too_sparse": "长文本章节数偏少，疑似标题识别过稀",
            "heading_too_dense": "平均章节过短且章节数较多，疑似把正文短行误切成标题",
            "many_tiny_chapters": "短章节占比偏高，疑似误切",
            "many_huge_chapters": "超长章节占比偏高，疑似漏切",
            "high_variance": "章节长度离散度过高，建议重点检查边界",
        }.get(problem, "")

    def _coefficient_of_variation(self, lengths: list[int]) -> float:
        if len(lengths) <= 1:
            return 0.0
        avg = mean(lengths)
        return 0.0 if avg <= 0 else stdev(lengths) / avg

    def _dialogue_line_ratio(self, text: str) -> float:
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        if not lines:
            return 0.0
        dialogue_lines = sum(1 for line in lines if line.startswith(("“", '"', "「", "『")))
        return dialogue_lines / len(lines)

    def _heading_candidate_density(self, text: str) -> float:
        body_punctuation = set("。，；：！？…、》）」』】")
        lines = [line.strip() for line in text.split("\n") if line.strip()]
        if not lines:
            return 0.0
        candidates = sum(1 for line in lines if 0 < len(line) <= 30 and not any(ch in body_punctuation for ch in line))
        return candidates / len(lines)

    def build_custom_pattern(self, template: str) -> Pattern[str] | None:
        template = (template or "").strip()
        if not template or len(template) > 200:
            return None
        if "*" in template:
            parts = [re.escape(part) for part in template.split("*")]
            body = rf"[{NUMBER_TOKEN}]+".join(parts)
            pattern_source = rf"^({body}.*)$"
        else:
            if not self.is_safe_regex(template):
                return None
            pattern_source = template
        if not self.is_safe_regex(pattern_source):
            return None
        try:
            return re.compile(pattern_source, re.MULTILINE)
        except re.error:
            return None

    def is_safe_regex(self, pattern: str) -> bool:
        """静态检查 + 动态 timeout 探测。

        静态：长度、嵌套 quantifier、lookaround、共同前缀分支重复 (a|aa)+
        动态：把模式跑一段"对抗性输入"，子进程 timeout，超过 0.3s 视为不安全。
        """
        if len(pattern) > 260:
            return False
        if re.search(r"(\+|\*|\{)\)(\+|\*|\?)|\(\?[^)]*(\+|\*)\)(\+|\*|\?)", pattern):
            return False
        if re.search(r"\(\?[=!<]", pattern):
            return False
        # 共同前缀分支重复：(foo|foobar)+ 或 (a|aa)*
        if re.search(r"\([^)]*\|[^)]*\)\s*[\+\*]", pattern):
            # 提取 alternation 看是否前缀重叠
            for grp in re.finditer(r"\(([^)]*)\)\s*[\+\*]", pattern):
                alts = grp.group(1).split("|")
                if len(alts) >= 2:
                    alts_sorted = sorted(alts, key=len)
                    for i, a in enumerate(alts_sorted[:-1]):
                        for b in alts_sorted[i+1:]:
                            if b.startswith(a) and a:
                                return False
        depth = 0
        max_depth = 0
        for ch in pattern:
            if ch == "(":
                depth += 1
                max_depth = max(max_depth, depth)
            elif ch == ")":
                depth -= 1
                if depth < 0:
                    return False
        if depth != 0 or max_depth > 5:
            return False

        # 动态 timeout 探测：子进程跑 0.3s，超时认为不安全
        return _regex_timeout_probe(pattern)

    def split_file(
        self,
        path: str | Path,
        *,
        split_rule: str = "auto",
        custom_pattern: str = "",
        title: str = "",
    ) -> tuple[list[dict], dict, str]:
        raw = Path(path).read_bytes()
        text, encoding = self.decode_bytes(raw)
        chapters, report = self.split_chapters_with_report(
            text,
            split_rule=split_rule,
            custom_pattern=custom_pattern,
            source_name=Path(path).name,
            title=title,
        )
        report["encoding"] = encoding
        return chapters, report, text

    @staticmethod
    def _compact(value: object) -> str:
        return re.sub(r"\s+", "", str(value or ""))

    @staticmethod
    def _to_int(value: str) -> int:
        fullwidth = str.maketrans("０１２３４５６７８９", "0123456789")
        try:
            return int(value.translate(fullwidth))
        except ValueError:
            return 0


def _regex_timeout_probe(pattern: str, timeout_sec: float = 0.3) -> bool:
    """子进程跑一次对抗性匹配，超时即 unsafe。"""
    import multiprocessing
    # 构造对抗输入：长 `a` + 让 backtrack 触发的尾字符
    payloads = [
        ("a" * 60) + "!",
        ("ab" * 30) + "x",
        ("(" * 20) + "y",
    ]

    def _worker(p, samples, q):
        import re as _re
        try:
            comp = _re.compile(p, _re.MULTILINE)
            for s in samples:
                comp.match(s)
            q.put("ok")
        except Exception as e:
            q.put(f"err:{e}")

    ctx = multiprocessing.get_context("fork") if hasattr(multiprocessing, "get_context") else multiprocessing
    q = ctx.Queue()
    proc = ctx.Process(target=_worker, args=(pattern, payloads, q))
    proc.start()
    proc.join(timeout=timeout_sec)
    if proc.is_alive():
        proc.terminate()
        proc.join(timeout=0.5)
        return False
    return not q.empty()


chapter_splitter = ChapterSplitter()
