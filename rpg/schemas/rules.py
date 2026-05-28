"""schemas.rules — 5E 规则模组与战斗路由请求模型。"""
from __future__ import annotations
from typing import Optional, Any
from schemas._common import _BaseRequest


class RulesModuleStartRequest(_BaseRequest):
    module_id: Optional[str] = "ash_mine"
    character: Optional[Any] = None


class RulesModuleLaunchRequest(_BaseRequest):
    module_id: Optional[str] = "ash_mine"
    character: Optional[Any] = None
    title: Optional[str] = ""


class RulesMoveRequest(_BaseRequest):
    to: Optional[str] = ""


class RulesActionRequest(_BaseRequest):
    """通用动作,字段由 body.kind 决定,允许任意额外字段。"""
    model_config = __import__('pydantic').ConfigDict(extra="allow")
    kind: Optional[str] = None


class RulesEncounterStartRequest(_BaseRequest):
    encounter_id: Optional[str] = ""
    seed: Optional[Any] = None


class RulesEncounterNextRequest(_BaseRequest):
    pass


class RulesEncounterEnemyRequest(_BaseRequest):
    attacker_id: Optional[str] = ""
    target_id: Optional[str] = "player"
    seed: Optional[Any] = None


class RulesSuggestRequest(_BaseRequest):
    text: Optional[str] = ""
