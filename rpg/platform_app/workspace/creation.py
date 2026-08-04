"""platform_app.workspace.creation — 存档【创建】编排(包化搬家)。

create_save(剧本存档)/ create_tavern_save(酒馆存档)+ 创建即 seed KB
(_seed_kb_at_creation)+ 角色卡内嵌世界书导入(_ingest_character_book)。
初始 state 委托 .snapshot._build_initial_snapshot 构建。
纯机械搬家,逐字复制,零行为变化(仅在函数体内 lazy 相对 import 加深一层 . -> ..)。
"""
from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from core.logging import get_logger
from state import SAVE_FILE

from .. import branches
from ..db import connect, expose, init_db
from ..perms import script_readable
from .snapshot import _build_initial_snapshot

log = get_logger(__name__)


def _seed_kb_at_creation(save_id: int, script_id: int | None, snapshot: dict) -> None:
    """新存档创建即 seed 进 KB(kb-native from birth)+ 打 `kb_native` 标记 →「封死新存档入口」:
    该档之后【始终】走 KB 新实现(不受每用户 kb_state 开关影响);旧档 kb_native=false 不变。
    scripted: 剧本 canon T0 seed + import_state;tavern: 仅 import_state(无剧本 T0)。
    additive:blob 仍在(分支/回溯/兜底不破)。任何失败只 log、不阻断建档(降级回懒迁移路径)。"""
    try:
        from kb import save_kb
        from kb.t0_seed import root_commit_id
        with connect() as db:
            cid = root_commit_id(db, save_id)
            if cid is None:
                return
            if script_id:
                save_kb.seed_full_t0(db, save_id, int(script_id), commit_id=cid)
            save_kb.import_state(db, save_id, cid, snapshot or {})
            db.execute("update game_saves set kb_native = true where id = %s", (save_id,))
    except Exception as exc:
        log.error(f"[kb_seed] creation-time KB seed failed save={save_id}: {type(exc).__name__}: {exc}")


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
        # 不影响剧本本身的 immutability,所以订阅者也能建存档。
        # 读级归属判定收敛到 perms.script_readable(返 select s.* 整行)。
        script = script_readable(db, script_id, user_id)
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
    # seed_tree / anchor_seed / kb_seed 运行在已提交的 save 行之外（各自开独立连接）。
    # 若任一步骤抛出非预期异常，save 行已提交却无可用状态 → 孤儿存档。
    # 补偿策略：用 try/except 包裹全部后续步骤；任一不可恢复的异常触发补偿删除，
    # 把孤儿存档清掉后再重新抛出，让调用方（create_save API）返回 500 而非静默留孤儿。
    # branches.seed_tree / anchor_seed / kb_seed 内部已各自捕获并 log 自身的业务异常，
    # 这里只捕获它们之外的意外异常（如 DB 连接断开等）；正常失败路径不会到达 except。
    _save_id = save["id"]
    try:
        branches.seed_tree(_save_id, str(SAVE_FILE))
        # task 136: 新存档创建后异步 seed 世界线收束锚点。
        # 800 章 × 5 events 量级,放后台不阻塞 UI;失败也不影响存档创建。
        #
        # 注意 (task 141 修正):**不**默认调 claim_protagonist_pov。
        # isekai 语义是「玩家的现代灵魂 + 用户自创角色卡的肉身,与原作主角【平行共存】」,
        # 不是「玩家接管原作主角 POV」。原作主角应作为独立 NPC 触发其登场 anchor,
        # 玩家(用户角色卡)在同一场景平行加入。两人可能在 ch1 相遇,但不是同一个人。
        # claim_protagonist_pov 工具保留,但只在玩家显式声明"我就是 X"时由 GM 主动调。
        # 锚点 seed:**同步**做(改自原后台 daemon 线程)。原因(bug 修复):daemon 线程不保证在
        # 玩家第一回合前完成 → 新存档常 0 pending 锚点 → 时间线锚点标记/收束整段失效(E2E 实测到)。
        # 锚点 seed 是纯 DB 写(从 chapter_facts 批量 insert),即便 800 章量级也就秒级,放创建路径里
        # 保证「存档一建好就有完整锚点」远比省那点延迟重要。失败不影响存档创建。
        try:
            from agents.anchor_seed_agent import seed_anchors_for_save
            res = seed_anchors_for_save(_save_id)
            log.info(f"[anchor_seed] save={_save_id} (sync) result={res}")
        except Exception as _seed_err:
            log.error(f"[anchor_seed] sync seed failed save={_save_id}: {type(_seed_err).__name__}: {_seed_err}")
        # 封死新存档入口:创建即 seed 进 KB + 打 kb_native 标记(新档按新实现)。
        # 用回写后的 save["state_snapshot"](含 identity_card_id),而非建档前的旧 snapshot 局部变量,
        # 否则 KB seed 拿不到身份卡 id。
        _seed_kb_at_creation(_save_id, script_id, save.get("state_snapshot") or snapshot)
    except Exception as _post_err:
        # 意外异常（非 seed 内部的业务失败）：补偿删除已插入的孤儿 save 行，再重抛。
        log.error(
            f"[create_save] post-insert step failed save={_save_id}, compensating delete: "
            f"{type(_post_err).__name__}: {_post_err}"
        )
        try:
            with connect() as _cdb:
                _cdb.execute("delete from game_saves where id = %s", (_save_id,))
        except Exception as _del_err:
            log.error(f"[create_save] compensation delete failed save={_save_id}: {_del_err}")
        raise
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

    from .. import user_cards as _ucards

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
    # 封死新存档入口(酒馆):创建即 seed 进 KB(无剧本 T0 → 仅 import_state)+ 打 kb_native 标记。
    _seed_kb_at_creation(save["id"], None, snapshot)
    return expose(save)  # type: ignore[return-value]
