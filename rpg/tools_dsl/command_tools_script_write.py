"""
command_tools_script_write.py — N (MD 编辑器) §5: script scope 「直写库」写工具。

给 MD 编辑器右栏 agent(console_assistant)5 个端到端直写剧本知识资产的写工具:
  · update_script_chapter   章节正文(覆盖整章 → destructive=True)
  · upsert_worldbook_entry  世界书条目(创建/更新)
  · update_npc_card         NPC 角色卡(复用 character_cards.upsert)
  · update_anchor           时间线锚点(keywords 是原生 text[])
  · upsert_canon_entity     canon 实体(aliases/attrs 是 jsonb)

executor 签名统一为 `(user_id, script_id, args, state) -> str`(script scope)。

安全铁律(每个写工具逐条遵守,漏一条 = 越权漏洞):
  ① sid = script_id or args.get("script_id");缺 → 友好失败。
  ② 进 DB 后第一件事调 perms.script_owned 严格 owner 闸 —— **绝不可用
     _user_can_read_script(订阅者可读,写会越权)**。非 owner 立即返回友好失败串。
  ③ 写成功后尽量 _write_commit 审计(照 script_edit 的 kind)。
  ④ 整个执行 try/except 包裹,返回友好失败串,绝不向 dispatcher 抛异常。

jsonb vs text[] 绑定(照搬 script_edit 的契约,搞反 = 写坏列):
  · worldbook  keys/regex_keys/character_filter/scene_filter = jsonb 字符串数组 → Jsonb([...])
  · canon      aliases = jsonb 字符串数组、attrs = jsonb 开放对象 → Jsonb(...)
  · anchor     keywords = PostgreSQL 原生 text[] → 参数直接绑 Python list,绝不 Jsonb/json.dumps
  · npc card   aliases/sample_dialogue/tags 由 character_cards.upsert 内部 Jsonb 化,本层不碰
"""
from __future__ import annotations

from typing import Any

from tools_dsl.command_dispatcher import ToolSpec, get_registry

# 写工具的 origin:UI 按钮 / 直连 API / 侧栏控制台助手(MD 编辑器右栏 agent)。
# 不含 llm_chat / llm_chat_json_op / autonomous_agent —— 剧情流式输出和黑天鹅代理不该直写剧本库。
_SCRIPT_WRITE_ORIGINS = frozenset({"ui_button", "api_direct", "console_assistant"})


def _resolve_sid(script_id: int | None, args: dict) -> int | None:
    """sid = script_id(服务端绑定,首选) or args.get("script_id")。返回 int 或 None。"""
    sid = script_id if script_id is not None else args.get("script_id")
    if sid is None:
        return None
    try:
        return int(sid)
    except (TypeError, ValueError):
        return None


# ────────────────────────────────────────────────────────────────────────────
# 1) update_script_chapter — 覆盖整章正文(destructive=True)
# ────────────────────────────────────────────────────────────────────────────


def _t_update_script_chapter(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    sid = _resolve_sid(script_id, args)
    if sid is None:
        return "失败: script_id 必填"
    chapter_index = args.get("chapter_index")
    if chapter_index is None:
        return "失败: chapter_index 必填"
    try:
        ci = int(chapter_index)
    except (TypeError, ValueError):
        return "失败: chapter_index 必须是整数"
    title = args.get("title")
    content = args.get("content")
    volume_title = args.get("volume_title")
    if title is None and content is None and volume_title is None:
        return "失败: 至少要传 title / content / volume_title 之一"
    try:
        from platform_app.db import connect, init_db
        from platform_app.perms import script_owned
        init_db()
        # ② 严格 owner 闸(在 update_chapter 内部也会再查一次 script_owned,这里前置一道
        #    给出统一的工具友好失败串;update_chapter 自身的 ValueError 兜底捕获)。
        with connect() as db:
            if not script_owned(db, sid, user_id):
                return "失败(权限): 剧本不属于当前用户"
        # ③ 复用现成写函数 platform_app.script_import.update_chapter(自带 owner 校验 + word_count 同步)。
        from platform_app.script_import import update_chapter
        update_chapter(
            user_id, sid, ci,
            title=(str(title) if title is not None else None),
            content=(str(content) if content is not None else None),
            volume_title=(str(volume_title) if volume_title is not None else None),
        )
        # 审计:章节正文走 script_commits,kind 取 chapter_edit(与 worldbook_edit 等对齐命名)。
        try:
            from platform_app.api.script_edit import _write_commit
            with connect() as adb:
                if script_owned(adb, sid, user_id):
                    _write_commit(
                        adb, script_id=sid, user_id=user_id,
                        kind="chapter_edit",
                        message=f"编辑章节 #{ci}",
                        payload={
                            "table": "script_chapters", "op": "edit",
                            "ids": {"chapter_index": ci},
                            "fields": [k for k in ("title", "content", "volume_title")
                                       if args.get(k) is not None],
                        },
                    )
                    adb.commit()
        except Exception:
            pass  # 审计失败不影响写主流程
        return f"已更新章节 #{ci}(剧本 #{sid})"
    except ValueError as exc:
        return f"失败: {exc}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


# ────────────────────────────────────────────────────────────────────────────
# 2) upsert_worldbook_entry — 创建(无 entry_id)/ 更新(有 entry_id)
# ────────────────────────────────────────────────────────────────────────────


def _strlist(v: Any) -> list[str]:
    return [str(x) for x in v] if isinstance(v, list) else []


def _t_upsert_worldbook_entry(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    sid = _resolve_sid(script_id, args)
    if sid is None:
        return "失败: script_id 必填"
    entry_id = args.get("entry_id")
    title = args.get("title")
    if not entry_id and not (title and str(title).strip()):
        return "失败: 创建世界书条目必须提供 title"
    try:
        from psycopg.types.json import Jsonb

        from platform_app.api.script_edit import _write_commit
        from platform_app.db import connect, init_db
        from platform_app.perms import script_owned
        init_db()
        with connect() as db:
            # ② 严格 owner 闸
            if not script_owned(db, sid, user_id):
                return "失败(权限): 剧本不属于当前用户"

            if entry_id:
                # ── 更新现有条目 ──
                try:
                    eid = int(entry_id)
                except (TypeError, ValueError):
                    return "失败: entry_id 必须是整数"
                before = db.execute(
                    "select id, title from worldbook_entries where id = %s and script_id = %s",
                    (eid, sid),
                ).fetchone()
                if not before:
                    return f"失败: worldbook 条目 #{eid} 不存在或不属于剧本 #{sid}"
                sets, params = [], []
                # text/标量列
                for col in ("title", "content", "insertion_position"):
                    if col in args and args[col] is not None:
                        sets.append(f"{col}=%s")
                        params.append(str(args[col]))
                for col in ("priority", "token_budget", "sticky_turns", "cooldown_turns"):
                    if col in args and args[col] is not None:
                        sets.append(f"{col}=%s")
                        params.append(int(args[col]))
                if args.get("probability") is not None:
                    sets.append("probability=%s")
                    params.append(float(args["probability"]))
                if args.get("enabled") is not None:
                    sets.append("enabled=%s")
                    params.append(bool(args["enabled"]))
                # jsonb 字符串数组列:keys/regex_keys/character_filter/scene_filter → Jsonb([...])
                for col in ("keys", "regex_keys", "character_filter", "scene_filter"):
                    if col in args and isinstance(args[col], list):
                        sets.append(f"{col}=%s")
                        params.append(Jsonb(_strlist(args[col])))
                if not sets:
                    return "失败: 没有要更新的字段"
                sets.append("updated_at=now()")
                params.extend([eid, sid])
                db.execute(
                    f"update worldbook_entries set {', '.join(sets)} "
                    f"where id=%s and script_id=%s",
                    tuple(params),
                )
                # ③ 审计
                try:
                    _write_commit(
                        db, script_id=sid, user_id=user_id, kind="worldbook_edit",
                        message=f"编辑 worldbook #{eid}",
                        payload={"table": "worldbook_entries", "op": "edit",
                                 "ids": {"entry_id": eid}},
                    )
                except Exception:
                    pass
                db.commit()
                _invalidate_worldbook_cache(sid)
                return f"已更新世界书条目 #{eid}(剧本 #{sid})"
            else:
                # ── 创建新条目 ──
                book = db.execute(
                    "select id from books where script_id = %s", (sid,),
                ).fetchone()
                book_id = int(book["id"]) if book else None
                new_row = db.execute(
                    """
                    insert into worldbook_entries
                      (book_id, script_id, title, content, priority, enabled, metadata,
                       keys, regex_keys, character_filter, scene_filter,
                       token_budget, sticky_turns, cooldown_turns, probability, insertion_position)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    returning id
                    """,
                    (
                        book_id, sid, str(title).strip(),
                        str(args.get("content") or ""),
                        int(args["priority"]) if args.get("priority") is not None else 50,
                        bool(args["enabled"]) if args.get("enabled") is not None else True,
                        Jsonb({}),
                        Jsonb(_strlist(args.get("keys"))),
                        Jsonb(_strlist(args.get("regex_keys"))),
                        Jsonb(_strlist(args.get("character_filter"))),
                        Jsonb(_strlist(args.get("scene_filter"))),
                        int(args["token_budget"]) if args.get("token_budget") is not None else 600,
                        int(args["sticky_turns"]) if args.get("sticky_turns") is not None else 0,
                        int(args["cooldown_turns"]) if args.get("cooldown_turns") is not None else 0,
                        float(args["probability"]) if args.get("probability") is not None else 100.0,
                        str(args.get("insertion_position") or "worldbook"),
                    ),
                ).fetchone()
                new_id = int(new_row["id"])
                try:
                    _write_commit(
                        db, script_id=sid, user_id=user_id, kind="worldbook_add",
                        message=f"新增 worldbook: {str(title).strip()}",
                        payload={"table": "worldbook_entries", "op": "add",
                                 "ids": {"entry_id": new_id}},
                    )
                except Exception:
                    pass
                db.commit()
                _invalidate_worldbook_cache(sid)
                return f"已创建世界书条目 #{new_id}(剧本 #{sid})"
    except ValueError as exc:
        return f"失败: {exc}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


def _invalidate_worldbook_cache(script_id: int) -> None:
    """worldbook 改动后清 constant 层缓存(照 script_edit 的做法)。"""
    try:
        from gm_serving.context_inject import invalidate_constant_cache
        invalidate_constant_cache(script_id)
    except Exception:
        pass


# ────────────────────────────────────────────────────────────────────────────
# 3) update_npc_card — 复用 character_cards.upsert(传 id=card_id)
# ────────────────────────────────────────────────────────────────────────────


def _t_update_npc_card(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    sid = _resolve_sid(script_id, args)
    if sid is None:
        return "失败: script_id 必填"
    card_id = args.get("card_id")
    if not card_id:
        return "失败: card_id 必填(先用 list_script_npcs 拿到角色卡 id)"
    try:
        cid = int(card_id)
    except (TypeError, ValueError):
        return "失败: card_id 必须是整数"
    try:
        from platform_app.db import connect, init_db
        from platform_app.perms import script_owned
        init_db()
        # ② 严格 owner 闸(upsert_character_card 内部用 _require_script_owner 也会再查,
        #    这里前置一道给统一友好失败串)。
        with connect() as db:
            if not script_owned(db, sid, user_id):
                return "失败(权限): 剧本不属于当前用户"
            # 取现有卡作为基底:upsert_character_card 是「全量覆盖式」,缺省字段会被清空,
            # 故先读现卡、再用 args 里出现的字段叠加,避免漏传字段被抹掉。name 是必填(空会报错)。
            existing = db.execute(
                "select * from character_cards where id = %s and script_id = %s and card_type='npc'",
                (cid, sid),
            ).fetchone()
            if not existing:
                return f"失败: 角色卡 #{cid} 不存在或不属于剧本 #{sid}"
        base = dict(existing)
        # 只接受这些字段(不收 avatar_path —— 头像走专用端点)。
        editable = (
            "name", "full_name", "aliases", "identity", "appearance", "personality",
            "speech_style", "current_status", "secrets", "background", "sample_dialogue",
            "tags", "importance", "first_revealed_chapter", "enabled",
        )
        payload: dict[str, Any] = {"id": cid}
        for k in editable:
            if k in args and args[k] is not None:
                payload[k] = args[k]
            elif k in base and base[k] is not None:
                payload[k] = base[k]
        # name 必填:确保有值(取 args 或现卡)。
        if not (str(payload.get("name") or "").strip()):
            return "失败: name 不能为空"
        # tags 不是 character_cards 直接列(存进 metadata),upsert 不读 tags → 落进 metadata。
        if "tags" in payload:
            meta = dict(base.get("metadata") or {})
            meta["tags"] = _strlist(payload.pop("tags"))
            payload["metadata"] = meta
        # ③ 复用 character_cards.upsert(内部 _require_script_owner + Jsonb 化 aliases/sample_dialogue)。
        from platform_app.knowledge.character_cards import upsert_character_card
        upsert_character_card(user_id, sid, payload)
        # 审计
        try:
            from platform_app.api.script_edit import _write_commit
            with connect() as adb:
                if script_owned(adb, sid, user_id):
                    _write_commit(
                        adb, script_id=sid, user_id=user_id, kind="card_edit",
                        message=f"编辑 NPC 角色卡 #{cid}",
                        payload={"table": "character_cards", "op": "edit",
                                 "ids": {"card_id": cid},
                                 "fields": [k for k in editable if args.get(k) is not None]},
                    )
                    adb.commit()
        except Exception:
            pass
        return f"已更新 NPC 角色卡 #{cid}(剧本 #{sid})"
    except ValueError as exc:
        return f"失败: {exc}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


# ────────────────────────────────────────────────────────────────────────────
# 4) update_anchor — keywords 是原生 text[](直接绑 Python list)
# ────────────────────────────────────────────────────────────────────────────


def _t_update_anchor(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    sid = _resolve_sid(script_id, args)
    if sid is None:
        return "失败: script_id 必填"
    anchor_id = args.get("anchor_id")
    if not anchor_id:
        return "失败: anchor_id 必填"
    try:
        aid = int(anchor_id)
    except (TypeError, ValueError):
        return "失败: anchor_id 必须是整数"
    try:
        from platform_app.api.script_edit import _write_commit
        from platform_app.db import connect, init_db
        from platform_app.perms import script_owned
        init_db()
        with connect() as db:
            # ② 严格 owner 闸
            if not script_owned(db, sid, user_id):
                return "失败(权限): 剧本不属于当前用户"
            before = db.execute(
                "select id, story_time_label from script_timeline_anchors "
                "where id = %s and script_id = %s",
                (aid, sid),
            ).fetchone()
            if not before:
                return f"失败: 锚点 #{aid} 不存在或不属于剧本 #{sid}"
            sets, params = [], []
            # story_summary 在 script_timeline_anchors 里列名是 sample_summary;这里直接收 sample_summary。
            for col in ("story_phase", "story_time_label", "sample_title", "sample_summary"):
                if col in args and args[col] is not None:
                    sets.append(f"{col}=%s")
                    params.append(str(args[col]))
            for col in ("chapter_min", "chapter_max"):
                if col in args and args[col] is not None:
                    sets.append(f"{col}=%s")
                    params.append(int(args[col]))
            if args.get("confidence") is not None:
                sets.append("confidence=%s")
                params.append(float(args["confidence"]))
            if "keywords" in args and isinstance(args["keywords"], list):
                # keywords 是 PostgreSQL 原生 text[]:参数直接绑 Python list,
                # psycopg 按数组写回;绝不 Jsonb / json.dumps(那会写坏 text[] 列)。
                sets.append("keywords=%s")
                params.append([str(x) for x in args["keywords"]])
            if not sets:
                return "失败: 没有要更新的字段"
            sets.append("updated_at=now()")
            params.extend([aid, sid])
            db.execute(
                f"update script_timeline_anchors set {', '.join(sets)} "
                f"where id=%s and script_id=%s",
                tuple(params),
            )
            # ③ 审计
            try:
                _write_commit(
                    db, script_id=sid, user_id=user_id, kind="anchor_edit",
                    message=f"编辑 anchor #{aid}",
                    payload={"table": "script_timeline_anchors", "op": "edit",
                             "ids": {"anchor_id": aid}},
                )
            except Exception:
                pass
            db.commit()
        return f"已更新时间线锚点 #{aid}(剧本 #{sid})"
    except ValueError as exc:
        return f"失败: {exc}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


# ────────────────────────────────────────────────────────────────────────────
# 5) upsert_canon_entity — aliases/attrs 是 jsonb;按 logical_key upsert
# ────────────────────────────────────────────────────────────────────────────


def _t_upsert_canon_entity(user_id: int, script_id: int | None, args: dict, state: Any) -> str:
    sid = _resolve_sid(script_id, args)
    if sid is None:
        return "失败: script_id 必填"
    logical_key = (args.get("logical_key") or "")
    logical_key = str(logical_key).strip()
    if not logical_key:
        return "失败: logical_key 必填"
    try:
        from psycopg.types.json import Jsonb

        from platform_app.api.script_edit import _write_commit
        from platform_app.db import connect, init_db
        from platform_app.perms import script_owned
        init_db()
        with connect() as db:
            # ② 严格 owner 闸
            if not script_owned(db, sid, user_id):
                return "失败(权限): 剧本不属于当前用户"
            existing = db.execute(
                "select id from kb_canon_entities where script_id = %s and logical_key = %s",
                (sid, logical_key),
            ).fetchone()

            if existing:
                # ── 更新 ──
                sets, params = [], []
                for col in ("name", "full_name", "type", "summary", "identity",
                            "background", "entity_subtype", "parent_logical_key"):
                    if col in args and args[col] is not None:
                        sets.append(f"{col}=%s")
                        params.append(str(args[col]))
                if args.get("importance") is not None:
                    sets.append("importance=%s")
                    params.append(int(args["importance"]))
                if args.get("first_revealed_chapter") is not None:
                    sets.append("first_revealed_chapter=%s")
                    params.append(int(args["first_revealed_chapter"]))
                if args.get("public_knowledge") is not None:
                    sets.append("public_knowledge=%s")
                    params.append(bool(args["public_knowledge"]))
                # aliases = jsonb 字符串数组;attrs = jsonb 开放对象。
                if "aliases" in args and isinstance(args["aliases"], list):
                    sets.append("aliases=%s")
                    params.append(Jsonb(_strlist(args["aliases"])))
                if "attrs" in args and isinstance(args["attrs"], dict):
                    sets.append("attrs=%s")
                    params.append(Jsonb(args["attrs"]))
                if not sets:
                    return "失败: 没有要更新的字段"
                params.extend([sid, logical_key])
                db.execute(
                    f"update kb_canon_entities set {', '.join(sets)} "
                    f"where script_id=%s and logical_key=%s",
                    tuple(params),
                )
                try:
                    _write_commit(
                        db, script_id=sid, user_id=user_id, kind="canon_edit",
                        message=f"编辑 canon entity: {logical_key}",
                        payload={"table": "kb_canon_entities", "op": "edit",
                                 "ids": {"logical_key": logical_key}},
                    )
                except Exception:
                    pass
                db.commit()
                return f"已更新 canon 实体「{logical_key}」(剧本 #{sid})"
            else:
                # ── 创建 ── name/type 是 NOT NULL,创建时必须给。
                name = str(args.get("name") or "").strip()
                entity_type = str(args.get("type") or "").strip()
                if not name or not entity_type:
                    return "失败: 创建 canon 实体必须提供 name 和 type"
                aliases = args.get("aliases")
                attrs = args.get("attrs")
                new_row = db.execute(
                    """
                    insert into kb_canon_entities
                      (script_id, logical_key, name, full_name, type, summary, identity, background,
                       entity_subtype, parent_logical_key, importance,
                       aliases, attrs, first_revealed_chapter, public_knowledge)
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    on conflict (script_id, logical_key) do nothing
                    returning id
                    """,
                    (
                        sid, logical_key, name,
                        str(args.get("full_name") or ""),
                        entity_type,
                        str(args.get("summary") or ""),
                        str(args.get("identity") or ""),
                        str(args.get("background") or ""),
                        str(args.get("entity_subtype") or ""),
                        str(args.get("parent_logical_key") or ""),
                        int(args["importance"]) if args.get("importance") is not None else 0,
                        Jsonb(_strlist(aliases)) if isinstance(aliases, list) else Jsonb([]),
                        Jsonb(attrs) if isinstance(attrs, dict) else Jsonb({}),
                        int(args["first_revealed_chapter"]) if args.get("first_revealed_chapter") is not None else 0,
                        bool(args["public_knowledge"]) if args.get("public_knowledge") is not None else False,
                    ),
                ).fetchone()
                if not new_row:
                    return f"失败: canon 实体「{logical_key}」已存在(并发创建?)"
                try:
                    _write_commit(
                        db, script_id=sid, user_id=user_id, kind="canon_add",
                        message=f"新增 canon entity: {logical_key}",
                        payload={"table": "kb_canon_entities", "op": "add",
                                 "ids": {"logical_key": logical_key}},
                    )
                except Exception:
                    pass
                db.commit()
                return f"已创建 canon 实体「{logical_key}」(剧本 #{sid})"
    except ValueError as exc:
        return f"失败: {exc}"
    except Exception as exc:
        return f"失败: {type(exc).__name__}: {exc}"


# ────────────────────────────────────────────────────────────────────────────
# 注册
# ────────────────────────────────────────────────────────────────────────────


def register_script_write_tools() -> None:
    registry = get_registry()
    specs: list[ToolSpec] = [
        ToolSpec(
            name="update_script_chapter",
            description=(
                "更新剧本某一章的正文/标题/分卷名(覆盖整章正文,destructive)。"
                "chapter_index 必填;title/content/volume_title 至少传一个。"
                "改前先向用户说清要改哪一章、改成什么。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "chapter_index": {"type": "integer", "description": "章序号(1-based)"},
                    "title": {"type": "string"},
                    "content": {"type": "string", "description": "整章正文(会覆盖原正文)"},
                    "volume_title": {"type": "string"},
                },
                "required": ["chapter_index"],
            },
            executor=_t_update_script_chapter,
            scope="script",
            origins=_SCRIPT_WRITE_ORIGINS,
            destructive=True,
        ),
        ToolSpec(
            name="upsert_worldbook_entry",
            description=(
                "创建或更新世界书条目。传 entry_id = 更新该条目;不传 = 新建(新建需 title)。"
                "keys/regex_keys/character_filter/scene_filter 是字符串数组。"
                "改前先向用户说清要改什么。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "entry_id": {"type": "integer", "description": "有=更新,无=创建"},
                    "title": {"type": "string"},
                    "content": {"type": "string"},
                    "priority": {"type": "integer"},
                    "enabled": {"type": "boolean"},
                    "keys": {"type": "array", "items": {"type": "string"}},
                    "regex_keys": {"type": "array", "items": {"type": "string"}},
                    "character_filter": {"type": "array", "items": {"type": "string"}},
                    "scene_filter": {"type": "array", "items": {"type": "string"}},
                    "token_budget": {"type": "integer"},
                    "sticky_turns": {"type": "integer"},
                    "cooldown_turns": {"type": "integer"},
                    "probability": {"type": "number"},
                    "insertion_position": {"type": "string"},
                },
                "required": ["title"],
            },
            executor=_t_upsert_worldbook_entry,
            scope="script",
            origins=_SCRIPT_WRITE_ORIGINS,
            destructive=False,
        ),
        ToolSpec(
            name="update_npc_card",
            description=(
                "更新剧本内某张 NPC 角色卡。card_id 必填(先用 list_script_npcs 拿 id)。"
                "只传要改的字段(其余保留)。不收 avatar_path(头像走专用端点)。"
                "改前先向用户说清要改什么。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "card_id": {"type": "integer"},
                    "name": {"type": "string"},
                    "full_name": {"type": "string"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "identity": {"type": "string"},
                    "appearance": {"type": "string"},
                    "personality": {"type": "string"},
                    "speech_style": {"type": "string"},
                    "current_status": {"type": "string"},
                    "secrets": {"type": "string"},
                    "background": {"type": "string"},
                    "sample_dialogue": {"type": "array"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "importance": {"type": "integer"},
                    "first_revealed_chapter": {"type": "integer"},
                    "enabled": {"type": "boolean"},
                },
                "required": ["card_id"],
            },
            executor=_t_update_npc_card,
            scope="script",
            origins=_SCRIPT_WRITE_ORIGINS,
            destructive=False,
        ),
        ToolSpec(
            name="update_anchor",
            description=(
                "更新时间线锚点。anchor_id 必填。keywords 是字符串数组。"
                "改前先向用户说清要改什么。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "anchor_id": {"type": "integer"},
                    "story_phase": {"type": "string"},
                    "story_time_label": {"type": "string"},
                    "chapter_min": {"type": "integer"},
                    "chapter_max": {"type": "integer"},
                    "sample_title": {"type": "string"},
                    "sample_summary": {"type": "string"},
                    "confidence": {"type": "number"},
                    "keywords": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["anchor_id"],
            },
            executor=_t_update_anchor,
            scope="script",
            origins=_SCRIPT_WRITE_ORIGINS,
            destructive=False,
        ),
        ToolSpec(
            name="upsert_canon_entity",
            description=(
                "创建或更新 canon 实体(按 logical_key)。logical_key 必填;"
                "创建时还需 name 和 type。aliases 是字符串数组,attrs 是开放对象。"
                "改前先向用户说清要改什么。"
            ),
            input_schema={
                "type": "object",
                "properties": {
                    "logical_key": {"type": "string"},
                    "name": {"type": "string"},
                    "full_name": {"type": "string"},
                    "type": {"type": "string"},
                    "summary": {"type": "string"},
                    "identity": {"type": "string"},
                    "background": {"type": "string"},
                    "entity_subtype": {"type": "string"},
                    "parent_logical_key": {"type": "string"},
                    "aliases": {"type": "array", "items": {"type": "string"}},
                    "attrs": {"type": "object"},
                    "first_revealed_chapter": {"type": "integer"},
                    "public_knowledge": {"type": "boolean"},
                    "importance": {"type": "integer"},
                },
                "required": ["logical_key"],
            },
            executor=_t_upsert_canon_entity,
            scope="script",
            origins=_SCRIPT_WRITE_ORIGINS,
            destructive=False,
        ),
    ]
    for spec in specs:
        if not registry.has(spec.name):
            registry.register(spec)


__all__ = ["register_script_write_tools"]
