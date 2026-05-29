//! Web Vitals RUM 上报端点
//! POST /api/metrics/web-vitals — 匿名接收浏览器 Core Web Vitals 数据,tracing 记录后丢弃

use axum::{
    extract::{DefaultBodyLimit, Json},
    http::StatusCode,
    response::IntoResponse,
    routing::post,
    Router,
};
use serde::Deserialize;

use crate::AppState;

/// Web Vitals 上报体最大允许 4 KB(浏览器单次上报远小于此值)。
const METRICS_BODY_LIMIT: usize = 4 * 1024;

pub fn router() -> Router<AppState> {
    Router::new()
        .route("/api/metrics/web-vitals", post(post_web_vitals))
        .layer(DefaultBodyLimit::max(METRICS_BODY_LIMIT))
}

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct WebVitalsReport {
    pub name: String,
    pub value: f64,
    pub rating: Option<String>,
    pub delta: Option<f64>,
    pub id: String,
    pub navigation_type: Option<String>,
    pub path: String,
}

pub async fn post_web_vitals(Json(r): Json<WebVitalsReport>) -> impl IntoResponse {
    tracing::info!(
        metric.name = %r.name,
        metric.value = r.value,
        metric.rating = ?r.rating,
        metric.path = %r.path,
        "web-vitals report"
    );
    StatusCode::NO_CONTENT
}
