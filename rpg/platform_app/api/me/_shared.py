"""platform_app.api.me._shared —— 拆包共享的单一 router 实例 + 跨资源族图片辅助。

各资源族子模块 `from ._shared import router[, _detect_image_mime, _store_imported_card_image]`
后用 `@router.<verb>(...)` 注册端点;`__init__.py` import 全部子模块触发装配,再把这同一个
router 暴露给 `platform_app.api`(`from .me import router`)。这样装配结果与拆分前的单文件
逐端点一致(共享同一 APIRouter 实例)。

_detect_image_mime / _store_imported_card_image 被角色卡头像上传(card_images)与酒馆卡导入
(tavern)多个子模块共用,故与 router 同居本模块(单一真相源,避免跨子模块循环 import);
生产侧 `from platform_app.api.me import _detect_image_mime`(frontend_routes)经 __init__ 门面
re-export 不变。
"""
from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()


def _detect_image_mime(data: bytes) -> tuple[str, str]:
    """读 data[:12] 魔数，返回 (mime, ext)。不合法抛 ValueError。"""
    head = data[:12]
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png", "png"
    if head[:2] == b"\xff\xd8":
        return "image/jpeg", "jpg"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp", "webp"
    raise ValueError("仅支持 PNG / JPEG / WebP 图片（魔数校验失败）")


def _store_imported_card_image(user_id: int, card_id: int, blob: bytes) -> None:
    """导入角色卡时把卡自带的原图(PNG/WEBP 卡本身即头像)存进 storage +
    设 character_cards.avatar_path + 登记 user_assets(功能组件→文件库)。
    非图片(魔数失败)会 raise，调用方 try/except 兜底。"""
    import secrets as _secrets

    from ... import storage as _storage
    from ...assets_registry import register_asset as _register_asset
    from ...db import connect as _connect

    mime, ext = _detect_image_mime(blob)  # 非图 → ValueError
    key, url = _storage.store_bytes(
        blob, kind="ai_images", filename=f"card_{user_id}_{_secrets.token_hex(12)}.{ext}"
    )
    with _connect() as db:
        db.execute(
            "update character_cards set avatar_path = %s where id = %s and user_id = %s",
            (url, card_id, user_id),
        )
    _register_asset(
        user_id=user_id, kind="card_image", storage_key=key, url=url,
        source="card_import", ref_kind="card", ref_id=card_id,
        mime=mime, size=len(blob),
    )
