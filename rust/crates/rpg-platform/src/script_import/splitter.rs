//! splitter —— 章节切分规则,基于 Python `rpg/chapter_splitter.py` 翻译。
//!
//! 同一文件里塞下 ChapterSplitter 的核心方法(暂不另开 crate),只翻"主流路径":
//! - `decode_bytes` —— utf-8 / utf-8-sig / gb18030 / gbk / big5 多编码兜底
//! - `clean_text`  —— 规范化换行、去 BOM、压缩多空行、过滤盗版水印行
//! - `split_chapters_with_report` —— 单一入口,返回章节 + 报告
//!
//! 内部支持四种模式(由 split_rule 参数选择):
//!   - "auto"        —— 强标题/弱标题 → 兜底窗口
//!   - "chapter_cn"  —— 中文章节正则
//!   - "chapter_en"  —— 英文章节正则
//!   - "corpus"      —— 中文语料章节(更宽松)
//!   - "number_dot"  —— 数字点号
//!   - "paren_num"   —— 括号编号
//!   - "custom"      —— 用户自定义 pattern,带通配 *
//!
//! 受 Python 启发但不强求 1:1 对齐:remulina_special / numbered_sections / pagination_headings
//! 这些少见路径留 stub,fallback 走 auto + fixed-window。后续若发现误切再补 (TODO[P2-SPLIT]).

use once_cell::sync::Lazy;
use regex::Regex;
use serde::{Deserialize, Serialize};

/// 一章。
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct Chapter {
    pub title: String,
    pub content: String,
    /// 1-based,内部生成
    pub chapter_number: i32,
    #[serde(default)]
    pub volume_title: String,
    #[serde(default)]
    pub source_marker: String,
}

/// 切分报告,大致对应 Python `_build_split_report`。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SplitReport {
    pub mode: String,
    pub mode_label: String,
    pub confidence: f32,
    pub chapter_count: i32,
    pub total_words: i64,
    pub average_words: i32,
    pub min_words: i32,
    pub max_words: i32,
    pub split_rule: String,
    pub reasons: Vec<String>,
}

impl SplitReport {
    pub fn empty(rule: &str) -> Self {
        SplitReport {
            mode: "empty".into(),
            mode_label: "空文本".into(),
            confidence: 0.0,
            chapter_count: 0,
            total_words: 0,
            average_words: 0,
            min_words: 0,
            max_words: 0,
            split_rule: rule.into(),
            reasons: vec!["空文本".into()],
        }
    }
}

// ── 编码探测 ────────────────────────────────────────────────────
/// Python `decode_bytes` —— utf-8 → gb18030 → gbk → big5 兜底。
pub fn decode_bytes(raw: &[u8]) -> (String, &'static str) {
    // utf-8-sig:剥去 BOM
    if raw.starts_with(&[0xEF, 0xBB, 0xBF]) {
        if let Ok(s) = std::str::from_utf8(&raw[3..]) {
            return (s.to_string(), "utf-8-sig");
        }
    }
    if let Ok(s) = std::str::from_utf8(raw) {
        return (s.to_string(), "utf-8");
    }
    for (label, enc) in &[
        ("gb18030", encoding_rs::GB18030),
        ("gbk", encoding_rs::GBK),
        ("big5", encoding_rs::BIG5),
    ] {
        let (cow, _, had_errors) = enc.decode(raw);
        if !had_errors {
            return (cow.into_owned(), *label);
        }
    }
    // 最后兜底:utf-8 lossy。
    (String::from_utf8_lossy(raw).into_owned(), "utf-8(ignore)")
}

// ── 盗版水印行 ──────────────────────────────────────────────────
static PIRATE_PATTERNS: Lazy<Vec<Regex>> = Lazy::new(|| {
    [
        // 啃书 / KenShu.cc 等
        r"(?i)啃书小说|KenShu\.?CC?|kenshu\.cc",
        r"以下是.{0,12}小说[网站].{0,30}(收集|整理|采集)",
        r"版权归.{0,30}(作者|出版社|所有)",
        r"本书.{0,12}(转载|搬运|盗版|首发|连载)于",
        r"(更多|最新)章节.{0,20}(请|尽在|访问|登陆|登录)",
        r"(?im)^[ \t]*(www|http|https?:)[\w./:%?=&-]+",
        r"(收藏本站|本站|笔趣|UU看书|UC浏览器|微信公众号).{0,40}(获取|追书|更新|阅读)",
        r"PS[:：].{0,80}(推荐|月票|订阅|打赏)",
    ]
    .iter()
    .filter_map(|p| Regex::new(p).ok())
    .collect()
});

fn strip_pirate_promo(text: &str) -> String {
    let mut kept = Vec::with_capacity(64);
    for line in text.split('\n') {
        if PIRATE_PATTERNS.iter().any(|p| p.is_search_optimized_match(line)) {
            continue;
        }
        kept.push(line);
    }
    let joined = kept.join("\n");
    static MULTI_NL: Lazy<Regex> = Lazy::new(|| Regex::new(r"\n{3,}").unwrap());
    MULTI_NL.replace_all(&joined, "\n\n").to_string()
}

// 给 Regex 起个朴素别名,让上面写得短点。
trait SearchOpt {
    fn is_search_optimized_match(&self, text: &str) -> bool;
}
impl SearchOpt for Regex {
    fn is_search_optimized_match(&self, text: &str) -> bool {
        self.is_match(text)
    }
}

// ── clean_text ──────────────────────────────────────────────────
/// Python `clean_text`:CRLF → LF,去 BOM,空白整理,压缩 4+ 空行 → 3,过滤盗版行。
pub fn clean_text(text: &str) -> String {
    let mut t = text.replace("\r\n", "\n").replace('\r', "\n");
    t = t.replace('\u{feff}', "");
    t = t.replace('\u{3000}', "  ");

    static TRAIL_WS: Lazy<Regex> = Lazy::new(|| Regex::new(r"[ \t]+\n").unwrap());
    t = TRAIL_WS.replace_all(&t, "\n").to_string();

    static FOUR_NL: Lazy<Regex> = Lazy::new(|| Regex::new(r"\n{4,}").unwrap());
    t = FOUR_NL.replace_all(&t, "\n\n\n").to_string();

    t = strip_pirate_promo(&t);
    t.trim().to_string()
}

// ── 强 / 弱章节识别 ────────────────────────────────────────────
/// 数字字符集合(中文 + 阿拉伯 + 全角),拼到正则里用。
const NUMBER_TOKEN: &str = r"零一二三四五六七八九十百千万〇两\d０-９";

static STRONG_PATTERNS: Lazy<Vec<Regex>> = Lazy::new(|| {
    let p1 = format!(r"^第[{n}]+(?:[章回卷集部篇幕场].*|节(?:$|[\s　:：、.．\-—]).*)$", n = NUMBER_TOKEN);
    [
        p1.as_str(),
        r"^(?:楔子|引子|序[章言曲]?|后记|尾声|终章|完本感言|番外)(?:$|[\s　:：、.．\-—].*)$",
        r"^#{1,3}\s+\S.+$",
        r"(?i)^(?:chapter|chap\.|part)\s*[0-9０-９ivxlcdm]+.*$",
        r"(?i)^(?:prologue|epilogue).*$",
    ]
    .iter()
    .map(|s| Regex::new(s).unwrap())
    .collect()
});

static BODY_PUNCT_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"[。！？!?；;]").unwrap());

fn is_strong_heading(line: &str) -> bool {
    let len = line.chars().count();
    if len > 120 {
        return false;
    }
    if len > 80 && BODY_PUNCT_RE.is_match(line) {
        return false;
    }
    STRONG_PATTERNS.iter().any(|p| p.is_match(line))
}

fn is_weak_heading(lines: &[&str], idx: usize) -> bool {
    let line = lines[idx].trim();
    let n = line.chars().count();
    if line.is_empty() || n > 25 {
        return false;
    }
    static PUNCT: Lazy<Regex> = Lazy::new(|| Regex::new(r"[，。！？；：,.!?;:]").unwrap());
    if PUNCT.is_match(line) {
        return false;
    }
    let opens = ['“', '‘', '"', '\'', '「', '『', '（', '(', '《'];
    let closes = ['”', '’', '"', '\'', '」', '』', '）', ')', '》'];
    if let Some(c) = line.chars().next() {
        if opens.contains(&c) {
            return false;
        }
    }
    if let Some(c) = line.chars().last() {
        if closes.contains(&c) {
            return false;
        }
    }
    let prev_blank = idx == 0 || lines[idx - 1].trim().is_empty();
    let next_blank = idx + 1 >= lines.len() || lines[idx + 1].trim().is_empty();
    prev_blank && next_blank
}

// ── rule patterns(对应 Python RULE_PATTERNS)──────────────────
fn rule_pattern(split_rule: &str) -> Option<(Regex, &'static str)> {
    let pat = match split_rule {
        "chapter_cn" => format!(
            r"(?m)^(.{{0,30}}(?:第[{n}]+[章节集回]|[序楔]章|楔子|引[子言]|前言|番外).*)$",
            n = NUMBER_TOKEN
        ),
        "corpus" => format!(
            r"(?m)^(.{{0,40}}(?:第[{n}]+[章节集回卷]|第[{n}]+部|[序楔终]章|楔子|引[子言]|前言|正文|番外|外传|大结局).*)$",
            n = NUMBER_TOKEN
        ),
        "chapter_en" => r"(?im)^(Chapter\s+[0-9０-９]+.*)$".to_string(),
        "number_dot" => r"(?m)^([0-9０-９]+[.、]\s*.*)$".to_string(),
        "paren_num" => format!(
            r"(?m)^(.{{0,10}}[（(]\s*[{n}]+\s*[)）].*)$",
            n = NUMBER_TOKEN
        ),
        _ => return None,
    };
    let label = match split_rule {
        "chapter_cn" => "中文章节",
        "corpus" => "语料章节",
        "chapter_en" => "英文章节",
        "number_dot" => "数字点号",
        "paren_num" => "括号编号",
        _ => return None,
    };
    Regex::new(&pat).ok().map(|r| (r, label))
}

fn mode_label(mode: &str) -> &'static str {
    match mode {
        "empty" => "空文本",
        "custom_pattern" => "自定义规则",
        "strong_headings" => "标准章节标题",
        "weak_headings" => "弱标题推断",
        "fallback_window" => "固定窗口兜底",
        "quality_fallback_window" => "质量兜底窗口",
        m if m.starts_with("rule_chapter_cn") => "中文章节规则",
        m if m.starts_with("rule_corpus") => "语料章节规则",
        m if m.starts_with("rule_chapter_en") => "英文章节规则",
        m if m.starts_with("rule_number_dot") => "数字点号规则",
        m if m.starts_with("rule_paren_num") => "括号编号规则",
        _ => "未知模式",
    }
}

fn mode_confidence(mode: &str) -> f32 {
    match mode {
        "strong_headings" => 0.88,
        "custom_pattern" => 0.72,
        "rule_chapter_cn" => 0.82,
        "rule_corpus" => 0.80,
        "rule_chapter_en" => 0.82,
        "rule_number_dot" => 0.74,
        "rule_paren_num" => 0.72,
        "weak_headings" => 0.58,
        "fallback_window" => 0.38,
        "quality_fallback_window" => 0.34,
        "empty" => 0.0,
        _ => 0.5,
    }
}

// ── build_custom_pattern ──────────────────────────────────────
/// Python `build_custom_pattern` —— `*` 当作 `[NUMBER_TOKEN]+` 占位。
pub fn build_custom_pattern(template: &str) -> Option<Regex> {
    let t = template.trim();
    if t.is_empty() || t.len() > 200 {
        return None;
    }
    let pattern_source = if t.contains('*') {
        let parts: Vec<String> = t.split('*').map(regex::escape).collect();
        let body = parts.join(&format!("[{}]+", NUMBER_TOKEN));
        format!(r"(?m)^({}.*)$", body)
    } else {
        if !is_safe_static(t) {
            return None;
        }
        format!("(?m){}", t)
    };
    Regex::new(&pattern_source).ok()
}

/// 静态安全检查(Python `is_safe_regex` 静态半):长度 / 嵌套量词 / lookaround / 共同前缀分支重复。
/// Rust 端不做子进程超时探测(regex crate 无回溯,RE2 风格,本来就不会指数爆炸)。
fn is_safe_static(pattern: &str) -> bool {
    if pattern.len() > 260 {
        return false;
    }
    let nested = Regex::new(r"(\+|\*|\{)\)(\+|\*|\?)|\(\?[^)]*(\+|\*)\)(\+|\*|\?)").unwrap();
    if nested.is_match(pattern) {
        return false;
    }
    let lookaround = Regex::new(r"\(\?[=!<]").unwrap();
    if lookaround.is_match(pattern) {
        return false;
    }
    let mut depth = 0i32;
    let mut max_depth = 0i32;
    for ch in pattern.chars() {
        if ch == '(' {
            depth += 1;
            if depth > max_depth {
                max_depth = depth;
            }
        } else if ch == ')' {
            depth -= 1;
            if depth < 0 {
                return false;
            }
        }
    }
    depth == 0 && max_depth <= 5
}

// ── 切分主入口 ────────────────────────────────────────────────
pub fn split_chapters_with_report(
    text: &str,
    split_rule: &str,
    custom_pattern: &str,
) -> (Vec<Chapter>, SplitReport) {
    let cleaned = clean_text(text);
    if cleaned.is_empty() {
        return (Vec::new(), SplitReport::empty(split_rule));
    }
    let rule = split_rule.trim();
    let rule = if rule.is_empty() { "auto" } else { rule };

    let (mut chapters, mode) = run_split(&cleaned, rule, custom_pattern);
    chapters = post_process(chapters);
    let report = build_report(&chapters, &mode, &cleaned, rule);
    (chapters, report)
}

fn run_split(text: &str, split_rule: &str, custom_pattern: &str) -> (Vec<Chapter>, String) {
    // 1) custom
    if split_rule == "custom" {
        if let Some(p) = build_custom_pattern(custom_pattern) {
            let chs = split_by_pattern(text, &p);
            if !chs.is_empty() {
                return (chs, "custom_pattern".to_string());
            }
        }
    }

    // 2) rule_xxx
    if let Some((p, _label)) = rule_pattern(split_rule) {
        let chs = post_process(split_by_pattern(text, &p));
        if !chs.is_empty() && has_reasonable_quality(&chs) {
            return (chs, format!("rule_{}", split_rule));
        }
    }

    // 3) auto
    let (chs, mode) = split_auto(text);

    // 4) corpus 救场(只在 auto 走兜底窗口时)
    if (split_rule == "auto" || split_rule.is_empty() || split_rule == "corpus")
        && (mode == "fallback_window" || mode == "quality_fallback_window")
    {
        if let Some((p, _)) = rule_pattern("corpus") {
            let corpus = post_process(split_by_pattern(text, &p));
            if corpus.len() > chs.len() && has_reasonable_quality(&corpus) {
                return (corpus, "rule_corpus".to_string());
            }
        }
    }
    (chs, mode)
}

fn split_auto(text: &str) -> (Vec<Chapter>, String) {
    let lines: Vec<&str> = text.split('\n').collect();
    let mut strong: Vec<usize> = Vec::new();
    let mut weak: Vec<usize> = Vec::new();

    for (idx, line) in lines.iter().enumerate() {
        let stripped = line.trim();
        if stripped.is_empty() {
            continue;
        }
        if is_strong_heading(stripped) {
            strong.push(idx);
        } else if is_weak_heading(&lines, idx) {
            weak.push(idx);
        }
    }

    let has_strong = strong.len() >= 2;
    let mut heading_indexes: Vec<usize> = if has_strong {
        strong.clone()
    } else {
        let mut combined: Vec<usize> = strong.into_iter().chain(weak.into_iter()).collect();
        combined.sort_unstable();
        combined.dedup();
        combined
    };

    if heading_indexes.is_empty() {
        return (fallback_split(text, 3000, 5000), "fallback_window".into());
    }

    heading_indexes.sort_unstable();
    let chapters = split_standard_headings(&lines, &heading_indexes);
    let processed = post_process(chapters);
    if !processed.is_empty() && has_reasonable_quality(&processed) {
        let mode = if has_strong { "strong_headings" } else { "weak_headings" };
        return (processed, mode.into());
    }
    (fallback_split(text, 3000, 5000), "quality_fallback_window".into())
}

fn split_standard_headings(lines: &[&str], heading_indexes: &[usize]) -> Vec<Chapter> {
    let mut out: Vec<Chapter> = Vec::with_capacity(heading_indexes.len() + 1);
    let mut chapter_no = 1;
    let first_h = heading_indexes[0];
    if first_h > 0 {
        let preface = lines[..first_h].join("\n").trim().to_string();
        if preface.chars().count() >= 200 {
            out.push(Chapter {
                title: "前言".into(),
                content: preface,
                chapter_number: chapter_no,
                volume_title: String::new(),
                source_marker: String::new(),
            });
            chapter_no += 1;
        }
    }
    for (i, &start_idx) in heading_indexes.iter().enumerate() {
        let end_idx = heading_indexes.get(i + 1).copied().unwrap_or(lines.len());
        let title_raw = lines[start_idx].trim();
        let mut title: String = title_raw.chars().take(200).collect();
        if title.is_empty() {
            title = format!("第{}章", chapter_no);
        }
        let body_start = start_idx + 1;
        let mut body = if body_start < end_idx {
            lines[body_start..end_idx].join("\n").trim().to_string()
        } else {
            String::new()
        };
        if body.is_empty() && i + 1 < heading_indexes.len() && body_start < lines.len() {
            body = lines[body_start].trim().to_string();
        }
        out.push(Chapter {
            title,
            content: body,
            chapter_number: chapter_no,
            volume_title: String::new(),
            source_marker: String::new(),
        });
        chapter_no += 1;
    }
    out.into_iter().filter(|c| !c.title.is_empty() || !c.content.is_empty()).collect()
}

/// 按 pattern 行扫描:每行 trim 后 match,作为新章标题;之前累积的内容归到上一章。
fn split_by_pattern(text: &str, pattern: &Regex) -> Vec<Chapter> {
    let lines: Vec<&str> = text.split('\n').collect();
    let mut chapters: Vec<Chapter> = Vec::new();
    let mut current_title = String::new();
    let mut current_lines: Vec<&str> = Vec::new();
    for line in lines.iter() {
        let trimmed = line.trim();
        if let Some(caps) = pattern.captures(trimmed) {
            if !current_title.is_empty() || !current_lines.is_empty() {
                let title_for = if current_title.is_empty() {
                    if chapters.is_empty() {
                        "序章".to_string()
                    } else {
                        format!("第{}章", chapters.len() + 1)
                    }
                } else {
                    current_title.clone()
                };
                chapters.push(Chapter {
                    title: title_for,
                    content: current_lines.join("\n").trim().to_string(),
                    chapter_number: chapters.len() as i32 + 1,
                    volume_title: String::new(),
                    source_marker: String::new(),
                });
            }
            let cap_title = caps
                .get(1)
                .map(|m| m.as_str())
                .unwrap_or(trimmed)
                .trim()
                .to_string();
            current_title = cap_title;
            current_lines.clear();
        } else {
            current_lines.push(*line);
        }
    }
    if !current_title.is_empty() || !current_lines.is_empty() {
        let title_for = if current_title.is_empty() {
            if chapters.is_empty() {
                "序章".to_string()
            } else {
                format!("第{}章", chapters.len() + 1)
            }
        } else {
            current_title.clone()
        };
        chapters.push(Chapter {
            title: title_for,
            content: current_lines.join("\n").trim().to_string(),
            chapter_number: chapters.len() as i32 + 1,
            volume_title: String::new(),
            source_marker: String::new(),
        });
    }
    chapters.into_iter().filter(|c| !c.content.is_empty()).collect()
}

/// 固定窗口兜底:Python `_fallback_split`,在 [min, max] 之间找一个标点边界。
fn fallback_split(text: &str, min_window: usize, max_window: usize) -> Vec<Chapter> {
    // 按字符走,不按字节走,中文文本下字节切会断 UTF-8。
    let chars: Vec<char> = text.chars().collect();
    let mut out: Vec<Chapter> = Vec::new();
    let mut start = 0;
    let punct: &[char] = &['。', '！', '？', '!', '?', '\n'];
    while start < chars.len() {
        let ideal_end = (start + max_window).min(chars.len());
        let end = if ideal_end >= chars.len() {
            chars.len()
        } else {
            let search_from = (start + min_window).min(chars.len());
            // 在 [search_from, ideal_end) 里找最靠右的标点
            let segment = &chars[search_from..ideal_end];
            let mut found: Option<usize> = None;
            for (i, c) in segment.iter().enumerate().rev() {
                if punct.contains(c) {
                    found = Some(i);
                    break;
                }
            }
            match found {
                Some(i) => search_from + i + 1,
                None => ideal_end,
            }
        };
        let chunk: String = chars[start..end].iter().collect::<String>().trim().to_string();
        if !chunk.is_empty() {
            let no = out.len() as i32 + 1;
            out.push(Chapter {
                title: format!("第{}章", no),
                content: chunk,
                chapter_number: no,
                volume_title: String::new(),
                source_marker: String::new(),
            });
        }
        start = end;
    }
    out
}

/// Python `_post_process_chapters`:
/// - title/content trim + 截断
/// - 超长(50000 字)切窗
/// - 去掉与上一章完全重复的
fn post_process(chapters: Vec<Chapter>) -> Vec<Chapter> {
    let mut out: Vec<Chapter> = Vec::with_capacity(chapters.len());
    for chapter in chapters {
        let title: String = chapter.title.trim().chars().take(200).collect();
        let content = chapter.content.trim().to_string();
        let volume_title = chapter.volume_title.trim().to_string();
        if title.is_empty() && content.is_empty() {
            continue;
        }
        let content_len = content.chars().count();
        if content_len > 50000 {
            // 50k 字以上拆窗
            for (idx, sub) in fallback_split(&content, 6000, 9000).into_iter().enumerate() {
                let prefix = if title.is_empty() { "章节".into() } else { title.clone() };
                let sub_title: String = format!("{}（{}）", prefix, idx + 1).chars().take(200).collect();
                let no = out.len() as i32 + 1;
                out.push(Chapter {
                    title: sub_title,
                    content: sub.content,
                    chapter_number: no,
                    volume_title: volume_title.clone(),
                    source_marker: chapter.source_marker.clone(),
                });
            }
            continue;
        }
        // 去重:与上一章 (title + 紧凑 content) 完全相同
        if let Some(prev) = out.last() {
            if prev.title == title && compact(&prev.content) == compact(&content) {
                continue;
            }
        }
        let no = out.len() as i32 + 1;
        let final_title = if title.is_empty() { format!("第{}章", no) } else { title };
        out.push(Chapter {
            title: final_title,
            content,
            chapter_number: no,
            volume_title: volume_title.clone(),
            source_marker: chapter.source_marker.clone(),
        });
    }
    for (i, c) in out.iter_mut().enumerate() {
        c.chapter_number = i as i32 + 1;
    }
    out
}

fn compact(s: &str) -> String {
    s.chars().filter(|c| !c.is_whitespace()).collect()
}

fn has_reasonable_quality(chapters: &[Chapter]) -> bool {
    if chapters.is_empty() {
        return false;
    }
    if chapters.len() <= 2 {
        return true;
    }
    let lengths: Vec<usize> = chapters.iter().map(|c| c.content.chars().count()).collect();
    let total: usize = lengths.iter().sum();
    if total < 3000 {
        return true;
    }
    let nonempty: Vec<usize> = lengths.iter().copied().filter(|n| *n > 0).collect();
    if nonempty.len() < std::cmp::max(2, chapters.len() / 2) {
        return false;
    }
    let tiny_count = nonempty.iter().filter(|n| **n < 200).count();
    if tiny_count as f64 / nonempty.len() as f64 > 0.45 {
        return false;
    }
    let mut sorted = nonempty.clone();
    sorted.sort_unstable();
    let median = sorted[sorted.len() / 2];
    !(chapters.len() >= 8 && median < 350)
}

fn build_report(chapters: &[Chapter], mode: &str, source: &str, rule: &str) -> SplitReport {
    let lengths: Vec<i32> = chapters
        .iter()
        .map(|c| c.content.chars().count() as i32)
        .collect();
    let total_words: i64 = lengths.iter().map(|n| *n as i64).sum();
    let chapter_count = chapters.len() as i32;
    let average_words = if chapter_count > 0 {
        (total_words / chapter_count as i64) as i32
    } else {
        0
    };
    let min_words = lengths.iter().copied().min().unwrap_or(0);
    let max_words = lengths.iter().copied().max().unwrap_or(0);

    let short_numbers: Vec<i32> = chapters
        .iter()
        .filter(|c| c.content.chars().count() < 300)
        .map(|c| c.chapter_number)
        .collect();
    let long_numbers: Vec<i32> = chapters
        .iter()
        .filter(|c| c.content.chars().count() > 12000)
        .map(|c| c.chapter_number)
        .collect();

    let mut confidence = mode_confidence(mode);
    let mut reasons: Vec<String> = Vec::new();

    match mode {
        "fallback_window" | "quality_fallback_window" => {
            reasons.push("未找到可靠章节标题,已按固定字数窗口兜底切分".into());
        }
        "weak_headings" => {
            reasons.push("仅识别到弱标题,建议人工确认章节边界".into());
        }
        "custom_pattern" => reasons.push("按用户自定义 pattern 切分".into()),
        m if m.starts_with("rule_") => reasons.push("按用户选择的旧项目规则切分".into()),
        _ => {}
    }
    if chapter_count <= 1 && source.chars().count() > 5000 {
        confidence -= 0.25;
        reasons.push("长文本只识别到一个章节,可能存在漏切".into());
    }
    if !short_numbers.is_empty() {
        let drop = (short_numbers.len() as f32 / chapter_count.max(1) as f32 * 0.25).min(0.20);
        confidence -= drop;
        reasons.push(format!(
            "有 {} 个章节短于300字,建议检查是否误切",
            short_numbers.len()
        ));
    }
    if !long_numbers.is_empty() {
        let drop = (long_numbers.len() as f32 / chapter_count.max(1) as f32 * 0.20).min(0.16);
        confidence -= drop;
        reasons.push(format!(
            "有 {} 个章节超过12000字,建议检查是否漏切",
            long_numbers.len()
        ));
    }

    SplitReport {
        mode: mode.to_string(),
        mode_label: mode_label(mode).to_string(),
        confidence: ((confidence * 100.0).round() / 100.0).clamp(0.0, 0.99),
        chapter_count,
        total_words,
        average_words,
        min_words,
        max_words,
        split_rule: rule.to_string(),
        reasons,
    }
}

// ─────────────────────────── tests ───────────────────────────
#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_decode_utf8() {
        let raw = "你好，世界".as_bytes();
        let (s, enc) = decode_bytes(raw);
        assert_eq!(s, "你好，世界");
        assert_eq!(enc, "utf-8");
    }

    #[test]
    fn test_decode_utf8_sig() {
        let mut raw = vec![0xEF, 0xBB, 0xBF];
        raw.extend_from_slice("hello".as_bytes());
        let (s, enc) = decode_bytes(&raw);
        assert_eq!(s, "hello");
        assert_eq!(enc, "utf-8-sig");
    }

    #[test]
    fn test_decode_gbk() {
        let (cow, _, had_errors) = encoding_rs::GBK.encode("中文测试");
        assert!(!had_errors);
        let bytes = cow.into_owned();
        let (s, enc) = decode_bytes(&bytes);
        assert_eq!(s, "中文测试");
        // utf-8 strict 拒掉之后会落到 gb18030(它是 gbk 超集)
        assert!(enc == "gbk" || enc == "gb18030");
    }

    #[test]
    fn test_clean_text_normalizes_newlines() {
        // CRLF → LF;过多空行被压缩(strip_pirate_promo 二次收口到 2 个空行)
        let s = "a\r\nb\r\n\r\n\r\n\r\nc";
        let cleaned = clean_text(s);
        // 三连以上 \n 已被收口
        assert!(!cleaned.contains("\n\n\n"));
        assert!(cleaned.starts_with('a'));
        assert!(cleaned.ends_with('c'));
        assert!(cleaned.contains("\nb\n"));
        // CRLF 已经被替换
        assert!(!cleaned.contains('\r'));
    }

    #[test]
    fn test_clean_text_strips_pirate_promo() {
        let s = "正文一\n啃书小说网最新章节请访问 www.kenshu.cc\n正文二";
        let cleaned = clean_text(s);
        assert!(cleaned.contains("正文一"));
        assert!(cleaned.contains("正文二"));
        assert!(!cleaned.contains("啃书"));
        assert!(!cleaned.contains("kenshu"));
    }

    #[test]
    fn test_is_strong_heading_chinese() {
        assert!(is_strong_heading("第一章 起航"));
        assert!(is_strong_heading("第123章"));
        assert!(is_strong_heading("楔子"));
        assert!(is_strong_heading("Chapter 5: Hello"));
        assert!(!is_strong_heading("这是普通正文，里面提到了第一章而已。"));
    }

    #[test]
    fn test_split_with_strong_headings() {
        let text = "前面少量铺垫\n\n第一章 启程\n他出门了。又走了一段路。\n\n第二章 抵达\n他到了目的地。说了几句话。";
        let (chapters, report) = split_chapters_with_report(text, "auto", "");
        assert_eq!(chapters.len(), 2);
        assert_eq!(chapters[0].title, "第一章 启程");
        assert_eq!(chapters[1].title, "第二章 抵达");
        assert_eq!(report.mode, "strong_headings");
        assert!(report.confidence > 0.5);
    }

    #[test]
    fn test_split_chapter_cn_rule() {
        let text = "第一章 开始\n正文一。\n第二章 继续\n正文二。\n第三章 结束\n正文三。";
        let (chapters, report) = split_chapters_with_report(text, "chapter_cn", "");
        // chapter_cn 规则正则比 strong headings 宽,应该能切到 3 章
        assert!(chapters.len() >= 2);
        assert!(report.mode.contains("rule_chapter_cn") || report.mode == "strong_headings");
    }

    #[test]
    fn test_split_empty_text() {
        let (chapters, report) = split_chapters_with_report("   \n\n  ", "auto", "");
        assert!(chapters.is_empty());
        assert_eq!(report.mode, "empty");
    }

    #[test]
    fn test_fallback_window_for_unmarked_text() {
        // 8000 字纯正文(无章节标题),应该走 fallback_window
        let body: String = "这是一段连续的正文文字。".repeat(800);
        let (chapters, report) = split_chapters_with_report(&body, "auto", "");
        assert!(chapters.len() >= 2);
        assert!(report.mode == "fallback_window" || report.mode == "quality_fallback_window");
    }

    #[test]
    fn test_custom_pattern_with_wildcard() {
        let template = "第*回";
        let p = build_custom_pattern(template).expect("custom pattern compiles");
        // 应该能匹配"第一回 ……" / "第123回 ……"
        assert!(p.is_match("第一回 起点"));
        assert!(p.is_match("第123回"));
        assert!(!p.is_match("第A回"));
    }

    #[test]
    fn test_custom_pattern_rejects_overlong() {
        let huge: String = "a".repeat(300);
        assert!(build_custom_pattern(&huge).is_none());
    }

    #[test]
    fn test_post_process_dedup_consecutive_repeats() {
        let chapters = vec![
            Chapter {
                title: "第一章".into(),
                content: "内容A".into(),
                chapter_number: 1,
                volume_title: String::new(),
                source_marker: String::new(),
            },
            Chapter {
                title: "第一章".into(),
                content: "内容A".into(),
                chapter_number: 2,
                volume_title: String::new(),
                source_marker: String::new(),
            },
            Chapter {
                title: "第二章".into(),
                content: "内容B".into(),
                chapter_number: 3,
                volume_title: String::new(),
                source_marker: String::new(),
            },
        ];
        let out = post_process(chapters);
        assert_eq!(out.len(), 2);
        assert_eq!(out[0].chapter_number, 1);
        assert_eq!(out[1].chapter_number, 2);
    }

    #[test]
    fn test_post_process_renumbers_chapters() {
        let chapters = vec![
            Chapter {
                title: "甲".into(),
                content: "X".into(),
                chapter_number: 100,
                volume_title: String::new(),
                source_marker: String::new(),
            },
            Chapter {
                title: "乙".into(),
                content: "Y".into(),
                chapter_number: 200,
                volume_title: String::new(),
                source_marker: String::new(),
            },
        ];
        let out = post_process(chapters);
        assert_eq!(out[0].chapter_number, 1);
        assert_eq!(out[1].chapter_number, 2);
    }

    #[test]
    fn test_split_report_long_text_single_chapter_low_confidence() {
        // 长文本只有 1 章 → 触发 confidence -= 0.25 reason
        let body: String = "嗯。".repeat(3000);
        let text = format!("第一章 唯一章\n{}", body);
        let (chapters, report) = split_chapters_with_report(&text, "auto", "");
        assert_eq!(chapters.len(), 1);
        assert!(report.reasons.iter().any(|r| r.contains("漏切") || r.contains("可能存在")));
    }
}
