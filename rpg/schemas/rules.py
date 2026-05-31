"""schemas.rules — 5E 规则模组与战斗路由请求模型。"""
from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field

from schemas._common import _BaseRequest


class RulesModuleStartRequest(_BaseRequest):
    module_id: str | None = "ash_mine"
    character: Any | None = None


class RulesModuleLaunchRequest(_BaseRequest):
    module_id: str | None = "ash_mine"
    character: Any | None = None
    title: str | None = ""


class RulesMoveRequest(_BaseRequest):
    to: str | None = ""


class RulesActionRequest(_BaseRequest):
    """通用动作,字段由 body.kind 决定,允许任意额外字段（skill/ability/target 等动作参数）。"""
    model_config = __import__('pydantic').ConfigDict(extra="allow")
    kind: str | None = None


class RulesEncounterStartRequest(_BaseRequest):
    encounter_id: str | None = ""
    seed: Any | None = None


class RulesEncounterNextRequest(_BaseRequest):
    pass


class RulesEncounterEnemyRequest(_BaseRequest):
    attacker_id: str | None = ""
    target_id: str | None = "player"
    seed: Any | None = None


class RulesSuggestRequest(_BaseRequest):
    text: Annotated[str, Field(max_length=2000)] | None = ""
