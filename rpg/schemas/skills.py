"""schemas.skills — Skill 导入与运行路由请求模型。"""
from __future__ import annotations

from typing import Any

from schemas._common import _BaseRequest


class SkillsImportRequest(_BaseRequest):
    file: Any | None = None


class SkillRunRequest(_BaseRequest):
    cmd: list[Any] | None = None
    command: list[Any] | None = None
    stdin: str | None = None
    timeout_sec: int | None = None
