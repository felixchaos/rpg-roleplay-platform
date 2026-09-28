"""kb/canon_repo.py — Phase B 规范层(per-script,钉死只读)读写。

提取(Phase A Pass2)产出写这里;GM 服务读这里(带进度过滤防剧透 + 元知识模式)。
设计 BC_kb_schema_worldtree.md §2 + D_gm_serving.md §7/§8(决策3 已揭示集合)。
"""
from __future__ import annotations

from typing import Literal

from psycopg.types.json import Jsonb

ForeknowledgeMode = Literal["none", "partial", "omniscient"]


# ── 进度过滤(决策3:已揭示集合) ─────────────────────────────────────────────
def _reveal_clause(progress_chapter: int | None, mode: ForeknowledgeMode,
                   *, prefix: str = "") -> tuple[str, list]:
    """返回 (sql 片段, 参数列表)。控制玩家在当前进度+元知识下能看到哪些规范知识。

    none        : first_revealed_chapter <= progress  或  public_knowledge
    partial     : 上 + metadata.famous=true(穿越者模糊知道大事)
    omniscient  : 不过滤
    progress=None: 不过滤(管理/编辑器视角)

    prefix: 列前缀(如 "p." 给 self-join 的别名表用),默认空串=裸列名。
            retrieval.py 层级图复用本函数同时过滤 CTE 实体与 parent join,保单一真源。
    """
    if mode == "omniscient" or progress_chapter is None:
        return "true", []
    fr = f"{prefix}first_revealed_chapter"
    pk = f"{prefix}public_knowledge"
    base = f"({fr} <= %s or {pk})"
    params: list = [progress_chapter]
    if mode == "partial":
        base = base[:-1] + f" or ({prefix}metadata->>'famous') = 'true')"
    return base, params


# ── kb_canon_entities ────────────────────────────────────────────────────────
_CANON_COLS = ("logical_key", "name", "aliases", "type", "summary", "attrs",
               "first_revealed_chapter", "public_knowledge", "importance", "metadata")


def upsert_canon_entity(db, script_id: int, logical_key: str, *, name: str, type: str,
                        aliases: list | None = None, summary: str = "", attrs: dict | None = None,
                        first_revealed_chapter: int = 0, public_knowledge: bool = False,
                        importance: int = 0, metadata: dict | None = None,
                        full_name: str = "", identity: str = "", background: str = "",
                        entity_subtype: str = "", parent_logical_key: str = "") -> dict | None:
    """主提取链路(extract.resolve)写规范实体。

    编辑器写入的实体(attrs.source='editor',REST / 编辑器 agent / 复核页打的标记)一律不动,
    与同链路的锚点(script_timeline_anchors.source)、世界书(metadata.source)保护同口径:
      · 同 key 撞上编辑器行 → do update 的 where 不成立,整行保持用户的值(返回 None);
      · 编辑器行的 key 不同但同名同类型(旧规则建的「奉天城_location」vs 提取的「奉天城」)
        → 不另插一条影子实体(返回 None),否则 GM 读到两份。
    名字 / 别名 / 摘要(参与嵌入的文本)真的变了 → 向量置空,等下一轮嵌入按新文本重算
    (embed_canon_entities 只补 embedding is null 的行)。
    """
    # v34: full_name / identity / background 进规范层 KB,GM 服务可从同一处取
    # v43: entity_subtype + parent_logical_key 解决"德军/铁人团/无忧宫"全平级 faction 问题
    # 空串语义=不覆盖旧值(case when 保留已有);只在 LLM 抽到非空时更新。
    if find_same_name_canon(db, script_id, name, type, exclude_key=logical_key, editor_only=True):
        return None
    return db.execute(
        """
        insert into kb_canon_entities(script_id, logical_key, name, aliases, type, summary, attrs,
          first_revealed_chapter, public_knowledge, importance, metadata,
          full_name, identity, background, entity_subtype, parent_logical_key)
        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s, %s,%s,%s, %s,%s)
        on conflict(script_id, logical_key) do update set
          name=excluded.name, aliases=excluded.aliases, type=excluded.type, summary=excluded.summary,
          attrs=excluded.attrs, first_revealed_chapter=excluded.first_revealed_chapter,
          public_knowledge=excluded.public_knowledge, importance=excluded.importance, metadata=excluded.metadata,
          full_name = case when length(excluded.full_name) > 0
                           then excluded.full_name else kb_canon_entities.full_name end,
          identity = case when length(excluded.identity) > 0
                          then excluded.identity else kb_canon_entities.identity end,
          background = case when length(excluded.background) > 0
                            then excluded.background else kb_canon_entities.background end,
          entity_subtype = case when length(excluded.entity_subtype) > 0
                                then excluded.entity_subtype else kb_canon_entities.entity_subtype end,
          parent_logical_key = case when length(excluded.parent_logical_key) > 0
                                    then excluded.parent_logical_key else kb_canon_entities.parent_logical_key end,
          embedding = case when kb_canon_entities.name is distinct from excluded.name
                             or kb_canon_entities.aliases is distinct from excluded.aliases
                             or kb_canon_entities.summary is distinct from excluded.summary
                           then null else kb_canon_entities.embedding end
        where coalesce(kb_canon_entities.attrs->>'source', '') <> 'editor'
        returning *
        """,
        (script_id, logical_key, name, Jsonb(aliases or []), type, summary, Jsonb(attrs or {}),
         first_revealed_chapter, public_knowledge, importance, Jsonb(metadata or {}),
         full_name or "", identity or "", background or "",
         entity_subtype or "", parent_logical_key or ""),
    ).fetchone()


def find_same_name_canon(db, script_id: int, name: str, entity_type: str, *,
                         exclude_key: str | None = None, editor_only: bool = False) -> dict | None:
    """本剧本里同名(忽略大小写)同类型的已有实体;没有返回 None。

    编辑器新建前用它拦重复(REST 回 409 / agent 工具返回失败并指向那条);
    主提取写入前用它(editor_only=True)避免给编辑器实体造一条同名影子。
    """
    nm = str(name or "").strip()
    if not nm or not entity_type:
        return None
    sql = ("select logical_key, name, type from kb_canon_entities "
           "where script_id = %s and type = %s and lower(name) = lower(%s)")
    args: list = [script_id, entity_type, nm]
    if exclude_key is not None:
        sql += " and logical_key <> %s"
        args.append(exclude_key)
    if editor_only:
        sql += " and coalesce(attrs->>'source', '') = 'editor'"
    sql += " order by id limit 1"
    return db.execute(sql, tuple(args)).fetchone()


# ── 编辑器写侧:嵌入文本变更 / 子实体引用 ─────────────────────────────────────
# 参与嵌入的文本列(extract.embed.embed_canon_entities 拼的是 name + aliases + summary)。
CANON_EMBED_TEXT_COLS: tuple[str, ...] = ("name", "aliases", "summary")


def canon_embedding_reset_sql(new_values: dict) -> tuple[str, list] | None:
    """UPDATE kb_canon_entities 的 SET 片段:本次要写的嵌入文本列只要有一列的值真变了,
    就把 embedding 置空(与世界书 PUT 置空 embedding_vec 同口径)。值没变(md-editor 整体回写)
    保留向量,不白白重嵌。new_values 里没有嵌入文本列 → None(调用方什么都不加)。

    UPDATE 的 SET 表达式读的是更新前的行,所以这个片段放在 SET 列表的任何位置都成立。
    """
    conds: list[str] = []
    params: list = []
    for col in CANON_EMBED_TEXT_COLS:
        if col not in new_values:
            continue
        if col == "aliases":
            conds.append("aliases is distinct from %s::jsonb")
            params.append(Jsonb(list(new_values[col] or [])))
        else:
            conds.append(f"{col} is distinct from %s")
            params.append(new_values[col])
    if not conds:
        return None
    return f"embedding = case when {' or '.join(conds)} then null else embedding end", params


def detach_canon_children(db, script_id: int, logical_key: str, *, new_parent: str = "") -> list[str]:
    """把 parent_logical_key 指向 logical_key 的子实体改挂到 new_parent(默认空串=无上级)。

    删除实体时不处理子实体,上级列会挂着一个不存在的 key,之后同名重建分到同一个 key 时
    旧子实体又被静默挂回去。合并(merge)时传 new_parent=合并目标,子实体跟着过去
    (合并目标自己原来就挂在被删实体下的,改成无上级,不自指)。返回受影响的子实体 key(排序)。
    """
    rows = db.execute(
        "update kb_canon_entities set parent_logical_key = "
        "case when logical_key = %s then '' else %s end "
        "where script_id = %s and parent_logical_key = %s and logical_key <> %s "
        "returning logical_key",
        (new_parent, new_parent, script_id, logical_key, logical_key),
    ).fetchall()
    return sorted(str(r["logical_key"]) for r in rows)


# ── 编辑器写侧:类型白名单 + logical_key 分配 ────────────────────────────────
# REST(platform_app/api/script_edit/canon.py)与编辑器 agent 工具
# (tools_dsl/command_tools_script_write/canon.py)共用的单一真相源。
# 合法类型与提取链路一致(extract/resolve._reclassify_canon_type 的值域)。
CANON_ENTITY_TYPES: tuple[str, ...] = (
    "character", "faction", "organization", "location", "item", "concept",
)
CANON_TYPE_LABELS_ZH: dict[str, str] = {
    "character": "人物", "faction": "势力", "organization": "组织",
    "location": "地点", "item": "物品", "concept": "概念",
}
# 常见同义写法(md-editor front-matter / 编辑器 agent 偶尔写中文或近义英文)。
_CANON_TYPE_SYNONYMS: dict[str, str] = {
    **{zh: en for en, zh in CANON_TYPE_LABELS_ZH.items()},
    "person": "character", "people": "character", "npc": "character", "角色": "character",
    "org": "organization", "place": "location", "地名": "location",
    "阵营": "faction", "道具": "item", "设定": "concept",
}


def normalize_canon_type(raw) -> str | None:
    """把调用方给的类型归一成合法值;认不出(含空)返回 None。"""
    s = str(raw or "").strip()
    if not s:
        return None
    low = s.lower()
    if low in CANON_ENTITY_TYPES:
        return low
    return _CANON_TYPE_SYNONYMS.get(low) or _CANON_TYPE_SYNONYMS.get(s)


def canon_type_choices_text() -> str:
    """报错文案用:「人物 character / 势力 faction / …」。"""
    return " / ".join(f"{CANON_TYPE_LABELS_ZH[t]} {t}" for t in CANON_ENTITY_TYPES)


def canon_logical_key_base(name: str, entity_type: str) -> str:
    """新建实体的 logical_key 基底 —— 与主提取链路(extract.resolve)同口径。

    名字规范化复用 extract.resolve._slug(简繁/全角归一、空白转下划线、去非法字符、
    只剩点号时退回 entity);只有 concept 加「_concept」后缀,其它类型直接用规范化名字
    (resolve 写人物 / 势力 / 地点 / 物品都是 _slug(名字),写概念是「<slug>_concept」)。
    同口径才撞得上:用户手建的「奉天城」和之后提取出的「奉天城」是同一个 key,提取按编辑器
    保护跳过,不会多出一条。纯函数:同名同类型恒得同一基底。
    """
    from extract.resolve import _slug  # 懒 import:extract.resolve 顶层 import 本模块
    base = _slug(name)
    return f"{base}_concept" if entity_type == "concept" else base


def canon_logical_key_problem(key: str) -> str | None:
    """显式传入的 logical_key 不能用时返回给用户看的原因,能用返回 None。

    key 会出现在 /canon-entities/{key} 路径里:斜杠会拆路径;只由点号组成的「.」「..」
    会被浏览器当成当前 / 上一级目录规范化掉,请求落到别的地址,这条实体就打不开、改不了、删不掉。
    """
    k = str(key or "")
    if "/" in k:
        return "logical_key 里不能有斜杠「/」"
    if k and not k.strip("."):
        return "logical_key 不能只由点号组成(「.」「..」在网址里会被当成目录,这条实体会打不开)"
    return None


def allocate_canon_logical_key(db, script_id: int, name: str, entity_type: str) -> str:
    """分配一个本剧本内未被占用的 logical_key:基底空闲就用基底,否则 基底_2 / _3 …。

    只做「选号」不落库;并发下两个请求可能选到同一个号,调用方用
    `on conflict do nothing` 插入,撞了再调一次本函数重选即可。
    """
    base = canon_logical_key_base(name, entity_type)
    like = base.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "\\_%"
    rows = db.execute(
        "select logical_key from kb_canon_entities where script_id = %s "
        "and (logical_key = %s or logical_key like %s escape '\\')",
        (script_id, base, like),
    ).fetchall()
    taken = {r["logical_key"] for r in rows}
    if base not in taken:
        return base
    n = 2
    while f"{base}_{n}" in taken:
        n += 1
    return f"{base}_{n}"


def read_canon_entities(db, script_id: int, *, progress_chapter: int | None = None,
                        mode: ForeknowledgeMode = "none", entity_type: str | None = None,
                        limit: int | None = None, save_id: int | None = None) -> list[dict]:
    from platform_app.knowledge._pin import effective_kb_script_id
    script_id = effective_kb_script_id(db, script_id)  # pin 重定向(纯读)
    cols = ", ".join(_CANON_COLS)

    # P4(S1):flag on 且有 save_id → 用前沿门控(reveal_clause_v2);否则旧标量门控。
    from kb.reveal import _frontier_on, _frontier_shadow, _shadow_diff_log, reveal_clause_v2
    from kb.reveal import derived_progress_chapter as _dpc
    use_v2 = save_id is not None and _frontier_on(save_id)
    # 进度章传给前沿门控:开局/当前章 canon(序章人物/世界知识)按章节可见,不被空前沿误藏。
    _prog = progress_chapter if progress_chapter is not None else (_dpc(int(save_id)) if save_id is not None else None)
    if use_v2:
        clause, params = reveal_clause_v2(int(save_id), mode, prefix="", progress_chapter=_prog)
    else:
        clause, params = _reveal_clause(progress_chapter, mode)

    def _build(_clause: str, _params: list, *, key_only: bool = False) -> tuple[str, tuple]:
        sel = "logical_key" if key_only else cols
        s = f"select {sel} from kb_canon_entities where script_id = %s and {_clause}"
        a: list = [script_id, *_params]
        if entity_type:
            s += " and type = %s"; a.append(entity_type)
        if not key_only:
            s += " order by importance desc, logical_key"
            if limit:
                s += " limit %s"; a.append(limit)
        return s, tuple(a)

    sql, args = _build(clause, params)
    rows = db.execute(sql, args).fetchall()

    # 影子比对:同连接跑另一套门控,diff 落日志,不改返回值。
    if _frontier_shadow() and save_id is not None:
        if use_v2:
            oc, op = _reveal_clause(progress_chapter, mode)
        else:
            oc, op = reveal_clause_v2(int(save_id), mode, prefix="", progress_chapter=_prog)
        ssql, sargs = _build(oc, op, key_only=True)
        shadow_ids = {r["logical_key"] for r in db.execute(ssql, sargs).fetchall()}
        _shadow_diff_log("canon read", {r["logical_key"] for r in rows}, shadow_ids)
    return rows


def lookup_canon_entity(db, script_id: int, logical_key: str, *, progress_chapter: int | None = None,
                        mode: ForeknowledgeMode = "none", save_id: int | None = None) -> dict | None:
    from platform_app.knowledge._pin import effective_kb_script_id
    script_id = effective_kb_script_id(db, script_id)  # pin 重定向(纯读)

    from kb.reveal import _frontier_on, _frontier_shadow, _shadow_diff_log, reveal_clause_v2
    from kb.reveal import derived_progress_chapter as _dpc
    use_v2 = save_id is not None and _frontier_on(save_id)
    _prog = progress_chapter if progress_chapter is not None else (_dpc(int(save_id)) if save_id is not None else None)
    if use_v2:
        clause, params = reveal_clause_v2(int(save_id), mode, prefix="", progress_chapter=_prog)
    else:
        clause, params = _reveal_clause(progress_chapter, mode)

    def _run(_clause: str, _params: list):
        return db.execute(
            f"select {', '.join(_CANON_COLS)} from kb_canon_entities "
            f"where script_id=%s and logical_key=%s and {_clause}",
            (script_id, logical_key, *_params),
        ).fetchone()

    row = _run(clause, params)
    if _frontier_shadow() and save_id is not None:
        if use_v2:
            oc, op = _reveal_clause(progress_chapter, mode)
        else:
            oc, op = reveal_clause_v2(int(save_id), mode, prefix="", progress_chapter=_prog)
        srow = _run(oc, op)
        _shadow_diff_log(
            "canon lookup", {logical_key} if row else set(), {logical_key} if srow else set())
    return row


# ── 规范世界线 DAG ───────────────────────────────────────────────────────────
def upsert_worldline(db, script_id: int, wl_key: str, *, label: str, parent_wl: str | None = None,
                     branch_at_node: str | None = None, is_primary: bool = False,
                     source: str = "extracted", metadata: dict | None = None) -> dict:
    return db.execute(
        """
        insert into script_worldlines(script_id, wl_key, label, parent_wl, branch_at_node, is_primary, source, metadata)
        values (%s,%s,%s,%s,%s,%s,%s,%s)
        on conflict(script_id, wl_key) do update set
          label=excluded.label, parent_wl=excluded.parent_wl, branch_at_node=excluded.branch_at_node,
          is_primary=excluded.is_primary, source=excluded.source, metadata=excluded.metadata
        returning *
        """,
        (script_id, wl_key, label, parent_wl, branch_at_node, is_primary, source, Jsonb(metadata or {})),
    ).fetchone()


def upsert_worldline_node(db, script_id: int, wl_key: str, node_key: str, *, seq: int, label: str,
                          summary: str = "", chapter_min: int | None = None, chapter_max: int | None = None,
                          anchor_keys: list | None = None, must_preserve: list | None = None,
                          may_vary: list | None = None, causal_centrality: float = 0.0,
                          first_revealed_chapter: int = 0) -> dict:
    return db.execute(
        """
        insert into script_worldline_nodes(script_id, wl_key, node_key, seq, label, summary,
          chapter_min, chapter_max, anchor_keys, must_preserve, may_vary, causal_centrality, first_revealed_chapter)
        values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        on conflict(script_id, wl_key, node_key) do update set
          seq=excluded.seq, label=excluded.label, summary=excluded.summary,
          chapter_min=excluded.chapter_min, chapter_max=excluded.chapter_max, anchor_keys=excluded.anchor_keys,
          must_preserve=excluded.must_preserve, may_vary=excluded.may_vary,
          causal_centrality=excluded.causal_centrality, first_revealed_chapter=excluded.first_revealed_chapter
        returning *
        """,
        (script_id, wl_key, node_key, seq, label, summary, chapter_min, chapter_max,
         Jsonb(anchor_keys or []), Jsonb(must_preserve or []), Jsonb(may_vary or []),
         causal_centrality, first_revealed_chapter),
    ).fetchone()


def read_worldlines(db, script_id: int) -> list[dict]:
    from platform_app.knowledge._pin import effective_kb_script_id
    script_id = effective_kb_script_id(db, script_id)  # pin 重定向(纯读)
    return db.execute(
        "select wl_key, label, parent_wl, branch_at_node, is_primary, source, metadata "
        "from script_worldlines where script_id=%s order by is_primary desc, wl_key",
        (script_id,),
    ).fetchall()


def read_worldline_nodes(db, script_id: int, wl_key: str, *, progress_chapter: int | None = None) -> list[dict]:
    from platform_app.knowledge._pin import effective_kb_script_id
    script_id = effective_kb_script_id(db, script_id)  # pin 重定向(纯读)
    sql = (
        "select wl_key, node_key, seq, label, summary, chapter_min, chapter_max, anchor_keys, "
        "must_preserve, may_vary, causal_centrality, first_revealed_chapter "
        "from script_worldline_nodes where script_id=%s and wl_key=%s"
    )
    args: list = [script_id, wl_key]
    if progress_chapter is not None:
        sql += " and first_revealed_chapter <= %s"
        args.append(progress_chapter)
    sql += " order by seq"
    return db.execute(sql, tuple(args)).fetchall()
