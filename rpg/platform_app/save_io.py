"""
save_io.py — 存档导入 / 导出

导出包含：
  - game_saves 主记录
  - branch_commits(剧情分支历史)+ branch_refs
  - messages(对话)+ memories(via game_sessions)
  - save_anchor_states(锚点状态,游戏体验核心)
  - kb_entities / kb_events / kb_relationships / kb_worldline_vars / kb_checkpoints
  - identity_cards / save_character_identities / save_history_anchors

不导出: token_usage(跨用户敏感)/ user_runtime(运行态,瞬时)
导入时按当前 user_id 重映射 owner,分配新 save_id / commit_id。
"""
from __future__ import annotations

import secrets
from typing import Any

from psycopg.types.json import Jsonb

from .db import connect, expose, init_db
from .perms import owns_save, script_owned

EXPORT_VERSION = 2  # task 69: v1 (commits+messages+memories only) → v2 (+ 8 状态表)

MAX_COMMITS = 50000
MAX_TEXT_BYTES = 65536          # gm_output / player_input / summary 字段
MAX_SNAPSHOT_JSON_BYTES = 1024 * 1024  # state_snapshot / metadata JSON

# task 69: 每张 per-save 状态表的导出/导入定义
# 顺序按依赖: 先核心,后辅助。导入时同序 insert。
_STATE_TABLES: tuple[tuple[str, str], ...] = (
    # (table_name, allow_missing) — allow_missing=True 表示老 schema 可能没这表(向后兼容)
    ("save_anchor_states", False),
    ("kb_entities", False),
    ("kb_events", False),
    ("kb_relationships", False),
    ("kb_worldline_vars", False),
    ("kb_checkpoints", True),
    ("identity_cards", True),
    ("save_character_identities", True),
    ("save_history_anchors", True),
)


def _check_json_size(obj: Any, field: str) -> Any:
    """序列化后检查字节数，超限抛 ValueError。"""
    import json as _j
    if len(_j.dumps(obj, ensure_ascii=False).encode()) > MAX_SNAPSHOT_JSON_BYTES:
        raise ValueError(f"{field} 超过 {MAX_SNAPSHOT_JSON_BYTES} 字节上限")
    return obj


def _dump_rows(db, table: str, save_id: int, allow_missing: bool) -> list[dict[str, Any]]:
    """通用 select * 导出。表不存在 / 列名变动 → 空列表 + warning,不阻断整盘导出。"""
    try:
        rows = db.execute(f"select * from {table} where save_id = %s order by id", (save_id,)).fetchall() or []
        return [expose(r) for r in rows]
    except Exception:
        if allow_missing:
            return []
        raise


def export_save(user_id: int, save_id: int) -> dict[str, Any]:
    """打包整份存档为 JSON。task 69: 加入 9 张状态表。"""
    init_db()
    with connect() as db:
        # 归属判定收敛到 perms.owns_save;通过后再取整行(导出用)。
        if not owns_save(db, save_id, user_id):
            raise ValueError("无权访问该存档")
        save = db.execute(
            "select * from game_saves where id = %s",
            (save_id,),
        ).fetchone()
        commits = db.execute(
            "select * from branch_commits where save_id = %s order by id",
            (save_id,),
        ).fetchall()
        refs = db.execute(
            "select * from branch_refs where save_id = %s order by id",
            (save_id,),
        ).fetchall()
        sessions = db.execute(
            "select id from game_sessions where save_id = %s",
            (save_id,),
        ).fetchall()
        session_ids = [int(s["id"]) for s in sessions]
        messages = []
        memories_rows = []
        if session_ids:
            messages = db.execute(
                "select * from messages where session_id = ANY(%s::bigint[]) order by id",
                (session_ids,),
            ).fetchall()
            memories_rows = db.execute(
                "select * from memories where session_id = ANY(%s::bigint[]) order by id",
                (session_ids,),
            ).fetchall()

        # task 69: 9 张 per-save 状态表导出
        state_tables: dict[str, list[dict[str, Any]]] = {}
        for table, allow_missing in _STATE_TABLES:
            state_tables[table] = _dump_rows(db, table, save_id, allow_missing)

    return {
        "export_version": EXPORT_VERSION,
        "exported_at": __import__("time").time(),
        "save": expose(save),
        "commits": [expose(c) for c in commits],
        "refs": [expose(r) for r in refs],
        "messages": [expose(m) for m in messages],
        "memories": [expose(m) for m in memories_rows],
        "state_tables": state_tables,
    }


def _strip_id_and_save_id(row: dict[str, Any], extra_strip: tuple[str, ...] = ()) -> dict[str, Any]:
    """剥离 id / save_id / created_at — 由数据库重新分配。"""
    out = dict(row)
    out.pop("id", None)
    out.pop("save_id", None)
    out.pop("created_at", None)
    for k in extra_strip:
        out.pop(k, None)
    return out


_COL_CACHE: dict[str, frozenset[str]] = {}
_JSONB_COL_CACHE: dict[str, frozenset[str]] = {}
# TTL(秒)——迁移后无需重启即可感知新列集
_COL_CACHE_AT: dict[str, float] = {}
_JSONB_COL_CACHE_AT: dict[str, float] = {}
_COL_CACHE_TTL = 60.0


def _jsonb_columns(db: Any, table: str) -> frozenset[str]:
    """该表的 jsonb 列集合(缓存,TTL=60s)。导入时 jsonb 列的【标量】值(如 kb_worldline_vars.value=3/turn)
    也必须包 Jsonb —— 否则裸标量塞进 jsonb 列 → 'column is of type jsonb but expression is of type
    integer' → 整行失败(kb_worldline_vars 全军覆没,核心存档态进不了 KB)。dict/list 原本就包,这里
    把标量也覆盖。TTL 保证迁移后不重启也能感知新列。"""
    import time as _time
    now = _time.monotonic()
    cached = _JSONB_COL_CACHE.get(table)
    if cached is not None and now - _JSONB_COL_CACHE_AT.get(table, 0.0) < _COL_CACHE_TTL:
        return cached
    rows = db.execute(
        "select column_name from information_schema.columns "
        "where table_schema = current_schema() and table_name = %s and data_type = 'jsonb'",
        (table,),
    ).fetchall()
    cols = frozenset(r["column_name"] for r in rows)
    _JSONB_COL_CACHE[table] = cols
    _JSONB_COL_CACHE_AT[table] = now
    return cols


def _table_columns(db: Any, table: str) -> frozenset[str]:
    """返回 table 在 DB 里实际存在的列名集合(来自 information_schema,可信源),带进程内缓存(TTL=60s)。

    安全关键:`_build_insert` 把列名直接拼进 SQL 字符串(列名无法参数化)。导入 payload 的
    row 键来自用户上传的 JSON,若原样当列名拼接 → 列名 SQL 注入(可构造 INSERT...SELECT 跨表
    窃取他人存档/凭证)。用本函数把列名**白名单到该表真实列**,目录列名本身可信,彻底堵注入,
    同时保留"容忍 schema 漂移"(未知列静默丢弃)的原意。table 来自 `_STATE_TABLES` 硬白名单。
    TTL 保证迁移后不重启也能感知新列。
    """
    import time as _time
    now = _time.monotonic()
    cached = _COL_CACHE.get(table)
    if cached is not None and now - _COL_CACHE_AT.get(table, 0.0) < _COL_CACHE_TTL:
        return cached
    rows = db.execute(
        "select column_name from information_schema.columns "
        "where table_schema = current_schema() and table_name = %s",
        (table,),
    ).fetchall()
    # 连接池配 row_factory=dict_row → 行是 dict,r[0] 会 KeyError(0)(被全局 handler 格式化成
    # 「missing field: 0」误导用户,且自包含存档导入半途失败留孤儿剧本)。按列名取。
    cols = frozenset(r["column_name"] for r in rows)
    _COL_CACHE[table] = cols
    _COL_CACHE_AT[table] = now
    return cols


def _build_insert(
    table: str, row: dict[str, Any], new_save_id: int, allowed_cols: frozenset[str],
    jsonb_cols: frozenset[str] = frozenset(),
) -> tuple[str, tuple]:
    """根据 row 实际包含的列动态构造 INSERT,容忍前后端 schema 漂移。

    列名先按 allowed_cols(该表真实列,来自 DB 目录)过滤:非真实列直接丢弃,既防列名 SQL
    注入,又对 schema 漂移健壮。allowed_cols 永不为空时才会带额外列(save_id 恒在)。
    jsonb_cols 给出的列(该表真实 jsonb 列):非 None 值一律包 Jsonb(含标量 3/"x"/true),
    防裸标量塞 jsonb 列报类型错。
    """
    cols = ["save_id"]
    vals: list[Any] = [new_save_id]
    for k, v in row.items():
        if k not in allowed_cols or k == "save_id":
            continue  # 未知/伪造列名一律丢弃(防注入 + schema 漂移容错)
        cols.append(k)
        if v is None:
            vals.append(None)  # NULL(jsonb 列也用 SQL NULL,而非 'null'::jsonb)
        elif k in jsonb_cols or isinstance(v, (dict, list)):
            # jsonb 列的任意值(标量/对象/数组)都包 Jsonb;dict/list 再过大小校验。
            vals.append(Jsonb(_check_json_size(v, f"{table}.{k}") if isinstance(v, (dict, list)) else v))
        else:
            vals.append(v)
    placeholders = ", ".join(["%s"] * len(cols))
    col_list = ", ".join(cols)
    sql = f"insert into {table} ({col_list}) values ({placeholders}) on conflict do nothing"
    return sql, tuple(vals)


def import_save(user_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """从导出 payload 重建存档。按当前 user 创建新 save_id。

    不导入 sessions / context_runs / token_usage 这些跨用户敏感数据。
    task 69: v1 / v2 双格式兼容。v1 缺 state_tables 不报错,只 warning。
    """
    init_db()
    if not isinstance(payload, dict):
        raise ValueError("payload 必须是对象")
    pv = int(payload.get("export_version") or 0)
    if pv not in (1, 2):
        raise ValueError(f"export_version 不支持({pv}),需 1 或 2")
    save_data = payload.get("save") or {}
    if not save_data:
        raise ValueError("payload.save 缺失")

    new_title = (save_data.get("title") or "导入存档")[:200]  # 限标题长度,防超长 title
    script_id_raw = save_data.get("script_id")
    # 酒馆模式(Tavern):save_kind='tavern' 的存档无剧本(script_id=NULL)。CHECK 约束
    # chk_game_save_needs_script 允许非 game 存档 script_id 为 NULL。必须跳过下面的
    # script 归属重映射(否则把酒馆存档错挂到用户首个 script,污染酒馆 lane);
    # save_kind='game'(默认)走原逻辑,零回归。
    save_kind = str(save_data.get("save_kind") or "game").strip() or "game"
    is_tavern = save_kind == "tavern"
    tavern_character_card_id_raw = save_data.get("tavern_character_card_id")
    # 主 save 的 state_snapshot 也必须过大小校验(原仅 per-commit 校验,主 snapshot 漏检):
    # application/json body 导入路径无 _MAX_SAVE_IMPORT_BYTES 上限,构造超大 save.state_snapshot
    # 可绕过端点大小关 + 直插 game_saves 撑 DB/内存。与 per-commit 一致用 _check_json_size。
    state_snapshot = _check_json_size(save_data.get("state_snapshot") or {}, "save.state_snapshot")
    warnings: list[str] = []
    if pv == 1:
        warnings.append("v1 存档包未含 anchor/kb/identity 状态表,建议在游戏内 /reseed 重建锚点")

    with connect() as db:
        # 校验 script_id 归属（用户必须拥有这个剧本，否则用 user 第一个 script 兜底）。
        # 酒馆存档无剧本:整段重映射跳过,script_id 恒 NULL。
        script_id = None
        tavern_character_card_id: int | None = None
        if is_tavern:
            # 酒馆角色卡归属校验(best-effort):不属于本人或不存在则置 NULL(FK on delete set null)
            if tavern_character_card_id_raw:
                try:
                    cid = int(tavern_character_card_id_raw)
                    owned_card = db.execute(
                        "select 1 from character_cards where id = %s and user_id = %s",
                        (cid, user_id),
                    ).fetchone()
                    if owned_card:
                        tavern_character_card_id = cid
                except (TypeError, ValueError):
                    tavern_character_card_id = None
        else:
            if script_id_raw:
                if script_owned(db, int(script_id_raw), user_id):
                    script_id = int(script_id_raw)
            if script_id is None:
                row = db.execute(
                    "select id from scripts where owner_id = %s order by id limit 1",
                    (user_id,),
                ).fetchone()
                if not row:
                    raise ValueError("当前用户没有剧本，无法导入存档")
                script_id = int(row["id"])
                warnings.append(f"原 script_id={script_id_raw} 不在当前账户,改挂到 script_id={script_id}")

        # 1. 新建 save —— 列清单按 save_kind 条件构造:
        #    game(默认)保持原始三列形态(byte-for-byte 不变);
        #    tavern 插 save_kind='tavern' + script_id=NULL + tavern_character_card_id。
        if is_tavern:
            new_save = db.execute(
                """
                insert into game_saves(user_id, script_id, title, state_path, state_snapshot,
                                       save_kind, tavern_character_card_id)
                values (%s, NULL, %s, %s, %s, 'tavern', %s)
                returning *
                """,
                (user_id, new_title, "", Jsonb(state_snapshot), tavern_character_card_id),
            ).fetchone()
        else:
            new_save = db.execute(
                """
                insert into game_saves(user_id, script_id, title, state_path, state_snapshot)
                values (%s, %s, %s, %s, %s)
                returning *
                """,
                (user_id, script_id, new_title, "", Jsonb(state_snapshot)),
            ).fetchone()
        new_save_id = int(new_save["id"])

        # 2. 重建 branch_commits（保留 parent 关系，但 ID 重映射）
        commits_raw = payload.get("commits") or []
        if len(commits_raw) > MAX_COMMITS:
            raise ValueError(f"commits 数量超上限 {MAX_COMMITS}")
        old_to_new: dict[int, int] = {}
        for c in commits_raw:
            old_id = int(c.get("id") or 0)
            old_parent = c.get("parent_id")
            new_parent = old_to_new.get(int(old_parent)) if old_parent else None
            new_commit = db.execute(
                """
                insert into branch_commits(
                  save_id, parent_id, object_hash, tree_hash, turn_index,
                  kind, title, message, summary, content_preview,
                  state_path, player_input, gm_output, metadata, state_snapshot
                ) values (
                  %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                ) returning id
                """,
                (
                    new_save_id, new_parent,
                    c.get("object_hash") or secrets.token_hex(20),
                    c.get("tree_hash") or "",
                    int(c.get("turn_index") or 0),
                    c.get("kind") or "round",
                    c.get("title") or "",
                    c.get("message") or "",
                    (c.get("summary") or "")[:MAX_TEXT_BYTES],
                    c.get("content_preview") or "",
                    "",
                    (c.get("player_input") or "")[:MAX_TEXT_BYTES],
                    (c.get("gm_output") or "")[:MAX_TEXT_BYTES],
                    Jsonb(_check_json_size(c.get("metadata") or {}, "metadata")),
                    Jsonb(_check_json_size(c.get("state_snapshot") or {}, "state_snapshot")),
                ),
            ).fetchone()
            old_to_new[old_id] = int(new_commit["id"])

        # 3. 重建 branch_refs(保留所有命名分支头 + active 标记;target 随 commit 重映射)
        #    旧实现只硬造单个 refs/heads/main 指向最后一个 commit → 丢失导出里的其余所有分支头。
        #    分支树(读 branch_commits DAG)仍显示全部节点,但选任意非 main 分支「继续」时,
        #    _find_or_create_ref_for_commit 找不到指向该节点的 ref → 另造 refs/runtime/user-N
        #    → 用户报 #78「选任意分支都从头开始,并自动从根创建新分支」。export 本就 dump 了 refs,
        #    导入照样恢复即可。
        active_commit_id: int | None = None
        made_ref = False
        for r in payload.get("refs") or []:
            if not isinstance(r, dict):
                continue
            _tgt = r.get("target_commit_id")
            try:
                new_tgt = old_to_new.get(int(_tgt)) if _tgt is not None else None
            except (TypeError, ValueError):
                new_tgt = None
            if new_tgt is None:
                continue  # 目标 commit 没导进来 → 跳过(别造悬空 ref)
            is_active = bool(r.get("is_active"))
            db.execute(
                """
                insert into branch_refs(save_id, name, kind, target_commit_id, is_active)
                values (%s, %s, %s, %s, %s)
                on conflict (save_id, name) do update set
                  target_commit_id = excluded.target_commit_id,
                  is_active = excluded.is_active
                """,
                (new_save_id, r.get("name") or "refs/heads/main",
                 r.get("kind") or "head", new_tgt, is_active),
            )
            made_ref = True
            if is_active:
                active_commit_id = new_tgt
        # 兜底:旧版导出无 refs(v1)或全部 ref 目标缺失 → 退回单个 main 指向最后 commit
        if not made_ref and old_to_new:
            last_commit_id = list(old_to_new.values())[-1]
            db.execute(
                """
                insert into branch_refs(save_id, name, kind, target_commit_id, is_active)
                values (%s, %s, %s, %s, true)
                """,
                (new_save_id, "refs/heads/main", "head", last_commit_id),
            )
            active_commit_id = last_commit_id
        # active_commit_id:优先 export 标 active 的 ref;否则退回最后一个 commit
        if active_commit_id is None and old_to_new:
            active_commit_id = list(old_to_new.values())[-1]
        if active_commit_id is not None:
            db.execute(
                "update game_saves set active_commit_id = %s where id = %s",
                (active_commit_id, new_save_id),
            )

        # 4. task 69: 导入 9 张 per-save 状态表(v2 才有)
        state_imported: dict[str, int] = {}
        state_errors: dict[str, int] = {}  # 每张表行级失败计数（供 warnings 汇总）
        # identity_cards 导入时分配新 id(全局序列);save_character_identities.identity_id
        # 指向旧 id → 必须按 old→new 重映射,否则 FK 永远指向孤儿或其他存档的行。
        old_identity_to_new: dict[int, int] = {}
        if pv >= 2:
            state_tables = payload.get("state_tables") or {}
            for table, allow_missing in _STATE_TABLES:
                rows = state_tables.get(table) or []
                # 该表真实列白名单(防列名 SQL 注入,见 _table_columns)。表不存在 → 空集 → 全丢。
                allowed_cols = _table_columns(db, table)
                jsonb_cols = _jsonb_columns(db, table)
                count = 0
                err_count = 0
                for raw_row in rows:
                    if not isinstance(raw_row, dict):
                        continue
                    # 保留旧 id(供 identity_cards→save_character_identities 重映射)再剥离
                    old_row_id = raw_row.get("id")
                    row = _strip_id_and_save_id(raw_row)
                    if not row:
                        continue
                    # commit 外键随 branch_commits 一并重映射(old→new)。kb_* COW 行的 born_commit /
                    # retired_at_commit、kb_checkpoints 的 commit_id 都指向旧 commit id;commit id 是全局
                    # 序列,导入到他库后旧 id 极可能撞上别的存档的 commit → FK 满足却插成【孤儿】(materialize
                    # 的祖先 CTE 按本档 commit 查,查不到孤儿行)→ count>0 又挡掉 migrate-on-load 重建
                    # → 导入的存档加载为空。故按 old_to_new 重映射;NOT NULL 外键映射不到则跳过该行
                    # (别插孤儿,留给 migrate-on-load 从 blob 重建);可空的 retired_at_commit 映射不到置 NULL。
                    _orphan = False
                    for _ck in ("born_commit", "commit_id", "retired_at_commit"):
                        if row.get(_ck) is None:
                            continue
                        try:
                            _mapped = old_to_new.get(int(row[_ck]))
                        except (TypeError, ValueError):
                            _mapped = None
                        if _mapped is not None:
                            row[_ck] = _mapped
                        elif _ck == "retired_at_commit":
                            row[_ck] = None
                        else:
                            _orphan = True
                            break
                    if _orphan:
                        continue
                    # save_character_identities.identity_id → 重映射为本次导入分配的新 id。
                    # NOT NULL FK;映射不到 → 跳过该行(别插孤儿绑定)。
                    if table == "save_character_identities" and "identity_id" in row:
                        try:
                            _old_iid = int(row["identity_id"])
                        except (TypeError, ValueError):
                            _old_iid = None
                        _new_iid = old_identity_to_new.get(_old_iid) if _old_iid is not None else None
                        if _new_iid is None:
                            continue  # identity_card 行未导入成功,跳过此绑定
                        row["identity_id"] = _new_iid
                    try:
                        sql, vals = _build_insert(table, row, new_save_id, allowed_cols, jsonb_cols)
                        # 存档点:单行插入失败只回滚到此,不污染外层事务。否则(psycopg)失败语句会把整个
                        # 事务标记 aborted → 后续任何语句(下一张表的 _table_columns)都 InFailedSqlTransaction
                        # → 500 → with connect() 退出回滚 → 整个 save 丢失(用户报的「导入失败/只有剧本没存档」)。
                        with db.transaction():
                            if table == "identity_cards" and old_row_id is not None:
                                # 需要捕获新分配的 id 以便重映射。_build_insert 生成 on conflict do nothing,
                                # 追加 RETURNING id;on conflict 时返回空集 → 映射缺失 → 下游绑定行自动跳过。
                                ret = db.execute(sql.rstrip() + " returning id", vals).fetchone()
                                if ret is not None:
                                    try:
                                        old_identity_to_new[int(old_row_id)] = int(ret["id"])
                                    except (TypeError, ValueError, KeyError):
                                        pass
                            else:
                                db.execute(sql, vals)
                        count += 1
                    except Exception as exc:
                        # 单行失败不阻断同表其余行(行级独立,schema 漂移容错);
                        # savepoint 已回滚,外层事务仍可用。累计错误,末尾汇总一条 warning。
                        err_count += 1
                        # else: 静默吞,allow_missing 表整张表都可能不存在
                        if err_count == 1 and not allow_missing:
                            # 只记首次出错详情,避免 warnings 列表爆炸
                            warnings.append(f"{table} 行导入失败(首次): {type(exc).__name__}: {str(exc)[:120]}")
                        continue
                state_imported[table] = count
                state_errors[table] = err_count
                if err_count > 1 and not allow_missing:
                    warnings.append(f"{table} 共 {err_count} 行导入失败(已跳过,成功导入 {count} 行)")
            # 原存档是 kb_native(新 KB 流,带完整 kb_* 状态)且确实导入了 KB 行 → 新存档也标 kb_native,
            # 否则加载时被当旧档走 migrate-on-load 从 blob 重建,刚导入的 KB 状态白导。
            if save_data.get("kb_native") and state_imported.get("kb_entities", 0) > 0:
                db.execute("update game_saves set kb_native = true where id = %s", (new_save_id,))

        # 5. 导入 messages(导出包含 messages,但旧实现从未写回 → 导入后 messages 表为空,
        #    save_kb 按 save_id 查 messages 拿不到对话历史,GM 上下文全盲)。
        #    先建一行 game_sessions(仅存 session_id 关联),再逐条 remap session_id → new_session_id,
        #    save_id → new_save_id 写入。payload v1 可能无 messages 字段,静默跳过。
        messages_raw = payload.get("messages") or []
        messages_imported = 0
        if messages_raw:
            # 取(或建)该 save 的 session:先查是否已在 step 4 的 state 里间接建了 session,
            # 否则直接 insert 一行最小 session。
            new_session_row = db.execute(
                "select id from game_sessions where save_id = %s order by id limit 1",
                (new_save_id,),
            ).fetchone()
            if new_session_row is None:
                new_session_row = db.execute(
                    """
                    insert into game_sessions(save_id, context_size)
                    values (%s, 8192)
                    on conflict do nothing
                    returning id
                    """,
                    (new_save_id,),
                ).fetchone()
            new_session_id: int | None = int(new_session_row["id"]) if new_session_row else None

            if new_session_id is not None:
                # 允许的 messages 列(防注入;content/metadata/role/turn 是固定结构,直接枚举白名单)
                _msg_allowed = frozenset({"session_id", "save_id", "turn", "role", "content", "metadata"})
                _msg_jsonb = frozenset({"metadata"})
                _msg_err_count = 0
                for raw_msg in messages_raw:
                    if not isinstance(raw_msg, dict):
                        continue
                    row = dict(raw_msg)
                    # 剥离 id / created_at;强制覆盖外键
                    row.pop("id", None)
                    row.pop("created_at", None)
                    row["session_id"] = new_session_id
                    row["save_id"] = new_save_id
                    # content 截断保护
                    if isinstance(row.get("content"), str) and len(row["content"].encode()) > MAX_TEXT_BYTES:
                        row["content"] = row["content"].encode()[:MAX_TEXT_BYTES].decode("utf-8", errors="ignore")
                    try:
                        sql, vals = _build_insert("messages", row, new_save_id, _msg_allowed, _msg_jsonb)
                        with db.transaction():
                            db.execute(sql, vals)
                        messages_imported += 1
                    except Exception as exc:
                        _msg_err_count += 1
                        if _msg_err_count == 1:
                            warnings.append(f"messages 单行导入失败(首次): {type(exc).__name__}: {str(exc)[:120]}")
                        continue  # 行级独立:单条消息失败不影响其余消息
                if _msg_err_count > 1:
                    warnings.append(f"messages 共 {_msg_err_count} 行导入失败(已跳过,成功导入 {messages_imported} 条)")

    return {
        "ok": True,
        "save_id": new_save_id,
        "commits_imported": len(old_to_new),
        "state_imported": state_imported,
        "messages_imported": messages_imported,
        "warnings": warnings,
        "script_id": script_id,
        "save_kind": save_kind,
        "tavern_character_card_id": tavern_character_card_id,
    }
