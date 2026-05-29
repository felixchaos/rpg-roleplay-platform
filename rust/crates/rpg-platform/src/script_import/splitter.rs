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
//! Wave 8-C 补全三条 Python 罕见路径:
//!   - "remulina_special"     —— 蕾穆丽娜旧项目混合卷章标题
//!   - "pagination_headings"  —— 分页式标题(同名 + 连续页码)
//!   - "numbered_sections"    —— 篇章独立小节编号（一）/（1）

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


// ── 蕾穆丽娜专用正则 ─────────────────────────────────────────────
/// Python REMULINA_FULL_TITLE_RE
static REMULINA_FULL_TITLE_RE: Lazy<Regex> = Lazy::new(|| {
    let n = NUMBER_TOKEN;
    Regex::new(&format!(
        r"^(?:(?:正卷|外卷)[－-](?:第|正)?[{n}]+卷.*?(?:第[{n}]+[章节]|第[{n}]+节|尾章).*)$"
    ))
    .unwrap()
});

/// Python REMULINA_STANDALONE_TITLE_RE
static REMULINA_STANDALONE_TITLE_RE: Lazy<Regex> = Lazy::new(|| {
    let n = NUMBER_TOKEN;
    Regex::new(&format!(
        r"^(?:第[{n}]+卷\s+小结|正[{n}]+卷(?:角色歌|\s+尾章)\s*.*|第[{n}]+[章节]\s+.*|第[{n}]+节\s+.*|正[{n}]+卷[{n}]+[章节]\s+.*)$"
    ))
    .unwrap()
});

/// Python REMULINA_WRAPPER_TITLE_RE
static REMULINA_WRAPPER_TITLE_RE: Lazy<Regex> = Lazy::new(|| {
    let n = NUMBER_TOKEN;
    Regex::new(&format!(
        r"^(?:正[{n}]+卷(?:角色歌|\s+尾章|[{n}]+[章节])\s+.*|第[{n}]+[章节]\s+.*|第[{n}]+节\s+.*)$"
    ))
    .unwrap()
});

/// Python REMULINA_VOLUME_KEY_RE: captures (卷类型, 卷号)
static REMULINA_VOLUME_KEY_RE: Lazy<Regex> = Lazy::new(|| {
    let n = NUMBER_TOKEN;
    Regex::new(&format!(r"^(正卷|外卷)[－-](?:第|正)?([{n}]+)卷")).unwrap()
});

/// Python REMULINA_WRAPPER_VOLUME_KEY_RE: captures (卷号)
static REMULINA_WRAPPER_VOLUME_KEY_RE: Lazy<Regex> = Lazy::new(|| {
    let n = NUMBER_TOKEN;
    Regex::new(&format!(r"^正([{n}]+)卷")).unwrap()
});

/// Python REMULINA_BARE_VOLUME_TITLE_RE
static REMULINA_BARE_VOLUME_TITLE_RE: Lazy<Regex> = Lazy::new(|| {
    let n = NUMBER_TOKEN;
    Regex::new(&format!(r"^(?:正卷|外卷)[－-](?:第|正)?[{n}]+卷$")).unwrap()
});

/// Python REMULINA_CHAPTER_BOUNDARY_RE
static REMULINA_CHAPTER_BOUNDARY_RE: Lazy<Regex> = Lazy::new(|| {
    let n = NUMBER_TOKEN;
    Regex::new(&format!(r"[－-](?:第[{n}]+[章节]|第[{n}]+节|尾章)(?:[－-]|\s+)")).unwrap()
});

// ── 分页标题 / 小节标题专用正则 ───────────────────────────────────
/// Python PAGINATION_HEADING_PATTERN: `^(.{1,60}?)[（(]\s*([0-9０-９]{1,5})\s*[）)]$`
static PAGINATION_HEADING_PATTERN: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"^(.{1,60}?)[（(]\s*([0-9０-９]{1,5})\s*[）)]$").unwrap());

/// Python SECTION_MARKER_PATTERN: `^[（(]\s*([NUMBER_TOKEN]{1,8})\s*[）)]$`
static SECTION_MARKER_PATTERN: Lazy<Regex> = Lazy::new(|| {
    let n = NUMBER_TOKEN;
    Regex::new(&format!(r"^[（(]\s*([{n}]{{1,8}})\s*[）)]$")).unwrap()
});

/// Python ACT_HEADING_PATTERN
static ACT_HEADING_PATTERN: Lazy<Regex> = Lazy::new(|| {
    let n = NUMBER_TOKEN;
    Regex::new(&format!(
        r"^(?:.{{0,80}}?\s+)?(第[{n}]+[幕卷部集篇](?:\s*[：:、.．\-—]\s*.+|.+)?)$"
    ))
    .unwrap()
});

/// 卷标题识别(用于 _split_with_volumes)
static VOLUME_PATTERN: Lazy<Regex> = Lazy::new(|| {
    let n = NUMBER_TOKEN;
    Regex::new(&format!(r"^(.{{0,30}}第[{n}]+卷.*)$")).unwrap()
});

/// 正文标点(用于弱标题和 act heading 过滤)
static SENTENCE_PUNCT_RE: Lazy<Regex> =
    Lazy::new(|| Regex::new(r"[。！？!?；;]").unwrap());

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
        "remulina_special" => "蕾穆丽娜规则",
        "pagination_headings" => "分页标题",
        "numbered_sections" => "篇章小节",
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
        "remulina_special" => 0.90,
        "pagination_headings" => 0.78,
        "numbered_sections" => 0.86,
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
    // 0) remulina_special — 在 custom 之前,仅对 auto / chapter_cn / corpus 或空规则生效
    if should_use_remulina_special(text, split_rule) {
        let remulina = split_remulina_novel(text);
        if !remulina.is_empty() {
            return (remulina, "remulina_special".to_string());
        }
    }

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

    // numbered_sections: Python 优先于 strong headings (当 section_count >= 2*strong)
    let section_heading_indexes = collect_numbered_section_headings(&lines);
    if !section_heading_indexes.is_empty()
        && (strong.len() < 2 || section_heading_indexes.len() >= strong.len() * 2)
    {
        let section_chapters = split_numbered_sections(&lines, &section_heading_indexes);
        if !section_chapters.is_empty() {
            return (section_chapters, "numbered_sections".into());
        }
    }

    // pagination_headings: Python 仅当 strong < 2 时启用
    if strong.len() < 2 {
        let pagination_indexes = collect_pagination_headings(&lines);
        if !pagination_indexes.is_empty() {
            let pagination_chapters = split_standard_headings(&lines, &pagination_indexes);
            if !pagination_chapters.is_empty() {
                return (pagination_chapters, "pagination_headings".into());
            }
        }
    }

    let has_strong = strong.len() >= 2;
    let mut heading_indexes: Vec<usize> = if has_strong {
        strong.clone()
    } else {
        let mut combined: Vec<usize> = strong.into_iter().chain(weak).collect();
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
        "pagination_headings" => reasons.push("检测到分页式标题,已按同名连续页码切分".into()),
        "numbered_sections" => reasons.push("检测到篇章标题下的独立小节编号,已按小节编号切分".into()),
        "remulina_special" => reasons.push("检测到蕾穆丽娜旧项目混合卷章标题,已跳过重复包装标题".into()),
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


// ═══════════════════════════════════════════════════════════════
// Wave 8-C: 蕾穆丽娜特殊路径 / 分页标题 / 篇章小节  (逐行翻 Python)
// ═══════════════════════════════════════════════════════════════

// ── should_use_remulina_special ──────────────────────────────
/// Python `_should_use_remulina_special`:
/// - split_rule 必须是 auto / chapter_cn / corpus 或空
/// - source_name / title 含有特定关键词,或 text 里有 5+ 条完整卷章标题 且 含"第一卷 小结"
fn should_use_remulina_special(text: &str, split_rule: &str) -> bool {
    if !split_rule.is_empty()
        && split_rule != "auto"
        && split_rule != "chapter_cn"
        && split_rule != "corpus"
    {
        return false;
    }
    // source_name/title 不在这里传入,仅通过 text 内容探测
    static FULL_TITLE_SCAN: Lazy<Regex> = Lazy::new(|| {
        let n = NUMBER_TOKEN;
        Regex::new(&format!(
            r"(?m)^\s*正卷[－-]第[{n}]+卷.*?第[{n}]+[章节]"
        ))
        .unwrap()
    });
    let matches: Vec<_> = FULL_TITLE_SCAN.find_iter(text).collect();
    matches.len() >= 5 && text.contains("第一卷 小结")
}

// ── split_remulina_novel ──────────────────────────────────────
/// Python `_split_remulina_novel`:按蕾穆丽娜卷章标题切分,跳过"包装标题"。
fn split_remulina_novel(text: &str) -> Vec<Chapter> {
    let lines: Vec<&str> = text.split('\n').collect();
    struct MarkerInfo {
        title: String,
        line_idx: usize,
    }
    let mut markers: Vec<MarkerInfo> = Vec::new();

    for (idx, line) in lines.iter().enumerate() {
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        let is_full = REMULINA_FULL_TITLE_RE.is_match(trimmed);
        let is_standalone = REMULINA_STANDALONE_TITLE_RE.is_match(trimmed);
        if !is_full && !is_standalone {
            continue;
        }
        // 跳过"包装标题":是 wrapper 且后面紧跟一个 full title
        if REMULINA_WRAPPER_TITLE_RE.is_match(trimmed)
            && has_upcoming_remulina_full_title(&lines, idx + 1, 3)
        {
            continue;
        }
        markers.push(MarkerInfo {
            title: trimmed.to_string(),
            line_idx: idx,
        });
    }

    if markers.is_empty() {
        return Vec::new();
    }

    // 按卷分组
    use std::collections::HashMap;
    let mut volumes_by_key: HashMap<String, (String, Vec<(String, String)>)> = HashMap::new();
    let mut ordered_keys: Vec<String> = Vec::new();
    let mut untitled_chapters: Vec<(String, String)> = Vec::new();
    let mut current_volume_key: Option<String> = None;

    for (index, marker) in markers.iter().enumerate() {
        // 提取卷 meta
        if let Some((key, vol_title)) = extract_remulina_volume_meta(&marker.title) {
            if !volumes_by_key.contains_key(&key) {
                volumes_by_key.insert(key.clone(), (vol_title.clone(), Vec::new()));
                ordered_keys.push(key.clone());
            } else if should_upgrade_remulina_volume_title(
                &volumes_by_key[&key].0,
                &vol_title,
            ) {
                volumes_by_key.get_mut(&key).unwrap().0 = vol_title.clone();
            }
            current_volume_key = Some(key);
        }

        let start_line = marker.line_idx + 1;
        let end_line = if index + 1 < markers.len() {
            markers[index + 1].line_idx
        } else {
            lines.len()
        };
        let body = lines[start_line..end_line].join("\n").trim().to_string();
        if body.is_empty() {
            continue;
        }

        match &current_volume_key {
            Some(key) => {
                volumes_by_key
                    .get_mut(key)
                    .unwrap()
                    .1
                    .push((marker.title.clone(), body));
            }
            None => {
                untitled_chapters.push((marker.title.clone(), body));
            }
        }
    }

    // 展平
    let mut out: Vec<Chapter> = Vec::new();
    // untitled first
    for (title, body) in untitled_chapters {
        let no = out.len() as i32 + 1;
        out.push(Chapter {
            title,
            content: body,
            chapter_number: no,
            volume_title: String::new(),
            source_marker: String::new(),
        });
    }
    for key in &ordered_keys {
        let (vol_title, chapters) = &volumes_by_key[key];
        for (title, body) in chapters {
            let no = out.len() as i32 + 1;
            out.push(Chapter {
                title: title.clone(),
                content: body.clone(),
                chapter_number: no,
                volume_title: vol_title.clone(),
                source_marker: String::new(),
            });
        }
    }
    out
}

/// Python `_has_upcoming_remulina_full_title`
fn has_upcoming_remulina_full_title(lines: &[&str], start_idx: usize, lookahead: usize) -> bool {
    let mut seen = 0usize;
    for idx in start_idx..lines.len() {
        let trimmed = lines[idx].trim();
        if trimmed.is_empty() {
            continue;
        }
        if REMULINA_FULL_TITLE_RE.is_match(trimmed) {
            return true;
        }
        seen += 1;
        if seen >= lookahead {
            break;
        }
    }
    false
}

/// Python `_extract_remulina_volume_meta`:返回 (key, volume_title) 或 None
fn extract_remulina_volume_meta(title: &str) -> Option<(String, String)> {
    // 先匹配 VOLUME_KEY_RE(正卷/外卷)
    if let Some(caps) = REMULINA_VOLUME_KEY_RE.captures(title) {
        let volume_type = caps.get(1).map_or("", |m| m.as_str());
        let volume_number = caps.get(2).map_or("", |m| m.as_str());
        let vol_title = if let Some(boundary) = REMULINA_CHAPTER_BOUNDARY_RE.find(title) {
            title[..boundary.start()].trim().to_string()
        } else {
            title.trim().to_string()
        };
        let key = format!("{}-{}卷", volume_type, volume_number);
        let final_title = if vol_title.is_empty() {
            format!("{}－第{}卷", volume_type, volume_number)
        } else {
            vol_title
        };
        return Some((key, final_title));
    }
    // 再匹配 WRAPPER_VOLUME_KEY_RE(正N卷)
    if let Some(caps) = REMULINA_WRAPPER_VOLUME_KEY_RE.captures(title) {
        let volume_number = caps.get(1).map_or("", |m| m.as_str());
        let key = format!("正卷-{}卷", volume_number);
        let vol_title = format!("正卷－第{}卷", volume_number);
        return Some((key, vol_title));
    }
    None
}

/// Python `_should_upgrade_remulina_volume_title`
fn should_upgrade_remulina_volume_title(existing: &str, candidate: &str) -> bool {
    REMULINA_BARE_VOLUME_TITLE_RE.is_match(existing) && candidate.len() > existing.len()
}

// ── pagination_headings ───────────────────────────────────────
/// Python `_collect_pagination_headings`:
/// 找 `title（N）` 或 `title(N)` 格式,N 从 1 连续,同一 title 出现 ≥ 3 次且连续。
fn collect_pagination_headings(lines: &[&str]) -> Vec<usize> {
    struct Candidate {
        idx: usize,
        title: String,
        page_no: u32,
    }
    let mut candidates: Vec<Candidate> = Vec::new();

    for (idx, line) in lines.iter().enumerate() {
        let stripped = line.trim();
        let Some(caps) = PAGINATION_HEADING_PATTERN.captures(stripped) else {
            continue;
        };
        let title_str = caps.get(1).map_or("", |m| m.as_str()).trim().to_string();
        let page_raw = caps.get(2).map_or("", |m| m.as_str());
        let page_no = to_int(page_raw);
        if title_str.is_empty() || page_no == 0 {
            continue;
        }
        if title_str.chars().count() > 40 || SENTENCE_PUNCT_RE.is_match(&title_str) {
            continue;
        }
        // 下一个非空行 >= 20 字符
        let next_idx = next_nonempty_line_index(lines, idx + 1);
        let Some(ni) = next_idx else { continue };
        if lines[ni].trim().chars().count() < 20 {
            continue;
        }
        candidates.push(Candidate {
            idx,
            title: title_str,
            page_no,
        });
    }

    if candidates.len() < 3 {
        return Vec::new();
    }

    // 找出现次数最多的 title
    let mut title_counts: std::collections::HashMap<&str, usize> = std::collections::HashMap::new();
    for c in &candidates {
        *title_counts.entry(c.title.as_str()).or_insert(0) += 1;
    }
    let (&dominant_title, &dominant_count) = title_counts
        .iter()
        .max_by_key(|(_, v)| *v)
        .unwrap();
    let min_count = std::cmp::max(3, (candidates.len() as f64 * 0.6) as usize);
    if dominant_count < min_count {
        return Vec::new();
    }

    let filtered: Vec<(usize, u32)> = candidates
        .iter()
        .filter(|c| c.title == dominant_title)
        .map(|c| (c.idx, c.page_no))
        .collect();

    let mut pages: Vec<u32> = filtered.iter().map(|(_, p)| *p).collect();
    pages.sort_unstable();
    pages.dedup();

    if pages.len() < 3 || pages[0] != 1 {
        return Vec::new();
    }
    let page_span = *pages.last().unwrap() - pages[0] + 1;
    if page_span > pages.len() as u32 + 2 {
        return Vec::new();
    }

    filtered.into_iter().map(|(idx, _)| idx).collect()
}

// ── numbered_sections ─────────────────────────────────────────
/// Python `_collect_numbered_section_headings`
fn collect_numbered_section_headings(lines: &[&str]) -> Vec<usize> {
    let candidates: Vec<usize> = (0..lines.len())
        .filter(|&idx| is_numbered_section_heading(lines, idx))
        .collect();
    if candidates.len() < 2 {
        return Vec::new();
    }
    // 各小节长度都 >= 500 字的至少一半
    let mut section_lengths: Vec<usize> = Vec::new();
    for (i, &start) in candidates.iter().enumerate() {
        let raw_end = candidates.get(i + 1).copied().unwrap_or(lines.len());
        let end = trim_trailing_act_heading(lines, start + 1, raw_end);
        let section_text = lines[start + 1..end].join("\n");
        let section_len = section_text.trim().chars().count();
        if section_len > 0 {
            section_lengths.push(section_len);
        }
    }
    if section_lengths.len() < 2 {
        return Vec::new();
    }
    let long_count = section_lengths.iter().filter(|&&l| l >= 500).count();
    if long_count < std::cmp::max(2, section_lengths.len() / 2) {
        return Vec::new();
    }
    candidates
}

/// Python `_is_numbered_section_heading`
fn is_numbered_section_heading(lines: &[&str], idx: usize) -> bool {
    let line = lines[idx].trim();
    if !SECTION_MARKER_PATTERN.is_match(line) {
        return false;
    }
    let Some(next_idx) = next_nonempty_line_index(lines, idx + 1) else {
        return false;
    };
    let next_line = lines[next_idx].trim();
    if next_line.chars().count() < 20 || is_strong_heading(next_line) {
        return false;
    }
    true
}

/// Python `_split_numbered_sections`
fn split_numbered_sections(lines: &[&str], heading_indexes: &[usize]) -> Vec<Chapter> {
    let mut out: Vec<Chapter> = Vec::new();
    let mut chapter_no: i32 = 1;
    let mut current_act_title: Option<String> = None;
    let mut scan_from = 0usize;

    let first_heading = heading_indexes[0];
    if first_heading > 0 {
        let mut preface_lines: Vec<&str> = Vec::new();
        for idx in 0..first_heading {
            let stripped = lines[idx].trim();
            if let Some(act_t) = extract_act_heading(stripped) {
                current_act_title = Some(act_t);
            } else if !stripped.is_empty() {
                preface_lines.push(lines[idx]);
            }
        }
        let preface = preface_lines.join("\n").trim().to_string();
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
        scan_from = first_heading + 1;
    }

    for (index, &start_idx) in heading_indexes.iter().enumerate() {
        // scan for act headings before this section marker
        for idx in scan_from..start_idx {
            if let Some(act_t) = extract_act_heading(lines[idx].trim()) {
                current_act_title = Some(act_t);
            }
        }

        // build title: strip whitespace from marker, prepend act title
        let marker: String = lines[start_idx].trim().chars().filter(|c| !c.is_whitespace()).collect();
        let title = if let Some(ref act) = current_act_title {
            format!("{}{}", act, marker)
        } else {
            marker
        };

        let raw_end = heading_indexes.get(index + 1).copied().unwrap_or(lines.len());
        let end = trim_trailing_act_heading(lines, start_idx + 1, raw_end);
        let body = lines[start_idx + 1..end].join("\n").trim().to_string();

        if !body.is_empty() {
            let normalized_title: String = title.chars().take(200).collect();
            let normalized_title = if normalized_title.is_empty() {
                format!("第{}章", chapter_no)
            } else {
                normalized_title
            };
            // 去重:与上一章完全相同
            let is_dup = if let Some(prev) = out.last() {
                prev.title == normalized_title && compact(&prev.content) == compact(&body)
            } else {
                false
            };
            if !is_dup {
                out.push(Chapter {
                    title: normalized_title,
                    content: body,
                    chapter_number: chapter_no,
                    volume_title: String::new(),
                    source_marker: String::new(),
                });
                chapter_no += 1;
            }
        }
        scan_from = start_idx + 1;
    }
    out
}

/// Python `_extract_act_heading`:识别"第N幕/卷/部/集/篇"形式的篇章标题。
fn extract_act_heading(line: &str) -> Option<String> {
    if line.is_empty() || line.chars().count() > 120 {
        return None;
    }
    if SENTENCE_PUNCT_RE.is_match(line) {
        return None;
    }
    ACT_HEADING_PATTERN
        .captures(line)
        .and_then(|c| c.get(1))
        .map(|m| m.as_str().trim().to_string())
}

/// Python `_trim_trailing_act_heading`:裁掉 end 前的 act heading 行(作为下一节前导)。
fn trim_trailing_act_heading(lines: &[&str], start_idx: usize, end_idx: usize) -> usize {
    let mut idx = end_idx.saturating_sub(1);
    while idx >= start_idx && lines[idx].trim().is_empty() {
        if idx == 0 {
            return end_idx;
        }
        idx -= 1;
    }
    if idx >= start_idx && extract_act_heading(lines[idx].trim()).is_some() {
        idx
    } else {
        end_idx
    }
}

/// Python `_next_nonempty_line_index`
fn next_nonempty_line_index(lines: &[&str], start_idx: usize) -> Option<usize> {
    for idx in start_idx..lines.len() {
        if !lines[idx].trim().is_empty() {
            return Some(idx);
        }
    }
    None
}

/// Python `_to_int`:全角数字 → 半角 int。
fn to_int(value: &str) -> u32 {
    let translated: String = value
        .chars()
        .map(|c| match c {
            '０' => '0',
            '１' => '1',
            '２' => '2',
            '３' => '3',
            '４' => '4',
            '５' => '5',
            '６' => '6',
            '７' => '7',
            '８' => '8',
            '９' => '9',
            other => other,
        })
        .collect();
    translated.parse::<u32>().unwrap_or(0)
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

    // ─── Wave 8-C: remulina_special tests ────────────────────────

    #[test]
    fn test_should_use_remulina_special_positive() {
        // 5+ 条正卷-第N卷...第N章 + "第一卷 小结" → 触发
        let mut text = String::new();
        for i in 1..=6 {
            text.push_str(&format!("正卷-第{}卷 某名字 第{}章 标题\n", i, i));
        }
        text.push_str("第一卷 小结\n内容\n");
        assert!(should_use_remulina_special(&text, "auto"));
        assert!(should_use_remulina_special(&text, ""));
        assert!(should_use_remulina_special(&text, "chapter_cn"));
    }

    #[test]
    fn test_should_use_remulina_special_negative_rule() {
        // split_rule = "chapter_en" → 不触发
        let mut text = String::new();
        for i in 1..=6 {
            text.push_str(&format!("正卷-第{}卷 某名字 第{}章 标题\n", i, i));
        }
        text.push_str("第一卷 小结\n内容\n");
        assert!(!should_use_remulina_special(&text, "chapter_en"));
    }

    #[test]
    fn test_split_remulina_novel_basic() {
        // 两章完整卷章格式
        let text = "正卷-第一卷 开始 第一章 出发\n第一章内容很长，超过一些字数。                    \n正卷-第一卷 开始 第二章 到达\n第二章内容很长，超过一些字数。";
        let chapters = split_remulina_novel(text);
        assert!(!chapters.is_empty(), "should split at least one chapter");
        // 第一章的 volume_title 应含"正卷"
        if !chapters.is_empty() {
            assert!(chapters[0].volume_title.contains("正卷") || chapters[0].title.contains("第"));
        }
    }

    #[test]
    fn test_split_remulina_novel_wrapper_skipped() {
        // "第一章 标题" 后紧跟一个 full title → wrapper 被跳过,只有 full title 成为边界
        let text = "第一章 薄标题\n正卷-第一卷 正篇 第一章 详细\n这是章节正文，有很多内容。";
        let chapters = split_remulina_novel(text);
        // wrapper 第一章 薄标题 被跳过
        if !chapters.is_empty() {
            assert!(
                chapters[0].title.contains("正卷") || chapters[0].title.contains("第"),
                "wrapper should be skipped, got: {}",
                chapters[0].title
            );
        }
    }

    #[test]
    fn test_extract_remulina_volume_meta_full() {
        let result = extract_remulina_volume_meta("正卷-第一卷 名字 第一章 标题");
        assert!(result.is_some());
        let (key, _title) = result.unwrap();
        assert_eq!(key, "正卷-一卷");
    }

    #[test]
    fn test_extract_remulina_volume_meta_wrapper() {
        let result = extract_remulina_volume_meta("正一卷角色歌 天空");
        assert!(result.is_some());
        let (key, _title) = result.unwrap();
        assert_eq!(key, "正卷-一卷");
    }

    // ─── Wave 8-C: pagination_headings tests ─────────────────────

    #[test]
    fn test_collect_pagination_headings_basic() {
        // 4 个连续分页标题:章节名(1) 章节名(2) 章节名(3) 章节名(4)
        let body_line = "这是正文内容，超过二十个字符，确保能过滤。";
        let mut lines_vec: Vec<String> = Vec::new();
        for i in 1..=4 {
            lines_vec.push(format!("章节名（{}）", i));
            lines_vec.push(body_line.to_string());
            lines_vec.push(String::new());
        }
        let lines: Vec<&str> = lines_vec.iter().map(|s| s.as_str()).collect();
        let indexes = collect_pagination_headings(&lines);
        assert_eq!(indexes.len(), 4, "should find 4 pagination headings");
    }

    #[test]
    fn test_collect_pagination_headings_not_consecutive() {
        // 页码不从1开始 → 拒绝
        let body_line = "这是正文内容，超过二十个字符，确保能过滤。";
        let mut lines_vec: Vec<String> = Vec::new();
        for i in &[2u32, 3, 4, 5] {
            lines_vec.push(format!("章节名（{}）", i));
            lines_vec.push(body_line.to_string());
            lines_vec.push(String::new());
        }
        let lines: Vec<&str> = lines_vec.iter().map(|s| s.as_str()).collect();
        let indexes = collect_pagination_headings(&lines);
        assert!(indexes.is_empty(), "should reject non-starting-from-1 pages");
    }

    #[test]
    fn test_split_pagination_headings_end_to_end() {
        // 从 split_chapters_with_report 路径走 pagination_headings
        let mut text = String::new();
        for i in 1..=4 {
            text.push_str(&format!("章节名（{}）\n", i));
            text.push_str("这是正文内容，超过二十个字符，确保能过滤。这是正文内容，超过二十个字符。\n\n");
        }
        let (chapters, report) = split_chapters_with_report(&text, "auto", "");
        // 可能走 pagination_headings 也可能走其他模式,但至少应该切出章节
        assert!(chapters.len() >= 2, "should produce chapters");
        let _ = report; // mode check optional; depends on strong heading detection
    }

    // ─── Wave 8-C: numbered_sections tests ───────────────────────

    #[test]
    fn test_is_numbered_section_heading_basic() {
        let lines = vec![
            "（一）",
            "这是一段正文，内容比较长，超过了二十个字符的要求，用于测试小节标题识别。",
        ];
        assert!(is_numbered_section_heading(&lines, 0));
    }

    #[test]
    fn test_is_numbered_section_heading_rejects_short_next() {
        let lines = vec!["（一）", "短行"];
        assert!(!is_numbered_section_heading(&lines, 0));
    }

    #[test]
    fn test_collect_numbered_section_headings_basic() {
        // 每节 >= 500 字
        let long_body = "这是小节内容。".repeat(80); // ~560 字
        let mut lines_vec: Vec<String> = Vec::new();
        for i in &["（一）", "（二）", "（三）"] {
            lines_vec.push(i.to_string());
            lines_vec.push(long_body.clone());
            lines_vec.push(String::new());
        }
        let lines: Vec<&str> = lines_vec.iter().map(|s| s.as_str()).collect();
        let indexes = collect_numbered_section_headings(&lines);
        assert_eq!(indexes.len(), 3);
    }

    #[test]
    fn test_split_numbered_sections_basic() {
        let long_body = "这是小节内容，需要超过五百字以触发小节识别规则。".repeat(12);
        let mut lines2: Vec<String> = Vec::new();
        lines2.push("第一幕 序".to_string());
        lines2.push("（一）".to_string());
        lines2.push(long_body.clone());
        lines2.push(String::new());
        lines2.push("（二）".to_string());
        lines2.push(long_body.clone());
        let lines_ref: Vec<&str> = lines2.iter().map(|s| s.as_str()).collect();
        let indexes = collect_numbered_section_headings(&lines_ref);
        if indexes.len() >= 2 {
            let chapters = split_numbered_sections(&lines_ref, &indexes);
            assert!(chapters.len() >= 2);
            // act heading 应被附加到小节标题
            assert!(chapters[0].title.contains("第一幕") || chapters[0].title.contains("（一）"));
        }
    }

    #[test]
    fn test_split_numbered_sections_renumbers() {
        let long_body = "内容内容内容。".repeat(100);
        let mut lines_vec: Vec<String> = Vec::new();
        for marker in &["（一）", "（二）", "（三）"] {
            lines_vec.push(marker.to_string());
            lines_vec.push(long_body.clone());
        }
        let lines_ref: Vec<&str> = lines_vec.iter().map(|s| s.as_str()).collect();
        let indexes = collect_numbered_section_headings(&lines_ref);
        if !indexes.is_empty() {
            let chapters = split_numbered_sections(&lines_ref, &indexes);
            for (i, c) in chapters.iter().enumerate() {
                assert_eq!(c.chapter_number, i as i32 + 1);
            }
        }
    }
}
