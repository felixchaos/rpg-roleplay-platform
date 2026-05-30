"""platform_app.knowledge.script_overrides — 剧本 overrides DB helper。

读 + 写 script_overrides 表,代替原 modules/_script_overrides/*.json 文件读取。
"""
from __future__ import annotations

import json
from typing import Any

from platform_app.db import connect


def get_overrides_by_script_id(script_id: int) -> dict:
    """从 DB 取指定 script 的 overrides data。无记录返回空 dict。"""
    if not script_id:
        return {}
    with connect() as db:
        cur = db.execute(
            "SELECT data FROM script_overrides WHERE script_id = %s",
            (script_id,),
        )
        row = cur.fetchone()
    if not row:
        return {}
    data = row["data"] if hasattr(row, "__getitem__") else row[0]
    return dict(data) if data else {}


def upsert_overrides(script_id: int, data: dict) -> None:
    """写入/更新 script overrides。"""
    if not script_id:
        return
    with connect() as db:
        db.execute(
            """
            INSERT INTO script_overrides (script_id, data, updated_at)
            VALUES (%s, %s::jsonb, NOW())
            ON CONFLICT (script_id) DO UPDATE
              SET data = EXCLUDED.data, updated_at = NOW()
            """,
            (script_id, json.dumps(data, ensure_ascii=False)),
        )


def load_all_overrides_by_key() -> dict[str, dict]:
    """兼容旧 _load_script_overrides() 接口:
    把所有 script 的 overrides 拉出来按 script_key 索引。

    script_key 优先来自 data.get("script_key"),否则 fallback 到 script.title。
    """
    out: dict[str, dict] = {}
    with connect() as db:
        rows = db.execute(
            """
            SELECT s.id, s.title, o.data
            FROM script_overrides o
            JOIN scripts s ON s.id = o.script_id
            """
        ).fetchall()
    for row in rows:
        sid = row["id"] if hasattr(row, "__getitem__") else row[0]
        title = row["title"] if hasattr(row, "__getitem__") else row[1]
        data = row["data"] if hasattr(row, "__getitem__") else row[2]
        if not data:
            continue
        d: dict[str, Any] = dict(data) if not isinstance(data, dict) else data
        key = d.get("script_key") or title
        if key:
            out[key] = d
    return out


def seed_from_json_dir(overrides_dir_path: str | None = None) -> dict[str, Any]:
    """从 modules/_script_overrides/*.json 把数据写入 DB。

    在 migration v16 已运行后可手动调用，也用于初次部署时补数据。
    返回 {"seeded": N, "skipped": M} 统计。
    """
    import json as _json
    from pathlib import Path

    if overrides_dir_path:
        overrides_dir = Path(overrides_dir_path)
    else:
        # 默认路径: rpg/modules/_script_overrides/
        overrides_dir = Path(__file__).resolve().parent.parent.parent / "modules" / "_script_overrides"

    if not overrides_dir.is_dir():
        return {"seeded": 0, "skipped": 0, "reason": "dir_not_found"}

    seeded = 0
    skipped = 0
    with connect() as db:
        for f in sorted(overrides_dir.glob("*.json")):
            try:
                data = _json.loads(f.read_text(encoding="utf-8"))
                key = data.get("script_key")
                if not key:
                    skipped += 1
                    continue
                row = db.execute(
                    "SELECT id FROM scripts WHERE title = %s LIMIT 1", (key,)
                ).fetchone()
                if not row:
                    skipped += 1
                    continue
                sid = row["id"] if hasattr(row, "__getitem__") else row[0]
                db.execute(
                    """
                    INSERT INTO script_overrides (script_id, data)
                    VALUES (%s, %s::jsonb)
                    ON CONFLICT (script_id) DO NOTHING
                    """,
                    (sid, _json.dumps(data, ensure_ascii=False)),
                )
                seeded += 1
            except Exception:
                skipped += 1
    return {"seeded": seeded, "skipped": skipped}
