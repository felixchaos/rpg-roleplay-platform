"""
rpg.context_providers.base — ContextProvider 抽象 + ContextContribution / Demand 数据结构。

设计要点：
- ContextProvider 是无状态的服务（每个 provider 一个全局单例）。
- 所有外部依赖通过 ProviderServices 注入（user_id, script_id, retrieval 引擎引用等），
  方便测试 mock。
- ContextContribution 是 provider 的统一输出契约。context_agent / build_context_bundle
  只合并 contribution.layers / facts / retrieval_items，不去理解每个 provider 的内部。
- Demand 是 Demand Resolver 的输出，由 context_agent 跑（LLM 或本地规则），
  传给所有 providers。providers 据此调整自己贡献的内容（如 Novel retrieval 用
  demand.retrieval_query）。
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass, field

# ── Demand：Demand Resolver 输出 ───────────────────────────────────

@dataclass
class Demand:
    """玩家本轮需求账本（Demand Ledger）。LLM 子代理或本地规则产出。

    Demand Resolver 不应该硬编码"小说时间线"等概念；它只描述玩家想做什么 +
    建议的检索方向。具体数据源由 ContextProvider 决定。
    """
    player_intent: str = ""
    active_goal: str = ""
    hard_constraints: list[str] = field(default_factory=list)
    soft_preferences: list[str] = field(default_factory=list)
    target_entities: list[str] = field(default_factory=list)
    target_location: str = ""
    target_time: str = ""
    timeline_target: str = ""           # 玩家显式时间跳跃请求；novel provider 才理解
    retrieval_query: str = ""           # 一个开放查询；具体怎么用由 provider 决定
    retrieval_needs: dict = field(default_factory=dict)  # provider 可选的细化需求
    # Q Phase 3 司命 RAG 闸：本轮是否需要检索原著正文。default True = 现状(总检索)。
    # 司命(curator)判定纯对话/情绪/简单互动 → False,NovelRetrievalProvider 在 RPG_RAG_GATE
    # 开启时据此跳过检索,省下 RAG 大头 token。见 docs/design/Q_three_sage_pipeline.md §6/§7。
    need_retrieval: bool = True
    rule_candidate_actions: list[dict] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    confidence: float = 1.0
    clarifying_question: str = ""
    reason: str = ""
    raw_curator_plan: dict | None = None  # 保留 LLM 原始输出便于审计

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def empty(cls) -> Demand:
        return cls()


# ── ContextContribution：单 provider 的产出 ──────────────────────

@dataclass
class ContextContribution:
    """一个 provider 在一轮里贡献的上下文。

    facts        - 短句事实清单。GM 必读，进入 state/memory 摘要层。
    layers       - 结构化文本层，build_context_bundle 直接拼到 prompt。
                   每个 layer = {id, title, content, sticky?, priority?, items?, source?}
    retrieval_items - 检索片段（小说才用；模组通常为空）。
    warnings     - 需要传递给 GM 或 UI 的告警（例如"无可用检索"）。
    debug        - 调试信息，前端 Run Feed 显示。
    tokens_estimate - 本 contribution 估算 token 数（用于 budget）。
    """
    provider_id: str
    kind: str = "generic"
    priority: int = 50           # 0-100，决定 prompt 层顺序
    # Q 三贤者分层缓存:本 contribution 的层默认缓存层(A 会话级稳定 / B 场景级稳定 /
    # C 回合动态)。单层可在 make_layer 里覆盖;空则 build_context_bundle 用 LAYER_CACHE_TIER
    # 兜底,再兜底 "C"。详见 docs/design/Q_three_sage_pipeline.md §5。
    cache_tier: str = ""
    facts: list[str] = field(default_factory=list)
    layers: list[dict] = field(default_factory=list)
    retrieval_items: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    debug: dict = field(default_factory=dict)
    tokens_estimate: int = 0
    applied: bool = True        # provider 显式跳过时置 False（避免 caller 误用）

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def skipped(cls, provider_id: str, reason: str = "") -> ContextContribution:
        return cls(provider_id=provider_id, applied=False, debug={"skipped": reason})


# ── ProviderServices：依赖注入容器 ───────────────────────────────

@dataclass
class ProviderServices:
    """所有外部服务的统一入口，方便测试 mock 全套依赖。"""
    user_id: int | None = None
    script_id: int | None = None
    book_id: int | None = None
    save_id: int | None = None  # task 107E: 给 RuntimePhaseDigestProvider 用
    # 检索引擎（可选）。给 NovelRetrievalProvider 用。
    retrieve_fn: Callable[..., str] | None = None
    # 时间线锚点查询（可选）。给 NovelTimelineProvider 用。
    timeline_filter_fn: Callable[[str], dict] | None = None
    # 模组加载器（可选）。给 ModuleSceneProvider 用。
    module_loader: Callable[[str], dict] | None = None


# ── ContextProvider 抽象 ─────────────────────────────────────────

class ContextProvider:
    """所有 provider 的基类。子类实现 applies + collect。

    设计原则：
    - provider 自己判断启用条件（applies），不依赖外部调度逻辑做 if 分支。
    - 失败必须吞掉异常并返回 skipped contribution，绝不影响其他 provider。
    - collect 返回的 contribution 是 provider 的唯一输出契约。
    """
    id: str = ""

    def __init__(self) -> None:
        if not self.id:
            raise ValueError(f"{type(self).__name__} 必须定义 id")

    def applies(self, state, manifest: dict, demand: Demand) -> bool:
        """是否在本轮启用。可基于 manifest.context_providers / state / demand 判断。"""
        # 默认：manifest 显式列出了 id 就启用
        listed = manifest.get("context_providers") or []
        return self.id in listed

    def collect(
        self,
        state,
        manifest: dict,
        demand: Demand,
        services: ProviderServices,
    ) -> ContextContribution:
        """收集 provider 的贡献。失败不应抛异常，应返回 warnings。"""
        raise NotImplementedError

    # 工具方法：构造 layer dict
    @staticmethod
    def make_layer(layer_id: str, title: str, content: str, *,
                   sticky: bool = False, priority: int = 50,
                   items: list[dict] | None = None,
                   source: str = "",
                   cache_tier: str = "") -> dict:
        layer = {
            "id": layer_id,
            "title": title,
            "content": content,
            "sticky": sticky,
            "priority": priority,
            "items": items or [],
            "source": source,
        }
        # Q 分层缓存:仅在 provider 显式指定时写入,否则留给 build_context_bundle 用
        # LAYER_CACHE_TIER 兜底(避免在这里硬塞 "C" 覆盖中央映射)。
        if cache_tier:
            layer["cache_tier"] = cache_tier
        return layer
