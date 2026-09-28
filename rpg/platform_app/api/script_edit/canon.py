"""platform_app.api.script_edit.canon —— canon 实体写侧 CRUD(编辑/新增/删除)。

写 commit(canon_edit/add/delete)。字段解析与校验收在 _parse_canon_fields 一处;
类型白名单与 logical_key 生成规则住 kb.canon_repo(与编辑器 agent 工具共用)。

契约(md-editor 资源管理器 / 剧本详情「知识库人物」表格 / 编辑器 agent 共用):
  · 新建:name、type 必填;logical_key 可省,省略时后端按主提取链路同口径生成
    (concept → 规范化名字_concept;其它 → 规范化名字;被占用则 _2/_3…),返回体带实际 key。
    显式给 logical_key 且已被占用 → 409(旧语义不变);只由点号组成 / 带斜杠 → 400。
    本剧本已有同名同类型实体 → 409 并指向那条(与 NPC 卡 create_only 同语义,不静默造重复)。
  · 编辑:只改 body 里出现的字段;name/type 可改(key 是固定标识,不随改名变)。
    名字 / 别名 / 摘要真改了 → 向量置空等重嵌(与世界书 PUT 同口径)。
  · 删除:物理删除,commit 里留删除前整行供审计(与世界书/锚点删除同语义);
    挂在它下面的子实体改成无上级,受影响的 key 记进 commit(detached_children)。
  · 编辑器写入一律打 attrs.source='editor',重建知识库 / 重新提取时保留(与 agent 工具同口径)。
  · 写完作废本进程的别名归并缓存(kb.alias)。
"""
from __future__ import annotations

from typing import Any

from fastapi import Depends, Request
from psycopg.types.json import Jsonb

from kb.alias import invalidate_alias_cache
from kb.canon_repo import (
    CANON_TYPE_LABELS_ZH,
    allocate_canon_logical_key,
    canon_embedding_reset_sql,
    canon_logical_key_problem,
    canon_type_choices_text,
    detach_canon_children,
    find_same_name_canon,
    normalize_canon_type,
)

from ...db import connect
from .._deps import json_response, require_user, value_error_response
from ._shared import _require_owner, _write_commit, router

_CANON_COLS = (
    "id, logical_key, name, full_name, type, entity_subtype, parent_logical_key, "
    "summary, identity, background, aliases, attrs, "
    "first_revealed_chapter, public_knowledge, importance, created_at"
)

# 纯文本列(NOT NULL default ''):null → 空串,绝不写字面量 "None"。
_TEXT_FIELDS = ("full_name", "summary", "identity", "background", "entity_subtype", "parent_logical_key")
# 整数列(NOT NULL default 0):null / 空串 → 0;非整数 → 400 可读报错。
_INT_FIELDS = {"importance": "重要度", "first_revealed_chapter": "首次出现章节"}
_EDITOR_SOURCE = {"source": "editor"}
_NOT_FOUND = "这个设定实体不存在(可能已被删除),刷新列表后再试"


def _type_label(entity_type: str) -> str:
    return CANON_TYPE_LABELS_ZH.get(entity_type, entity_type)


class _CanonInputError(ValueError):
    """请求字段不合法:文案直接给用户看。"""


def _parse_int(label: str, value: Any) -> int:
    if value is None or (isinstance(value, str) and not value.strip()):
        return 0
    try:
        return int(value.strip()) if isinstance(value, str) else int(value)
    except (TypeError, ValueError):
        raise _CanonInputError(f"「{label}」要填整数,现在是「{value}」") from None


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in ("1", "true", "yes", "y", "on", "是")


def _parse_canon_fields(body: dict, *, creating: bool) -> dict[str, Any]:
    """body → {列名: 已校验的值}。只收 body 里出现的键(新建时 name/type 必填)。

    未识别的键(logical_key / id / created_at 等只读回显)一律忽略。
    """
    out: dict[str, Any] = {}
    if creating or "name" in body:
        name = str(body.get("name") or "").strip()
        if not name:
            raise _CanonInputError("名称不能为空")
        out["name"] = name
    if creating or "type" in body:
        raw = body.get("type")
        if raw is None or not str(raw).strip():
            raise _CanonInputError(f"请选择类型:{canon_type_choices_text()}")
        entity_type = normalize_canon_type(raw)
        if not entity_type:
            raise _CanonInputError(f"类型「{raw}」不认识,可选:{canon_type_choices_text()}")
        out["type"] = entity_type
    for col in _TEXT_FIELDS:
        if col in body:
            v = body[col]
            out[col] = "" if v is None else str(v)
    if "parent_logical_key" in out:
        out["parent_logical_key"] = out["parent_logical_key"].strip()
    for col, label in _INT_FIELDS.items():
        if col in body:
            out[col] = _parse_int(label, body[col])
    if "public_knowledge" in body:
        out["public_knowledge"] = _parse_bool(body["public_knowledge"])
    if "aliases" in body:
        v = body["aliases"]
        if v is None:
            out["aliases"] = []
        elif isinstance(v, list):
            out["aliases"] = [str(x) for x in v]
        else:
            s = str(v).strip()
            out["aliases"] = [s] if s else []
    if "attrs" in body:
        v = body["attrs"]
        if v is None:
            out["attrs"] = {}
        elif isinstance(v, dict):
            out["attrs"] = v
        else:
            raise _CanonInputError("attrs 要是键值对象")
    return out


async def _read_body(request: Request) -> dict | None:
    try:
        body = await request.json()
    except Exception:
        return None
    return body if isinstance(body, dict) else None


# ─── canon-entities CRUD ─────────────────────────────────────────────────────

@router.put("/api/scripts/{script_id}/canon-entities/{logical_key}")
async def api_canon_update(
    request: Request, script_id: int, logical_key: str, user=Depends(require_user)
):
    """编辑 canon entity，写 commit kind=canon_edit。

    body: {name?, type?, full_name?, summary?, identity?, background?, parent_logical_key?,
           entity_subtype?, importance?, aliases?, attrs?, first_revealed_chapter?, public_knowledge?}
    （aliases 为 jsonb 字符串数组,attrs 为 jsonb 开放对象;logical_key 是固定标识,不可改)
    """
    body = await _read_body(request)
    if body is None:
        return json_response({"ok": False, "error": "请求内容不是合法的 JSON 对象"}, status_code=400)
    try:
        fields = _parse_canon_fields(body, creating=False)
    except _CanonInputError as exc:
        return value_error_response(exc)
    if fields.get("parent_logical_key") == logical_key:
        return json_response({"ok": False, "error": "不能把实体自己设为上级"}, status_code=400)
    if not fields:
        return json_response(
            {"ok": False, "error": "没有可更新的字段(可改:名称、类型、摘要、身份、背景、别名、上级、重要度等)"},
            status_code=400,
        )

    with connect() as db:
        try:
            _require_owner(db, script_id, user["id"])
        except ValueError as exc:
            return value_error_response(exc, status_code=403)

        before_row = db.execute(
            f"SELECT {_CANON_COLS} FROM kb_canon_entities WHERE script_id = %s AND logical_key = %s",
            (script_id, logical_key),
        ).fetchone()
        if not before_row:
            return json_response({"ok": False, "error": _NOT_FOUND}, status_code=404)

        before = dict(before_row)
        sets, args = [], []
        for col, val in fields.items():
            if col == "attrs":
                continue
            sets.append(f"{col}=%s")
            args.append(Jsonb(val) if col == "aliases" else val)
        # 编辑器改过 → 标 source='editor',重建知识库保留(与 agent 工具 upsert_canon_entity 同口径)。
        # md-editor 会整体回写 attrs(不含 source),所以替换时也要把标记合进去。
        if "attrs" in fields:
            sets.append("attrs=%s")
            args.append(Jsonb({**fields["attrs"], **_EDITOR_SOURCE}))
        else:
            sets.append("attrs = coalesce(attrs, '{}'::jsonb) || %s::jsonb")
            args.append(Jsonb(_EDITOR_SOURCE))
        # 名字 / 别名 / 摘要真改了 → 向量置空(否则召回一直按旧文本命中,embed 只补 null 行)。
        reset = canon_embedding_reset_sql(fields)
        if reset:
            sets.append(reset[0])
            args.extend(reset[1])

        args.extend([script_id, logical_key])
        db.execute(
            f"UPDATE kb_canon_entities SET {', '.join(sets)} WHERE script_id=%s AND logical_key=%s",
            tuple(args),
        )

        after_row = db.execute(
            f"SELECT {_CANON_COLS} FROM kb_canon_entities WHERE script_id = %s AND logical_key = %s",
            (script_id, logical_key),
        ).fetchone()
        after = dict(after_row)

        commit_id = _write_commit(
            db,
            script_id=script_id,
            user_id=user["id"],
            kind="canon_edit",
            message=f"编辑 canon entity: {logical_key}",
            payload={"table": "kb_canon_entities", "op": "edit", "before": before, "after": after, "ids": {"logical_key": logical_key}},
        )
        db.commit()
    invalidate_alias_cache(script_id)

    return json_response({"ok": True, "entity": after, "commit_id": commit_id})


@router.post("/api/scripts/{script_id}/canon-entities")
async def api_canon_add(
    request: Request, script_id: int, user=Depends(require_user)
):
    """新增 canon entity，写 commit kind=canon_add。

    body: {name, type, logical_key?, summary?, identity?, background?, entity_subtype?,
           parent_logical_key?, importance?, full_name?, aliases?, attrs?,
           first_revealed_chapter?, public_knowledge?}
    logical_key 省略时由后端生成(见模块 docstring);返回 {ok, entity, logical_key, commit_id}。
    """
    body = await _read_body(request)
    if body is None:
        return json_response({"ok": False, "error": "请求内容不是合法的 JSON 对象"}, status_code=400)
    try:
        fields = _parse_canon_fields(body, creating=True)
    except _CanonInputError as exc:
        return value_error_response(exc)
    explicit_key = str(body.get("logical_key") or "").strip()
    key_problem = canon_logical_key_problem(explicit_key)
    if key_problem:
        return json_response({"ok": False, "error": key_problem}, status_code=400)
    if explicit_key and fields.get("parent_logical_key") == explicit_key:
        return json_response({"ok": False, "error": "不能把实体自己设为上级"}, status_code=400)

    with connect() as db:
        try:
            _require_owner(db, script_id, user["id"])
        except ValueError as exc:
            return value_error_response(exc, status_code=403)

        dup = find_same_name_canon(db, script_id, fields["name"], fields["type"])
        if dup:
            return json_response({
                "ok": False,
                "error": (f"本剧本已经有同名的{_type_label(fields['type'])}「{dup['name']}」"
                          f"(logical_key: {dup['logical_key']}),请直接编辑那一条,不要重复新建"),
                "existing_logical_key": dup["logical_key"],
            }, status_code=409)

        new_row = None
        # 自动生成的 key:并发新建可能选到同一个号 → 撞了就重选(最多 5 次)。显式 key 只试一次。
        for _ in range(1 if explicit_key else 5):
            logical_key = explicit_key or allocate_canon_logical_key(
                db, script_id, fields["name"], fields["type"])
            new_row = db.execute(
                f"""
                INSERT INTO kb_canon_entities
                  (script_id, logical_key, name, full_name, type, summary, identity, background,
                   entity_subtype, parent_logical_key, importance,
                   aliases, attrs, first_revealed_chapter, public_knowledge)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (script_id, logical_key) DO NOTHING
                RETURNING {_CANON_COLS}
                """,
                (
                    script_id, logical_key, fields["name"],
                    fields.get("full_name", ""),
                    fields["type"],
                    fields.get("summary", ""),
                    fields.get("identity", ""),
                    fields.get("background", ""),
                    fields.get("entity_subtype", ""),
                    fields.get("parent_logical_key", ""),
                    fields.get("importance", 0),
                    Jsonb(fields.get("aliases", [])),
                    # 编辑器新建 → source='editor',重建知识库保留不删
                    Jsonb({**fields.get("attrs", {}), **_EDITOR_SOURCE}),
                    fields.get("first_revealed_chapter", 0),
                    fields.get("public_knowledge", False),
                ),
            ).fetchone()
            if new_row:
                break
        if not new_row:
            if explicit_key:
                msg = (f"logical_key「{explicit_key}」已被本剧本的另一个实体占用。"
                       "换一个,或者留空让系统自动生成")
            else:
                msg = "自动编号时和别人同时新建撞了号,请再试一次"
            return json_response({"ok": False, "error": msg}, status_code=409)
        after = dict(new_row)
        logical_key = after["logical_key"]

        commit_id = _write_commit(
            db,
            script_id=script_id,
            user_id=user["id"],
            kind="canon_add",
            message=f"新增 canon entity: {logical_key}",
            payload={"table": "kb_canon_entities", "op": "add", "after": after, "ids": {"logical_key": logical_key}},
        )
        db.commit()
    invalidate_alias_cache(script_id)

    return json_response({"ok": True, "entity": after, "logical_key": logical_key, "commit_id": commit_id})


@router.delete("/api/scripts/{script_id}/canon-entities/{logical_key}")
async def api_canon_delete(
    script_id: int, logical_key: str, user=Depends(require_user)
):
    """删除 canon entity(物理删除),写 commit kind=canon_delete,payload 留删除前整行。

    历史上这里是「软删除」(importance=-1),但 GET 列表 / 单条、GM 读 canon
    (kb.canon_repo.read_canon_entities)、编辑器 agent 的 list 工具都不认这个标记 ——
    删了照样出现、GM 照样用,且同一个 logical_key 再也建不回来(恒 409)。
    与世界书 / 锚点删除、复核页 PATCH /canon delete_entity 对齐为真删除。
    """
    with connect() as db:
        try:
            _require_owner(db, script_id, user["id"])
        except ValueError as exc:
            return value_error_response(exc, status_code=403)

        before_row = db.execute(
            f"SELECT {_CANON_COLS} FROM kb_canon_entities WHERE script_id = %s AND logical_key = %s",
            (script_id, logical_key),
        ).fetchone()
        if not before_row:
            return json_response({"ok": False, "error": _NOT_FOUND}, status_code=404)

        before = dict(before_row)
        db.execute(
            "DELETE FROM kb_canon_entities WHERE script_id=%s AND logical_key=%s",
            (script_id, logical_key),
        )
        # 子实体的上级指向被删的 key:改成无上级(否则上级列挂着不存在的 key,
        # 之后同名重建分到同一个 key 时旧子实体又被静默挂回去)。
        detached = detach_canon_children(db, script_id, logical_key)

        commit_id = _write_commit(
            db,
            script_id=script_id,
            user_id=user["id"],
            kind="canon_delete",
            message=f"删除 canon entity: {logical_key}",
            payload={"table": "kb_canon_entities", "op": "delete", "before": before,
                     "detached_children": detached, "ids": {"logical_key": logical_key}},
        )
        db.commit()
    invalidate_alias_cache(script_id)

    return json_response({"ok": True, "deleted": True, "detached_children": detached, "commit_id": commit_id})
