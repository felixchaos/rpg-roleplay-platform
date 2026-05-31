"""schemas.memory — 记忆管理路由请求模型 + MemorySettings 配置 schema。"""
from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field

from schemas._common import _BaseRequest


class MemoryModeRequest(_BaseRequest):
    mode: str | None = "normal"


class MemoryAddRequest(_BaseRequest):
    bucket: Annotated[str, Field(max_length=128)] | None = "notes"
    text: Annotated[str, Field(max_length=2000)] | None = ""


class MemoryRemoveRequest(_BaseRequest):
    bucket: str | None = "notes"
    index: int | None = None


class MemorySettings(BaseModel):
    """用户级记忆系统配置（存于 settings 表，key 前缀 memory.*）。

    前端 MemorySection 通过 /api/settings POST 写入各 key，
    后端消费方通过 get_memory_settings(user_id) 读取。
    """

    token_budget: int = Field(
        default=800,
        ge=200,
        le=2000,
        description="每轮注入记忆 token 上限（字符数 // 2 估算）",
    )
    auto_archive_after_turns: int = Field(
        default=50,
        ge=10,
        le=200,
        description="N 轮后自动归档旧记忆（不删除，仅不注入上下文）",
    )
    pinned_max: int = Field(
        default=20,
        ge=5,
        le=100,
        description="固定记忆桶（pinned）条目上限",
    )
    bucket_pinned_enabled: bool = Field(
        default=True,
        description="是否在上下文中注入 pinned 桶",
    )
    bucket_world_enabled: bool = Field(
        default=True,
        description="是否在上下文中注入 world memory（main_quest / objective / facts）",
    )
    bucket_character_enabled: bool = Field(
        default=True,
        description="是否在上下文中注入 character memory（abilities / resources）",
    )
    recall_depth: int = Field(
        default=5,
        ge=1,
        le=20,
        description="每个桶最多召回条目数（影响 MemoryProvider 的每桶 slice 上限）",
    )
    summary_window: int = Field(
        default=10,
        ge=1,
        le=50,
        description="归档检查窗口大小（每隔 summary_window 轮触发一次归档扫描）",
    )
