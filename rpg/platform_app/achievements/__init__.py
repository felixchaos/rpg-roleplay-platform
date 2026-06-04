"""platform_app.achievements — 成就系统(见 docs/design/I_achievements.md)。

- 目录(achievement_defs)存 DB、admin 可编辑;规则是声明式白名单 jsonb。
- 判定永远是纯函数 eval_rule(rule, snapshot),跑在确定性事件缝,不经 LLM。
- 解锁状态(user_achievements)只增不减,只记已解锁。
"""
from .engine import (
    ALLOWED_METRICS,
    ALLOWED_OPS,
    build_stats_snapshot,
    compute_rarity,
    evaluate,
    eval_rule,
    public_catalog,
    public_wall,
    validate_rule,
)

__all__ = [
    "ALLOWED_METRICS",
    "ALLOWED_OPS",
    "build_stats_snapshot",
    "compute_rarity",
    "evaluate",
    "eval_rule",
    "public_catalog",
    "public_wall",
    "validate_rule",
]
