from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from core.logging import get_logger
from platform_app.db import connect, expose, init_db
from platform_app.knowledge._session_repo import _db_upsert_game_session
from platform_app.knowledge._sync import _ensure_book
from platform_app.knowledge._utils import _clean_text

log = get_logger(__name__)


def _state_from_save(user_id: int, save_id: int) -> dict[str, Any]:
    init_db()
    with connect() as db:
        row = db.execute(
            "select state_snapshot from game_saves where id = %s and user_id = %s",
            (save_id, user_id),
        ).fetchone()
    if not row:
        raise ValueError("无权访问该存档")
    state = row.get("state_snapshot") if isinstance(row, dict) else {}
    return state if isinstance(state, dict) else {}


def _sync_session_state(db, session: dict[str, Any], book_id: int, user_id: int, payload: dict[str, Any]) -> None:
    if not isinstance(payload, dict):
        return
    session_id = session["id"]
    db.execute(
        "delete from memories where session_id = %s and metadata->>'sync_source' = 'state_snapshot'",
        (session_id,),
    )
    memory = payload.get("memory") or {}
    for bucket in ("pinned", "facts", "abilities", "resources", "notes"):
        for index, content in enumerate(memory.get(bucket) or []):
            text = _clean_text(content)
            if not text:
                continue
            db.execute(
                """
                insert into memories(session_id, book_id, user_id, bucket, content, importance, metadata)
                values (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    session_id,
                    book_id,
                    user_id,
                    bucket,
                    text,
                    90 if bucket == "pinned" else 60,
                    Jsonb({"sync_source": "state_snapshot", "index": index}),
                ),
            )
    for key in ("main_quest", "current_objective"):
        text = _clean_text(memory.get(key) or "")
        if text:
            db.execute(
                """
                insert into memories(session_id, book_id, user_id, bucket, content, importance, metadata)
                values (%s, %s, %s, 'summary', %s, %s, %s)
                """,
                (session_id, book_id, user_id, text, 70, Jsonb({"sync_source": "state_snapshot", "field": key})),
            )

    worldline = payload.get("worldline") or {}
    variables = worldline.get("user_variables") or {}
    db.execute("delete from worldline_variables where session_id = %s", (session_id,))
    for key, raw in variables.items():
        value = raw.get("value") if isinstance(raw, dict) else raw
        value_text = _clean_text(value)
        key_text = _clean_text(key)
        if not key_text or not value_text:
            continue
        db.execute(
            """
            insert into worldline_variables(session_id, key, value, locked, source, metadata)
            values (%s, %s, %s, %s, %s, %s)
            on conflict(session_id, key) do update set
              value = excluded.value,
              locked = excluded.locked,
              source = excluded.source,
              metadata = excluded.metadata,
              updated_at = now()
            """,
            (
                session_id,
                key_text,
                value_text,
                bool(raw.get("locked", True)) if isinstance(raw, dict) else True,
                str(raw.get("source", "state")) if isinstance(raw, dict) else "state",
                Jsonb(raw if isinstance(raw, dict) else {"raw": raw}),
            ),
        )

    projection = worldline.get("last_projection") or worldline.get("pending_projection")
    if projection:
        projection_text = projection.get("text") or projection.get("projection") if isinstance(projection, dict) else str(projection)
        projection_text = _clean_text(projection_text)
        validation = worldline.get("last_validation") or {}
        exists = db.execute(
            """
            select 1 from worldline_projections
            where session_id = %s and turn = %s and projection = %s
            limit 1
            """,
            (session_id, int(payload.get("turn") or 0), projection_text),
        ).fetchone()
        if projection_text and not exists:
            db.execute(
                """
                insert into worldline_projections(
                  session_id, turn, projection, validated, validation_status, variables_snapshot, metadata
                )
                values (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    session_id,
                    int(payload.get("turn") or 0),
                    projection_text,
                    (validation.get("status") == "passed") if isinstance(validation, dict) else False,
                    validation.get("status", "none") if isinstance(validation, dict) else "none",
                    Jsonb(variables),
                    Jsonb(projection if isinstance(projection, dict) else {}),
                ),
            )


def ensure_game_session(user_id: int, save_id: int, state: dict[str, Any] | None = None) -> dict[str, Any]:
    init_db()
    with connect() as db:
        save = db.execute(
            """
            select game_saves.*, scripts.owner_id, scripts.title as script_title
            from game_saves
            join scripts on scripts.id = game_saves.script_id
            where game_saves.id = %s and game_saves.user_id = %s
            """,
            (save_id, user_id),
        ).fetchone()
        if not save:
            raise ValueError("无权访问该存档")
        script = db.execute("select * from scripts where id = %s", (save["script_id"],)).fetchone()
        book = _ensure_book(db, script)
        payload = state or {}
        session = _db_upsert_game_session(
            db, save_id, book["id"], save["script_id"], user_id,
            save["title"] or save["script_title"], payload,
        )
        _sync_session_state(db, session, book["id"], user_id, payload)
    return expose(session)  # type: ignore[return-value]


_CHINESE_NON_NAME_BLACKLIST: frozenset[str] = frozenset({
    # 高频副词/连词/语气词,_rank_entities 误识别的"假人名"
    "不知道", "知道", "起来", "不过", "这时候", "看起来", "实际上",
    "自己的", "你们的", "我们的", "他们的", "我们", "你们", "他们",
    "的时候", "的话", "之前", "之后", "现在", "刚刚", "突然", "终于",
    "继续", "已经", "可能", "或者", "如果", "因为", "所以", "但是",
    "然后", "为了", "对于", "关于", "通过", "根据", "比如",
    # 章节常见副词
    "整理", "感觉", "样子", "时候", "事情", "东西", "地方", "问题",
    "情况", "样式", "做法", "想法", "意思", "结果", "原因", "办法",
    # 数量/方位
    "一下", "一直", "一边", "一切", "一种", "一些", "上面", "下面",
    # 盗版网站宣传残留(防止 task 43 sanitizer 漏过的)
    "出版社", "作者或", "收集并", "版权归", "啃书", "KenShu",
    # 常见动词性短语
    "决定", "看看", "想想", "走过", "看着", "知道了",
    # 称呼通用词(不该单独成角色)
    "先生", "小姐", "夫人", "女士", "同志", "朋友", "敌人", "对方",
    # 数字
    "第一", "第二", "第三", "第四", "第五",
    # task 47 ext: 国籍/种族 — 不该单独成角色卡
    "中国人", "美国人", "德国人", "英国人", "法国人", "日本人", "韩国人",
    "俄国人", "苏联人", "意大利人", "西班牙人", "印度人", "犹太人",
    "中国", "美国", "德国", "英国", "法国", "日本", "苏联", "俄罗斯",
    # task 47 ext: 设定词 / 装备词被切碎的 ngram(应入 worldbook 不是 character)
    "魔导装", "导装甲", "战姬装", "机甲装", "动力装", "魔法装",
    # task 47 ext: 称呼后缀 / 人称后缀切碎
    "有德的", "晓芸的", "林有", "林晓", "晓芸",
    # 太通用的 2 字职业/身份
    "学生", "老师", "军官", "士兵", "警察", "医生", "护士", "工人",
    "农民", "商人", "记者", "演员", "歌手", "作家",
})


def _aggregate_characters_from_facts(script_id: int) -> dict[str, Any]:
    """从 chapter_facts.characters 纯聚合角色卡候选,不调 LLM。

    返回与 _load_characters() 兼容的 chars dict:
    {name: {name, identity, appearance, personality, speech_style,
            secrets, sample_dialogue, priority, aliases}}
    过滤 count < 2 的路人,取 top 50 by priority(总出场次数)。

    task 47: 加 _CHINESE_NON_NAME_BLACKLIST 过滤副词/连词/语气词等
    被 _rank_entities 误识别的"假人名"(如"不知道/起来/不过/这时候")。
    """
    out: dict[str, Any] = {}
    with connect() as db:
        rows = db.execute(
            "select characters from chapter_facts where script_id=%s and characters is not null",
            (script_id,),
        ).fetchall()

    for row in rows:
        characters = row.get("characters") if isinstance(row, dict) else row[0]
        if not characters or not isinstance(characters, list):
            continue
        for ch in characters:
            if not isinstance(ch, dict):
                continue
            name = (ch.get("name") or "").strip()
            if not name or len(name) < 2:
                continue
            # task 47: 黑名单过滤(必须放在 priority 累加之前)
            if name in _CHINESE_NON_NAME_BLACKLIST:
                continue
            # task 47: 含数字 / 纯数字 / 含 URL 字符的不是人名
            if any(c.isdigit() for c in name) or "/" in name or "." in name:
                continue
            count = int(ch.get("count") or 1)
            if name not in out:
                out[name] = {
                    "name": name,
                    "identity": "",
                    "appearance": "",
                    "personality": "",
                    "speech_style": "",
                    "current_status": "",
                    "secrets": "",
                    "sample_dialogue": [],
                    "priority": count,
                    "aliases": [],
                }
            else:
                out[name]["priority"] += count

    # task 47: 提高路人过滤门槛 — count<5 不要(原 <2 太宽松,2-3 字假名词容易达到)
    filtered = {name: card for name, card in out.items() if card["priority"] >= 5}

    # 取 top 50 by priority
    top = sorted(filtered.items(), key=lambda kv: -kv[1]["priority"])[:50]
    return dict(top)


def sync_script_knowledge(user_id: int, script_id: int, *, rebuild: bool = False) -> dict[str, Any]:
    """Build the Postgres knowledge layer for one imported script.

    The import path stays deterministic and cheap: it creates documents/chunks,
    ChapterFact rows, character cards, and worldbook entries without requiring an
    LLM pass. A later refinement pass can overwrite the same rows.
    """
    from chapter_fact_indexer import (
        _known_concepts,
        _known_locations,
        _known_names,
        _load_summaries,
    )
    from context_engine.loaders import (
        _load_characters,
        _load_world,
    )
    from platform_app.db import init_db as _init_db
    from platform_app.knowledge._chunks import (
        _fact_from_chapter,
        _insert_chunk,
        _upsert_chapter_fact,
        _upsert_document,
    )
    from platform_app.knowledge._sync import (
        _backfill_chapters_from_local_source,
        _sync_character_cards,
        _sync_worldbook_entries,
    )
    from platform_app.knowledge._utils import _chunk_text

    _init_db()
    # task 47: rebuild=True 时不读旧 character_cards,避免 feedback loop:
    # 旧 character_cards 有"不知道/出版社"等垃圾 → known_names 包含它们 →
    # _rank_entities 在新章节里 count 这些词 → 写回 chapter_facts.characters →
    # _aggregate_characters_from_facts 又把它们当 character → 又写回 character_cards。
    # rebuild 应该从 0 开始,只让 chapter_facts 文本本身决定角色。
    chars = {} if rebuild else (_load_characters(script_id=script_id) or {})
    world = {} if rebuild else (_load_world(script_id=script_id) or {})
    summaries = _load_summaries()
    known_names = _known_names(chars)
    known_locations = _known_locations(world)
    known_concepts = _known_concepts(world)

    with connect() as db:
        script = db.execute(
            "select * from scripts where id = %s and owner_id = %s",
            (script_id, user_id),
        ).fetchone()
        if not script:
            raise ValueError("无权访问该剧本")
        book = _ensure_book(db, script)
        if rebuild:
            # task 43/47: rebuild 必须连 character_cards + worldbook 一起清,
            # 否则旧的(污染版)残留,UPSERT 只更新同名行,新角色加进来但旧垃圾还在。
            db.execute("delete from documents where script_id = %s", (script_id,))
            db.execute("delete from chapter_facts where script_id = %s", (script_id,))
            db.execute("delete from character_cards where script_id = %s", (script_id,))
            db.execute("delete from worldbook_entries where script_id = %s", (script_id,))

        chapters = db.execute(
            """
            select * from script_chapters
            where script_id = %s
            order by chapter_index
            """,
            (script_id,),
        ).fetchall()
        if not chapters:
            _backfill_chapters_from_local_source(db, script)
            chapters = db.execute(
                """
                select * from script_chapters
                where script_id = %s
                order by chapter_index
                """,
                (script_id,),
            ).fetchall()
        # task 40 bug fix: 之前这段(character/worldbook 提取)在 chapter_facts 循环之前,
        # 所以新书首次 import 时 _aggregate_characters_from_facts 读到 0 个 fact → 0 个角色 →
        # 0 张卡。必须先把 chapter_facts 全部插入,然后再从 facts 聚合角色 + 生成 cards。

        chunk_count = 0
        fact_count = 0
        for chapter in chapters:
            document = _upsert_document(db, book, script, chapter)
            db.execute("delete from document_chunks where document_id = %s", (document["id"],))
            chunks = _chunk_text(chapter["content"])
            for chunk_index, content in enumerate(chunks):
                _insert_chunk(db, book, script, chapter, document, chunk_index, content)
            chunk_count += len(chunks)

            fact = _fact_from_chapter(chapter, summaries, known_names, known_locations, known_concepts)
            _upsert_chapter_fact(db, book, script, chapter, document, fact)
            fact_count += 1

        # P0 fix #3: 新书 chars 为空时,从 chapter_facts 纯聚合写 character_cards (不调 LLM)
        # 必须在 chapter_facts 循环之后调,否则聚合到 0 个角色
        if not chars:
            try:
                chars = _aggregate_characters_from_facts(script_id)
                log.info(
                    "[sync] 从 chapter_facts 聚合 %d 个角色 for script %s",
                    len(chars),
                    script_id,
                )
            except Exception as e:
                log.warning("[sync] 聚合 characters from facts failed: %s", e)
                chars = {}
        card_count = _sync_character_cards(db, book, script, chars)
        worldbook_count = _sync_worldbook_entries(db, book, script, world)

    # P0 fix #2: chapter_facts 完成后,聚合 script_timeline_anchors
    try:
        from script_timeline import rebuild_timeline_anchors
        rebuild_timeline_anchors(script_id)
    except Exception as e:
        log.warning("[sync_script_knowledge] rebuild_timeline_anchors failed: %s", e)

    # P0 fix #1: chapter_facts 完成后,聚合 phase_digests
    try:
        from scripts.aggregate_phase_digests import aggregate_for_script
        aggregate_for_script(script_id)
    except Exception as e:
        log.warning("[sync_script_knowledge] aggregate_for_script failed: %s", e)

    with connect() as db:
        db.execute(
            """
            update scripts
            set import_report = import_report || %s::jsonb,
                row_version = row_version + 1,
                updated_at = now()
            where id = %s
            """,
            (
                Jsonb({
                    "knowledge": {
                        "status": "ready",
                        "chapters": len(chapters),
                        "chunks": chunk_count,
                        "chapter_facts": fact_count,
                        "character_cards": card_count,
                        "worldbook_entries": worldbook_count,
                    }
                }),
                script_id,
            ),
        )

    return {
        "book": expose(book),
        "chapters": len(chapters),
        "chunks": chunk_count,
        "chapter_facts": fact_count,
        "character_cards": card_count,
        "worldbook_entries": worldbook_count,
    }
