"""schemas.skills — Skill 导入与运行路由请求模型。"""
from __future__ import annotations
from typing import Optional, Any
from schemas._common import _BaseRequest


class SkillsImportRequest(_BaseRequest):
    file: Optional[Any] = None


class SkillRunRequest(_BaseRequest):
    cmd: Optional[list[Any]] = None
    command: Optional[list[Any]] = None
    stdin: Optional[str] = None
    timeout_sec: Optional[int] = None
