//! usage —— token_usage 表写入 + 聚合查询。
//!
//! 对应 Python: `rpg/platform_app/usage.py`。
//!
//! 复用 `rpg_db::repos::token_usage::insert` 走底层;聚合查询保留在本模块。
//!
//! 价格表通过 `rpg_llm::LlmRouter::pricing_for` 拿,优先 catalog 内联 pricing,
//! 回落 BUILTIN_PRICING(2025-Q2 各家官网定价)。`compute_cost` 把
//! `UsageBreakdown` 转成 USD numeric(12,6) 字符串。

use once_cell::sync::Lazy;
use rpg_core::UserId;
use rpg_llm::{LlmRouter, ModelPricing};
use serde::{Deserialize, Serialize};
use sqlx::{PgPool, Row};

use crate::error::{PlatformError, PlatformResult};

/// 全局只读 router,用来查 builtin pricing(无需 backend 注册)。
static PRICING_ROUTER: Lazy<LlmRouter> = Lazy::new(|| {
    let mut r = LlmRouter::new();
    r.set_catalog(rpg_llm::ModelCatalog::default());
    r
});

/// 单轮 token 用量(对应 Python `backend.last_usage`)。
#[derive(Debug, Clone, Default, Deserialize)]
pub struct UsageBreakdown {
    #[serde(default)]
    pub input_tokens: i32,
    #[serde(default)]
    pub output_tokens: i32,
    #[serde(default)]
    pub cached_input_tokens: i32,
    #[serde(default)]
    pub reasoning_tokens: i32,
    #[serde(default)]
    pub total_tokens: i32,
}

/// Python `compute_cost`。返回 USD 字符串(numeric(12,6))。
///
/// 实现:`LlmRouter::pricing_for(api_id, model_id)` -> `ModelPricing`,
/// 按 input/output/cache_read/cache_write 四档 per-1k token 计价相加。
/// 找不到定价时返回 "0.000000"。
pub fn compute_cost(
    api_id: &str,
    model_real_name: &str,
    usage: &UsageBreakdown,
) -> String {
    let Some(pricing) = PRICING_ROUTER.pricing_for(api_id, model_real_name) else {
        return "0.000000".to_string();
    };
    let cost = cost_from_pricing(pricing, usage);
    // numeric(12,6) → 截到 6 位小数,负数夹到 0。
    format!("{:.6}", cost.max(0.0))
}

fn cost_from_pricing(pricing: &ModelPricing, u: &UsageBreakdown) -> f64 {
    // cached_input_tokens 走 cache_read 价;其余 input 走 input 价。
    // reasoning_tokens 算 output 范畴(供应商一般这样算)。
    let cached = u.cached_input_tokens.max(0) as f64;
    let fresh_input = (u.input_tokens.max(0) as f64 - cached).max(0.0);
    let output = (u.output_tokens.max(0) + u.reasoning_tokens.max(0)) as f64;

    let input_cost = fresh_input / 1000.0 * pricing.input_per_1k_usd;
    let cached_cost = cached / 1000.0 * pricing.cache_read_per_1k_usd;
    let output_cost = output / 1000.0 * pricing.output_per_1k_usd;
    input_cost + cached_cost + output_cost
}

/// 返回 (api_id, model_id) 当前生效的定价(供 routes 层展示用)。
pub fn pricing_for(api_id: &str, model_id: &str) -> Option<ModelPricing> {
    PRICING_ROUTER.pricing_for(api_id, model_id).cloned()
}

/// Python `record_usage` —— 写一条 token_usage。
#[allow(clippy::too_many_arguments)]
pub async fn record_token_usage(
    pool: &PgPool,
    user_id: UserId,
    save_id: Option<i64>,
    context_run_id: Option<i64>,
    api_id: &str,
    model_real_name: &str,
    usage: &UsageBreakdown,
    context_used: i32,
    context_max: i32,
    metadata: serde_json::Value,
) -> PlatformResult<rpg_db::repos::token_usage::TokenUsageRow> {
    let cost = compute_cost(api_id, model_real_name, usage);
    let row = rpg_db::repos::token_usage::TokenUsageRow {
        id: 0,
        user_id,
        save_id,
        context_run_id,
        api_id: api_id.to_string(),
        model_real_name: model_real_name.to_string(),
        input_tokens: usage.input_tokens,
        output_tokens: usage.output_tokens,
        cached_input_tokens: usage.cached_input_tokens,
        reasoning_tokens: usage.reasoning_tokens,
        total_tokens: usage.total_tokens,
        cost_usd: cost,
        context_used,
        context_max,
        metadata,
        created_at: chrono::Utc::now(),
    };
    rpg_db::repos::token_usage::insert(pool, &row)
        .await
        .map_err(PlatformError::from)
}

// ─── 聚合 ──────────────────────────────────────────────────────────────

/// `aggregate_usage` 总览。
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UsageTotals {
    pub input_tokens: i64,
    pub output_tokens: i64,
    pub cached_input_tokens: i64,
    pub total_tokens: i64,
    pub cost_usd: f64,
    pub turns: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UsageByModel {
    pub api_id: String,
    pub model: String,
    pub input_tokens: i64,
    pub output_tokens: i64,
    pub cost_usd: f64,
    pub turns: i64,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UsageRecent {
    pub at: String,
    pub api_id: String,
    pub model: String,
    pub input_tokens: i32,
    pub output_tokens: i32,
    pub cost_usd: f64,
    pub context_used: i32,
    pub context_max: i32,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UsageAggregate {
    pub window_days: i32,
    pub totals: UsageTotals,
    pub by_model: Vec<UsageByModel>,
    pub recent_turns: Vec<UsageRecent>,
}

/// Python `aggregate_usage(user_id, days=30)`。
pub async fn aggregate_usage(
    pool: &PgPool,
    user_id: UserId,
    days: i32,
) -> PlatformResult<UsageAggregate> {
    let days = days.clamp(1, 365);
    let total_row = sqlx::query(
        "select \
           coalesce(sum(input_tokens), 0)::bigint as input_tokens, \
           coalesce(sum(output_tokens), 0)::bigint as output_tokens, \
           coalesce(sum(cached_input_tokens), 0)::bigint as cached_input_tokens, \
           coalesce(sum(total_tokens), 0)::bigint as total_tokens, \
           coalesce(sum(cost_usd), 0)::float8 as cost_usd, \
           count(*)::bigint as turns \
         from token_usage \
         where user_id = $1 and created_at >= now() - (interval '1 day' * $2)",
    )
    .bind(user_id)
    .bind(days)
    .fetch_one(pool)
    .await?;
    let totals = UsageTotals {
        input_tokens: total_row.try_get("input_tokens")?,
        output_tokens: total_row.try_get("output_tokens")?,
        cached_input_tokens: total_row.try_get("cached_input_tokens")?,
        total_tokens: total_row.try_get("total_tokens")?,
        cost_usd: total_row.try_get("cost_usd")?,
        turns: total_row.try_get("turns")?,
    };

    let by_rows = sqlx::query(
        "select api_id, model_real_name as model, \
                coalesce(sum(input_tokens), 0)::bigint as input_tokens, \
                coalesce(sum(output_tokens), 0)::bigint as output_tokens, \
                coalesce(sum(cost_usd), 0)::float8 as cost_usd, \
                count(*)::bigint as turns \
         from token_usage \
         where user_id = $1 and created_at >= now() - (interval '1 day' * $2) \
         group by api_id, model_real_name \
         order by cost_usd desc",
    )
    .bind(user_id)
    .bind(days)
    .fetch_all(pool)
    .await?;
    let by_model = by_rows
        .iter()
        .map(|r| {
            Ok::<_, sqlx::Error>(UsageByModel {
                api_id: r.try_get("api_id")?,
                model: r.try_get("model")?,
                input_tokens: r.try_get("input_tokens")?,
                output_tokens: r.try_get("output_tokens")?,
                cost_usd: r.try_get("cost_usd")?,
                turns: r.try_get("turns")?,
            })
        })
        .collect::<Result<Vec<_>, _>>()?;

    let recent_rows = sqlx::query(
        "select created_at, api_id, model_real_name as model, input_tokens, output_tokens, \
                cost_usd::float8 as cost_usd, context_used, context_max \
         from token_usage where user_id = $1 order by id desc limit 20",
    )
    .bind(user_id)
    .fetch_all(pool)
    .await?;
    let recent_turns = recent_rows
        .iter()
        .map(|r| {
            Ok::<_, sqlx::Error>(UsageRecent {
                at: r
                    .try_get::<chrono::DateTime<chrono::Utc>, _>("created_at")
                    .map(|t| t.to_rfc3339())
                    .unwrap_or_default(),
                api_id: r.try_get("api_id")?,
                model: r.try_get("model")?,
                input_tokens: r.try_get("input_tokens")?,
                output_tokens: r.try_get("output_tokens")?,
                cost_usd: r.try_get("cost_usd")?,
                context_used: r.try_get("context_used")?,
                context_max: r.try_get("context_max")?,
            })
        })
        .collect::<Result<Vec<_>, _>>()?;

    Ok(UsageAggregate {
        window_days: days,
        totals,
        by_model,
        recent_turns,
    })
}

/// Python `list_usage_for_user`(简化版,paginate by id desc)。
pub async fn list_usage_for_user(
    pool: &PgPool,
    user_id: UserId,
    limit: i64,
) -> PlatformResult<Vec<UsageRecent>> {
    let limit = limit.clamp(1, 500);
    let rows = sqlx::query(
        "select created_at, api_id, model_real_name as model, input_tokens, output_tokens, \
                cost_usd::float8 as cost_usd, context_used, context_max \
         from token_usage where user_id = $1 order by id desc limit $2",
    )
    .bind(user_id)
    .bind(limit)
    .fetch_all(pool)
    .await?;
    rows.iter()
        .map(|r| {
            Ok(UsageRecent {
                at: r
                    .try_get::<chrono::DateTime<chrono::Utc>, _>("created_at")
                    .map(|t| t.to_rfc3339())
                    .unwrap_or_default(),
                api_id: r.try_get("api_id")?,
                model: r.try_get("model")?,
                input_tokens: r.try_get("input_tokens")?,
                output_tokens: r.try_get("output_tokens")?,
                cost_usd: r.try_get("cost_usd")?,
                context_used: r.try_get("context_used")?,
                context_max: r.try_get("context_max")?,
            })
        })
        .collect::<Result<Vec<_>, sqlx::Error>>()
        .map_err(Into::into)
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct UsageTimelineRow {
    pub bucket: String,
    pub input_tokens: i64,
    pub output_tokens: i64,
    pub cost_usd: f64,
    pub turns: i64,
}

/// Python `timeline_usage`。
pub async fn timeline_usage(
    pool: &PgPool,
    user_id: UserId,
    days: i32,
    group_by: &str,
) -> PlatformResult<Vec<UsageTimelineRow>> {
    if group_by != "day" && group_by != "model" {
        return Err(PlatformError::validation("group_by 只支持 day / model"));
    }
    let days = days.clamp(1, 365);
    let sql = if group_by == "day" {
        "select to_char(date_trunc('day', created_at), 'YYYY-MM-DD') as bucket, \
                coalesce(sum(input_tokens), 0)::bigint as input_tokens, \
                coalesce(sum(output_tokens), 0)::bigint as output_tokens, \
                coalesce(sum(cost_usd), 0)::float8 as cost_usd, \
                count(*)::bigint as turns \
         from token_usage \
         where user_id = $1 and created_at >= now() - (interval '1 day' * $2) \
         group by bucket order by bucket"
    } else {
        "select (api_id || '/' || model_real_name) as bucket, \
                coalesce(sum(input_tokens), 0)::bigint as input_tokens, \
                coalesce(sum(output_tokens), 0)::bigint as output_tokens, \
                coalesce(sum(cost_usd), 0)::float8 as cost_usd, \
                count(*)::bigint as turns \
         from token_usage \
         where user_id = $1 and created_at >= now() - (interval '1 day' * $2) \
         group by bucket order by cost_usd desc"
    };
    let rows = sqlx::query(sql)
        .bind(user_id)
        .bind(days)
        .fetch_all(pool)
        .await?;
    rows.iter()
        .map(|r| {
            Ok(UsageTimelineRow {
                bucket: r.try_get("bucket")?,
                input_tokens: r.try_get("input_tokens")?,
                output_tokens: r.try_get("output_tokens")?,
                cost_usd: r.try_get("cost_usd")?,
                turns: r.try_get("turns")?,
            })
        })
        .collect::<Result<Vec<_>, sqlx::Error>>()
        .map_err(Into::into)
}

/// 粗略估算 token 数。中文按 0.6,其他按 4 字符/token。
pub fn estimate_input_tokens(text: &str) -> i64 {
    let mut cn = 0i64;
    for ch in text.chars() {
        if ('\u{4e00}'..='\u{9fff}').contains(&ch) {
            cn += 1;
        }
    }
    let other = text.chars().count() as i64 - cn;
    ((cn as f64) * 0.6 + (other as f64) / 4.0) as i64
}

/// 最近 N 轮该模型的平均 output tokens。
pub async fn average_output_tokens(
    pool: &PgPool,
    user_id: UserId,
    model_real_name: &str,
    last_n: i64,
) -> PlatformResult<i32> {
    let last_n = last_n.clamp(1, 200);
    let row = if !model_real_name.is_empty() {
        sqlx::query(
            "select coalesce(avg(output_tokens), 0)::int as avg from ( \
               select output_tokens from token_usage \
               where user_id = $1 and model_real_name = $2 order by id desc limit $3 \
             ) t",
        )
        .bind(user_id)
        .bind(model_real_name)
        .bind(last_n)
        .fetch_one(pool)
        .await?
    } else {
        sqlx::query(
            "select coalesce(avg(output_tokens), 0)::int as avg from ( \
               select output_tokens from token_usage \
               where user_id = $1 order by id desc limit $2 \
             ) t",
        )
        .bind(user_id)
        .bind(last_n)
        .fetch_one(pool)
        .await?
    };
    Ok(row.try_get::<i32, _>("avg").unwrap_or(0))
}

/// Python `context_window_for(api_id, model_real_name)` —— 从 rpg-llm 内置表查模型 context。
///
/// `ModelPricing` 目前不含 context_window 字段(仅有计价信息)。
/// 这里通过已知的 builtin 映射表提供常用模型的 context window 大小(tokens)。
/// 找不到定价条目或不在映射中时返回 0(对应 Python `get_pricing` 失败时的 0)。
///
/// TODO[P2-LLM]: 当 rpg-llm ModelEntry 补充 context_tokens 字段后,改从 pricing_for 路由拿。
pub fn context_window_for(api_id: &str, model_real_name: &str) -> i64 {
    // 先确认模型在定价表里(复用 PRICING_ROUTER 已有的 pricing_for 逻辑作有效性校验)。
    if PRICING_ROUTER.pricing_for(api_id, model_real_name).is_none() {
        return 0;
    }
    // 按 api_id/model_real_name 的已知 context window(tokens)。
    // 来源:各 provider 2025-Q2 文档,与 Python model_probe.get_pricing("context") 对齐。
    let key = format!("{api_id}/{model_real_name}");
    match key.as_str() {
        // Anthropic
        "anthropic/claude-opus-4-7"    => 200_000,
        "anthropic/claude-sonnet-4-6"  => 200_000,
        "anthropic/claude-haiku-4-5"   => 200_000,
        // Vertex AI / Google
        "vertex_ai/gemini-2.5-flash"   => 1_000_000,
        "vertex_ai/gemini-2.5-pro"     => 1_000_000,
        // DeepSeek
        "openai_compat/deepseek-v3"    => 64_000,
        // OpenAI
        "openai/gpt-4o"                => 128_000,
        "openai/gpt-5"                 => 128_000,
        _ => 0,
    }
}

// ─── tests ─────────────────────────────────────────────────────────────────
#[cfg(test)]
mod usage_tests {
    use super::*;

    #[test]
    fn compute_cost_known_model_nonzero() {
        let u = UsageBreakdown {
            input_tokens: 1000,
            output_tokens: 500,
            cached_input_tokens: 0,
            reasoning_tokens: 0,
            total_tokens: 1500,
        };
        let cost = compute_cost("anthropic", "claude-sonnet-4-6", &u);
        // 1000 input @ 0.003/1k + 500 output @ 0.015/1k = 0.003 + 0.0075 = 0.0105
        assert_ne!(cost, "0.000000", "已知模型应有非零费用");
    }

    #[test]
    fn compute_cost_unknown_model_zero() {
        let u = UsageBreakdown::default();
        let cost = compute_cost("unknown_api", "unknown_model", &u);
        assert_eq!(cost, "0.000000");
    }

    #[test]
    fn context_window_for_known_models() {
        assert_eq!(context_window_for("anthropic", "claude-sonnet-4-6"), 200_000);
        assert_eq!(context_window_for("vertex_ai", "gemini-2.5-flash"), 1_000_000);
        assert_eq!(context_window_for("openai", "gpt-4o"), 128_000);
    }

    #[test]
    fn context_window_for_unknown_returns_zero() {
        assert_eq!(context_window_for("no_api", "no_model"), 0);
    }
}
