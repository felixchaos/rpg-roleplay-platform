"""schemas.sidebar — 侧栏 inline-edit 请求模型。

3 组端点的请求体:
  · POST /api/relationships/set    {character, status}
  · POST /api/relationships/delete {character}
  · POST /api/world/set            {key, value}  key ∈ {time, weather, phase, location, <任意 world.scalar>}
"""
from __future__ import annotations

from schemas._common import _BaseRequest


class RelationshipSetRequest(_BaseRequest):
    character: str | None = ""
    status: str | None = ""


class RelationshipDeleteRequest(_BaseRequest):
    character: str | None = ""


class WorldSetRequest(_BaseRequest):
    # key 受路由侧白名单收敛,这里不限制以便扩展(scenario/atmosphere 等)
    key: str | None = ""
    value: str | None = ""
