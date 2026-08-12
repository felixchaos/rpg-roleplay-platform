"""platform_app/assets_registry.py — user_assets 登记 CRUD（S2）。

所有函数都做 owner 校验（user_id 严格匹配），不对外暴露跨用户数据。
物理文件操作（delete_file / find_references）lazy import storage，避免循环。
"""
from __future__ import annotations

from typing import Any

from .db import connect, init_db, limit_value

# ---------------------------------------------------------------------------
# 写：登记 / 幂等 upsert
# ---------------------------------------------------------------------------

def register_asset(
    *,
    user_id: int,
    kind: str,
    storage_key: str,
    url: str,
    source: str = "",
    ref_kind: str | None = None,
    ref_id: int | None = None,
    mime: str = "",
    size: int = 0,
    meta: dict[str, Any] | None = None,
) -> int:
    """登记一条资产记录，返回 user_assets.id。

    幂等：on conflict(user_id, storage_key) do update，
    更新 url / source / ref_kind / ref_id / mime / size / meta。
    """
    from psycopg.types.json import Jsonb  # lazy import

    init_db()
    meta_val = meta if meta is not None else {}
    with connect() as db:
        row = db.execute(
            """
            insert into user_assets
                (user_id, kind, storage_key, url, source, ref_kind, ref_id, mime, size, meta)
            values
                (%(user_id)s, %(kind)s, %(storage_key)s, %(url)s, %(source)s,
                 %(ref_kind)s, %(ref_id)s, %(mime)s, %(size)s, %(meta)s)
            on conflict (user_id, storage_key) do update set
                url      = excluded.url,
                source   = excluded.source,
                ref_kind = excluded.ref_kind,
                ref_id   = excluded.ref_id,
                mime     = excluded.mime,
                size     = excluded.size,
                meta     = excluded.meta
            returning id
            """,
            {
                "user_id":     user_id,
                "kind":        kind,
                "storage_key": storage_key,
                "url":         url,
                "source":      source,
                "ref_kind":    ref_kind,
                "ref_id":      ref_id,
                "mime":        mime,
                "size":        size,
                "meta":        Jsonb(meta_val),
            },
        ).fetchone()
    if row is None:
        raise RuntimeError(f"assets_registry: insert/upsert 未返回行 (storage_key={storage_key!r})")
    return int(row["id"])


# ---------------------------------------------------------------------------
# 读：列表
# ---------------------------------------------------------------------------

def list_user_assets(
    user_id: int,
    kind: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[dict]:
    """列出 user_id 的资产，按 created_at desc。

    kind 不为 None 时只返回该 kind。limit 上限 500 防爆。
    """
    init_db()
    limit = limit_value(limit, default=200, maximum=500)
    offset = max(0, offset)

    with connect() as db:
        if kind is not None:
            rows = db.execute(
                """
                select id, user_id, kind, storage_key, url, source,
                       ref_kind, ref_id, mime, size, meta, created_at
                from user_assets
                where user_id = %s and kind = %s
                order by created_at desc
                limit %s offset %s
                """,
                (user_id, kind, limit, offset),
            ).fetchall()
        else:
            rows = db.execute(
                """
                select id, user_id, kind, storage_key, url, source,
                       ref_kind, ref_id, mime, size, meta, created_at
                from user_assets
                where user_id = %s
                order by created_at desc
                limit %s offset %s
                """,
                (user_id, limit, offset),
            ).fetchall()

    return [_row_to_dict(r) for r in rows]


# ---------------------------------------------------------------------------
# 读：单个
# ---------------------------------------------------------------------------

def get_asset(user_id: int, asset_id: int) -> dict | None:
    """查单个资产，做 owner 校验（user_id 不匹配返回 None）。"""
    init_db()
    with connect() as db:
        row = db.execute(
            """
            select id, user_id, kind, storage_key, url, source,
                   ref_kind, ref_id, mime, size, meta, created_at
            from user_assets
            where id = %s and user_id = %s
            """,
            (asset_id, user_id),
        ).fetchone()
    return _row_to_dict(row) if row is not None else None


# ---------------------------------------------------------------------------
# 删除辅助：查引用
# ---------------------------------------------------------------------------

def find_asset_references(user_id: int, asset_id: int) -> dict:
    """查询该资产的业务引用，供删除前确认弹窗使用。

    返回 {ok, asset, references:[...]}。
    asset 不存在或不属于 user_id 则返回 {ok: False, error: 'not_found'}。
    """
    asset = get_asset(user_id, asset_id)
    if asset is None:
        return {"ok": False, "error": "not_found"}

    from . import storage as _storage  # lazy import，避免循环
    url = asset.get("url") or ""
    refs: list[dict] = []
    if url:
        try:
            refs = _storage.find_references(url)
        except Exception:
            pass  # 引用查询失败不阻断

    return {"ok": True, "asset": asset, "references": refs}


# ---------------------------------------------------------------------------
# 删除：删 DB 行 + 物理文件（不置空引用字段，调用方 / S5 端点负责）
# ---------------------------------------------------------------------------

def delete_asset(user_id: int, asset_id: int, *, force: bool = False) -> dict:
    """删除 user_assets 行 + 物理文件。

    调用约定：
      - 调用方应先调 find_asset_references 取得引用列表；
        若 references 非空且 force=False，本函数返回 {ok: False, error: 'has_references',
        references: [...]}，**不执行删除**，由端点弹确认后以 force=True 再调；
      - force=True 则无论引用与否直接删除（调用方负责已置空引用字段）。

    返回 {ok, deleted: bool, storage_key, references: [...]}。
    资产不存在或不属于 user_id 则 {ok: False, error: 'not_found'}。
    """
    asset = get_asset(user_id, asset_id)
    if asset is None:
        return {"ok": False, "error": "not_found", "deleted": False}

    url = asset.get("url") or ""
    storage_key = asset.get("storage_key") or ""

    # 查引用（有 url 时才查）
    refs: list[dict] = []
    if url:
        from . import storage as _storage  # lazy import
        try:
            refs = _storage.find_references(url)
        except Exception:
            pass

    # 有引用且未强制 → 拒绝
    if refs and not force:
        return {
            "ok": False,
            "error": "has_references",
            "deleted": False,
            "references": refs,
        }

    # 删 DB 行
    with connect() as db:
        db.execute(
            "delete from user_assets where id = %s and user_id = %s",
            (asset_id, user_id),
        )

    # 删物理文件（失败 silent）
    if storage_key:
        try:
            from . import storage as _storage  # lazy import
            _storage.delete_file(storage_key)
        except Exception:
            pass

    return {
        "ok": True,
        "deleted": True,
        "storage_key": storage_key,
        "references": refs,
    }


# ---------------------------------------------------------------------------
# 内部工具
# ---------------------------------------------------------------------------

def _row_to_dict(row: Any) -> dict:
    """把 psycopg Row 转成普通 dict，created_at 转 ISO 字符串。"""
    d = dict(row)
    if "created_at" in d and d["created_at"] is not None:
        try:
            d["created_at"] = d["created_at"].isoformat()
        except Exception:
            pass
    return d
