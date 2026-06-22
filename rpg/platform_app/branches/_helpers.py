"""Shared helper utilities used across branches sub-modules.

These are pure functions with no cross-submodule imports.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ── 常量 ─────────────────────────────────────────────────────────────────────
BASE = Path(__file__).resolve().parents[2]
BRANCH_STATE_DIR = BASE / "platform_data" / "branch_states"
MAIN_REF = "refs/heads/main"


# ── 并发锁 ────────────────────────────────────────────────────────────────────

def acquire_save_advisory_lock(db: Any, save_id: int, user_id: int | None) -> None:
    """取与 record_runtime_turn / persist_runtime_state 同 key 的事务级 advisory lock。

    分支写操作(continue_from / activate_node / activate_save / delete_subtree /
    rollback_to_message)都会改 game_saves 活跃指针;若不持此锁,会与并发的回合提交
    (record_runtime_turn)或 autosave(persist_runtime_state)互相覆盖指针 →
    用户丢回合 / 刚做的回滚被并发回合冲掉(多 tab 实测可触发)。

    key 表达式必须与 runtime.py 两处**逐字一致**(rpg_turn_{uid} + save_{save_id},
    uid = user_id or save_id*7919),否则两把锁算出不同 id、互不排斥、形同虚设。
    必须在读 game_saves 之前调用,保证 save.active_commit_id 在本事务内稳定。
    """
    # 不能吞异常:吞掉(如 deadlock_detected 40P01)= 未持锁仍继续写 game_saves 活跃指针 →
    # 并发两 worker 同时改、指针错乱。失败必须上抛让调用方整事务回滚(用户重试),不可静默。
    uid_for_lock = int(user_id or (save_id * 7919))
    try:
        db.execute(
            "select pg_advisory_xact_lock(hashtext(%s)::int, hashtext(%s)::int)",
            (f"rpg_turn_{uid_for_lock}", f"save_{save_id}"),
        )
    except Exception as exc:
        log.error("[advisory_lock] 获取 save=%s 锁失败,放弃本次写以防指针错乱: %s", save_id, exc)
        raise


# ── 文本工具 ──────────────────────────────────────────────────────────────────

def compact(text: str, limit: int = 120) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "..."


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


# ── 状态文件 IO ────────────────────────────────────────────────────────────────

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


def tavern_card_cols(state: dict[str, Any]) -> tuple[int | None, int | None]:
    """从 state_snapshot 抽出酒馆角色/persona 卡 id,用于把 game_saves 的
    tavern_character_card_id / tavern_persona_card_id 列与 state JSON 对齐。

    背景:LLM 工具 set_tavern_character / set_tavern_persona / import_character_card
    只 mutate state.data['tavern'](单写者铁律),不裸写列。单写者(record_runtime_turn /
    persist_runtime_state)持久化 state_snapshot 时,顺带把这两个列同步过来 —— 否则列保持
    create 时的初值(空起手对话为 NULL),而 JSON 已是新卡 id,导致走列的读卡路径 404。

    只返回**有值**的 id(int);缺失/非整数返回 None,调用方用 COALESCE(%s, 旧列) 落库,
    故非酒馆存档(无 tavern 块)与字段缺失时绝不把已有列清成 NULL。"""
    if not isinstance(state, dict):
        return (None, None)
    tav = state.get("tavern")
    if not isinstance(tav, dict):
        return (None, None)

    def _as_id(v: Any) -> int | None:
        if v is None:
            return None
        try:
            iv = int(v)
        except (TypeError, ValueError):
            return None
        return iv if iv > 0 else None

    return (_as_id(tav.get("character_card_id")), _as_id(tav.get("persona_card_id")))


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
    import json as _json
    BRANCH_STATE_DIR.mkdir(parents=True, exist_ok=True)
    snap = _json.loads(_json.dumps(data, ensure_ascii=False))
    path = BRANCH_STATE_DIR / f"save_{save_id}_commit_seed_{index}.json"
    path.write_text(_json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def write_runtime_snapshot(save_id: int, data: dict[str, Any]) -> str:
    import json as _json
    import secrets as _secrets
    BRANCH_STATE_DIR.mkdir(parents=True, exist_ok=True)
    snap = _json.loads(_json.dumps(data, ensure_ascii=False))
    turn = int(snap.get("turn") or 0)
    path = BRANCH_STATE_DIR / f"save_{save_id}_runtime_turn_{turn}_{_secrets.token_hex(4)}.json"
    path.write_text(_json.dumps(snap, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(path)


def copy_state(source_path: str, save_id: int, label: str) -> str:
    import secrets as _secrets
    import shutil as _shutil
    BRANCH_STATE_DIR.mkdir(parents=True, exist_ok=True)
    target = BRANCH_STATE_DIR / f"save_{save_id}_{label}_{_secrets.token_hex(4)}.json"
    source = Path(source_path)
    if source.exists():
        _shutil.copy2(source, target)
    else:
        target.write_text(json.dumps({"history": [], "turn": 0}, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(target)


def write_named_snapshot(save_id: int, label: str, data: dict[str, Any]) -> str:
    import secrets as _secrets
    BRANCH_STATE_DIR.mkdir(parents=True, exist_ok=True)
    target = BRANCH_STATE_DIR / f"save_{save_id}_{label}_{_secrets.token_hex(4)}.json"
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
