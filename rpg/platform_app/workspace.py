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

BASE_TITLE = "《我蕾穆丽娜不爱你》"


def ensure_default(user_id: int) -> None:
    init_db()
    with connect() as db:
        script = db.execute("select * from scripts where owner_id = %s order by id limit 1", (user_id,)).fetchone()
        if not script:
            script = db.execute(
                """
                insert into scripts(owner_id, title, description, source_path)
                values (%s, %s, %s, %s)
                returning *
                """,
                (user_id, BASE_TITLE, "柏林 RPG 默认剧本", "rpg/indexes"),
            ).fetchone()
        save = db.execute(
            "select * from game_saves where user_id = %s and script_id = %s order by id limit 1",
            (user_id, script["id"]),
        ).fetchone()
        if not save:
            save = db.execute(
                """
                insert into game_saves(user_id, script_id, title, state_path, state_snapshot)
                values (%s, %s, %s, %s, %s)
                returning *
                """,
                (user_id, script["id"], "当前自动存档", str(SAVE_FILE), Jsonb(_read_state_snapshot())),
            ).fetchone()
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
        scripts = db.execute("select * from scripts where owner_id = %s order by updated_at desc, id desc limit 50", (user["id"],)).fetchall()
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
    identity  = {"name": str, "role": str, "background": str}
                 —— 入场选初始身份，直接 setup_player（覆盖 character_card 默认值）

    无 new_card / character / birthpoint / identity 时退回到旧行为（空白快照）。
    """
    init_db()
    with connect() as db:
        script = db.execute("select * from scripts where id = %s and owner_id = %s", (script_id, user_id)).fetchone()
        if not script:
            raise ValueError("无权访问该剧本")
        snapshot = _build_initial_snapshot(user_id, script_id, new_card, character, birthpoint=birthpoint, identity=identity, story_intent=story_intent)
        save = db.execute(
            """
            insert into game_saves(user_id, script_id, title, state_path, state_snapshot)
            values (%s, %s, %s, %s, %s)
            returning *
            """,
            (user_id, script_id, title.strip() or "新存档", str(SAVE_FILE), Jsonb(snapshot)),
        ).fetchone()
    branches.seed_tree(save["id"], str(SAVE_FILE))
    # task 136: 新存档创建后异步 seed 世界线收束锚点。
    # 800 章 × 5 events 量级,放后台不阻塞 UI;失败也不影响存档创建。
    try:
        import threading

        from agents.anchor_seed_agent import seed_anchors_for_save
        def _bg_seed():
            try:
                res = seed_anchors_for_save(save["id"])
                log.info(f"[anchor_seed] save={save['id']} result={res}")
            except Exception as exc:
                log.error(f"[anchor_seed] save={save['id']} failed: {type(exc).__name__}: {exc}")
        threading.Thread(target=_bg_seed, daemon=True, name=f"anchor-seed-{save['id']}").start()
    except Exception as _seed_err:
        log.error(f"[anchor_seed] schedule failed save={save['id']}: {_seed_err}")
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

    # 入场选初始身份：**逐字段** merge,只覆盖非空字段。
    # task 126: 用户选了角色卡(已写 name=杭雁菱) → Step 4 又选了 LLM 推荐身份
    # (可能 name="" role="穿越者") → 不能因为 identity.name=="" 就把 player.name 抹成"无名者"。
    # task 137: identity.background 是"入场身份描述"(局外观察者/穿越者等定位说明),
    # 不能覆盖 user_card 的 personality/appearance 等详细设定。
    # 改为存入 player.identity_role_desc 单独字段,供 short_summary 作为"入场定位"显示。
    if isinstance(identity, dict):
        try:
            id_name = str(identity.get("name") or "").strip()
            id_role = str(identity.get("role") or "").strip()
            id_background = str(identity.get("background") or "").strip()
            player = state.data.setdefault("player", {})
            # 只覆盖 name/role 非空字段;为空时保留角色卡已写入的值
            if id_name:
                player["name"] = id_name
            if id_role:
                player["role"] = id_role
            # identity.background 不覆盖角色卡的 personality/background，
            # 改为存到独立字段 identity_role_desc（入场定位描述）
            if id_background:
                player["identity_role_desc"] = id_background
            # 兜底:如果一直空(没角色卡也没身份),才用占位
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
    # 用户选择了某个 script（不论是 5E 模组容器还是空白容器），就不该再继承《我蕾穆丽娜不爱你》
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
    ensure_default(user_id)
    page_limit = limit_value(limit)
    before_id = cursor_id(cursor)
    with connect() as db:
        rows = db.execute(
            """
            select * from scripts
            where owner_id = %s and (%s::bigint is null or id < %s)
            order by id desc
            limit %s
            """,
            (user_id, before_id, before_id, page_limit + 1),
        ).fetchall()
    return page_payload(rows, page_limit)


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
    created_at, updated_at, row_version,
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
