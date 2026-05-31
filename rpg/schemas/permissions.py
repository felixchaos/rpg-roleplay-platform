"""schemas.permissions — 权限/确认管理路由请求模型。"""
from __future__ import annotations

from typing import Any, Literal

from schemas._common import _BaseRequest


class PermissionsRequest(_BaseRequest):
    mode: str | None = "full_access"


class PendingWriteRequest(_BaseRequest):
    id: Any | None = None
    index: Any | None = None
    action: str | None = None
    decision: Literal["approve", "reject"] | None = None


class QuestionClearRequest(_BaseRequest):
    id: Any | None = None
    index: Any | None = None
    choice: Any | None = None


class DebugPendingQuestionRequest(_BaseRequest):
    text: str | None = None
