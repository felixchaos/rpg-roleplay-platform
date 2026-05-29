//! SSE chunk JSON parse micro-benchmarks
//!
//! 对比 simd-json(via rpg_llm::simd_parse) 与 serde_json::from_str 的解析速度。
//! 覆盖:
//!   - 小 chunk  ~100 B  (典型 text_delta)
//!   - 中 chunk  ~500 B  (content_block_start with input)
//!   - 大 chunk  ~1 KB   (usage metadata / cache tokens)
//!   - 深嵌套     (message_start payload)

use criterion::{black_box, criterion_group, criterion_main, BenchmarkId, Criterion};
use rpg_llm::simd_parse::{parse_json_str, parse_sse_value};
use serde_json::Value;

// ── SSE chunk fixtures ───────────────────────────────────────────────────────

/// text_delta ~100 B
const CHUNK_TEXT_DELTA: &str =
    r#"{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"Hello, world!"}}"#;

/// content_block_stop ~60 B
const CHUNK_BLOCK_STOP: &str =
    r#"{"type":"content_block_stop","index":0}"#;

/// content_block_start tool_use ~220 B
const CHUNK_TOOL_START: &str =
    r#"{"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_XYZABC","name":"get_weather","input":{}}}"#;

/// input_json_delta with escaped JSON ~180 B
const CHUNK_INPUT_JSON_DELTA: &str =
    r#"{"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\"location\":\"Tokyo\",\"unit\":\"celsius\",\"date\":\"2026-05-29\"}"}}"#;

/// message_start with usage (深嵌套) ~500 B
const CHUNK_MESSAGE_START: &str = r#"{
  "type": "message_start",
  "message": {
    "id": "msg_01XFDUDYJgAACTypGgBSX8FY",
    "type": "message",
    "role": "assistant",
    "content": [],
    "model": "claude-opus-4-5",
    "stop_reason": null,
    "stop_sequence": null,
    "usage": {
      "input_tokens": 25,
      "output_tokens": 1,
      "cache_creation_input_tokens": 0,
      "cache_read_input_tokens": 0
    }
  }
}"#;

/// 大 usage payload ~350 B (cache tokens)
const CHUNK_LARGE_USAGE: &str = r#"{
  "type": "message_delta",
  "delta": {
    "stop_reason": "end_turn",
    "stop_sequence": null
  },
  "usage": {
    "output_tokens": 8192,
    "input_tokens": 200000,
    "cache_creation_input_tokens": 150000,
    "cache_read_input_tokens": 50000,
    "cache_ttl_seconds": 300
  }
}"#;

/// ~1 KB chunk (Vertex SSE candidates array)
const CHUNK_VERTEX_1KB: &str = r#"{
  "candidates": [
    {
      "content": {
        "parts": [
          {"text": "这是一段测试用的 Vertex AI 回复文本，包含中文内容与常见 Unicode 字符。长度约为一千字节左右，用于基准测试 simd-json 对较大 SSE payload 的解析性能。字符越多测试越有意义。"}
        ],
        "role": "model"
      },
      "finishReason": "STOP",
      "index": 0,
      "safetyRatings": [
        {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "probability": "NEGLIGIBLE"},
        {"category": "HARM_CATEGORY_HATE_SPEECH",       "probability": "NEGLIGIBLE"},
        {"category": "HARM_CATEGORY_HARASSMENT",        "probability": "NEGLIGIBLE"},
        {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "probability": "NEGLIGIBLE"}
      ]
    }
  ],
  "usageMetadata": {
    "promptTokenCount": 512,
    "candidatesTokenCount": 128,
    "thoughtsTokenCount": 0
  },
  "modelVersion": "gemini-2.0-flash-001"
}"#;

// ── helpers ──────────────────────────────────────────────────────────────────

fn serde_parse(s: &str) -> Value {
    serde_json::from_str(s).expect("serde_json parse failed in bench")
}

fn simd_parse(s: &str) -> Value {
    parse_sse_value(s).expect("simd parse failed in bench")
}

// ── individual chunk benches (simd vs serde) ─────────────────────────────────

fn bench_text_delta(c: &mut Criterion) {
    let mut g = c.benchmark_group("sse_parse/text_delta_~100B");
    g.bench_function("simd_json", |b| {
        b.iter(|| black_box(simd_parse(black_box(CHUNK_TEXT_DELTA))));
    });
    g.bench_function("serde_json", |b| {
        b.iter(|| black_box(serde_parse(black_box(CHUNK_TEXT_DELTA))));
    });
    g.finish();
}

fn bench_tool_start(c: &mut Criterion) {
    let mut g = c.benchmark_group("sse_parse/tool_start_~220B");
    g.bench_function("simd_json", |b| {
        b.iter(|| black_box(simd_parse(black_box(CHUNK_TOOL_START))));
    });
    g.bench_function("serde_json", |b| {
        b.iter(|| black_box(serde_parse(black_box(CHUNK_TOOL_START))));
    });
    g.finish();
}

fn bench_input_json_delta(c: &mut Criterion) {
    let mut g = c.benchmark_group("sse_parse/input_json_delta_~180B");
    g.bench_function("simd_json", |b| {
        b.iter(|| black_box(simd_parse(black_box(CHUNK_INPUT_JSON_DELTA))));
    });
    g.bench_function("serde_json", |b| {
        b.iter(|| black_box(serde_parse(black_box(CHUNK_INPUT_JSON_DELTA))));
    });
    g.finish();
}

fn bench_message_start(c: &mut Criterion) {
    let mut g = c.benchmark_group("sse_parse/message_start_~500B");
    g.bench_function("simd_json", |b| {
        b.iter(|| black_box(simd_parse(black_box(CHUNK_MESSAGE_START))));
    });
    g.bench_function("serde_json", |b| {
        b.iter(|| black_box(serde_parse(black_box(CHUNK_MESSAGE_START))));
    });
    g.finish();
}

fn bench_large_usage(c: &mut Criterion) {
    let mut g = c.benchmark_group("sse_parse/large_usage_~350B");
    g.bench_function("simd_json", |b| {
        b.iter(|| black_box(simd_parse(black_box(CHUNK_LARGE_USAGE))));
    });
    g.bench_function("serde_json", |b| {
        b.iter(|| black_box(serde_parse(black_box(CHUNK_LARGE_USAGE))));
    });
    g.finish();
}

fn bench_vertex_1kb(c: &mut Criterion) {
    let mut g = c.benchmark_group("sse_parse/vertex_~1KB");
    g.bench_function("simd_json", |b| {
        b.iter(|| black_box(simd_parse(black_box(CHUNK_VERTEX_1KB))));
    });
    g.bench_function("serde_json", |b| {
        b.iter(|| black_box(serde_parse(black_box(CHUNK_VERTEX_1KB))));
    });
    g.finish();
}

/// 变参对比:随 payload 大小缩放
fn bench_size_scaling(c: &mut Criterion) {
    let payloads: &[(&str, &str)] = &[
        ("100B_block_stop",  CHUNK_BLOCK_STOP),
        ("100B_text_delta",  CHUNK_TEXT_DELTA),
        ("220B_tool_start",  CHUNK_TOOL_START),
        ("500B_msg_start",   CHUNK_MESSAGE_START),
        ("1KB_vertex",       CHUNK_VERTEX_1KB),
    ];
    let mut g = c.benchmark_group("sse_parse/simd_size_scaling");
    for (label, payload) in payloads {
        g.bench_with_input(BenchmarkId::from_parameter(label), payload, |b, p| {
            b.iter(|| black_box(simd_parse(black_box(p))));
        });
    }
    g.finish();
}

// ── typed struct parse (concrete type, not Value) ────────────────────────────

#[derive(Debug, serde::Deserialize)]
#[allow(dead_code)]
struct ContentBlockDelta {
    #[serde(rename = "type")]
    event_type: String,
    index: u32,
}

fn bench_typed_struct_parse(c: &mut Criterion) {
    let mut g = c.benchmark_group("sse_parse/typed_struct");
    g.bench_function("simd_json", |b| {
        b.iter(|| {
            let v: ContentBlockDelta =
                parse_json_str(black_box(CHUNK_TEXT_DELTA)).expect("parse failed");
            black_box(v);
        });
    });
    g.bench_function("serde_json", |b| {
        b.iter(|| {
            let v: ContentBlockDelta =
                serde_json::from_str(black_box(CHUNK_TEXT_DELTA)).expect("parse failed");
            black_box(v);
        });
    });
    g.finish();
}

criterion_group!(
    benches,
    bench_text_delta,
    bench_tool_start,
    bench_input_json_delta,
    bench_message_start,
    bench_large_usage,
    bench_vertex_1kb,
    bench_size_scaling,
    bench_typed_struct_parse,
);
criterion_main!(benches);
