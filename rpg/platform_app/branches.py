from __future__ import annotations

import hashlib
import json
import re
import secrets
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from state import SAVE_FILE

from . import runtime
from .db import connect, cursor_id, expose, init_db, limit_value


BASE = Path(__file__).resolve().parents[1]
BRANCH_STATE_DIR = BASE / "platform_data" / "branch_states"
MAIN_REF = "refs/heads/main"

# ── 异步 LLM 摘要 ─────────────────────────────────────────────────────────────
_SUMMARY_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="branch-summary")
_SUMMARY_GM = None
_SUMMARY_GM_LOCK = threading.Lock()
_LLM_SUMMARY_SYSTEM = (
    "你是剧情摘要助手。读完一回合的玩家输入和 GM 响应后，用 15-22 字概括这一回合发生了什么。\n"
    "要求：\n"
    "- 只输出摘要本身，不要前缀\n"
    "- 用动词为主，避免主语\n"
    "- 不带句号、引号、标签\n"
    "- 失败/拒绝/打断也要客观描述"
)


def _get_summary_gm():
    global _SUMMARY_GM
    if _SUMMARY_GM is not None:
        return _SUMMARY_GM
    with _SUMMARY_GM_LOCK:
        if _SUMMARY_GM is None:
            try:
                from gm import GameMaster
                _SUMMARY_GM = GameMaster()  # 默认 gemini-3.5-flash，够用
            except Exception:
                _SUMMARY_GM = False
    return _SUMMARY_GM or None


def _run_llm_summary(commit_id: int, player_text: str, gm_text: str) -> None:
    """后台线程：用 LLM 重写 branch_commits.summary。失败静默。"""
    try:
        gm = _get_summary_gm()
        if not gm:
            return
        prompt = f"玩家输入：\n{player_text[:600]}\n\nGM 响应：\n{gm_text[:1200]}"
        summary = gm._backend.call(
            system=_LLM_SUMMARY_SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=64,
        ).strip()
        # 清理标点、引号、前缀
        summary = re.sub(r"^[【「\"'：:\-—]+", "", summary)
        summary = re.sub(r"[】」\"'。！？!?]+$", "", summary)
        summary = summary.replace("\n", " ").strip()
        if len(summary) > 32:
            summary = summary[:32]
        if len(summary) < 4:
            return  # 太短的不写回，保留 rough_summary
        with connect() as db:
            db.execute(
                "update branch_commits set summary = %s where id = %s",
                (summary, commit_id),
            )
    except Exception:
        pass


def schedule_llm_summary(commit_id: int, player_text: str, gm_text: str) -> None:
    """fire-and-forget 触发 LLM 摘要后台任务。"""
    if not commit_id or not (player_text or gm_text):
        return
    try:
        _SUMMARY_POOL.submit(_run_llm_summary, int(commit_id), player_text or "", gm_text or "")
    except Exception:
        pass


def seed_tree(save_id: int, state_path: str) -> None:
    """Seed or migrate the immutable branch graph for one save.

    The public API still speaks in "nodes" for frontend compatibility, but the
    storage model is now Git-like:
    - branch_commits: immutable snapshots/rounds
    - branch_refs: named pointers to branch heads
    - runtime_checkouts: the currently running game worktree
    """

    init_db()
    BRANCH_STATE_DIR.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        if db.execute("select 1 from branch_commits where save_id = %s limit 1", (save_id,)).fetchone():
            ensure_state_snapshots(db, save_id)
            ensure_summaries(db, save_id)
            _ensure_active_ref(db, save_id)
            return
        if db.execute("select 1 from branch_nodes where save_id = %s limit 1", (save_id,)).fetchone():
            _migrate_legacy_nodes(db, save_id)
            ensure_state_snapshots(db, save_id)
            ensure_summaries(db, save_id)
            _ensure_active_ref(db, save_id)
            return

        # task 25：之前如果该 save 的 state_snapshot 是空（新建 save 的正常状态），
        # 会 fallback 到读 state_path 指向的共享 game_state.json —— 这是上一个激活的
        # save 的运行态，里面有别的玩家身份 / pending question / user_variables，
        # 直接污染新 save 的根 snapshot，用户进新游戏看到旧会话状态。
        # 修法：只信任 game_saves.state_snapshot 字段，它在 create_save() 里就已经写入
        # （新存档为 turn=0/history=[] 的种子）。如果 snapshot 完全为 None/空 dict 才允许
        # fallback —— 仅供历史数据（pre-snapshot 时代）兼容。
        save_row = db.execute("select state_snapshot from game_saves where id = %s", (save_id,)).fetchone()
        raw_snapshot = (save_row or {}).get("state_snapshot") if isinstance(save_row, dict) else None
        if isinstance(raw_snapshot, dict) and raw_snapshot:
            # snapshot 已存在（即便 history=[] turn=0 也算"权威的初始态"），不读共享文件
            data = json.loads(json.dumps(raw_snapshot, ensure_ascii=False))
        else:
            # 仅当 snapshot 完全缺失时才回退到 state_path（兼容历史数据）
            data = load_state(Path(state_path))
        root_snapshot = snapshot_for_history(data, 0)
        root_state = write_snapshot(save_id, 0, root_snapshot)
        root = _insert_commit(
            db,
            save_id=save_id,
            parent_id=None,
            turn_index=0,
            kind="root",
            title="开始",
            message="存档起点",
            summary="存档起点",
            content_preview="存档起点",
            state_path=root_state,
            state_snapshot=root_snapshot,
            metadata={"source": "seed"},
        )
        parent_id = root["id"]
        history = list(data.get("history") or [])
        history_index = 0
        turn = 1
        while history_index < len(history):
            player_msg = history[history_index] if history[history_index].get("role") == "user" else None
            if player_msg:
                history_index += 1
            gm_msg = None
            if history_index < len(history) and history[history_index].get("role") != "user":
                gm_msg = history[history_index]
                history_index += 1
            elif not player_msg and history_index < len(history):
                gm_msg = history[history_index]
                history_index += 1
            player_text = (player_msg or {}).get("content", "")
            gm_text = (gm_msg or {}).get("content", "")
            snapshot_data = snapshot_for_history(data, history_index)
            snapshot = write_snapshot(save_id, turn, snapshot_data)
            row = _insert_commit(
                db,
                save_id=save_id,
                parent_id=parent_id,
                turn_index=turn,
                kind="round",
                title=f"第 {turn} 回合",
                message=rough_summary(player_text, gm_text),
                summary=rough_summary(player_text, gm_text),
                content_preview=round_preview(player_text, gm_text),
                state_path=snapshot,
                state_snapshot=snapshot_data,
                player_input=player_text,
                gm_output=gm_text,
                metadata={"source": "seed", "history_index": history_index},
            )
            parent_id = row["id"]
            turn += 1
        ref = _upsert_ref(db, save_id, MAIN_REF, parent_id, active=True)
        _set_save_active(db, save_id, parent_id, ref["id"])


def tree(user_id: int, save_id: int, limit: int | str | None = None, cursor: str | None = None) -> dict[str, Any]:
    init_db()
    page_limit = limit_value(limit, default=1000, maximum=5000)
    after_id = cursor_id(cursor)
    with connect() as db:
        save = db.execute("select * from game_saves where id = %s and user_id = %s", (save_id, user_id)).fetchone()
        if not save:
            raise ValueError("无权访问该存档")
        needs_seed = not db.execute("select 1 from branch_commits where save_id = %s limit 1", (save_id,)).fetchone()
    if needs_seed:
        seed_tree(save_id, save["state_path"])
    with connect() as db:
        ensure_summaries(db, save_id)
        save = db.execute("select * from game_saves where id = %s and user_id = %s", (save_id, user_id)).fetchone()
        rows = db.execute(
            """
            select * from branch_commits
            where save_id = %s and (%s::bigint is null or id > %s)
            order by id
            limit %s
            """,
            (save_id, after_id, after_id, page_limit + 1),
        ).fetchall()
        visible_raw = rows[:page_limit]
        ref_rows = db.execute(
            "select name, target_commit_id, is_active from branch_refs where save_id = %s",
            (save_id,),
        ).fetchall()
    refs_by_commit: dict[int, list[str]] = {}
    active_ref_by_commit: set[int] = set()
    for ref in ref_rows:
        if ref.get("target_commit_id"):
            refs_by_commit.setdefault(ref["target_commit_id"], []).append(ref["name"])
            if ref.get("is_active"):
                active_ref_by_commit.add(ref["target_commit_id"])
    has_more = len(rows) > page_limit
    visible = display_nodes(visible_raw)
    active_commit_id = save.get("active_commit_id") or save.get("active_branch_node_id")
    for row in visible:
        row["commit_id"] = row["id"]
        row["node_id"] = row["id"]
        row["ref_names"] = refs_by_commit.get(row["id"], [])
        row["is_active"] = row["id"] == active_commit_id or row["id"] in active_ref_by_commit
        if row.get("object_hash"):
            row["object_hash_short"] = row["object_hash"][:10]
    return {
        "save": expose(save),
        "nodes": [expose(row) for row in visible],
        "refs": [expose(row) for row in ref_rows],
        "page": {
            "limit": page_limit,
            "next_cursor": str(visible_raw[-1]["id"]) if has_more and visible_raw else None,
            "has_more": has_more,
        },
    }


def continue_from(user_id: int, node_id: int) -> dict[str, Any]:
    init_db()
    active_commit_id = 0
    active_ref_id: int | None = None
    save_id = 0
    state_path = ""
    ref_row: dict[str, Any] | None = None
    with connect() as db:
        node = _commit_for_user(db, user_id, node_id)
        if not node:
            raise ValueError("无权访问该分支节点")

        save_id = node["save_id"]
        state_snapshot = commit_state(node)
        state_path = node["state_path"]
        ref = _upsert_ref(
            db,
            node["save_id"],
            f"refs/heads/from-{node['id']}-{secrets.token_hex(4)}",
            node["id"],
            active=True,
        )
        active_commit_id = node["id"]
        active_ref_id = ref["id"]
        ref_row = ref
        _set_save_active(db, save_id, active_commit_id, active_ref_id)
        _write_checkout(db, user_id, save_id, active_ref_id, active_commit_id)

    runtime_info = runtime.activate_state_snapshot(user_id, save_id, active_commit_id, state_snapshot, state_path, ref_id=active_ref_id)
    result = tree(user_id, save_id)
    result["ok"] = True
    result["runtime"] = runtime_info
    result["game_url"] = runtime_info["game_url"]
    result["runtime_url"] = runtime_info["game_url"]
    result["active_ref"] = expose(ref_row) if ref_row else None
    result["active_branch_node_id"] = active_commit_id
    result["active_commit_id"] = active_commit_id
    return result


def resolve_commit_id_by_message(user_id: int, save_id: int, message_index: int) -> int | None:
    """task 38：把 frontend 的 chat history message index 映射到 branch_commits.id。

    history 数组里 user/assistant 成对出现：msg=0 是 turn 0 的 player，msg=1 是 turn 0 的 gm，
    msg=2 是 turn 1 的 player ... → turn_index = message_index // 2，kind 取决于奇偶。
    优先返回该 turn_index 的 gm commit（fork 自然继承到 gm 输出之后），没有 gm 就退到 player。
    """
    init_db()
    try:
        turn_index = int(message_index) // 2
    except (TypeError, ValueError):
        return None
    is_player = (int(message_index) % 2 == 0) if message_index is not None else False
    with connect() as db:
        # 校验 save 归属
        owned = db.execute(
            "select 1 from game_saves where id = %s and user_id = %s",
            (save_id, user_id),
        ).fetchone()
        if not owned:
            return None
        # 优先 gm commit；如果点的是玩家自己的消息且 gm 还没回（最后一条 player）就用 player commit
        preferred_kind = "player" if is_player else "gm"
        row = db.execute(
            """
            select id, kind from branch_commits
            where save_id = %s and turn_index = %s and kind = %s
            order by id desc limit 1
            """,
            (save_id, turn_index, preferred_kind),
        ).fetchone()
        if row:
            return int(row["id"])
        # 兜底：任一 kind
        row = db.execute(
            """
            select id from branch_commits
            where save_id = %s and turn_index = %s
            order by id desc limit 1
            """,
            (save_id, turn_index),
        ).fetchone()
        return int(row["id"]) if row else None


def activate_node(user_id: int, node_id: int) -> dict[str, Any]:
    init_db()
    with connect() as db:
        node = _commit_for_user(db, user_id, node_id)
        if not node:
            raise ValueError("无权访问该分支节点")
        ref = _find_or_create_ref_for_commit(db, user_id, node)
        _set_save_active(db, node["save_id"], node["id"], ref["id"])
        _write_checkout(db, user_id, node["save_id"], ref["id"], node["id"])
        save_id = node["save_id"]
        state_path = node["state_path"]
        state_snapshot = commit_state(node)
        active_ref_id = ref["id"]
    runtime_info = runtime.activate_state_snapshot(user_id, save_id, node_id, state_snapshot, state_path, ref_id=active_ref_id)
    result = tree(user_id, save_id)
    result["ok"] = True
    result["runtime"] = runtime_info
    result["game_url"] = runtime_info["game_url"]
    result["runtime_url"] = runtime_info["game_url"]
    result["active_branch_node_id"] = node_id
    result["active_commit_id"] = node_id
    return result


def activate_save(user_id: int, save_id: int) -> dict[str, Any]:
    """task 30：切到目标 save 的当前激活分支（或没有就 root），并真的切换 user_runtime。

    原 frontend_routes.api_save_activate 只 select 1 ownership 就返回 ok=True，
    既不写 user_runtime，也不清 ui 内存缓存 → GET /api/state 仍读旧 save 的 state，
    用户看到的是上一份存档的 player/world。

    这里：
      1. 找 save 的 active_branch_node_id（无则取最早的 root commit）
      2. 加载/创建对应 ref，写 _set_save_active + _write_checkout
      3. runtime.activate_state_snapshot 把 user_runtime 写成该 save_id + commit
      4. 调用方（frontend_routes / ui）负责清 ui._state_by_user 缓存
    """
    init_db()
    with connect() as db:
        save = db.execute(
            "select * from game_saves where id = %s and user_id = %s",
            (save_id, user_id),
        ).fetchone()
        if not save:
            raise ValueError("无权访问该存档")
        node_id = save.get("active_branch_node_id")
        commit_row = None
        if node_id:
            commit_row = db.execute(
                "select * from branch_commits where id = %s and save_id = %s",
                (int(node_id), save_id),
            ).fetchone()
        if not commit_row:
            commit_row = db.execute(
                "select * from branch_commits where save_id = %s order by turn_index asc, id asc limit 1",
                (save_id,),
            ).fetchone()
        if not commit_row:
            # 没有任何 commit：先 seed_tree 把 root 建出来再取
            seed_tree(save_id, save.get("state_path") or "")
            commit_row = db.execute(
                "select * from branch_commits where save_id = %s order by turn_index asc, id asc limit 1",
                (save_id,),
            ).fetchone()
        if not commit_row:
            raise ValueError("save 没有任何 commit，无法激活")
        ref = _find_or_create_ref_for_commit(db, user_id, commit_row)
        _set_save_active(db, save_id, commit_row["id"], ref["id"])
        _write_checkout(db, user_id, save_id, ref["id"], commit_row["id"])
        state_snapshot = commit_state(commit_row)
        state_path = commit_row.get("state_path") or save.get("state_path") or ""
        active_ref_id = ref["id"]
        active_commit_id = commit_row["id"]
    runtime_info = runtime.activate_state_snapshot(
        user_id, save_id, active_commit_id, state_snapshot, state_path, ref_id=active_ref_id,
    )
    return {
        "ok": True,
        "active_save_id": save_id,
        "active_commit_id": active_commit_id,
        "active_branch_node_id": active_commit_id,
        "runtime": runtime_info,
    }


def record_runtime_turn(
    player_input: str,
    gm_response: str,
    runtime_state_path: str | None = None,
    user_id: int | None = None,
    state_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """多用户安全：调用方应传 state_data=state.data 而不是依赖 runtime_state_path 读文件。

    state_data 优先于 runtime_state_path。只有兼容旧调用且 state_data=None 时
    才从文件读，这时如果路径是全局 SAVE_FILE，多用户场景会读错——这是历史 bug。
    """
    meta = runtime.read_runtime(user_id=user_id) or bootstrap_runtime_binding(user_id=user_id)
    if user_id and int(meta.get("user_id") or 0) not in {0, int(user_id)}:
        meta = bootstrap_runtime_binding(user_id=user_id)
    if not meta:
        return {"ok": False, "reason": "未激活存档分支 runtime"}
    save_id = int(meta.get("save_id") or 0)
    parent_id = int(meta.get("active_commit_id") or meta.get("active_branch_node_id") or 0)
    ref_id = int(meta.get("active_ref_id") or 0) or None
    if not save_id or not parent_id:
        return {"ok": False, "reason": "runtime 缺少存档或节点"}

    state_path = Path(runtime_state_path or SAVE_FILE)
    # 优先用调用方传入的 state_data，避免读全局 SAVE_FILE 引入并发污染
    if isinstance(state_data, dict):
        data = json.loads(json.dumps(state_data, ensure_ascii=False))
    else:
        data = load_state(state_path)
    turn = int(data.get("turn") or 0)
    summary = rough_summary(player_input, gm_response)
    preview = round_preview(player_input, gm_response)
    snapshot_path = write_runtime_snapshot(save_id, data)

    init_db()
    missing_parent = False
    with connect() as db:
        parent = db.execute("select * from branch_commits where id = %s and save_id = %s", (parent_id, save_id)).fetchone()
        if not parent:
            missing_parent = True
        else:
            save = db.execute("select * from game_saves where id = %s", (save_id,)).fetchone()
            if user_id and (not save or int(save["user_id"]) != int(user_id)):
                return {"ok": False, "reason": "runtime 不属于当前用户"}
            if not ref_id:
                ref = _find_or_create_ref_for_commit(db, int(save["user_id"]), parent)
                ref_id = ref["id"]
            row = _insert_commit(
                db,
                save_id=save_id,
                parent_id=parent_id,
                turn_index=turn,
                kind="round",
                title=f"第 {turn} 回合",
                message=summary,
                summary=summary,
                content_preview=preview,
                state_path=snapshot_path,
                state_snapshot=data,
                player_input=player_input,
                gm_output=gm_response,
                metadata={"source": "runtime", "parent_commit_id": parent_id, "nonce": secrets.token_hex(8)},
            )
            _upsert_ref_by_id(db, ref_id, row["id"], active=True)
            _set_save_active(db, save_id, row["id"], ref_id)
            _write_checkout(db, int(save["user_id"]), save_id, ref_id, row["id"])
    if missing_parent:
        rebound = bootstrap_runtime_binding(user_id=user_id)
        if rebound and rebound.get("active_commit_id") != parent_id:
            return record_runtime_turn(player_input, gm_response, runtime_state_path, user_id=user_id)
        return {"ok": False, "reason": "runtime 指向的父节点不存在"}
    # 关键修复：把当前 user_id 传给 update_active_node，确保 per-user runtime 文件
    # 更新到最新 commit，否则下一轮 parent_id 会停在旧 commit 上，分支链断裂。
    effective_user_id = user_id or int(save.get("user_id") or 0)
    runtime_info = runtime.update_active_node(
        row["id"], snapshot_path, ref_id=ref_id, user_id=effective_user_id,
    )
    # 异步重写 summary 为 LLM 生成的 15-22 字剧情摘要
    schedule_llm_summary(int(row["id"]), player_input, gm_response)
    return {"ok": True, "node": expose(row), "runtime": runtime_info}


def persist_runtime_state(
    runtime_state_path: str | None = None,
    user_id: int | None = None,
    state_data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Persist the mutable game worktree without creating a new commit.

    Story turns create immutable branch_commits. Commands such as /time, /set,
    permission changes, or manual saves are closer to a Git worktree update: the
    active checkout and save head should reflect them, but the commit graph must
    not grow a fake round.
    """

    meta = runtime.read_runtime(user_id=user_id) or bootstrap_runtime_binding(user_id=user_id)
    if user_id and int(meta.get("user_id") or 0) not in {0, int(user_id)}:
        meta = bootstrap_runtime_binding(user_id=user_id)
    if not meta:
        return {"ok": False, "reason": "未激活存档 runtime"}

    save_id = int(meta.get("save_id") or 0)
    commit_id = int(meta.get("active_commit_id") or meta.get("active_branch_node_id") or 0)
    ref_id = int(meta.get("active_ref_id") or 0) or None
    if not save_id or not commit_id:
        return {"ok": False, "reason": "runtime 缺少存档或节点"}

    state_path = Path(runtime_state_path or meta.get("runtime_state_path") or SAVE_FILE)
    state_data = json.loads(json.dumps(state_data, ensure_ascii=False)) if isinstance(state_data, dict) else load_state(state_path)
    init_db()
    with connect() as db:
        save = db.execute("select * from game_saves where id = %s", (save_id,)).fetchone()
        if user_id and (not save or int(save["user_id"]) != int(user_id)):
            return {"ok": False, "reason": "runtime 不属于当前用户"}
        if not save:
            return {"ok": False, "reason": "存档不存在"}
        db_snapshot = commit_state(save)
        if _snapshot_quality(state_data) + 5 < _snapshot_quality(db_snapshot):
            state_data = db_snapshot
            state_path = Path(save.get("state_path") or state_path)
        db.execute(
            """
            update game_saves
            set state_snapshot = %s,
                active_commit_id = %s,
                active_branch_node_id = %s,
                active_branch_ref_id = %s,
                row_version = row_version + 1,
                updated_at = now()
            where id = %s
            """,
            (Jsonb(state_data), commit_id, commit_id, ref_id, save_id),
        )
        snap_hash = _state_snapshot_hash(state_data)
        turn = int(state_data.get("turn", 0)) if isinstance(state_data, dict) else 0
        # 用户主动 save 时，runtime 与最新 commit 已对齐 → dirty=false
        db.execute(
            """
            insert into runtime_checkouts(user_id, save_id, ref_id, commit_id, runtime_state_path, state_snapshot,
                                           snapshot_hash, dirty, turn_at_commit, turn_runtime)
            values (%s, %s, %s, %s, %s, %s, %s, false, %s, %s)
            on conflict(user_id, save_id) do update
              set ref_id = excluded.ref_id,
                  commit_id = excluded.commit_id,
                  runtime_state_path = excluded.runtime_state_path,
                  state_snapshot = excluded.state_snapshot,
                  snapshot_hash = excluded.snapshot_hash,
                  dirty = false,
                  turn_at_commit = excluded.turn_at_commit,
                  turn_runtime = excluded.turn_runtime,
                  row_version = runtime_checkouts.row_version + 1,
                  updated_at = now()
            """,
            (int(save["user_id"]), save_id, ref_id, commit_id, str(state_path), Jsonb(state_data),
             snap_hash, turn, turn),
        )
    runtime_info = runtime.write_runtime(int(save["user_id"]), save_id, commit_id, str(state_path), ref_id=ref_id)
    runtime_info["commit_id"] = commit_id
    runtime_info["dirty"] = False
    return {"ok": True, "runtime": runtime_info, "commit_id": commit_id}


def bootstrap_runtime_binding(user_id: int | None = None) -> dict[str, Any]:
    init_db()
    seed_request: tuple[int, int, str] | None = None
    with connect() as db:
        if user_id:
            save = db.execute(
                """
                select game_saves.*, users.id as owner_id
                from game_saves join users on users.id = game_saves.user_id
                where users.id = %s
                order by game_saves.updated_at desc, game_saves.id desc
                limit 1
                """,
                (user_id,),
            ).fetchone()
        else:
            save = db.execute(
                """
                select game_saves.*, users.id as owner_id
                from game_saves join users on users.id = game_saves.user_id
                order by game_saves.updated_at desc, game_saves.id desc
                limit 1
                """
            ).fetchone()
        if not save:
            return {}
        commit = None
        commit_id = save.get("active_commit_id") or save.get("active_branch_node_id")
        if commit_id:
            commit = db.execute("select * from branch_commits where id = %s and save_id = %s", (commit_id, save["id"])).fetchone()
        if not commit:
            commit = db.execute(
                "select * from branch_commits where save_id = %s order by id desc limit 1",
                (save["id"],),
            ).fetchone()
        if not commit:
            seed_path = save.get("state_path") or str(SAVE_FILE)
            owner_id = save["owner_id"]
            save_id = save["id"]
            seed_request = (owner_id, save_id, seed_path)
            ref = None
        else:
            ref = db.execute(
                "select * from branch_refs where save_id = %s and is_active = true and target_commit_id = %s order by id desc limit 1",
                (save["id"], commit["id"]),
            ).fetchone()
            if not ref:
                ref = _find_or_create_ref_for_commit(db, int(save["owner_id"]), commit)
            _set_save_active(db, save["id"], commit["id"], ref["id"])
            _write_checkout(db, int(save["owner_id"]), save["id"], ref["id"], commit["id"])
    if seed_request:
        owner_id, save_id, seed_path = seed_request
        return _seed_and_bootstrap(owner_id, save_id, seed_path, user_id=user_id)
    return runtime.activate_state_snapshot(save["owner_id"], save["id"], commit["id"], commit_state(commit), commit["state_path"], ref_id=ref["id"])


def delete_subtree(user_id: int, node_id: int) -> dict[str, Any]:
    init_db()
    runtime_payload: dict[str, Any] | None = None
    with connect() as db:
        node = _commit_for_user(db, user_id, node_id)
        if not node:
            raise ValueError("无权访问该分支节点")
        node = round_start_node(db, node)
        if node["parent_id"] is None:
            raise ValueError("不能删除根节点")
        ids = collect_ids(db, node["id"])
        paths = [
            row["state_path"]
            for row in db.execute("select state_path from branch_commits where id = any(%s)", (ids,)).fetchall()
        ]
        save = db.execute("select * from game_saves where id = %s", (node["save_id"],)).fetchone()
        fallback = db.execute(
            "select * from branch_commits where id = %s and save_id = %s",
            (node["parent_id"], node["save_id"]),
        ).fetchone()
        active_commit_id = save.get("active_commit_id") or save.get("active_branch_node_id")
        active_deleted = active_commit_id in ids
        db.execute("delete from branch_refs where save_id = %s and target_commit_id = any(%s)", (node["save_id"], ids))
        db.execute("delete from branch_commits where id = any(%s)", (ids,))
        if active_deleted and fallback:
            ref = _upsert_ref(db, node["save_id"], MAIN_REF, fallback["id"], active=True)
            _set_save_active(db, node["save_id"], fallback["id"], ref["id"])
            _write_checkout(db, user_id, node["save_id"], ref["id"], fallback["id"])
            runtime_payload = runtime.activate_state_snapshot(
                user_id,
                node["save_id"],
                fallback["id"],
                commit_state(fallback),
                fallback["state_path"],
                ref_id=ref["id"],
            )
        save_id = node["save_id"]
    for path in paths:
        _unlink_branch_state(path)
    result = tree(user_id, save_id)
    if runtime_payload:
        result["runtime"] = runtime_payload
    return result


def _seed_and_bootstrap(owner_id: int, save_id: int, state_path: str, user_id: int | None) -> dict[str, Any]:
    seed_tree(save_id, state_path)
    return bootstrap_runtime_binding(user_id=user_id or owner_id)


def _migrate_legacy_nodes(db, save_id: int) -> None:
    rows = db.execute("select * from branch_nodes where save_id = %s order by id", (save_id,)).fetchall()
    id_map: dict[int, int] = {}
    for row in rows:
        parent_id = id_map.get(row.get("parent_id"))
        state_snapshot = load_state(Path(str(row.get("state_path") or "")))
        commit = _insert_commit(
            db,
            save_id=save_id,
            parent_id=parent_id,
            turn_index=int(row.get("turn_index") or 0),
            kind=str(row.get("role") or "round"),
            title=str(row.get("title") or ""),
            message=str(row.get("summary") or row.get("title") or ""),
            summary=str(row.get("summary") or ""),
            content_preview=str(row.get("content_preview") or ""),
            state_path=str(row.get("state_path") or ""),
            state_snapshot=state_snapshot,
            metadata={"source": "legacy_branch_nodes", "legacy_node_id": row["id"]},
        )
        id_map[row["id"]] = commit["id"]
        if row.get("role") == "branch":
            _upsert_ref(db, save_id, f"refs/heads/legacy-{row['id']}", commit["id"], active=False)
    save = db.execute("select * from game_saves where id = %s", (save_id,)).fetchone()
    active_old = save.get("active_branch_node_id") if save else None
    active_commit_id = id_map.get(active_old) if active_old else None
    if not active_commit_id and rows:
        active_commit_id = id_map.get(rows[-1]["id"])
    if active_commit_id:
        ref = _upsert_ref(db, save_id, MAIN_REF, active_commit_id, active=True)
        _set_save_active(db, save_id, active_commit_id, ref["id"])


def _insert_commit(
    db,
    *,
    save_id: int,
    parent_id: int | None,
    turn_index: int,
    kind: str,
    title: str,
    message: str,
    summary: str,
    content_preview: str,
    state_path: str,
    state_snapshot: dict[str, Any] | None = None,
    player_input: str = "",
    gm_output: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    metadata = metadata or {}
    snapshot = state_snapshot if isinstance(state_snapshot, dict) else load_state(Path(state_path))
    tree_hash = _state_snapshot_hash(snapshot) or _state_file_hash(state_path)
    object_hash = _object_hash(
        {
            "save_id": save_id,
            "parent_id": parent_id,
            "turn_index": turn_index,
            "kind": kind,
            "title": title,
            "message": message,
            "summary": summary,
            "content_preview": content_preview,
            "state_path": state_path,
            "tree_hash": tree_hash,
            "state_snapshot": snapshot,
            "player_input": player_input,
            "gm_output": gm_output,
            "metadata": metadata,
        }
    )
    return db.execute(
        """
        insert into branch_commits(
          save_id, parent_id, object_hash, tree_hash, turn_index, kind, title,
          message, summary, content_preview, state_path, state_snapshot, player_input, gm_output, metadata
        )
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict(save_id, object_hash) do update
          set state_snapshot = branch_commits.state_snapshot,
              row_version = branch_commits.row_version
        returning *
        """,
        (
            save_id,
            parent_id,
            object_hash,
            tree_hash,
            int(turn_index or 0),
            kind,
            title,
            message,
            summary,
            content_preview,
            state_path,
            Jsonb(snapshot),
            player_input,
            gm_output,
            Jsonb(metadata),
        ),
    ).fetchone()


def _object_hash(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _state_file_hash(path: str) -> str:
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except Exception:
        return ""


def _state_snapshot_hash(state: dict[str, Any]) -> str:
    try:
        encoded = json.dumps(state or {}, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()
    except Exception:
        return ""


def _upsert_ref(db, save_id: int, name: str, target_commit_id: int, *, active: bool, kind: str = "head") -> dict[str, Any]:
    if active:
        db.execute("update branch_refs set is_active = false where save_id = %s", (save_id,))
    return db.execute(
        """
        insert into branch_refs(save_id, name, kind, target_commit_id, is_active)
        values (%s, %s, %s, %s, %s)
        on conflict(save_id, name) do update
          set kind = excluded.kind,
              target_commit_id = excluded.target_commit_id,
              is_active = excluded.is_active,
              row_version = branch_refs.row_version + 1,
              updated_at = now()
        returning *
        """,
        (save_id, name, kind, target_commit_id, active),
    ).fetchone()


def _upsert_ref_by_id(db, ref_id: int, target_commit_id: int, *, active: bool) -> dict[str, Any]:
    ref = db.execute("select * from branch_refs where id = %s", (ref_id,)).fetchone()
    if not ref:
        raise ValueError("runtime 指向的分支引用不存在")
    if active:
        db.execute("update branch_refs set is_active = false where save_id = %s", (ref["save_id"],))
    return db.execute(
        """
        update branch_refs
        set target_commit_id = %s, is_active = %s, row_version = row_version + 1, updated_at = now()
        where id = %s
        returning *
        """,
        (target_commit_id, active, ref_id),
    ).fetchone()


def _find_or_create_ref_for_commit(db, user_id: int, commit: dict[str, Any]) -> dict[str, Any]:
    ref = db.execute(
        """
        select * from branch_refs
        where save_id = %s and target_commit_id = %s
        order by case when kind = 'head' then 0 else 1 end, id desc
        limit 1
        """,
        (commit["save_id"], commit["id"]),
    ).fetchone()
    if ref:
        return _upsert_ref(db, commit["save_id"], ref["name"], commit["id"], active=True, kind=ref["kind"])
    return _upsert_ref(
        db,
        commit["save_id"],
        f"refs/runtime/user-{user_id}",
        commit["id"],
        active=True,
        kind="runtime",
    )


def _ensure_active_ref(db, save_id: int) -> None:
    save = db.execute("select * from game_saves where id = %s", (save_id,)).fetchone()
    if not save:
        return
    commit_id = save.get("active_commit_id") or save.get("active_branch_node_id")
    commit = None
    if commit_id:
        commit = db.execute("select * from branch_commits where id = %s and save_id = %s", (commit_id, save_id)).fetchone()
    if not commit:
        commit = db.execute("select * from branch_commits where save_id = %s order by id desc limit 1", (save_id,)).fetchone()
    if not commit:
        return
    ref = db.execute(
        "select * from branch_refs where save_id = %s and is_active = true and target_commit_id = %s order by id desc limit 1",
        (save_id, commit["id"]),
    ).fetchone()
    if not ref:
        ref = _upsert_ref(db, save_id, MAIN_REF, commit["id"], active=True)
    _set_save_active(db, save_id, commit["id"], ref["id"])


def _set_save_active(db, save_id: int, commit_id: int, ref_id: int | None) -> None:
    commit = db.execute("select state_snapshot from branch_commits where id = %s and save_id = %s", (commit_id, save_id)).fetchone()
    state_snapshot = commit_state(commit or {})
    db.execute(
        """
        update game_saves
        set active_branch_node_id = %s,
            active_commit_id = %s,
            active_branch_ref_id = %s,
            state_snapshot = %s,
            row_version = row_version + 1,
            updated_at = now()
        where id = %s
        """,
        (commit_id, commit_id, ref_id, Jsonb(state_snapshot), save_id),
    )


def _write_checkout(db, user_id: int, save_id: int, ref_id: int | None, commit_id: int) -> None:
    commit = db.execute("select state_snapshot from branch_commits where id = %s and save_id = %s", (commit_id, save_id)).fetchone()
    state_snapshot = commit_state(commit or {})
    snap_hash = _state_snapshot_hash(state_snapshot)
    turn_at_commit = int(state_snapshot.get("turn", 0)) if isinstance(state_snapshot, dict) else 0
    db.execute(
        """
        insert into runtime_checkouts(user_id, save_id, ref_id, commit_id, runtime_state_path, state_snapshot,
                                       snapshot_hash, dirty, turn_at_commit, turn_runtime)
        values (%s, %s, %s, %s, %s, %s, %s, false, %s, %s)
        on conflict(user_id, save_id) do update
          set ref_id = excluded.ref_id,
              commit_id = excluded.commit_id,
              runtime_state_path = excluded.runtime_state_path,
              state_snapshot = excluded.state_snapshot,
              snapshot_hash = excluded.snapshot_hash,
              dirty = false,
              turn_at_commit = excluded.turn_at_commit,
              turn_runtime = excluded.turn_runtime,
              row_version = runtime_checkouts.row_version + 1,
              updated_at = now()
        """,
        (user_id, save_id, ref_id, commit_id, str(SAVE_FILE), Jsonb(state_snapshot), snap_hash, turn_at_commit, turn_at_commit),
    )


def mark_runtime_dirty(save_id: int, runtime_state: dict[str, Any]) -> None:
    """Runtime state 已被改写、但尚未 commit 时调用。"""
    snap_hash = _state_snapshot_hash(runtime_state)
    turn = int(runtime_state.get("turn", 0)) if isinstance(runtime_state, dict) else 0
    with connect() as db:
        db.execute(
            """
            update runtime_checkouts
               set state_snapshot = %s,
                   snapshot_hash = %s,
                   turn_runtime = %s,
                   dirty = (snapshot_hash <> %s OR turn_runtime <> %s),
                   row_version = row_version + 1,
                   updated_at = now()
             where save_id = %s
            """,
            (Jsonb(runtime_state), snap_hash, turn, snap_hash, turn, save_id),
        )


def _commit_for_user(db, user_id: int, commit_id: int) -> dict[str, Any] | None:
    row = db.execute(
        """
        select branch_commits.*, game_saves.user_id
        from branch_commits join game_saves on game_saves.id = branch_commits.save_id
        where branch_commits.id = %s
        """,
        (commit_id,),
    ).fetchone()
    if not row or int(row["user_id"]) != int(user_id):
        return None
    return row


def collect_ids(db, node_id: int) -> list[int]:
    ids = [node_id]
    queue = [node_id]
    while queue:
        current = queue.pop(0)
        children = [row["id"] for row in db.execute("select id from branch_commits where parent_id = %s", (current,)).fetchall()]
        ids.extend(children)
        queue.extend(children)
    return ids


def round_start_node(db, node: dict[str, Any]) -> dict[str, Any]:
    if node.get("kind") != "gm" or not node.get("parent_id"):
        return node
    parent = db.execute("select * from branch_commits where id = %s", (node["parent_id"],)).fetchone()
    if parent and parent["kind"] == "player" and parent["save_id"] == node["save_id"] and parent["turn_index"] == node["turn_index"]:
        return {**parent, "user_id": node["user_id"]}
    return node


def ensure_summaries(db, save_id: int) -> None:
    rows = db.execute("select * from branch_commits where save_id = %s order by id", (save_id,)).fetchall()
    by_id = {row["id"]: row for row in rows}
    for row in rows:
        current = row.get("summary") or ""
        if current and current != "空回合" and not current.startswith("我好像"):
            continue
        player_text = row.get("player_input") or ""
        gm_text = row.get("gm_output") or ""
        if not player_text and not gm_text:
            if row["kind"] == "gm":
                parent = by_id.get(row.get("parent_id"))
                if parent and parent["kind"] == "player" and parent["turn_index"] == row["turn_index"]:
                    player_text = parent.get("content_preview", "")
                gm_text = row.get("content_preview", "")
            elif row["kind"] == "player":
                player_text = row.get("content_preview", "")
            elif row["kind"] == "round":
                gm_text = row.get("content_preview", "")
            else:
                gm_text = row.get("content_preview", "") or row.get("title", "")
        db.execute("update branch_commits set summary = %s where id = %s", (rough_summary(player_text, gm_text), row["id"]))


def ensure_state_snapshots(db, save_id: int) -> None:
    rows = db.execute(
        """
        select id, state_path, state_snapshot
        from branch_commits
        where save_id = %s
          and (state_snapshot = '{}'::jsonb or state_snapshot is null)
        order by id
        """,
        (save_id,),
    ).fetchall()
    for row in rows:
        snapshot = load_state(Path(row.get("state_path") or ""))
        db.execute(
            """
            update branch_commits
            set state_snapshot = %s,
                tree_hash = %s,
                row_version = row_version + 1
            where id = %s
            """,
            (Jsonb(snapshot), _state_snapshot_hash(snapshot), row["id"]),
        )


def display_nodes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = [dict(row) for row in rows]
    children: dict[int | None, list[dict[str, Any]]] = {}
    for row in ordered:
        row["role"] = row.get("kind", row.get("role"))
        children.setdefault(row.get("parent_id"), []).append(row)

    consumed: set[int] = set()
    raw_to_display: dict[int | None, int | None] = {None: None}
    displays: list[dict[str, Any]] = []

    for row in ordered:
        if row["id"] in consumed:
            continue
        role = row.get("role")
        if role == "player":
            gm = next(
                (
                    child
                    for child in children.get(row["id"], [])
                    if child.get("role") == "gm" and child.get("turn_index") == row.get("turn_index")
                ),
                None,
            )
            if gm:
                display = dict(gm)
                display.update(
                    {
                        "kind": "round",
                        "role": "round",
                        "title": f"第 {row['turn_index']} 回合",
                        "summary": rough_summary(row.get("content_preview", ""), gm.get("content_preview", "")),
                        "content_preview": round_preview(row.get("content_preview", ""), gm.get("content_preview", "")),
                        "source_node_ids": [row["id"], gm["id"]],
                        "_parent_raw": row.get("parent_id"),
                    }
                )
                raw_to_display[row["id"]] = gm["id"]
                raw_to_display[gm["id"]] = gm["id"]
                consumed.update({row["id"], gm["id"]})
                displays.append(display)
                continue
            display = dict(row)
            display.update(
                {
                    "kind": "round",
                    "role": "round",
                    "title": f"第 {row['turn_index']} 回合",
                    "summary": rough_summary(row.get("content_preview", ""), ""),
                    "content_preview": round_preview(row.get("content_preview", ""), ""),
                    "source_node_ids": [row["id"]],
                    "_parent_raw": row.get("parent_id"),
                }
            )
        elif role == "gm":
            display = dict(row)
            display.update(
                {
                    "kind": "round",
                    "role": "round",
                    "title": f"第 {row['turn_index']} 回合",
                    "summary": rough_summary("", row.get("content_preview", "")),
                    "content_preview": round_preview("", row.get("content_preview", "")),
                    "source_node_ids": [row["id"]],
                    "_parent_raw": row.get("parent_id"),
                }
            )
        else:
            display = dict(row)
            display["role"] = display.get("kind", display.get("role"))
            display["_parent_raw"] = row.get("parent_id")
            display["source_node_ids"] = [row["id"]]
            if not display.get("summary"):
                display["summary"] = rough_summary("", display.get("content_preview", "") or display.get("title", ""))
        raw_to_display[row["id"]] = display["id"]
        consumed.add(row["id"])
        displays.append(display)

    for display in displays:
        parent_raw = display.pop("_parent_raw", display.get("parent_id"))
        parent_id = raw_to_display.get(parent_raw, parent_raw)
        display["parent_id"] = None if parent_id == display["id"] else parent_id
    return displays


def load_state(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"history": [], "turn": 0}


def commit_state(row: dict[str, Any]) -> dict[str, Any]:
    snapshot = row.get("state_snapshot") if isinstance(row, dict) else None
    if isinstance(snapshot, dict) and snapshot:
        return json.loads(json.dumps(snapshot, ensure_ascii=False))
    path = row.get("state_path") if isinstance(row, dict) else ""
    if path:
        return load_state(Path(path))
    return {"history": [], "turn": 0}


def _snapshot_quality(state: dict[str, Any]) -> int:
    if not isinstance(state, dict):
        return 0
    player = state.get("player") if isinstance(state.get("player"), dict) else {}
    history = state.get("history") if isinstance(state.get("history"), list) else []
    return len(history) * 10 + int(state.get("turn") or 0) + (10 if player.get("name") else 0)


def snapshot_for_history(data: dict[str, Any], history_len: int) -> dict[str, Any]:
    snap = json.loads(json.dumps(data, ensure_ascii=False))
    snap["history"] = list((snap.get("history") or [])[:history_len])
    snap["turn"] = max(0, history_len // 2)
    return snap


def write_snapshot(save_id: int, index: int, data: dict[str, Any]) -> str:
    BRANCH_STATE_DIR.mkdir(parents=True, exist_ok=True)
    snap = json.loads(json.dumps(data, ensure_ascii=False))
    path = BRANCH_STATE_DIR / f"save_{save_id}_commit_seed_{index}.json"
    path.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def write_runtime_snapshot(save_id: int, data: dict[str, Any]) -> str:
    BRANCH_STATE_DIR.mkdir(parents=True, exist_ok=True)
    snap = json.loads(json.dumps(data, ensure_ascii=False))
    turn = int(snap.get("turn") or 0)
    path = BRANCH_STATE_DIR / f"save_{save_id}_runtime_turn_{turn}_{secrets.token_hex(4)}.json"
    path.write_text(json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def copy_state(source_path: str, save_id: int, label: str) -> str:
    BRANCH_STATE_DIR.mkdir(parents=True, exist_ok=True)
    target = BRANCH_STATE_DIR / f"save_{save_id}_{label}_{secrets.token_hex(4)}.json"
    source = Path(source_path)
    if source.exists():
        shutil.copy2(source, target)
    else:
        target.write_text(json.dumps({"history": [], "turn": 0}, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(target)


def write_named_snapshot(save_id: int, label: str, data: dict[str, Any]) -> str:
    BRANCH_STATE_DIR.mkdir(parents=True, exist_ok=True)
    target = BRANCH_STATE_DIR / f"save_{save_id}_{label}_{secrets.token_hex(4)}.json"
    target.write_text(json.dumps(data or {"history": [], "turn": 0}, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(target)


def _unlink_branch_state(path: str) -> None:
    if not path:
        return
    try:
        state_path = Path(path).resolve()
        root = BRANCH_STATE_DIR.resolve()
        if str(state_path).startswith(str(root) + "/"):
            state_path.unlink(missing_ok=True)
    except Exception:
        return


def compact(text: str, limit: int = 120) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "..."


def round_preview(player_text: str, gm_text: str, limit: int = 260) -> str:
    parts = []
    if clean_text(player_text):
        parts.append(f"玩家：{compact(clean_text(player_text), 90)}")
    if clean_text(gm_text):
        parts.append(f"GM：{compact(clean_text(gm_text), 170)}")
    return compact(" / ".join(parts) or "空回合", limit)


def rough_summary(player_text: str, gm_text: str = "", limit: int = 22) -> str:
    player = clean_text(player_text)
    gm = clean_text(gm_text)
    source = player
    if is_continue(player):
        source = gm or "继续当前剧情"
    elif len(source) <= 2:
        source = gm or source
    if not source:
        source = "空回合"
    source = first_clause(source)
    source = re.sub(r"^(我好像|我想要|我想|我要|我把|我先|我)", "", source)
    source = source.strip(" ，。！？；：、,.!?;:-")
    return source if len(source) <= limit else source[:limit]


def clean_text(text: str) -> str:
    text = re.sub(r"【[^】]*】", " ", text or "")
    text = re.sub(r"[*_#>`]+", " ", text)
    text = text.replace("“", "").replace("”", "").replace("「", "").replace("」", "")
    text = text.replace("（", " ").replace("）", " ").replace("(", " ").replace(")", " ")
    return " ".join(text.split()).strip()


def first_clause(text: str) -> str:
    for part in re.split(r"[。！？!?；;\n]", text):
        part = part.strip(" ，、：:,.")
        if part:
            return part
    return text


def is_continue(text: str) -> bool:
    normalized = re.sub(r"[\s。！？!?,，、（）()]+", "", text or "")
    return normalized in {"继续", "续", "接着", "下一步"}
