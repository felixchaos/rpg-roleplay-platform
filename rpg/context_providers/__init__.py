"""
rpg.context_providers — 数据驱动的上下文管线。

context_agent 不再硬编码"小说"或"模组"是什么。它只做两件事：
  1. Demand Resolver（理解玩家本轮意图）
  2. 按 ContentPack manifest 的 context_providers 列表调度 ContextProvider，
     收集每个 provider 的 ContextContribution。

每个 ContentPack（小说 adaptation / 模组 / freeform / 未来其他形态）在自己的
manifest 里声明 context_providers，互不串。
"""
# 触发各 provider 子模块加载与注册
from . import memory as _memory  # noqa: F401
from . import module as _module  # noqa: F401
from . import novel as _novel  # noqa: F401
from . import recent_chat as _recent_chat  # noqa: F401
from . import rules as _rules  # noqa: F401

# task 107E: 双时间线 provider
from . import runtime_phase_digests as _rpd  # noqa: F401
from . import script_phase_anticipation as _spa  # noqa: F401
from . import worldline as _worldline  # noqa: F401
from .base import (
    ContextContribution,
    ContextProvider,
    Demand,
    ProviderServices,
)
from .registry import (
    DEFAULT_FREEFORM_MANIFEST,
    DEFAULT_MODULE_MANIFEST,
    DEFAULT_NOVEL_MANIFEST,
    available_providers,
    get_provider,
    register_provider,
    resolve_content_pack,
    run_providers,
)

__all__ = [
    "ContextProvider", "ContextContribution", "Demand", "ProviderServices",
    "register_provider", "get_provider", "available_providers",
    "resolve_content_pack",
    "DEFAULT_NOVEL_MANIFEST", "DEFAULT_MODULE_MANIFEST", "DEFAULT_FREEFORM_MANIFEST",
    "run_providers",
]
