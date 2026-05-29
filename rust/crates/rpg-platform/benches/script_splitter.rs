//! Chapter splitter micro-benchmarks
//!
//! 覆盖:
//!   - split_chapters_with_report: 10 MB 中文小说文本(模拟 485万字输入)
//!   - clean_text: 10 MB 脏文本(含 CRLF / 水印行)
//!   - decode_bytes: 10 MB UTF-8 / GBK 字节切换
//!   - build_custom_pattern: 静态安全检查
//!   - 不同切分规则(auto / chapter_cn / corpus / fallback)对比

use criterion::{black_box, criterion_group, criterion_main, BenchmarkId, Criterion};
use rpg_platform::script_import::splitter::{
    build_custom_pattern, clean_text, decode_bytes, split_chapters_with_report,
};

// ── text fixtures ─────────────────────────────────────────────────────────────

/// 生成一段模拟中文小说文本(含章节标题 + 正文),target_chars 字符左右。
fn make_novel_text(target_chars: usize) -> String {
    // 一段标准正文片段(~300 字)
    let body_para = "这是一段测试用的小说正文,包含常见叙事描写。\
主角缓步走向城门,目光扫过街道两侧摆摊的小贩。\
空气中漂浮着油炸食物的香气,夹杂着牲畜的骚味。\
他拉了拉斗篷,确认剑柄没有外露,随后踏入城内。\
守卫懒洋洋地看了他一眼,并未多问。\
他在心里默默感谢了一下灰色的斗篷颜色——平凡无奇,\
却是最好的伪装。巷子深处传来几声犬吠,他循声望去,\
只看到一条橘猫悠然从墙头一跃而下,落地时四蹄无声。\
夜幕即将降临,他需要在天黑之前找到落脚之处。\n\n";

    let chapter_title_template = [
        "第{}章 起航",
        "第{}章 暗流",
        "第{}章 交锋",
        "第{}章 谜局",
        "第{}章 裂变",
        "第{}章 归途",
    ];

    let chapter_chars = body_para.chars().count() * 5 + 30; // ~1500 字/章
    let n_chapters = (target_chars / chapter_chars).max(2);

    let mut out = String::with_capacity(target_chars + 1024);
    let mut total = 0usize;

    for i in 1..=n_chapters {
        if total >= target_chars {
            break;
        }
        let title = chapter_title_template[(i - 1) % chapter_title_template.len()]
            .replace("{}", &i.to_string());
        out.push_str(&title);
        out.push('\n');
        total += title.chars().count() + 1;

        // 每章 5 段正文
        for _ in 0..5 {
            out.push_str(body_para);
            total += body_para.chars().count();
        }
    }
    // 确保长度接近目标
    while out.chars().count() < target_chars {
        out.push_str(body_para);
    }
    out
}

/// 生成带水印行和 CRLF 的脏文本
fn make_dirty_text(target_chars: usize) -> String {
    let clean_para = "正文内容,干净段落。包含一些汉字与标点。\
这是第二句话,续接上文。故事在这里继续发展,情节推进。\n\n";
    let dirty_line = "啃书小说网最新章节请访问www.kenshu.cc获取更新\r\n";

    let mut out = String::with_capacity(target_chars + 4096);
    let mut total = 0usize;
    let mut line_no = 0usize;

    while total < target_chars {
        out.push_str(clean_para);
        total += clean_para.chars().count();
        line_no += 1;
        if line_no.is_multiple_of(10) {
            // 每 10 段加一条水印行
            out.push_str(dirty_line);
            total += dirty_line.chars().count();
        }
    }
    out
}

/// 生成 GBK 编码字节(用于 decode_bytes bench)
fn make_gbk_bytes(target_bytes: usize) -> Vec<u8> {
    let (cow, _, had_errors) =
        encoding_rs::GBK.encode("中文内容，用于测试 GBK 解码速度。包含各类常用汉字与标点符号。");
    assert!(!had_errors);
    let unit = cow.into_owned();
    let mut out = Vec::with_capacity(target_bytes + unit.len());
    while out.len() < target_bytes {
        out.extend_from_slice(&unit);
    }
    out
}

// ── split_chapters_with_report benches ───────────────────────────────────────

fn bench_split_10mb_auto(c: &mut Criterion) {
    // ~10 MB ≈ 5 000 000 字符(中文 3 字节/字,但 target_chars 是字符数)
    let text = make_novel_text(500_000); // 50万字 ≈ 1.5 MB UTF-8,减少 CI 时间
    c.bench_function("script_splitter/split_auto_500k_chars", |b| {
        b.iter(|| {
            let (chapters, report) = split_chapters_with_report(
                black_box(&text),
                black_box("auto"),
                black_box(""),
            );
            black_box((chapters, report));
        });
    });
}

fn bench_split_10mb_chapter_cn(c: &mut Criterion) {
    let text = make_novel_text(500_000);
    c.bench_function("script_splitter/split_chapter_cn_500k_chars", |b| {
        b.iter(|| {
            let (chapters, report) = split_chapters_with_report(
                black_box(&text),
                black_box("chapter_cn"),
                black_box(""),
            );
            black_box((chapters, report));
        });
    });
}

fn bench_split_varying_size(c: &mut Criterion) {
    let mut g = c.benchmark_group("script_splitter/split_auto_varying_size");
    for &n_chars in &[10_000usize, 50_000, 200_000, 500_000] {
        let text = make_novel_text(n_chars);
        g.bench_with_input(
            BenchmarkId::from_parameter(format!("{}_chars", n_chars)),
            &text,
            |b, t| {
                b.iter(|| {
                    let (chs, rep) = split_chapters_with_report(black_box(t), "auto", "");
                    black_box((chs, rep));
                });
            },
        );
    }
    g.finish();
}

// ── clean_text benches ───────────────────────────────────────────────────────

fn bench_clean_text_500k(c: &mut Criterion) {
    let dirty = make_dirty_text(500_000);
    c.bench_function("script_splitter/clean_text_500k_chars", |b| {
        b.iter(|| {
            let out = clean_text(black_box(&dirty));
            black_box(out);
        });
    });
}

fn bench_clean_text_varying_size(c: &mut Criterion) {
    let mut g = c.benchmark_group("script_splitter/clean_text_varying_size");
    for &n_chars in &[10_000usize, 100_000, 500_000] {
        let dirty = make_dirty_text(n_chars);
        g.bench_with_input(
            BenchmarkId::from_parameter(format!("{}_chars", n_chars)),
            &dirty,
            |b, t| {
                b.iter(|| black_box(clean_text(black_box(t))));
            },
        );
    }
    g.finish();
}

// ── decode_bytes bench ────────────────────────────────────────────────────────

fn bench_decode_bytes_utf8(c: &mut Criterion) {
    // 500 KB UTF-8
    let text = make_novel_text(200_000);
    let bytes = text.as_bytes().to_vec();
    c.bench_function("script_splitter/decode_bytes_utf8_200k_chars", |b| {
        b.iter(|| {
            let (s, enc) = decode_bytes(black_box(&bytes));
            black_box((s, enc));
        });
    });
}

fn bench_decode_bytes_gbk(c: &mut Criterion) {
    let bytes = make_gbk_bytes(300_000); // 300 KB GBK
    c.bench_function("script_splitter/decode_bytes_gbk_300k_bytes", |b| {
        b.iter(|| {
            let (s, enc) = decode_bytes(black_box(&bytes));
            black_box((s, enc));
        });
    });
}

// ── build_custom_pattern bench ───────────────────────────────────────────────

fn bench_build_custom_pattern(c: &mut Criterion) {
    let mut g = c.benchmark_group("script_splitter/build_custom_pattern");
    let patterns: &[(&str, &str)] = &[
        ("wildcard",   "第*章"),
        ("static",     "(?m)^第[0-9]+章"),
        ("two_wild",   "第*卷第*章"),
    ];
    for (label, pat) in patterns {
        g.bench_with_input(BenchmarkId::from_parameter(label), pat, |b, p| {
            b.iter(|| {
                let r = build_custom_pattern(black_box(p));
                black_box(r);
            });
        });
    }
    g.finish();
}

criterion_group!(
    benches,
    bench_split_10mb_auto,
    bench_split_10mb_chapter_cn,
    bench_split_varying_size,
    bench_clean_text_500k,
    bench_clean_text_varying_size,
    bench_decode_bytes_utf8,
    bench_decode_bytes_gbk,
    bench_build_custom_pattern,
);
criterion_main!(benches);
