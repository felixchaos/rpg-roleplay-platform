//! google_ai_studio — Google AI Studio (Gemini) backend with API key auth.
//!
//! Shares the same Gemini request/response protocol as `vertex.rs`, but uses
//! `x-goog-api-key` header authentication instead of OAuth ServiceAccount.
//!
//! Endpoint:
//!   `https://generativelanguage.googleapis.com/v1beta/models/{model}:{action}`
//!   action: `streamGenerateContent` / `generateContent`
//!
//! Streaming uses `?alt=sse`, identical SSE format to Vertex.

use async_trait::async_trait;
use futures_util::stream::{self, StreamExt, TryStreamExt};
use eventsource_stream::Eventsource;
use smallvec::SmallVec;

use crate::pipeline::{
    build_http_client, BackendKind, ChatChunk, ChatRequest, ChunkStream, LlmBackend, LlmError,
    ModelInfo, Usage,
};
// Reuse Gemini protocol helpers from vertex module.
use crate::vertex;

const BASE_URL: &str = "https://generativelanguage.googleapis.com/v1beta";

pub struct GoogleAiStudioBackend {
    api_key: String,
    http: reqwest::Client,
}

impl GoogleAiStudioBackend {
    pub fn new(api_key: impl Into<String>) -> Result<Self, LlmError> {
        Ok(Self {
            api_key: api_key.into(),
            http: build_http_client(600)?,
        })
    }

    fn endpoint(&self, model: &str, action: &str) -> String {
        format!("{BASE_URL}/models/{model}:{action}")
    }
}

#[async_trait]
impl LlmBackend for GoogleAiStudioBackend {
    fn kind(&self) -> BackendKind {
        BackendKind::GoogleAiStudio
    }

    #[tracing::instrument(skip(self, req), fields(model = %req.model, stream = req.stream))]
    async fn stream_chat<'a>(&'a self, req: ChatRequest) -> Result<ChunkStream<'a>, LlmError> {
        let body = vertex::build_gemini_body(&req);

        let action = if req.stream {
            "streamGenerateContent"
        } else {
            "generateContent"
        };
        let mut url = self.endpoint(&req.model, action);
        if req.stream {
            url.push_str("?alt=sse");
        }

        let resp = self
            .http
            .post(&url)
            .header("x-goog-api-key", &self.api_key)
            .header("Content-Type", "application/json")
            .json(&body)
            .send()
            .await?;
        let status = resp.status();
        if !status.is_success() {
            let body = resp.text().await.unwrap_or_default();
            return Err(LlmError::Provider {
                status: status.as_u16(),
                body,
            });
        }

        if !req.stream {
            let v: serde_json::Value = resp.json().await?;
            let mut out: Vec<Result<ChatChunk, LlmError>> = Vec::new();
            vertex::push_gemini_response_chunks(&v, &mut out);
            out.push(Ok(ChatChunk::Stop {
                reason: vertex::gemini_stop_reason(&v).unwrap_or_else(|| "stop".into()),
            }));
            return Ok(Box::pin(stream::iter(out)));
        }

        // SSE stream: identical format to Vertex.
        let event_stream = resp
            .bytes_stream()
            .map_err(std::io::Error::other)
            .eventsource()
            .map_err(|e| LlmError::Stream(e.to_string()));

        let parsed = event_stream.scan((), |_state, ev_res| {
            let chunks: SmallVec<[Result<ChatChunk, LlmError>; 2]> = match ev_res {
                Ok(ev) => vertex::parse_gemini_sse_data(&ev.data),
                Err(e) => {
                    let mut sv = SmallVec::new();
                    sv.push(Err(e));
                    sv
                }
            };
            futures_util::future::ready(Some(chunks))
        });
        let flat = parsed.flat_map(stream::iter);
        Ok(Box::pin(flat))
    }

    #[tracing::instrument(skip(self))]
    async fn list_models(&self) -> Result<Vec<ModelInfo>, LlmError> {
        Ok(default_google_ai_studio_models())
    }

    async fn embed(&self, _model: &str, _texts: &[String]) -> Result<Vec<Vec<f32>>, LlmError> {
        Err(LlmError::Config(
            "google_ai_studio: embedding not implemented (use Vertex for embeddings)".into(),
        ))
    }
}

fn default_google_ai_studio_models() -> Vec<ModelInfo> {
    let ids = [
        ("gemini-3.5-flash", "Gemini 3.5 Flash"),
        ("gemini-3.1-pro", "Gemini 3.1 Pro"),
        ("gemini-2.5-flash", "Gemini 2.5 Flash"),
    ];
    ids.iter()
        .map(|(id, name)| ModelInfo {
            id: (*id).to_string(),
            display_name: (*name).to_string(),
            capabilities: vec![
                "text".into(),
                "streaming".into(),
                "tools".into(),
                "image_input".into(),
            ],
            context_window: Some(1_000_000),
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_endpoint_format() {
        let b = GoogleAiStudioBackend {
            api_key: "test".into(),
            http: reqwest::Client::new(),
        };
        let url = b.endpoint("gemini-2.5-flash", "streamGenerateContent");
        assert_eq!(
            url,
            "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:streamGenerateContent"
        );
    }

    #[test]
    fn test_kind() {
        let b = GoogleAiStudioBackend {
            api_key: "test".into(),
            http: reqwest::Client::new(),
        };
        assert!(matches!(b.kind(), BackendKind::GoogleAiStudio));
    }
}
