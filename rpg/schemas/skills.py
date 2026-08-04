"""schemas.skills — Skill 导入与运行路由请求模型。"""
from __future__ import annotations

from typing import Any

from pydantic import Field

from schemas._common import _BaseRequest


class SkillsImportRequest(_BaseRequest):
    file: Any | None = None


class SkillRunRequest(_BaseRequest):
    cmd: list[str] = Field(default_factory=list, max_length=64)
    command: list[str] = Field(default_factory=list, max_length=64)
    stdin: str | None = None
    timeout_sec: int | None = None
