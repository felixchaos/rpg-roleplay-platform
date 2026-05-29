//! simd_parse — 包裹 simd-json 的 hot-path JSON 解析工具函数。
//!
//! simd-json 通过 in-place mutation 实现零拷贝解析,其 `from_str` 签名为
//! `fn from_str<'a, T>(s: &'a mut str) -> Result<T, Error>`。
//! SSE data 到达时是 `&str`,故此处做一次 `.to_string()` 拷贝再传入;
//! 对于 LLM 流式 chunk(通常几十到几百字节)来说开销可忽略,
//! 同时规避了原始字符串引用的生命周期问题。
//!
//! # Fallback 策略
//! 当 simd-json 解析失败时,自动尝试 serde_json::from_str。
//! 失败理由举例:BOM 字符、非 UTF-8 转义序列边角情况。
//! 两层均失败才返回错误。

use serde::de::DeserializeOwned;

/// 解析 JSON 字符串为 `T`。
///
/// 优先走 simd-json(SIMD 加速,hot path 用);
/// 若 simd-json 报错则 fallback 到 serde_json。
///
/// 调用者无需关心底层选择;接口与 `serde_json::from_str` 完全兼容。
#[inline]
pub fn parse_json_str<T: DeserializeOwned>(s: &str) -> Result<T, serde_json::Error> {
    // simd-json 需要可变 String(in-place mutation),且 from_str 是 unsafe。
    let mut owned = s.to_string();
    // SAFETY: simd-json 对合法 UTF-8 JSON 字符串做 in-place 解析;
    // owned 是 Rust String,满足有效 UTF-8 前提条件。
    // 错误时回退到 serde_json,不依赖已突变的 owned。
    match unsafe { simd_json::from_str::<T>(&mut owned) } {
        Ok(v) => Ok(v),
        Err(_) => {
            // simd-json 失败 → fallback serde_json。
            serde_json::from_str(s)
        }
    }
}

/// 专用于 SSE 流式 chunk 的 `serde_json::Value` 解析。
///
/// 等同于 `parse_json_str::<serde_json::Value>(s)`,但更直接,
/// 减少单态化开销。
#[inline]
pub fn parse_sse_value(s: &str) -> Result<serde_json::Value, serde_json::Error> {
    parse_json_str::<serde_json::Value>(s)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    // -------------------------------------------------------------------------
    // 基础兼容性:simd-json 与 serde_json parse 同一 JSON 结果应一致。
    // -------------------------------------------------------------------------

    #[test]
    fn test_parse_simple_object() {
        let s = r#"{"type":"message_start","index":0}"#;
        let v: serde_json::Value = parse_json_str(s).unwrap();
        assert_eq!(v["type"], "message_start");
        assert_eq!(v["index"], 0);
    }

    #[test]
    fn test_parse_nested_object() {
        let s = r#"{"message":{"id":"msg_01","usage":{"input_tokens":42,"output_tokens":7}}}"#;
        let v: serde_json::Value = parse_json_str(s).unwrap();
        assert_eq!(v["message"]["usage"]["input_tokens"], 42);
        assert_eq!(v["message"]["usage"]["output_tokens"], 7);
    }

    #[test]
    fn test_parse_array_field() {
        let s = r#"{"candidates":[{"content":{"parts":[{"text":"hello"}]}}]}"#;
        let v: serde_json::Value = parse_json_str(s).unwrap();
        let text = v["candidates"][0]["content"]["parts"][0]["text"].as_str().unwrap();
        assert_eq!(text, "hello");
    }

    #[test]
    fn test_parse_unicode_text() {
        let s = r#"{"text":"你好世界"}"#;
        let v: serde_json::Value = parse_json_str(s).unwrap();
        assert_eq!(v["text"], "你好世界");
    }

    #[test]
    fn test_parse_empty_object() {
        let s = r#"{}"#;
        let v: serde_json::Value = parse_json_str(s).unwrap();
        assert!(v.as_object().unwrap().is_empty());
    }

    #[test]
    fn test_parse_invalid_json_returns_error() {
        let s = "not valid json {{{";
        let result: Result<serde_json::Value, _> = parse_json_str(s);
        assert!(result.is_err(), "invalid JSON should return an error");
    }

    #[test]
    fn test_parse_sse_value_matches_serde_json() {
        let s = r#"{"delta":{"type":"text_delta","text":"Hello"}}"#;
        let via_simd = parse_sse_value(s).unwrap();
        let via_serde: serde_json::Value = serde_json::from_str(s).unwrap();
        assert_eq!(via_simd, via_serde, "simd and serde_json must yield identical Value");
    }

    #[test]
    fn test_parse_tool_use_payload() {
        // Anthropic content_block_start tool_use 典型 payload
        let s = r#"{"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_X","name":"calc_tool","input":{}}}"#;
        let via_simd = parse_sse_value(s).unwrap();
        let via_serde: serde_json::Value = serde_json::from_str(s).unwrap();
        assert_eq!(via_simd, via_serde);
    }

    #[test]
    fn test_parse_partial_json_delta() {
        // input_json_delta payload(包含转义引号)
        let s = r#"{"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\"a\":1}"}}"#;
        let via_simd = parse_sse_value(s).unwrap();
        let via_serde: serde_json::Value = serde_json::from_str(s).unwrap();
        assert_eq!(via_simd, via_serde);
    }

    #[test]
    fn test_parse_gemini_usage_metadata() {
        // Vertex SSE 典型 usageMetadata payload
        let s = r#"{"usageMetadata":{"promptTokenCount":100,"candidatesTokenCount":50,"thoughtsTokenCount":10}}"#;
        let via_simd = parse_sse_value(s).unwrap();
        let via_serde: serde_json::Value = serde_json::from_str(s).unwrap();
        assert_eq!(via_simd, via_serde);
    }

    #[test]
    fn test_parse_responses_api_event() {
        // OpenAI Responses API SSE 典型事件
        let s = r#"{"delta":"Hello world","item_id":"item_01","output_index":0,"content_index":0}"#;
        let via_simd = parse_sse_value(s).unwrap();
        let via_serde: serde_json::Value = serde_json::from_str(s).unwrap();
        assert_eq!(via_simd, via_serde);
    }

    #[test]
    fn test_parse_float_numbers() {
        // 温度等浮点 JSON
        let s = r#"{"temperature":0.7,"top_p":0.95}"#;
        let v: serde_json::Value = parse_json_str(s).unwrap();
        let temp = v["temperature"].as_f64().unwrap();
        assert!((temp - 0.7).abs() < 1e-9);
    }

    #[test]
    fn test_parse_large_usage_values() {
        // cache_creation_input_tokens 可能很大
        let s = r#"{"input_tokens":200000,"output_tokens":8192,"cache_creation_input_tokens":150000,"cache_read_input_tokens":50000}"#;
        let via_simd = parse_sse_value(s).unwrap();
        let via_serde: serde_json::Value = serde_json::from_str(s).unwrap();
        assert_eq!(via_simd, via_serde);
    }

    #[test]
    fn test_parse_bool_and_null() {
        let s = r#"{"stream":true,"stop_reason":null,"done":false}"#;
        let via_simd = parse_sse_value(s).unwrap();
        let via_serde: serde_json::Value = serde_json::from_str(s).unwrap();
        assert_eq!(via_simd, via_serde);
    }

    // -------------------------------------------------------------------------
    // 反序列化到具体类型 (serde Deserialize)
    // -------------------------------------------------------------------------

    #[derive(Debug, serde::Deserialize, PartialEq)]
    struct SimpleEvent {
        #[serde(rename = "type")]
        event_type: String,
        index: u32,
    }

    #[test]
    fn test_parse_to_struct() {
        let s = r#"{"type":"content_block_stop","index":2}"#;
        let via_simd: SimpleEvent = parse_json_str(s).unwrap();
        let via_serde: SimpleEvent = serde_json::from_str(s).unwrap();
        assert_eq!(via_simd, via_serde);
    }

    // -------------------------------------------------------------------------
    // 幂等性:parse → serialize → re-parse 结果相同
    // -------------------------------------------------------------------------

    #[test]
    fn test_round_trip_idempotent() {
        let s = r#"{"type":"message_stop","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":42}}"#;
        let v1: serde_json::Value = parse_sse_value(s).unwrap();
        let serialized = serde_json::to_string(&v1).unwrap();
        let v2: serde_json::Value = parse_sse_value(&serialized).unwrap();
        assert_eq!(v1, v2, "round-trip must be idempotent");
    }
}
