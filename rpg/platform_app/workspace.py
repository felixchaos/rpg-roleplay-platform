from __future__ import annotations

import re
from typing import Any

from psycopg.types.json import Jsonb

from core.logging import get_logger
from state import SAVE_FILE
from state.core import _extract_secret_sections, _strip_secret_sections

from . import branches, runtime
from .db import connect, cursor_id, expose, init_db, limit_value, page_payload
from .db import status as db_status
from .security import public_user

log = get_logger(__name__)


def ensure_default(user_id: int) -> None:
    """Maintain existing save runtime state without creating demo content.

    Production users must be able to have an empty script list. Older builds
    seeded a default novel whenever the list was empty, which made successful
    deletes appear to re-create the same script on the next authenticated
    request.
    """
    init_db()
    with connect() as db:
        save = db.execute(
            """
            select gs.*
            from game_saves gs
            join scripts s on s.id = gs.script_id
            where gs.user_id = %s and s.owner_id = %s
            order by gs.id
            limit 1
            """,
            (user_id, user_id),
        ).fetchone()
    # P0 修复:不再自动创建名为「当前自动存档」的引导存档。
    # 自动存档是「每个存档每回合无感提交」的能力,不是一个独立槽位。
    # 新用户进来 saves 列表为空 → 前端走空态 + 引导去建档(用户决策)。
    # 仅当用户已有存档时,补齐分支树 / runtime 指针(兼容存量数据)。
    if not save:
        return
    branches.seed_tree(save["id"], str(SAVE_FILE))
    if not runtime.read_runtime(user_id=user_id):
        with connect() as db:
            active = db.execute("select active_branch_node_id from game_saves where id = %s", (save["id"],)).fetchone()
            node_id = active.get("active_branch_node_id") if active else None
        if node_id:
            branches.activate_node(user_id, int(node_id))


def overview(user: dict | None) -> dict[str, Any]:
    if not user:
        return {"user": None, "auth_required": True, "database": db_status()}
    ensure_default(user["id"])
    with connect() as db:
        # 不要 select *:import_report jsonb 单行可达数 MB(felixchaos 4 个剧本合计 ~4MB,
        # script 11 历史实测单行 65MB),select * 把整列拉出来 + 序列化,实测把 /api/platform
        # 概览拖到 ~3.8s 本地 / 过网络 15s → nginx 超时 503(用户端表现为"加载转圈 30s")。
        # 概览/首页不渲染 import_report(全前端仅 scripts.jsx 详情用,且走 /api/scripts 分页端点,
        # scripts_page 那里也已显式列字段跳过此列)。这里照搬同款显式列清单,O(行数) 不 de-TOAST。
        scripts = db.execute(
            """
            select id, owner_id, title, description, source_path, created_at, updated_at,
                   public_id, row_version, chapter_count, word_count, content_fingerprint,
                   shareable, extracted_through_chapter, extraction_seeded,
                   is_public, published_at, clone_count, review_status, reviewed_at,
                   embed_api_id, embed_model,
                   forked_from_script_id, forked_at_commit_id, sharing_mode,
                   current_pin_script_id, current_pin_commit_id, head_commit_id
            from scripts where owner_id = %s order by updated_at desc, id desc limit 50
            """,
            (user["id"],),
        ).fetchall()
        saves = db.execute("select * from game_saves where user_id = %s order by updated_at desc, id desc limit 50", (user["id"],)).fetchall()
        settings = db.execute("select key, value from settings where user_id = %s", (user["id"],)).fetchall()
        branch_counts = {
            row["save_id"]: row["count"]
            for row in db.execute(
                """
                select n.save_id,
                       sum(
                         case
                           when n.kind = 'gm' and exists (
                             select 1 from branch_commits p
                             where p.id = n.parent_id
                               and p.kind = 'player'
                               and p.turn_index = n.turn_index
                           ) then 0
                           else 1
                         end
                       )::int as count
                from branch_commits n
                where n.save_id in (select id from game_saves where user_id = %s)
                group by n.save_id
                """,
                (user["id"],),
            ).fetchall()
        }
        assets = db.execute("select * from assets where user_id = %s order by id desc limit 20", (user["id"],)).fetchall()
    return {
        "user": public_user(user),
        "database": db_status(),
        "scripts": [expose(row) for row in scripts],
        "saves": [{**expose(row), "branch_count": branch_counts.get(row["id"], 0)} for row in saves],
        "settings": {row["key"]: row["value"] for row in settings},
        "assets": [expose(row) for row in assets],
        "runtime": runtime.read_runtime(user_id=user["id"]),
    }


def create_save(
    user_id: int,
    script_id: int,
    title: str,
    new_card: dict[str, Any] | None = None,
    character: dict[str, Any] | None = None,
    *,
    birthpoint: dict[str, Any] | None = None,
    identity: dict[str, Any] | None = None,
    story_intent: str | None = None,
    player_origin: str | None = None,
    identity_known: bool | None = None,
) -> dict[str, Any]:
    """创建新存档。

    task 29：原来只用 GameState.new() 的空白快照，UI 填的 new_card.{name,role,background}
    全部丢失，state_snapshot.player 始终空字符串。这里支持把 new_card / character
    应用到初始 state，再写库；branches.seed_tree() 由 task 25 修复后会信任
    state_snapshot 字段，所以 root commit 自动同步。

    new_card  = {"name": str, "role": str, "background": str}  —— UI「新建角色卡」分支
    character = {"kind": "persona"|"user_card"|"script_card", "id"|"slug": ...}
                —— UI「使用现有」分支，留作扩展，本次先 best-effort 取 name/role/background
    birthpoint = {"phase_label": str, "anchor_id": int, "chapter_min": int,
                  "chapter_max": int, "story_time_label": str}
                 —— 入场选出生点，写入 world.timeline / world.time
    identity  = {"name"?: str, "role"?: str, "background"?: str, "source"?: "custom"|"ai"}
                 —— v27: 可选「身份卡」overlay。**不** 覆盖 player.name/role(那些来自角色卡)。
                 落库:insert identity_cards 行 + save_character_identities 绑定;
                 运行时副本写到 player.identity overlay + player.identity_role_desc(兼容字段)。
                 留空(或全字段都空)= 没身份,直接用角色卡。

    无 new_card / character / birthpoint / identity 时退回到旧行为（空白快照）。
    """
    init_db()
    with connect() as db:
        # task 74: 接受 owner OR subscriber(公开剧本订阅)— 存档是 per-user 的活态层,
        # 不影响剧本本身的 immutability,所以订阅者也能建存档
        script = db.execute(
            """
            select s.* from scripts s
            where s.id = %s and (
              s.owner_id = %s
              or s.id in (select script_id from user_script_subscriptions where user_id = %s)
            )
            """,
            (script_id, user_id, user_id),
        ).fetchone()
        if not script:
            raise ValueError("无权访问该剧本")
        # 复核闸:KB 提取/复核未完成的剧本不允许开局,避免 GM 拿到错章节/未审实体/未消歧别名
        # 直接喂玩家(治"复核机制完全孤立于导入流程"的架构裂缝)。重切后会自动回 unreviewed。
        if (script.get("review_status") or "unreviewed") == "unreviewed":
            raise ValueError("剧本尚未通过 KB 复核,请先在剧本复核页 (script-review) 检查实体/时间线/世界观无误后点击「标记已复核」")
        snapshot = _build_initial_snapshot(user_id, script_id, new_card, character, birthpoint=birthpoint, identity=identity, story_intent=story_intent, player_origin=player_origin, identity_known=identity_known)
        save = db.execute(
            """
            insert into game_saves(user_id, script_id, title, state_path, state_snapshot)
            values (%s, %s, %s, %s, %s)
            returning *
            """,
            (user_id, script_id, title.strip() or "新存档", str(SAVE_FILE), Jsonb(snapshot)),
        ).fetchone()
        # v27: 持久化身份卡 + 角色↔身份绑定。身份卡是 save 级独立实体,不修改角色卡行。
        # 注意:仅当 identity 至少有 role 或 background 才落库;否则视为"没身份,直接用角色卡"。
        if isinstance(identity, dict):
            id_name = str(identity.get("name") or "").strip()
            id_role = str(identity.get("role") or "").strip()
            id_bg = str(identity.get("background") or "").strip()
            id_source = str(identity.get("source") or "custom").strip() or "custom"
            # 反馈#1:npc_card = 主角占用某原著 NPC 的失忆身份(provenance,与 ai/custom 并列)
            if id_source not in ("custom", "ai", "npc_card"):
                id_source = "custom"
            if id_role or id_bg or id_name:
                ic_row = db.execute(
                    """
                    insert into identity_cards(save_id, name, role, background, source)
                    values (%s, %s, %s, %s, %s)
                    returning id
                    """,
                    (save["id"], id_name, id_role, id_bg, id_source),
                ).fetchone()
                identity_card_id = int(ic_row["id"]) if ic_row else None
                if identity_card_id is not None:
                    # 把 id 回写到 state_snapshot.player.identity.id,便于运行时定位 canonical 行
                    try:
                        snap = save.get("state_snapshot") or {}
                        if isinstance(snap, dict):
                            player = snap.setdefault("player", {})
                            overlay = player.setdefault("identity", {})
                            overlay["id"] = identity_card_id
                            db.execute(
                                "update game_saves set state_snapshot = %s where id = %s",
                                (Jsonb(snap), save["id"]),
                            )
                            save["state_snapshot"] = snap
                    except Exception:
                        pass
                    # 角色↔身份绑定。character_ref 取所选角色卡的 id/slug;若是新建分支
                    # (new_card),写占位 'inline' 表示就地新建角色,身份直接挂存档。
                    char_kind = ""
                    char_ref = ""
                    if isinstance(new_card, dict):
                        char_kind = "new_card"
                        char_ref = "inline"
                    elif isinstance(character, dict):
                        char_kind = str(character.get("kind") or "").strip()
                        cid = character.get("id")
                        if cid is not None:
                            char_ref = str(cid)
                        else:
                            char_ref = str(character.get("slug") or "")
                    if char_kind and char_ref:
                        try:
                            db.execute(
                                """
                                insert into save_character_identities
                                  (save_id, character_kind, character_ref, identity_id, is_current)
                                values (%s, %s, %s, %s, true)
                                """,
                                (save["id"], char_kind, char_ref, identity_card_id),
                            )
                        except Exception as _bind_err:
                            log.warning(
                                f"[identity] bind failed save={save['id']} "
                                f"char={char_kind}:{char_ref} id={identity_card_id}: {_bind_err}"
                            )
    branches.seed_tree(save["id"], str(SAVE_FILE))
    # task 136: 新存档创建后异步 seed 世界线收束锚点。
    # 800 章 × 5 events 量级,放后台不阻塞 UI;失败也不影响存档创建。
    #
    # 注意 (task 141 修正):**不**默认调 claim_protagonist_pov。
    # isekai 语义是「玩家的现代灵魂 + 用户自创角色卡的肉身,与原作主角【平行共存】」,
    # 不是「玩家接管原作主角 POV」。原作主角应作为独立 NPC 触发其登场 anchor,
    # 玩家(用户角色卡)在同一场景平行加入。两人可能在 ch1 相遇,但不是同一个人。
    # claim_protagonist_pov 工具保留,但只在玩家显式声明"我就是 X"时由 GM 主动调。
    try:
        import threading

        from agents.anchor_seed_agent import seed_anchors_for_save
        _save_id_for_seed = save["id"]
        def _bg_seed():
            try:
                res = seed_anchors_for_save(_save_id_for_seed)
                log.info(f"[anchor_seed] save={_save_id_for_seed} result={res}")
            except Exception as exc:
                log.error(f"[anchor_seed] save={_save_id_for_seed} failed: {type(exc).__name__}: {exc}")
        threading.Thread(target=_bg_seed, daemon=True, name=f"anchor-seed-{save['id']}").start()
    except Exception as _seed_err:
        log.error(f"[anchor_seed] schedule failed save={save['id']}: {_seed_err}")
    return expose(save)  # type: ignore[return-value]


def _ingest_character_book(save_id: int, character_book: Any) -> int:
    """SillyTavern 角色卡内嵌世界书(character_book)→ save 级 worldbook overlay(决策3)。

    复用现有 save_worldbook_overlays(kind='addition')基建 —— 检索侧
    retrieval._load_worldbook_for_retrieval 会自动把它纳入(priority 高的恒进),
    与剧本无关、save 级,正好契合无剧本的酒馆存档。返回写入条目数。
    """
    if not isinstance(character_book, dict):
        return 0
    entries = character_book.get("entries")
    if not isinstance(entries, list) or not entries:
        return 0
    rows: list[tuple] = []
    for e in entries:
        if not isinstance(e, dict):
            continue
        if e.get("enabled") is False:
            continue
        content = str(e.get("content") or "").strip()
        if not content:
            continue
        keys = e.get("keys") or e.get("key") or []
        if isinstance(keys, str):
            keys = [keys]
        keys = [str(k).strip() for k in keys if str(k).strip()][:32]
        title = (str(e.get("comment") or e.get("name") or (keys[0] if keys else "") or "世界书条目")).strip()[:200]
        # SillyTavern 的 priority 越大越优先(与我们一致);缺省给 60(高于普通 50,
        # 低于角色卡高优先级层),让角色专属设定较易命中检索。
        try:
            priority = int(e.get("priority") if e.get("priority") is not None else 60)
        except (TypeError, ValueError):
            priority = 60
        rows.append((int(save_id), title, content[:16000], Jsonb(keys), priority, 0))
    if not rows:
        return 0
    with connect() as db:
        for r in rows:
            db.execute(
                """
                insert into save_worldbook_overlays
                  (save_id, kind, title, content, keys, priority, introduced_turn)
                values (%s, 'addition', %s, %s, %s, %s, %s)
                """,
                r,
            )
    return len(rows)


def create_tavern_save(
    user_id: int,
    character_card_id: int | None,
    *,
    persona_card_id: int | None = None,
    title: str | None = None,
) -> dict[str, Any]:
    """创建酒馆模式存档(无剧本):玩家与所选 AI 角色卡 1:1 对话。

    复用 game_saves(save_kind='tavern', script_id=NULL)+ branch_commits/messages/
    advisory-lock 单写者全套基建。写入的 state 形状供 TavernCharacterProvider
    (context_providers/tavern.py)与 master._SYSTEM_TAVERN 消费;
    content_pack=DEFAULT_TAVERN_MANIFEST 让整条 GM 管线无剧本运行。
    路由层(routes/tavern.py)创建后再 activate_save 绑定 runtime。

    酒馆 v2(决策1):character_card_id 可为 None —— 空起手对话,不预设角色,由 agent
    在对话中用 set_tavern_character 工具自举。此时 tavern.character={},无 first_mes 开场,
    tavern_character_card_id 列保持 NULL(本就 nullable)。
    """
    init_db()
    import copy as _copy

    from context_providers.registry import DEFAULT_TAVERN_MANIFEST

    from . import user_cards as _ucards

    card: dict[str, Any] | None = None
    meta: dict[str, Any] = {}
    if character_card_id is not None:
        card = _ucards.get_user_card(user_id, int(character_card_id))
        if not card:
            raise ValueError("找不到该角色卡(需 card_type='pc' 且属于当前用户)")
        meta = card.get("metadata") or {}

    # —— persona:显式 → 默认 persona → inline 占位 ——
    persona_fields: dict[str, Any] = {}
    resolved_persona_id: int | None = None

    def _persona_to_fields(p: dict) -> dict:
        return {
            "name": (p.get("name") or "你"),
            "role": (p.get("role") or ""),
            "background": (p.get("background") or ""),
            "appearance": (p.get("appearance") or ""),
        }

    if persona_card_id is not None:
        p = _ucards.get_persona(user_id, int(persona_card_id))
        if p:
            persona_fields = _persona_to_fields(p)
            resolved_persona_id = int(persona_card_id)
    if not persona_fields:
        try:
            personas = _ucards.list_personas(user_id).get("items", [])
            default_p = next((p for p in personas if p.get("is_default")), None) or (personas[0] if personas else None)
            if default_p:
                persona_fields = _persona_to_fields(default_p)
                resolved_persona_id = int(default_p["id"]) if default_p.get("id") else None
        except Exception:
            pass
    if not persona_fields:
        persona_fields = {"name": "你"}

    # 空起手:character={};否则投影角色卡字段
    if card is not None:
        character_snapshot: dict[str, Any] = {
            "name": card.get("name") or "角色",
            "identity": card.get("identity") or "",
            "background": card.get("background") or "",
            "appearance": card.get("appearance") or "",
            "personality": card.get("personality") or "",
            "speech_style": card.get("speech_style") or "",
            "current_status": card.get("current_status") or "",
            "sample_dialogue": card.get("sample_dialogue") or [],
        }
    else:
        character_snapshot = {}

    # —— 初始 snapshot ——
    try:
        from state import GameState
        snapshot: dict[str, Any] = GameState.new().data
    except Exception:
        snapshot = {"history": [], "turn": 0}
    snapshot["content_pack"] = _copy.deepcopy(DEFAULT_TAVERN_MANIFEST)
    snapshot["player"] = {**(snapshot.get("player") or {}), **persona_fields}
    snapshot["tavern"] = {
        "character_card_id": int(character_card_id) if character_card_id is not None else None,
        "persona_card_id": resolved_persona_id,
        "character": character_snapshot,
        "system_prompt": str(meta.get("system_prompt") or ""),
        "post_history_instructions": str(meta.get("post_history_instructions") or ""),
        "scenario": str(meta.get("scenario") or ""),
        "alternate_greetings": meta.get("alternate_greetings") or [],
        # 酒馆 v2(R2):本对话绑定的剧本 id(None=纯净无剧本)
        "bound_script_id": None,
    }
    # first_mes → 开场 assistant 消息(seed_tree 会把它落成 turn-1 round commit,player_input 为空)
    # 空起手无角色 → 无 first_mes 开场。
    first_mes = str(meta.get("first_mes") or "").strip() if card is not None else ""
    if first_mes:
        hist = list(snapshot.get("history") or [])
        hist.append({"role": "assistant", "content": first_mes})
        snapshot["history"] = hist

    if card is not None:
        save_title = (title or "").strip() or f"与 {character_snapshot.get('name') or '角色'} 的对话"
    else:
        save_title = (title or "").strip() or "新对话"

    with connect() as db:
        save = db.execute(
            """
            insert into game_saves(user_id, script_id, title, state_path, state_snapshot,
                                   save_kind, tavern_character_card_id, tavern_persona_card_id)
            values (%s, NULL, %s, %s, %s, 'tavern', %s, %s)
            returning *
            """,
            (user_id, save_title, str(SAVE_FILE), Jsonb(snapshot),
             int(character_card_id) if character_card_id is not None else None,
             resolved_persona_id),
        ).fetchone()
    branches.seed_tree(save["id"], str(SAVE_FILE))
    # 决策3:角色卡内嵌世界书 → save 级 worldbook overlay(仅有角色卡时)
    if card is not None:
        try:
            n = _ingest_character_book(save["id"], meta.get("character_book"))
            if n:
                log.info(f"[tavern] save={save['id']} ingested {n} character_book entries → worldbook overlay")
        except Exception as exc:
            log.warning(f"[tavern] character_book ingest failed save={save['id']}: {type(exc).__name__}: {exc}")
    return expose(save)  # type: ignore[return-value]


def _build_initial_snapshot(
    user_id: int,
    script_id: int,
    new_card: dict[str, Any] | None,
    character: dict[str, Any] | None,
    *,
    birthpoint: dict[str, Any] | None = None,
    identity: dict[str, Any] | None = None,
    story_intent: str | None = None,
    player_origin: str | None = None,
    identity_known: bool | None = None,
) -> dict[str, Any]:
    """根据 UI 选择构造新存档的初始 state。任何异常退到空白快照。"""
    try:
        from state import GameState
        state = GameState.new()
    except Exception:
        return {"history": [], "turn": 0}

    name = role = background = ""
    # task 91: 没传 new_card/character 时,默认拿用户的"默认 persona",
    # 没有就回退到最近的 user_character_card。避免新建存档总是空玩家。
    if not isinstance(new_card, dict) and not isinstance(character, dict):
        try:
            from . import user_cards as _ucards
            personas = _ucards.list_personas(user_id).get("items", [])
            default_p = next((p for p in personas if p.get("is_default")), None) or (personas[0] if personas else None)
            if default_p:
                character = {"kind": "persona", "id": default_p.get("id")}
            else:
                cards = _ucards.list_user_cards(user_id).get("items", [])
                if cards:
                    character = {"kind": "user_card", "id": cards[0].get("id")}
        except Exception:
            pass
    # task 137: 详细角色卡字段（外貌/性格/语气/别名），setup_player 后再单独写入
    # task 138: secrets 字段 *不再* 放入 player namespace,改成 _extra_private 收集到
    # player_private.secrets。同时把 personality/appearance/background 里的 ## 秘密 段
    # 用 _extract_secret_sections 抽走 → player_private.secrets;原字段用 _strip_secret_sections
    # 剥离后保留 NPC 可观察部分。这样 short_summary 注入 GM prompt 时秘密物理上不存在。
    _extra_card_fields: dict[str, str] = {}
    _extra_private_secrets: list[str] = []

    def _absorb_card_secrets(card: dict[str, Any]) -> None:
        """从一张 user_card / script_card / persona dict 抽出秘密 → _extra_private_secrets,
        同时把 personality/appearance/background 里的秘密段 strip 后写回 _extra_card_fields。
        通用底座 — 不依赖具体剧本字段名。"""
        # 1. 直接的 secrets 字段(角色卡 schema 里独立的"秘密"字段)→ player_private
        _sec_raw = str(card.get("secrets") or "").strip()
        if _sec_raw and _sec_raw not in _extra_private_secrets:
            _extra_private_secrets.append(_sec_raw)
        # 2. personality / appearance / background 里嵌入的 ## 秘密 / ## 隐藏 / ## 元知识 …段
        #    → 抽到 player_private.secrets, 原字段保留 strip 后的剩余
        for _f in ("appearance", "personality", "background"):
            _v = str(card.get(_f) or "").strip()
            if not _v:
                continue
            _hidden_sections = _extract_secret_sections(_v)
            for _h in _hidden_sections:
                if _h and _h not in _extra_private_secrets:
                    _extra_private_secrets.append(_h)
            _stripped = _strip_secret_sections(_v)
            if _stripped:
                _extra_card_fields[_f] = _stripped
        # 3. speech_style / aliases 一般 NPC 可观察,直接保留
        for _f in ("speech_style", "aliases"):
            _v = str(card.get(_f) or "").strip()
            if _v:
                _extra_card_fields[_f] = _v

    if isinstance(new_card, dict):
        name = str(new_card.get("name") or "").strip()
        role = str(new_card.get("role") or "").strip()
        # background 也 strip 一遍秘密段(玩家在向导里手填可能也写 ## 秘密)
        _new_bg = str(new_card.get("background") or "").strip()
        if _new_bg:
            for _h in _extract_secret_sections(_new_bg):
                if _h and _h not in _extra_private_secrets:
                    _extra_private_secrets.append(_h)
            background = _strip_secret_sections(_new_bg)
        else:
            background = ""
    elif isinstance(character, dict):
        # best-effort：从已有 persona / character card 取 name + role + background
        kind = str(character.get("kind") or "").strip()
        cid = character.get("id")
        try:
            cid_int = int(cid) if cid is not None else None
        except (TypeError, ValueError):
            cid_int = None
        if cid_int is not None:
            try:
                if kind == "persona":
                    from . import user_cards as _ucards
                    p = _ucards.get_persona(user_id, cid_int) or {}
                    name = str(p.get("name") or "").strip()
                    role = str(p.get("role") or "").strip()
                    _p_bg = str(p.get("background") or "").strip()
                    for _h in _extract_secret_sections(_p_bg):
                        if _h and _h not in _extra_private_secrets:
                            _extra_private_secrets.append(_h)
                    background = _strip_secret_sections(_p_bg) if _p_bg else ""
                elif kind == "user_card":
                    from . import user_cards as _ucards
                    c = _ucards.get_user_card(user_id, cid_int) or {}
                    name = str(c.get("name") or "").strip()
                    role = str(c.get("identity") or "").strip()
                    # background 优先取 personality（详细设定），其次 appearance
                    _bg_src = str(c.get("personality") or c.get("appearance") or "").strip()
                    background = _strip_secret_sections(_bg_src) if _bg_src else ""
                    _absorb_card_secrets(c)
                elif kind == "script_card":
                    from . import knowledge as _know
                    c = _know.get_character_card(user_id, script_id, cid_int) or {}
                    name = str(c.get("name") or "").strip()
                    role = str(c.get("identity") or "").strip()
                    _bg_src = str(c.get("personality") or c.get("appearance") or "").strip()
                    background = _strip_secret_sections(_bg_src) if _bg_src else ""
                    _absorb_card_secrets(c)
                    # task 114: LLM 经常用 script_card_id 传了 user_card_id (混淆),
                    # 找不到时自动兜底到 user_card 表 — 因为 user_card 跨 script 共享,
                    # 给空白 player 强过让用户开局看到 "—"。
                    if not name:
                        from . import user_cards as _ucards
                        uc = _ucards.get_user_card(user_id, cid_int) or {}
                        if uc:
                            name = str(uc.get("name") or "").strip()
                            role = str(uc.get("identity") or "").strip()
                            _bg_src = str(uc.get("personality") or uc.get("appearance") or "").strip()
                            background = _strip_secret_sections(_bg_src) if _bg_src else ""
                            _absorb_card_secrets(uc)
            except Exception:
                pass

    if name or role or background:
        try:
            state.setup_player(name or "无名者", role or "未指定", background or "（无背景）")
        except Exception:
            pass
        # #6 代入去重: script_card = 玩家代入原作已有角色。登记 player↔NPC 绑定
        # (player.aliases + player_meta.pov_replaces + bound_npc_card_id),让 GM 上下文
        # (_active_character_cards)把这张同名 NPC 卡视为玩家本人、不再当独立 NPC 注入,
        # 消除"代入原作角色后出现两个相同角色"。用函数参数 character 重判(局部 kind/cid 此处不在作用域)。
        if isinstance(character, dict) and str(character.get("kind") or "").strip() == "script_card" and name:
            try:
                _pdata = getattr(state, "data", state)
                _player = _pdata.setdefault("player", {})
                _al = _player.get("aliases") or []
                if isinstance(_al, str):
                    _al = [a.strip() for a in _al.split(",") if a.strip()]
                if name not in _al:
                    _al.append(name)
                _player["aliases"] = _al
                try:
                    _player["bound_npc_card_id"] = int(character.get("id")) if character.get("id") is not None else None
                except (TypeError, ValueError):
                    pass
                _meta = _pdata.setdefault("player_meta", {})
                _pov = _meta.get("pov_replaces") or []
                if name not in _pov:
                    _pov.append(name)
                _meta["pov_replaces"] = _pov
            except Exception:
                pass

    # task 137: 把详细角色卡字段写入 state.data["player"]（供 short_summary → GM 读取）
    # task 138: 已经在 _absorb_card_secrets 里 strip 过秘密段,_extra_card_fields 里
    # 只剩 NPC 可观察部分;secrets 字段不再被 _absorb 收集进 _extra_card_fields,
    # 不会污染 player namespace。
    if _extra_card_fields:
        try:
            player = state.data.setdefault("player", {})
            for _f, _v in _extra_card_fields.items():
                player[_f] = _v
        except Exception:
            pass

    # task 138: 把从角色卡 secrets 字段 + ## 秘密 段抽出来的内容写到 player_private.secrets。
    # player_private namespace 永远不进 GM system prompt(short_summary 显式排除)。
    if _extra_private_secrets:
        try:
            pp = state.data.setdefault("player_private", {})
            sec_list = pp.setdefault("secrets", [])
            for _s in _extra_private_secrets:
                if _s and _s not in sec_list:
                    sec_list.append(_s)
        except Exception:
            pass

    # task 34：DEFAULT_STATE 是 MuMuAINovel 柏林剧情的硬编码（time=图卢兹失守后翌日，柏林、
    # current_location=柏林哈布斯堡庄园附近、known_events=宴会/图卢兹/蛇信、
    # current_objective=观察柏林局势...）。从导入剧本创建 save 时必须用 script 的首章覆盖，
    # 否则用户看到的开场是别人剧本的状态。
    try:
        _apply_script_opening(state, user_id, script_id)
    except Exception:
        # 任何解析失败都不应该让 create_save 整个崩；退到 user/角色卡已写入的最小可玩 state。
        pass

    # 入场选出生点：覆盖 world.timeline / world.time（优先级高于 _apply_script_opening）
    if isinstance(birthpoint, dict):
        try:
            phase_label = str(birthpoint.get("phase_label") or "").strip()
            story_time_label = str(birthpoint.get("story_time_label") or "").strip()
            chapter_min = birthpoint.get("chapter_min")
            chapter_max = birthpoint.get("chapter_max")
            world = state.data.setdefault("world", {})
            timeline = world.setdefault("timeline", {})
            if phase_label:
                timeline["current_phase"] = phase_label
            if story_time_label:
                world["time"] = story_time_label
                timeline["current_label"] = story_time_label
            if chapter_min is not None and chapter_max is not None:
                timeline["anchor_chapter_range"] = [int(chapter_min), int(chapter_max)]
        except Exception:
            pass

    # v27: 入场初始身份。身份卡是「角色卡之上的定位 overlay」,*不再覆盖* player.name/role —
    # 那两个字段永远来自角色卡(身份脱离角色,无关具体人物)。
    # - identity.role/background → state_snapshot.player.identity 子对象 (overlay 运行时副本) +
    #   player.identity_role_desc (state/core short_summary 读的旧字段,保持兼容)
    # - identity.name 视为「代号/化名」,仅作为 identity.name_label 记录;玩家姓名仍是角色卡名字
    # - 没传 identity 时,player 完全等同于角色卡(用户明确接受此默认)
    # - 数据库侧:身份卡作为独立行存进 identity_cards 表 + save_character_identities 绑定,
    #   见 create_save() 在 insert game_saves 之后的处理
    if isinstance(identity, dict):
        try:
            id_name_label = str(identity.get("name") or "").strip()
            id_role = str(identity.get("role") or "").strip()
            id_background = str(identity.get("background") or "").strip()
            id_source = str(identity.get("source") or "custom").strip() or "custom"
            player = state.data.setdefault("player", {})
            overlay: dict[str, Any] = {
                "name_label": id_name_label,
                "role": id_role,
                "background": id_background,
                "source": id_source,
            }
            player["identity"] = overlay
            if id_background:
                player["identity_role_desc"] = id_background
        except Exception:
            pass

    # 兜底:角色卡也没传时占位(保留旧行为,避免下游 NPE)
    try:
        player = state.data.setdefault("player", {})
        if not player.get("name"):
            player["name"] = "无名者"
        if not player.get("role"):
            player["role"] = "未指定"
        if not player.get("background"):
            player["background"] = "（无背景）"
    except Exception:
        pass

    if story_intent:
        try:
            from datetime import datetime as _dt
            # task 138: 主存到 player_private.story_intent(NPC / GM 看不到)。
            # user_variables.story_intent 仍写一份保留旧代码 dual-read 兼容,但 short_summary
            # 已经显式跳过该 key,不会注入到 GM prompt。后续把这条 dual-write 删掉前,
            # 任何读 worldline.user_variables.story_intent 的地方应该改读 player_private.story_intent。
            pp = state.data.setdefault("player_private", {})
            pp["story_intent"] = str(story_intent)
            variables = state.data.setdefault("worldline", {}).setdefault("user_variables", {})
            variables["story_intent"] = {
                "value": story_intent,
                "source": "user:new_game_wizard",
                "locked": False,
                "turn": 0,
                "updated_at": _dt.now().isoformat(timespec="seconds"),
            }
        except Exception:
            pass

    # player_origin: 玩家定位类型 (isekai = 穿越者 / native = 原作角色)。
    # 持久化到 state.player.player_origin,GM context provider 据此注入「穿越者特殊规则」,
    # 前端 status panel 显示穿越者徽章。saves wizard 显式提供,与身份卡 overlay 正交。
    # 出身 4 档:soul(魂穿)/body(肉穿)/dual(一体双魂)/native(彻底扮演)。旧值 isekai→soul 兼容。
    _po = str(player_origin or "").lower()
    if _po == "isekai":
        _po = "soul"
    if _po in ("soul", "body", "dual", "native"):
        _pnode = state.data.setdefault("player", {})
        _pnode["player_origin"] = _po
        # identity_known 只在实际挂了身份卡时有意义。
        if _po != "body" and isinstance(identity, dict) and isinstance(identity_known, bool):
            _pnode["identity_known"] = identity_known

    # Bug 5 fix: 新建存档时把用户偏好里的 perm.default_mode 注入 state.permissions.mode。
    # 偏好由 settings.jsx PermSection 通过 save("default_mode", val) 写入
    # user_preferences 表，key="perm.default_mode"。
    # 若无偏好或读取失败，保留 GameState.new() 的默认值（"review"）。
    try:
        with connect() as _pdb:
            _pref_row = _pdb.execute(
                "select preferences from user_preferences where user_id = %s",
                (user_id,),
            ).fetchone()
        _prefs = dict(_pref_row["preferences"]) if _pref_row else {}
        _default_mode = _prefs.get("perm.default_mode") or _prefs.get("default_perm_mode")
        _VALID_MODES = {"default", "review", "full_access"}
        if _default_mode and _default_mode in _VALID_MODES:
            state.data.setdefault("permissions", {})["mode"] = _default_mode
    except Exception:
        pass

    return state.data


# task 34 + task 40：从首章内容解析的几个 inline 元数据正则。
# 真实导入后 chapter_splitter.clean_text 会把换行折叠成空格，所以正则不能再要求 ^...$ 行起止。
# 形态示例（一行内连缀）："...灯塔。  当前地点：雾港码头。 当前目标：确认...灯塔星门。 时间锚点：申时三刻。"
# 用 [^。\n；;]+ 直到下一个句号/换行/分号作为 value 边界。
_OPENING_LOCATION_RE = re.compile(r"(?:当前地点|地点)\s*[:：]\s*([^。\n；;]+)")
_OPENING_OBJECTIVE_RE = re.compile(r"(?:当前目标|主线目标|目标)\s*[:：]\s*([^。\n；;]+)")
_OPENING_TIME_RE = re.compile(r"(?:时间锚点|时刻|时间)\s*[:：]\s*([^。\n；;]+)")


def _is_doc_title_only(content: str, title: str) -> bool:
    """判断这一章是不是『纯文档总标题 / 空内容 / 只复述标题』形态。"""
    c = (content or "").strip()
    if not c:
        return True
    if len(c) < 4:
        return True
    # 去掉 markdown # 标记，只比较剩余文字
    t = re.sub(r"^#+\s*", "", (title or "")).strip()
    bare = re.sub(r"^#+\s*", "", c).strip()
    if t and bare == t:
        return True
    return False


def _has_opening_meta(content: str) -> bool:
    """是否含至少一项 inline 元数据 (当前地点 / 当前目标 / 时间锚点) 之一。"""
    if not content:
        return False
    return bool(
        _OPENING_LOCATION_RE.search(content)
        or _OPENING_OBJECTIVE_RE.search(content)
        or _OPENING_TIME_RE.search(content)
    )


def _apply_script_opening(state: Any, user_id: int, script_id: int) -> None:
    """从 script_chapters 找『真实首章』（不是文档总标题/空前言），把 inline 元数据填到 state：
       当前地点 → player.current_location, world (location 同步)
       当前目标 → memory.current_objective
       时间锚点 → world.time + world.timeline (走 state.update_time，会刷 phase/anchor)
       known_events → 用首章 title + 首两行非元数据正文摘要替换默认柏林事件
       last_retrieval → 首章正文前 ~400 字作为初始检索预览
    一旦走到这里（用户从某 script 创建 save），就一定 scrub DEFAULT_STATE 里 MuMuAINovel
    柏林剧情的硬编码（柏林/图卢兹/哈布斯堡/蛇信/...），避免跨剧本污染——不论是否找到有效首章。

    task 40 修复：真实 markdown 导入后 chapter_index=1 常常是 `# 文档总标题` 单行
    （word_count=0、content=""），第 2 章才是 `## 第一章 雾港入夜` 含正文+inline meta。
    所以这里不能只 limit 1，要扫前 N 章选第一个『有 inline meta 或显著正文』的章节。
    """
    # 任何 save（不论 script 有无导入章节）都先 scrub DEFAULT_STATE 的柏林硬编码：
    # 用户选择了某个 script（不论是 5E 模组容器还是空白容器），就不该再继承默认小说
    # 的开场地点/事件/目标。原代码把 scrub 放在 `if not rows: return` 之后，导致 chapter_count=0
    # 的 script（例如 5E 模组容器）创建的新存档全部带柏林污染。
    _scrub_berlin_default(state)

    with connect() as db:
        rows = db.execute(
            """
            select chapter_index, title, content
            from script_chapters
            where script_id = %s
            order by chapter_index asc
            limit 10
            """,
            (script_id,),
        ).fetchall()
    if not rows:
        return

    # task 40：选第一个『有 inline meta』的章节；没有 meta 时退到第一个『显著正文』章节
    chosen = None
    for row in rows:
        c = str(row.get("content") or "")
        if _is_doc_title_only(c, str(row.get("title") or "")):
            continue
        if _has_opening_meta(c):
            chosen = row
            break
    if chosen is None:
        for row in rows:
            c = str(row.get("content") or "").strip()
            if _is_doc_title_only(c, str(row.get("title") or "")):
                continue
            if len(c) >= 40:
                chosen = row
                break

    world = state.data.setdefault("world", {})
    memory = state.data.setdefault("memory", {})

    if chosen is None:
        # 全部章节都是空 / 总标题：至少用第一条 title 作为 opening 事件
        first = rows[0]
        first_title = str(first.get("title") or "").strip()
        if first_title:
            # 去掉 markdown # 前缀，让 event 文本干净
            ev_title = re.sub(r"^#+\s*", "", first_title).strip()
            world["known_events"] = [f"开场：{ev_title}"] if ev_title else []
        return

    title = str(chosen.get("title") or "").strip()
    content = str(chosen.get("content") or "")
    # 去掉 markdown # 前缀（"## 第一章 雾港入夜" → "第一章 雾港入夜"）
    title_clean = re.sub(r"^#+\s*", "", title).strip()

    # 1) 解析三类 inline 元数据
    loc_m = _OPENING_LOCATION_RE.search(content)
    obj_m = _OPENING_OBJECTIVE_RE.search(content)
    time_m = _OPENING_TIME_RE.search(content)
    loc = (loc_m.group(1).strip() if loc_m else "")
    obj = (obj_m.group(1).strip() if obj_m else "")
    tm = (time_m.group(1).strip() if time_m else "")

    # 2) 写回 state
    if loc:
        try:
            state.update_location(loc)
        except Exception:
            state.data.setdefault("player", {})["current_location"] = loc
    if tm:
        try:
            state.update_time(tm, source="script_opening")
            tl = state.data.get("world", {}).get("timeline", {})
            if isinstance(tl, dict):
                tl["last_transition"] = None
        except Exception:
            state.data.setdefault("world", {})["time"] = tm

    if obj:
        memory["current_objective"] = obj

    # 3) known_events：『开场：<标题>』+ 首两段去元数据后的正文摘要
    # 真实 import 把换行折叠成空格 → 不能按行切，按句号切，过滤掉以"当前地点/当前目标/时间锚点"开头的句子
    sentences = [s.strip() for s in re.split(r"[。\n]+", content) if s.strip()]
    body_sents = [
        s for s in sentences
        if not re.match(r"^(?:当前地点|地点|当前目标|主线目标|目标|时间锚点|时刻|时间)\s*[:：]", s)
    ]
    events: list[str] = []
    if title_clean:
        events.append(f"开场：{title_clean}")
    for s in body_sents[:2]:
        events.append(s if len(s) <= 80 else (s[:77] + "…"))
    if events:
        world["known_events"] = events  # 整段替换

    # 4) last_retrieval：首章前 ~400 字给检索面板/上下文做初始预览
    snippet = content.strip()
    if len(snippet) > 400:
        snippet = snippet[:400].rstrip() + "…"
    memory["last_retrieval"] = (
        f"=== 剧本开场 · {title_clean or '第1章'} ===\n{snippet}"
        if snippet else memory.get("last_retrieval", "")
    )


# task 34：DEFAULT_STATE 是 MuMuAINovel 柏林剧情，从其他剧本创建新 save 时必须清掉
# 这些硬编码，避免新存档里出现 上个剧本 的 location/time/known_events/objective。
_DEFAULT_BERLIN_LOC = "柏林，哈布斯堡庄园附近"
_DEFAULT_BERLIN_TIME = "图卢兹失守后翌日，柏林"
_DEFAULT_BERLIN_PHASE = "柏林暗流篇"
_DEFAULT_BERLIN_OBJECTIVE_FRAG = "柏林局势"


def _scrub_berlin_default(state: Any) -> None:
    """清掉 DEFAULT_STATE 的柏林硬编码 location/time/timeline/known_events/objective。
    后续如果首章里有显式 inline meta，再覆盖回去；没有就保持安全空值。"""
    player = state.data.setdefault("player", {})
    if str(player.get("current_location") or "") == _DEFAULT_BERLIN_LOC:
        player["current_location"] = ""

    world = state.data.setdefault("world", {})
    if str(world.get("time") or "") == _DEFAULT_BERLIN_TIME:
        world["time"] = ""
    # known_events：DEFAULT_STATE 写死的 4 条柏林事件全部清掉
    default_events = {
        "宴会上调令伪造事件已曝光",
        "图卢兹战役：薇瑟帝国八位渊戮大胜，地联溃败",
        "娅赛兰决定暂留柏林",
        "蛇信在外围全程监视",
    }
    if isinstance(world.get("known_events"), list):
        world["known_events"] = [e for e in world["known_events"] if str(e) not in default_events]

    timeline = world.setdefault("timeline", {})
    if str(timeline.get("current_label") or "") == _DEFAULT_BERLIN_TIME:
        timeline["current_label"] = ""
    if str(timeline.get("current_phase") or "") == _DEFAULT_BERLIN_PHASE:
        timeline["current_phase"] = ""
    # last_transition 如果是 DEFAULT_STATE 的 None，留空
    if timeline.get("last_transition") is None:
        timeline["last_transition"] = None

    memory = state.data.setdefault("memory", {})
    if _DEFAULT_BERLIN_OBJECTIVE_FRAG in str(memory.get("current_objective") or ""):
        memory["current_objective"] = ""


def scripts(user_id: int) -> list[dict[str, Any]]:
    ensure_default(user_id)
    with connect() as db:
        return [expose(row) for row in db.execute("select * from scripts where owner_id = %s order by updated_at desc, id desc limit 200", (user_id,)).fetchall()]


def scripts_page(user_id: int, limit: int | str | None = None, cursor: str | None = None) -> dict[str, Any]:
    """列表 API:**显式列字段**,跳过 import_report jsonb (实测 script 11 这一行
    65 MB,select * 直接把列表 API 拖到 15s + 2.3MB 响应)。完整字段走 detail endpoint。

    顺手把游戏就绪度 (readiness) 摊到每行 — 列表"状态"列要用,见 _readiness_for_scripts。

    task 74: union owned + subscribed(公开剧本订阅,immutable knowledge,不复制数据,
    只挂指针)。前端通过 item.is_subscribed 区分是否本人拥有(决定能否编辑)。
    """
    ensure_default(user_id)
    page_limit = limit_value(limit)
    before_id = cursor_id(cursor)
    with connect() as db:
        rows = db.execute(
            """
            select s.id, s.owner_id, s.title, s.description, s.source_path, s.created_at, s.updated_at,
                   s.public_id, s.row_version, s.chapter_count, s.word_count, s.content_fingerprint,
                   s.shareable, s.extracted_through_chapter, s.extraction_seeded,
                   s.is_public, s.published_at, s.clone_count, s.review_status, s.reviewed_at,
                   s.embed_api_id, s.embed_model,
                   s.forked_from_script_id, s.forked_at_commit_id, s.sharing_mode,
                   s.current_pin_script_id, s.current_pin_commit_id, s.head_commit_id,
                   (s.owner_id != %s) as is_subscribed
            from scripts s
            where (
              s.owner_id = %s
              or s.id in (select script_id from user_script_subscriptions where user_id = %s)
            )
              and (%s::bigint is null or s.id < %s)
            order by s.id desc
            limit %s
            """,
            (user_id, user_id, user_id, before_id, before_id, page_limit + 1),
        ).fetchall()
        readiness = _readiness_for_scripts(db, [int(r["id"]) for r in rows])
    payload = page_payload(rows, page_limit)
    for item in payload["items"]:
        item["readiness"] = readiness.get(int(item["id"])) or _empty_readiness()
    return payload


# 游戏就绪度 — 5 个维度:章节切片 / 向量嵌入 / 知识库人物 / 世界观条目 / 时间线锚点。
# cards 是用户级别(user_character_cards 没有 script_id),不算 per-script 就绪度。
_READINESS_KEYS = ("chunks", "embeddings", "canon", "worldbook", "anchors")


def _empty_readiness() -> dict[str, Any]:
    return {
        "ok": False,
        "missing": list(_READINESS_KEYS),
        "items": [
            {"key": k, "ok": False, "count": 0, "total": 0} for k in _READINESS_KEYS
        ],
    }


def _readiness_for_scripts(db, script_ids: list[int]) -> dict[int, dict[str, Any]]:
    """一次查询拿到 N 个剧本的就绪度计数,避免 N+1。

    返 {script_id: {ok, missing, items: [{key, ok, count, total}]}}。
    每个 item 含 jump 信息留给前端拼(后端只给 raw counts)。
    """
    if not script_ids:
        return {}
    # 单 SQL,对每张表 group-by script_id;script_id 表 left-join
    # 用 UNION ALL 把 5 张表的 (script_id, dim, count, total) 全拍平,再 Python 侧组装。
    sql = """
        select script_id, 'chunks'::text as dim, count(*)::bigint as cnt, count(*)::bigint as total
          from document_chunks where script_id = any(%(ids)s) group by script_id
        union all
        select script_id, 'embeddings'::text,
               sum(case when embedding is not null then 1 else 0 end)::bigint as cnt,
               count(*)::bigint as total
          from document_chunks where script_id = any(%(ids)s) group by script_id
        union all
        select script_id, 'canon'::text, count(*)::bigint, count(*)::bigint
          from kb_canon_entities where script_id = any(%(ids)s) group by script_id
        union all
        select script_id, 'worldbook'::text, count(*)::bigint, count(*)::bigint
          from worldbook_entries where script_id = any(%(ids)s) group by script_id
        union all
        select script_id, 'anchors'::text, count(*)::bigint, count(*)::bigint
          from script_timeline_anchors where script_id = any(%(ids)s) group by script_id
    """
    rows = db.execute(sql, {"ids": script_ids}).fetchall()
    # 初始化全 0
    out: dict[int, dict[str, dict[str, int]]] = {
        sid: {k: {"count": 0, "total": 0} for k in _READINESS_KEYS}
        for sid in script_ids
    }
    for r in rows:
        sid = int(r["script_id"])
        dim = r["dim"]
        if sid in out and dim in out[sid]:
            out[sid][dim]["count"] = int(r["cnt"] or 0)
            out[sid][dim]["total"] = int(r["total"] or 0)
    # 拼装最终结构 + ok 判定
    result: dict[int, dict[str, Any]] = {}
    for sid in script_ids:
        items = []
        missing = []
        for key in _READINESS_KEYS:
            cnt = out[sid][key]["count"]
            total = out[sid][key]["total"]
            # chunks/canon/worldbook/anchors 只看 count>0;embeddings 看 == 或近似 ==(允许少 5%)
            if key == "embeddings":
                ready = total > 0 and cnt >= max(1, int(total * 0.95))
            else:
                ready = cnt > 0
            if not ready:
                missing.append(key)
            items.append({"key": key, "ok": ready, "count": cnt, "total": total})
        result[sid] = {
            "ok": not missing,
            "missing": missing,
            "items": items,
        }
    return result


def _read_state_snapshot() -> dict[str, Any]:
    """新存档的初始 state。

    安全：绝对不能读全局 SAVE_FILE（那是 admin 的运行态，会泄露给新用户）。
    走 state.GameState.new()，得到干净的初始 state。
    """
    try:
        from state import GameState
        return GameState.new().data
    except Exception:
        return {"history": [], "turn": 0}


# 列表页只取摘要字段；完整 state_snapshot 通过 save_detail() 单独取
_SAVE_LIST_COLUMNS = """
    id, public_id, user_id, script_id, title, state_path,
    active_commit_id, active_branch_node_id, active_branch_ref_id,
    created_at, updated_at, coalesce(last_played_at, updated_at) as last_played_at, row_version,
    (state_snapshot->>'turn')::int as turn,
    (state_snapshot->'player'->>'name') as player_name,
    coalesce(jsonb_array_length(state_snapshot->'history'), 0) as history_count,
    coalesce((state_snapshot->'world'->>'time'), '') as world_time
"""


def saves(user_id: int) -> list[dict[str, Any]]:
    ensure_default(user_id)
    with connect() as db:
        return [expose(row) for row in db.execute(
            f"select {_SAVE_LIST_COLUMNS} from game_saves where user_id = %s order by updated_at desc, id desc limit 200",
            (user_id,),
        ).fetchall()]


def saves_page(user_id: int, limit: int | str | None = None, cursor: str | None = None) -> dict[str, Any]:
    ensure_default(user_id)
    page_limit = limit_value(limit)
    before_id = cursor_id(cursor)
    with connect() as db:
        rows = db.execute(
            f"""
            select {_SAVE_LIST_COLUMNS} from game_saves
            where user_id = %s and (%s::bigint is null or id < %s)
            order by id desc
            limit %s
            """,
            (user_id, before_id, before_id, page_limit + 1),
        ).fetchall()
    return page_payload(rows, page_limit)


def save_detail(user_id: int, save_id: int) -> dict[str, Any]:
    """单条详情：包含完整 state_snapshot。前端只在打开 save 时才调。"""
    with connect() as db:
        row = db.execute(
            "select * from game_saves where id = %s and user_id = %s",
            (save_id, user_id),
        ).fetchone()
    if not row:
        raise ValueError(f"无权访问该存档: {save_id}")
    return expose(row) or {}
