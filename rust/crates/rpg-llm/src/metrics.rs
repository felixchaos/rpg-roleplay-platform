//! metrics — LLM 调用指标埋点。
//!
//! 指标列表:
//!   * `llm_request_duration_seconds` — Histogram,label `backend`(anthropic/vertex_ai/openai/openai_compat)
//!   * `llm_request_total`           — Counter,label `backend`, `status`(ok/error)
//!   * `llm_tokens_used_total`       — Counter,label `backend`, `model`, `direction`(input/output)
//!
//! 使用 `metrics` crate 的宏(0.24 API),与 `axum-prometheus` 通过
//! `metrics-exporter-prometheus` 共享同一个全局 recorder,所有指标自动出现在 /metrics 端点。

use std::time::Duration;

/// 记录一次 LLM 请求延迟 + 结果状态。
///
/// `backend` — BackendKind::Display 字符串("anthropic" / "vertex_ai" / "openai" / "openai_compat")
/// `duration` — 整个 stream_chat 调用耗时(从发请求到流关闭)
/// `ok`       — true 表示成功拿到 stream(不论流内是否含 ChatChunk::Error)
pub fn record_llm_request(backend: &str, duration: Duration, ok: bool) {
    let status = if ok { "ok" } else { "error" };
    metrics::histogram!(
        "llm_request_duration_seconds",
        "backend" => backend.to_owned(),
        "status"  => status,
    )
    .record(duration.as_secs_f64());

    metrics::counter!(
        "llm_request_total",
        "backend" => backend.to_owned(),
        "status"  => status,
    )
    .increment(1);
}

/// 记录一次 LLM 调用消耗的 token 数。
///
/// `backend`   — 同上。
/// `model`     — 模型 id 字符串(从 `ChatRequest::model` 取)。
/// `input`     — `Usage::input_tokens`。
/// `output`    — `Usage::output_tokens`。
/// `cache_read`— `Usage::cache_read`(Anthropic prompt cache hit)。
pub fn record_llm_tokens(backend: &str, model: &str, input: u32, output: u32, cache_read: u32) {
    if input > 0 {
        metrics::counter!(
            "llm_tokens_used_total",
            "backend"   => backend.to_owned(),
            "model"     => model.to_owned(),
            "direction" => "input",
        )
        .increment(u64::from(input));
    }
    if output > 0 {
        metrics::counter!(
            "llm_tokens_used_total",
            "backend"   => backend.to_owned(),
            "model"     => model.to_owned(),
            "direction" => "output",
        )
        .increment(u64::from(output));
    }
    if cache_read > 0 {
        metrics::counter!(
            "llm_tokens_used_total",
            "backend"   => backend.to_owned(),
            "model"     => model.to_owned(),
            "direction" => "cache_read",
        )
        .increment(u64::from(cache_read));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    /// 验证 record_llm_request / record_llm_tokens 在无 recorder 时不 panic。
    /// (metrics 0.24 默认 noop recorder — 若 recorder 未安装, counter/histogram 操作无副作用)
    #[test]
    fn record_llm_request_ok_no_panic() {
        record_llm_request("anthropic", Duration::from_millis(300), true);
        record_llm_request("vertex_ai", Duration::from_millis(500), false);
    }

    #[test]
    fn record_llm_tokens_no_panic() {
        record_llm_tokens("anthropic", "claude-3-5-sonnet-20241022", 1024, 256, 512);
        record_llm_tokens("openai", "gpt-4o", 0, 0, 0);
    }
}
