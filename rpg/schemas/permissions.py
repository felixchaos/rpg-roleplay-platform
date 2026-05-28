"""schemas.permissions — 权限/确认管理路由请求模型。"""
from __future__ import annotations
from typing import Optional, Any
from schemas._common import _BaseRequest


class PermissionsRequest(_BaseRequest):
    mode: Optional[str] = "full_access"


class PendingWriteRequest(_BaseRequest):
    id: Optional[Any] = None
    index: Optional[Any] = None
    action: Optional[str] = None
    decision: Optional[str] = None


class QuestionClearRequest(_BaseRequest):
    id: Optional[Any] = None
    index: Optional[Any] = None
    choice: Optional[Any] = None


class DebugPendingQuestionRequest(_BaseRequest):
    text: Optional[str] = None
