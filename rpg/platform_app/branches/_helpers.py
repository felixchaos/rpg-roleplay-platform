"""Shared helper utilities used across branches sub-modules.

These are pure functions with no cross-submodule imports.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


# ── 常量 ─────────────────────────────────────────────────────────────────────
BASE = Path(__file__).resolve().parents[2]
BRANCH_STATE_DIR = BASE / "platform_data" / "branch_states"
MAIN_REF = "refs/heads/main"


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
    import shutil as _shutil
    import secrets as _secrets
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
