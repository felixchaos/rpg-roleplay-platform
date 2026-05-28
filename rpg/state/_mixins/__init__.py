"""state._mixins — GameState 的职责分组 mixin 类。

GameState 通过多继承组合所有 mixin 拿到全部方法:
    class GameState(ApplyOpsMixin, RulesGameplayMixin, PendingMixin):
        ...

mixin 间通过 self.xxx 互相调用,运行时由 Python MRO 解析。
"""
from .apply_ops import ApplyOpsMixin
from .pending import PendingMixin
from .rules_gameplay import RulesGameplayMixin

__all__ = ["ApplyOpsMixin", "RulesGameplayMixin", "PendingMixin"]
