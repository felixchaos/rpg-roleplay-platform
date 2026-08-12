"""platform_app/library.py — 用户资产只读管理（S5 重构）。

原"手动上传文件管理器"（list_dir/mkdir/upload/download_path）已完整删除。
本模块现在只提供：
  - list_assets        — 列出用户资产（调 assets_registry.list_user_assets）
  - get_asset          — 查单个资产（owner 校验）
  - asset_download_path — 解析物理路径供 FileResponse
  - nullify_references  — 置空引用字段（删除前后处理）
  - delete_asset_with_refs — 完整删除流程（引用检查 → 置空 → force 删）
  - list_dir           — 兼容 shim（_deps.platform_for 调用，返回 list_assets 结构）
  - decode_upload / safe_filename / unique_path — script_import 仍在引用的工具函数 shim
    （S5 重构保留，因 script_import.py 的 txt 上传流程仍需它们）

手动上传/mkdir 已彻底移除，不再暴露任意文件上传入口。
"""
from __future__ import annotations

import base64
import binascii
import re as _re
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# 兼容 shim — script_import.py / api/scripts.py 仍在引用这些工具函数
# （剧本 txt 上传管线用，非文件库手动上传）
# ---------------------------------------------------------------------------

def decode_upload(item: dict) -> bytes:
    """从前端上传的 item dict 解出原始 bytes（base64 / data_url 两路）。"""
    encoded = str(
        item.get("base64") or item.get("content_base64") or item.get("contentBase64") or ""
    )
    data_url = str(item.get("data_url") or item.get("dataUrl") or "")
    if "," in data_url:
        encoded = data_url.split(",", 1)[1]
    if not encoded:
        raise ValueError("上传内容为空")
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("上传内容不是有效 base64") from exc


def safe_filename(name: str) -> str:
    """把原始文件名清洗为安全文件名（保留 ASCII / CJK，其他替换为 _）。"""
    stem = Path(name).name
    cleaned = _re.sub(r"[^A-Za-z0-9._\- 一-鿿]", "_", stem)
    if not _re.search(r"[A-Za-z0-9一-鿿]", cleaned):
        cleaned = "untitled"
    return cleaned or "file.bin"


def unique_path(path: Path) -> Path:
    """若 path 已存在则加数字后缀找可用路径。"""
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise ValueError("无法分配文件名")


# ---------------------------------------------------------------------------
# 读：列表
# ---------------------------------------------------------------------------

def list_assets(
    user_id: int,
    kind: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    """列出 user_id 的资产，返回 {ok, items, total_count_hint}。"""
    from . import assets_registry as _reg  # lazy import

    items = _reg.list_user_assets(user_id, kind=kind, limit=limit, offset=offset)
    return {
        "ok": True,
        "items": items,
        "kind_filter": kind,
        "limit": limit,
        "offset": offset,
    }


def list_dir(user_id: int, path: str = "", limit: int | None = None, cursor: str | None = None) -> dict:
    """兼容 shim：_deps.platform_for 调用此函数；重定向到 list_assets。

    path/cursor 参数已无实际含义（无目录树），直接忽略。
    """
    return list_assets(user_id, kind=None, limit=int(limit) if limit else 50, offset=0)


# ---------------------------------------------------------------------------
# 读：单个
# ---------------------------------------------------------------------------

def get_asset(user_id: int, asset_id: int) -> dict | None:
    """查单个资产，owner 校验（不属于 user_id 返回 None）。"""
    from . import assets_registry as _reg  # lazy import

    return _reg.get_asset(user_id, asset_id)


# ---------------------------------------------------------------------------
# 下载：解析物理路径
# ---------------------------------------------------------------------------

def asset_download_path(user_id: int, asset_id: int):
    """返回 (asset_dict, Path)。资产不存在/不属于该用户 → ValueError。"""
    from . import assets_registry as _reg  # lazy import
    from . import storage as _storage  # lazy import

    asset = _reg.get_asset(user_id, asset_id)
    if asset is None:
        raise ValueError("not_found")
    storage_key = asset.get("storage_key") or ""
    if not storage_key:
        raise ValueError("no_storage_key")
    path = _storage.resolve_path(storage_key)
    if not path.exists() or not path.is_file():
        raise ValueError("file_missing")
    return asset, path


# ---------------------------------------------------------------------------
# 删除关联：置空引用字段
# ---------------------------------------------------------------------------

def nullify_references(user_id: int, url: str, references: list[dict]) -> None:
    """按 find_references 返回的 reference 列表，逐条置空对应业务字段。

    ownership 防越权策略（每条 SQL 都带 owner/user_id 条件）：
      - users.avatar_url         → WHERE id = %s AND id = user_id（只能清自己）
      - scripts.cover_image_url  → WHERE id = %s AND owner_id = user_id
      - character_cards.avatar_path → via script_id: subquery scripts.owner_id = user_id
      - card_persona_images       → DELETE WHERE id = %s AND card_id IN
                                     (cc.id FROM character_cards cc
                                      JOIN scripts s ON s.id=cc.script_id
                                      WHERE s.owner_id = user_id)
    """
    if not references:
        return

    from .db import connect  # lazy import

    with connect() as db:
        for ref in references:
            kind = ref.get("kind")
            ref_id = ref.get("id")
            if ref_id is None:
                continue

            if kind == "avatar":
                # users.avatar_url — 只能清自己的
                db.execute(
                    "update users set avatar_url = NULL where id = %s and id = %s",
                    (ref_id, user_id),
                )

            elif kind == "cover":
                # scripts.cover_image_url — owner_id 防越权
                db.execute(
                    "update scripts set cover_image_url = '' where id = %s and owner_id = %s",
                    (ref_id, user_id),
                )

            elif kind == "card_avatar":
                # character_cards.avatar_path — 两类卡都要覆盖防越权：
                #   用户 pc/persona 卡 = character_cards.user_id 直挂(script_id 为 null)；
                #   剧本 NPC 卡 = 通过 script_id → scripts.owner_id。
                db.execute(
                    """
                    update character_cards
                       set avatar_path = ''
                     where id = %s
                       and (
                           user_id = %s
                           or script_id in (select id from scripts where owner_id = %s)
                       )
                    """,
                    (ref_id, user_id, user_id),
                )

            elif kind == "persona_image":
                # card_persona_images — DELETE 行（纯引用记录，无内容价值）
                # 通过 card_id → character_cards(user_id 直挂 或 script owner)防越权
                db.execute(
                    """
                    delete from card_persona_images
                     where id = %s
                       and card_id in (
                           select cc.id from character_cards cc
                            where cc.user_id = %s
                               or cc.script_id in (select id from scripts where owner_id = %s)
                       )
                    """,
                    (ref_id, user_id, user_id),
                )
            # 其余未知 kind 静默跳过，不中断整体流程


# ---------------------------------------------------------------------------
# 删除：完整流程（引用检查 → 置空 → force 删）
# ---------------------------------------------------------------------------

def delete_asset_with_refs(
    user_id: int,
    asset_id: int,
    confirm: bool = False,
) -> dict[str, Any]:
    """S5 删除端点的核心逻辑。

    流程：
    1. find_asset_references 拿引用列表（同时做 owner 校验）。
    2. 若有引用且 confirm=False → 返回 {ok:False, needs_confirm:True, references:[...]}。
    3. 若无引用，或有引用但 confirm=True：
       a. nullify_references 置空所有引用字段（带 owner 条件）。
       b. delete_asset(force=True) 删 user_assets 行 + 物理文件。
       c. 返回 {ok:True, deleted:True}。
    4. 资产不存在/不属于该用户 → {ok:False, error:'not_found'}。
    """
    from . import assets_registry as _reg  # lazy import

    ref_result = _reg.find_asset_references(user_id, asset_id)
    if not ref_result.get("ok"):
        return {"ok": False, "error": ref_result.get("error", "not_found")}

    references = ref_result.get("references") or []
    asset = ref_result.get("asset") or {}

    # 有引用 + 未确认 → 要求前端二次确认
    if references and not confirm:
        return {
            "ok": False,
            "needs_confirm": True,
            "references": references,
            "asset": asset,
        }

    # 置空所有引用字段（有引用且 confirm=True，或本来就无引用）
    url = asset.get("url") or ""
    if references and url:
        nullify_references(user_id, url, references)

    # 物理删除（force=True：引用已处理）
    result = _reg.delete_asset(user_id, asset_id, force=True)
    return result
