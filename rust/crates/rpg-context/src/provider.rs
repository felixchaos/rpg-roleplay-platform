//! ContextProvider trait + ProviderServices 依赖注入容器。
//! 对应 Python: rpg/context_providers/base.py (ContextProvider 抽象 + ProviderServices)。

use crate::error::ContextResult;
use crate::types::{ContextContribution, Demand, Manifest};
use async_trait::async_trait;
use serde_json::Value;
use std::sync::Arc;

/// 所有外部服务的统一入口,方便测试 mock 全套依赖。
///
/// 对应 Python `ProviderServices` dataclass。
#[derive(Clone, Default)]
pub struct ProviderServices {
    pub user_id: Option<i64>,
    pub script_id: Option<i64>,
    pub book_id: Option<i64>,
    /// task 107E:给 RuntimePhaseDigestProvider 用
    pub save_id: Option<i64>,
    /// 检索引擎(可选)。给 NovelRetrievalProvider 用。
    pub retrieve_fn: Option<RetrieveFn>,
    /// 时间线锚点查询(可选)。给 NovelTimelineProvider 用。
    pub timeline_filter_fn: Option<TimelineFilterFn>,
    /// 模组加载器(可选)。给 ModuleSceneProvider 用。
    pub module_loader: Option<ModuleLoaderFn>,
    /// 数据库连接池(可选)。runtime_phase_digests / script_phase_anticipation /
    /// 角色卡 / 世界书 都需要。
    pub db_pool: Option<sqlx::PgPool>,
}

/// 检索回调签名:`fn(query, state_data) -> retrieval text`。
///
/// 用 async trait 也行;现阶段同步回调够用。Box::new(|q,s| ...).
pub type RetrieveFn = Arc<
    dyn Fn(&str, &Value) -> futures::future::BoxFuture<'static, anyhow::Result<String>>
        + Send
        + Sync,
>;

/// 时间线锚点查询回调。返回 anchor dict(chapter_min/chapter_max/anchor_chapter/...).
pub type TimelineFilterFn = Arc<dyn Fn(&str) -> anyhow::Result<Value> + Send + Sync>;

/// 模组加载器。返回 bundle dict(manifest/rooms/encounters/worldbook).
pub type ModuleLoaderFn = Arc<dyn Fn(&str) -> anyhow::Result<Value> + Send + Sync>;

/// ContextProvider 抽象。子类实现 `applies` + `collect`。
///
/// 对应 Python `ContextProvider` 基类。
#[async_trait]
pub trait ContextProvider: Send + Sync {
    /// provider 全局唯一 id。
    fn id(&self) -> &'static str;

    /// 是否在本轮启用。可基于 manifest.context_providers / state / demand 判断。
    /// 默认实现:manifest.context_providers 里出现就启用。
    fn applies(&self, _state_data: &Value, manifest: &Manifest, _demand: &Demand) -> bool {
        manifest.context_providers.iter().any(|p| p == self.id())
    }

    /// 收集 provider 的贡献。失败应返回 warnings,而非抛错。
    async fn collect(
        &self,
        state_data: &Value,
        manifest: &Manifest,
        demand: &Demand,
        services: &ProviderServices,
    ) -> ContextResult<ContextContribution>;
}
