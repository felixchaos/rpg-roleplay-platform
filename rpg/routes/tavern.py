"""routes/tavern.py — 酒馆模式(Tavern / SillyTavern 风格 1:1 角色对话)路由。

无剧本(script_id=NULL, save_kind='tavern')存档的生命周期 + 双向 JSONL 导入/导出 +
拖卡秒聊。复用:
  - workspace.create_tavern_save / _ingest_character_book
  - branches.activate_save(与 script 无关)
  - tavern_cards.parse_card / parse_png_card / tavern_to_user_card
  - user_cards.upsert_user_card / get_user_card
  - tavern_chats.parse_chat_jsonl / chat_to_save_payload / save_to_chat_jsonl
  - save_io.import_save(save_kind='tavern' → script_id NULL lane)
  - 删除复用 frontend_routes 同款:直接 delete game_saves(FK on delete cascade
    清 branch_commits / messages / 各 per-save 状态表)

所有端点 Depends(get_current_user),按 user_id 归属隔离,且 kind 校验 save_kind='tavern'。
流式发送沿用 POST /api/chat(不在此文件)。
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse, Response

from routes._deps_fastapi import get_current_user

router = APIRouter()

_MAX_IMPORT_PAYLOAD_BYTES = 16 * 1024 * 1024


def _json(content: Any, status_code: int = 200) -> JSONResponse:
    """统一 JSON 响应:jsonable_encoder 处理 datetime → iso。"""
    return JSONResponse(jsonable_encoder(content), status_code=status_code)


def _bad(msg: str, code: int = 400) -> JSONResponse:
    return _json({"ok": False, "error": msg}, status_code=code)


def _uid(api_user: dict[str, Any]) -> int:
    return int(api_user["id"])


def _invalidate_cache(api_user: dict[str, Any]) -> None:
    """切档后清 app 进程内的 per-user state 缓存(否则 GET /api/state 读旧档)。"""
    try:
        import app as _ui
        _ui._invalidate_user_cache(api_user)
    except Exception:
        pass


def _expose_save(save: dict[str, Any]) -> dict[str, Any]:
    """统一对外存档字段(create_tavern_save 返回的 expose(row) 已含全部列)。"""
    return {
        "id": save.get("id"),
        "title": save.get("title"),
        "save_kind": save.get("save_kind"),
        "tavern_character_card_id": save.get("tavern_character_card_id"),
        "tavern_persona_card_id": save.get("tavern_persona_card_id"),
        "archived_at": save.get("archived_at"),
        "updated_at": save.get("updated_at"),
        "created_at": save.get("created_at"),
    }


def _require_tavern_save(db: Any, save_id: int, user_id: int) -> dict[str, Any] | None:
    """归属 + kind 校验:返回 game_saves 行(save_kind='tavern' 且属于本人),否则 None。"""
    return db.execute(
        "select * from game_saves where id = %s and user_id = %s and save_kind = 'tavern'",
        (save_id, user_id),
    ).fetchone()


# ── 创建 / 拖卡秒聊 ────────────────────────────────────────────────────
@router.post("/api/tavern/chats")
async def api_tavern_create_chat(
    request: Request,
    api_user: dict[str, Any] = Depends(get_current_user),
) -> JSONResponse:
    """body {"character_card_id": int|null, "persona_card_id": int|null, "title": str|null}
    → create_tavern_save + activate_save → {"save": {...}}。

    酒馆 v2(决策1):character_card_id 可缺省 → 建空起手对话(无角色),由 agent
    在对话中用 set_tavern_character 工具自举。"""
    from platform_app import branches, workspace

    user_id = _uid(api_user)
    body = await request.json()
    character_card_id = body.get("character_card_id")
    if character_card_id is not None:
        try:
            character_card_id = int(character_card_id)
        except (TypeError, ValueError):
            return _bad("character_card_id 非法")
    persona_card_id = body.get("persona_card_id")
    try:
        persona_card_id = int(persona_card_id) if persona_card_id is not None else None
    except (TypeError, ValueError):
        persona_card_id = None
    title = (body.get("title") or "").strip() or None

    try:
        save = workspace.create_tavern_save(
            user_id, character_card_id, persona_card_id=persona_card_id, title=title,
        )
        branches.activate_save(user_id, int(save["id"]))
    except ValueError as exc:
        return _bad(str(exc))
    _invalidate_cache(api_user)
    return _json({"ok": True, "save": _expose_save(save)})


@router.post("/api/tavern/import-character")
async def api_tavern_import_character(
    request: Request,
    api_user: dict[str, Any] = Depends(get_current_user),
) -> JSONResponse:
    """拖入酒馆角色卡(.png/.json/.webp multipart 或 JSON body)→ 解析 + upsert + 建+激活存档。

    复用 platform_app/api/me.py:api_import_tavern_card 的解析逻辑。
    Returns {"save_id": int, "card": {...}, "character_name": str}。
    """
    from platform_app import branches, tavern_cards, user_cards, workspace

    user_id = _uid(api_user)
    content_type = request.headers.get("content-type", "")
    try:
        if "multipart/form-data" in content_type:
            form = await request.form()
            file_field = form.get("file")
            if file_field is None:
                return _bad("multipart 中缺少 file 字段")
            blob = await file_field.read()
            if len(blob) > _MAX_IMPORT_PAYLOAD_BYTES:
                raise ValueError(f"文件过大（上限 {_MAX_IMPORT_PAYLOAD_BYTES // (1024*1024)} MB）")
            fname = (getattr(file_field, "filename", "") or "").lower()
            if fname.endswith(".png") or fname.endswith(".webp"):
                v2 = tavern_cards.parse_png_card(blob)
            else:
                try:
                    v2 = tavern_cards.parse_card(blob.decode("utf-8", errors="replace"))
                except Exception as exc:
                    raise ValueError(f"JSON 解析失败：{exc}") from exc
        else:
            body = await request.json()
            if body.get("png_base64"):
                import base64 as _b64
                png_b64 = body["png_base64"]
                if not isinstance(png_b64, str) or len(png_b64) > _MAX_IMPORT_PAYLOAD_BYTES:
                    raise ValueError(f"png_base64 过大或非字符串（上限 {_MAX_IMPORT_PAYLOAD_BYTES} 字节）")
                try:
                    blob = _b64.b64decode(png_b64, validate=True)
                except Exception as exc:
                    raise ValueError(f"png_base64 不合法：{exc}") from exc
                if len(blob) > 10 * 1024 * 1024:
                    raise ValueError("PNG 文件过大（解码后最大 10MB）")
                v2 = tavern_cards.parse_png_card(blob)
            elif body.get("json") is not None:
                v2 = tavern_cards.parse_card(body["json"])
            elif body.get("json_string"):
                v2 = tavern_cards.parse_card(body["json_string"])
            elif body.get("base64"):
                v2 = tavern_cards.parse_card(body["base64"])
            else:
                return _bad("需要 file(multipart) / json / json_string / base64 / png_base64 之一")

        payload = tavern_cards.tavern_to_user_card(v2)
        card = user_cards.upsert_user_card(user_id, payload)
        save = workspace.create_tavern_save(user_id, int(card["id"]))
        branches.activate_save(user_id, int(save["id"]))
    except ValueError as exc:
        return _bad(str(exc))
    _invalidate_cache(api_user)
    return _json({
        "ok": True,
        "save_id": int(save["id"]),
        "card": card,
        "character_name": card.get("name") or "",
    })


# ── 列表(活跃 / 归档)─────────────────────────────────────────────────
def _list_chats(user_id: int, archived: bool) -> list[dict[str, Any]]:
    from platform_app.db import connect, init_db

    init_db()
    arch_clause = "archived_at is not null" if archived else "archived_at is null"
    with connect() as db:
        rows = db.execute(
            f"""
            select id, title, state_snapshot, archived_at, updated_at, created_at
            from game_saves
            where user_id = %s and save_kind = 'tavern' and {arch_clause}
            order by updated_at desc, id desc
            limit 200
            """,
            (user_id,),
        ).fetchall() or []
        out: list[dict[str, Any]] = []
        for r in rows:
            snap = r.get("state_snapshot") or {}
            if not isinstance(snap, dict):
                snap = {}
            tavern = snap.get("tavern") if isinstance(snap.get("tavern"), dict) else {}
            character = tavern.get("character") if isinstance(tavern.get("character"), dict) else {}
            char_name = str((character or {}).get("name") or "").strip() or (r.get("title") or "")
            # last_snippet:该存档最新一条 round/gm commit 的预览
            snippet_row = db.execute(
                """
                select content_preview, gm_output
                from branch_commits
                where save_id = %s and kind <> 'root'
                order by turn_index desc, id desc
                limit 1
                """,
                (r["id"],),
            ).fetchone()
            last_snippet = ""
            if snippet_row:
                # 优先用 gm_output 原文(无角色前缀);content_preview 经 round_preview 带 "GM："
                # 前缀(剧本语义),酒馆里去掉更贴合"和角色对话"的观感。
                raw = (
                    str(snippet_row.get("gm_output") or "").strip()
                    or str(snippet_row.get("content_preview") or "").strip()
                )
                raw = re.sub(r"^(GM|玩家)\s*[:：]\s*", "", raw)
                last_snippet = raw[:80]
            out.append({
                "id": r.get("id"),
                "title": r.get("title"),
                "character_name": char_name,
                "last_snippet": last_snippet,
                "updated_at": r.get("updated_at"),
                "archived_at": r.get("archived_at"),
            })
    return out


@router.get("/api/tavern/chats")
async def api_tavern_list_chats(
    archived: int = 0,
    api_user: dict[str, Any] = Depends(get_current_user),
) -> JSONResponse:
    """活跃对话列表(默认);?archived=1 → 归档列表。{"chats": [...]}。"""
    user_id = _uid(api_user)
    chats = _list_chats(user_id, archived=bool(archived))
    return _json({"ok": True, "chats": chats})


# ── 激活 / 归档 / 重命名 / 删除 ─────────────────────────────────────────
@router.post("/api/tavern/chats/{chat_id}/activate")
async def api_tavern_activate(
    chat_id: int,
    api_user: dict[str, Any] = Depends(get_current_user),
) -> JSONResponse:
    from platform_app import branches
    from platform_app.db import connect, init_db

    user_id = _uid(api_user)
    init_db()
    with connect() as db:
        if not _require_tavern_save(db, chat_id, user_id):
            return _bad("无权操作该对话", 403)
    try:
        branches.activate_save(user_id, chat_id)
    except ValueError as exc:
        return _bad(str(exc), 403)
    _invalidate_cache(api_user)
    return _json({"ok": True})


@router.patch("/api/tavern/chats/{chat_id}/archive")
async def api_tavern_archive(
    chat_id: int,
    request: Request,
    api_user: dict[str, Any] = Depends(get_current_user),
) -> JSONResponse:
    """body {"archived": bool} → set archived_at = now()/NULL。"""
    from platform_app.db import connect, init_db

    user_id = _uid(api_user)
    body = await request.json()
    archived = bool(body.get("archived"))
    init_db()
    with connect() as db:
        if not _require_tavern_save(db, chat_id, user_id):
            return _bad("无权操作该对话", 403)
        if archived:
            row = db.execute(
                "update game_saves set archived_at = now(), updated_at = now() "
                "where id = %s and user_id = %s and save_kind = 'tavern' returning archived_at",
                (chat_id, user_id),
            ).fetchone()
        else:
            row = db.execute(
                "update game_saves set archived_at = NULL, updated_at = now() "
                "where id = %s and user_id = %s and save_kind = 'tavern' returning archived_at",
                (chat_id, user_id),
            ).fetchone()
    archived_at = row.get("archived_at") if row else None
    return _json({"ok": True, "archived_at": archived_at})


@router.post("/api/tavern/chats/{chat_id}/rename")
async def api_tavern_rename(
    chat_id: int,
    request: Request,
    api_user: dict[str, Any] = Depends(get_current_user),
) -> JSONResponse:
    """body {"title": str} → 更新标题。"""
    from platform_app.db import connect, init_db

    user_id = _uid(api_user)
    body = await request.json()
    title = (body.get("title") or "").strip()
    if not title:
        return _bad("标题不能为空")
    init_db()
    with connect() as db:
        if not _require_tavern_save(db, chat_id, user_id):
            return _bad("无权操作该对话", 403)
        db.execute(
            "update game_saves set title = %s, updated_at = now() "
            "where id = %s and user_id = %s and save_kind = 'tavern'",
            (title[:200], chat_id, user_id),
        )
    return _json({"ok": True, "title": title[:200]})


_TITLE_SYS = (
    '你为一段对话起一个简短的中文标题,概括主题或场景。'
    '只返回 JSON,格式 {"title":"标题"};标题 4-14 字,不含引号/书名号/标点/表情/解释。'
)


def _is_default_title(t: str) -> bool:
    """create_tavern_save 起的占位名(可被自动标题覆盖);用户自定义名则不覆盖。"""
    t = (t or "").strip()
    if not t or t == "新对话":
        return True
    return bool(re.match(r"^与 .+ 的对话$", t))


def _sanitize_title(raw: str) -> str:
    """从模型输出里抠出干净标题:容忍 JSON / markdown 围栏 / 引号包裹。"""
    import json as _j
    t = (raw or "").strip()
    if not t:
        return ""
    if t.startswith("```"):
        t = t.strip("`")
        t = t[4:] if t[:4].lower() == "json" else t
        t = t.strip()
    try:
        obj = _j.loads(t)
        if isinstance(obj, dict) and obj.get("title"):
            t = str(obj["title"])
    except Exception:
        pass
    t = t.splitlines()[0].strip() if t else ""
    t = t.strip('“”"\'`《》「」【】（）()').strip()
    t = t.rstrip("。.!！?？,，、;；:：…")
    return t[:24]


@router.post("/api/tavern/chats/{chat_id}/autotitle")
async def api_tavern_autotitle(
    chat_id: int,
    api_user: dict[str, Any] = Depends(get_current_user),
) -> JSONResponse:
    """类 Claude:按对话内容自动生成标题。幂等 —— 仅当 title 仍为空(未被用户改名或上次生成)时才生成。"""
    import json as _j
    from platform_app.db import connect, init_db

    user_id = _uid(api_user)
    init_db()
    with connect() as db:
        row = db.execute(
            "select title, state_snapshot from game_saves "
            "where id = %s and user_id = %s and save_kind = 'tavern'",
            (chat_id, user_id),
        ).fetchone()
    if not row:
        return _bad("无权操作该对话", 403)
    existing = (row.get("title") or "").strip()
    if existing and not _is_default_title(existing):
        return _json({"ok": True, "title": existing, "skipped": "already_titled"})
    ss = row.get("state_snapshot")
    if isinstance(ss, str):
        ss = _j.loads(ss or "{}")
    history = ((ss or {}).get("history")) or []
    user_msg = next((m.get("content") for m in history
                     if m.get("role") == "user" and str(m.get("content") or "").strip()), "")
    asst_msg = next((m.get("content") for m in history
                     if m.get("role") == "assistant" and str(m.get("content") or "").strip()), "")
    if not (user_msg and asst_msg):
        return _json({"ok": True, "title": None, "skipped": "too_short"})
    excerpt = f"玩家:{str(user_msg)[:500]}\n回应:{str(asst_msg)[:500]}"
    try:
        from app import _get_gm
        from agents._harness import call_agent_json
        gm = _get_gm(api_user)
        api_id = getattr(gm, "api_id", None)
        backend = getattr(gm, "_backend", None)
        model = getattr(backend, "model_name", None)
        if not (api_id and model):
            return _json({"ok": True, "title": None, "skipped": "no_model"})
        text, _usage = call_agent_json(
            api_id, model, _TITLE_SYS, excerpt, user_id,
            max_tokens=64, timeout_sec=20, agent_kind="tavern_title", save_id=chat_id,
        )
    except Exception as exc:  # 标题生成失败绝不影响对话:吞掉,返回 skipped
        return _json({"ok": True, "title": None, "skipped": f"llm_failed:{type(exc).__name__}"})
    title = _sanitize_title(text)
    if not title:
        return _json({"ok": True, "title": None, "skipped": "empty"})
    with connect() as db:
        db.execute(
            "update game_saves set title = %s, updated_at = now() "
            "where id = %s and user_id = %s and save_kind = 'tavern' "
            "and (title is null or title = '' or title = '新对话' or title like '与 %% 的对话')",
            (title, chat_id, user_id),
        )
    return _json({"ok": True, "title": title})


@router.delete("/api/tavern/chats/{chat_id}")
async def api_tavern_delete(
    chat_id: int,
    api_user: dict[str, Any] = Depends(get_current_user),
) -> JSONResponse:
    """归属 + kind 校验后删除存档。FK on delete cascade 自动清 branch_commits /
    messages / 各 per-save 状态表(复用 frontend_routes:api_save_delete 同款裸删)。"""
    from platform_app.db import connect, init_db

    user_id = _uid(api_user)
    init_db()
    with connect() as db:
        if not _require_tavern_save(db, chat_id, user_id):
            return _bad("无权操作该对话", 403)
        db.execute(
            "delete from game_saves where id = %s and user_id = %s and save_kind = 'tavern'",
            (chat_id, user_id),
        )
    return _json({"ok": True})


# ── 双向 JSONL 导入 / 导出 ─────────────────────────────────────────────
@router.post("/api/tavern/chats/import-jsonl")
async def api_tavern_import_jsonl(
    request: Request,
    api_user: dict[str, Any] = Depends(get_current_user),
) -> JSONResponse:
    """导入 SillyTavern 聊天记录 JSONL(multipart file 或 {"jsonl": "...", "title": "..."}),
    自动解析/建一张同名角色卡,经 import_save(save_kind='tavern')建存档并激活。
    Returns {"save_id": int}。"""
    from platform_app import branches, save_io, tavern_chats, user_cards
    from platform_app.db import connect, init_db

    user_id = _uid(api_user)
    content_type = request.headers.get("content-type", "")
    custom_title: str | None = None
    try:
        if "multipart/form-data" in content_type:
            form = await request.form()
            file_field = form.get("file")
            if file_field is None:
                return _bad("multipart 中缺少 file 字段")
            blob = await file_field.read()
            if len(blob) > _MAX_IMPORT_PAYLOAD_BYTES:
                raise ValueError(f"文件过大（上限 {_MAX_IMPORT_PAYLOAD_BYTES // (1024*1024)} MB）")
            jsonl_text = blob.decode("utf-8", errors="replace")
            custom_title = (str(form.get("title") or "")).strip() or None
        else:
            body = await request.json()
            jsonl_text = body.get("jsonl") or ""
            if not isinstance(jsonl_text, str) or not jsonl_text.strip():
                return _bad("需要 jsonl 字段（JSONL 字符串）或 multipart file")
            custom_title = (body.get("title") or "").strip() or None

        header, commits = tavern_chats.parse_chat_jsonl(jsonl_text)

        # 自动 resolve / 建一张角色卡(best-effort:同名已存在则复用,否则建最小 pc 卡)。
        char_name = str(header.get("character_name") or "").strip() or "导入角色"
        character_card_id: int | None = None
        init_db()
        with connect() as db:
            existing = db.execute(
                "select id from character_cards where user_id = %s and card_type = 'pc' "
                "and name = %s order by id asc limit 1",
                (user_id, char_name),
            ).fetchone()
        if existing:
            character_card_id = int(existing["id"])
        else:
            try:
                card = user_cards.upsert_user_card(user_id, {"name": char_name})
                character_card_id = int(card["id"])
            except ValueError:
                character_card_id = None

        payload = tavern_chats.chat_to_save_payload(
            header, commits, title=custom_title, character_card_id=character_card_id,
        )
        result = save_io.import_save(user_id, payload)
        save_id = int(result["save_id"])
        branches.activate_save(user_id, save_id)
    except ValueError as exc:
        return _bad(str(exc))
    _invalidate_cache(api_user)
    return _json({
        "ok": True,
        "save_id": save_id,
        "commits_imported": result.get("commits_imported", 0),
        "header": header,
    })


@router.get("/api/tavern/chats/{chat_id}/export-jsonl")
async def api_tavern_export_jsonl(
    chat_id: int,
    api_user: dict[str, Any] = Depends(get_current_user),
) -> Response:
    """导出对话为 SillyTavern JSONL(决策2 往返)。"""
    from platform_app import tavern_chats
    from platform_app.db import connect, init_db

    user_id = _uid(api_user)
    init_db()
    with connect() as db:
        if not _require_tavern_save(db, chat_id, user_id):
            return _bad("无权操作该对话", 403)
    text = tavern_chats.save_to_chat_jsonl(chat_id, user_id=user_id)
    return Response(
        content=text,
        media_type="application/jsonl",
        headers={"Content-Disposition": f"attachment; filename=tavern-chat-{chat_id}.jsonl"},
    )
