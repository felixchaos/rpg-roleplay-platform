"""
tavern_chats.py — SillyTavern 聊天记录 JSONL 导入

格式规范（JSONL，每行一个 JSON 对象）：
  Line 0 (header):  {"user_name":"...", "character_name":"...", "create_date":"..."}
  Line 1..N (msg):  {"name":"...", "is_user":bool, "mes":"...", "send_date":..., "extra":{}}

导入策略：
  - 转换为 branch_commits 列表（player_input / gm_output 交替）
  - 归一化字段并做长度截断
  - 不直接写库，返回 payload 供调用方决定存档/预览

使用：
  header, commits = parse_chat_jsonl(text)
  # header: {user_name, character_name, create_date}
  # commits: list of branch_commit dicts ready for import_save
"""
from __future__ import annotations

import json
from typing import Any

_MAX_JSONL_BYTES = 8 * 1024 * 1024   # 8MB 上限
_MAX_LINES       = 20_000             # 单文件最多 20000 条消息
_MAX_MES_BYTES   = 65_000             # 单条消息最大字节（对齐 branch_commits.gm_output 限制）


def parse_chat_jsonl(text: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """解析 SillyTavern JSONL 聊天记录。

    Returns:
        (header, commits)
        header   — 来自第一行的元数据：user_name / character_name / create_date
        commits  — 每条消息对应一个 branch_commit payload，含：
                   turn_index, kind, player_input, gm_output,
                   title, message, metadata:{tavern_name, is_user, send_date, extra}
    """
    if len(text.encode("utf-8")) > _MAX_JSONL_BYTES:
        raise ValueError(f"JSONL 文件过大（上限 {_MAX_JSONL_BYTES // (1024*1024)} MB）")

    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        raise ValueError("JSONL 为空")
    if len(lines) > _MAX_LINES:
        raise ValueError(f"消息数量超过上限 {_MAX_LINES}")

    # ── 解析 header（第一行）────────────────────────────────────────────
    try:
        header_raw = json.loads(lines[0])
    except json.JSONDecodeError as exc:
        raise ValueError(f"第 1 行 JSON 解析失败：{exc}") from exc

    header: dict[str, Any] = {
        "user_name":      str(header_raw.get("user_name") or "User"),
        "character_name": str(header_raw.get("character_name") or "Character"),
        "create_date":    str(header_raw.get("create_date") or ""),
    }

    # ── 解析消息行（第 2 行起）─────────────────────────────────────────
    commits: list[dict[str, Any]] = []
    player_acc: list[str] = []   # 累积连续 user 发言
    turn_index = 0

    def flush_player():
        nonlocal turn_index
        if not player_acc:
            return
        combined = "\n".join(player_acc)[:_MAX_MES_BYTES]
        commits.append(_make_commit(
            turn_index=turn_index,
            player_input=combined,
            gm_output="",
            name=header["user_name"],
            is_user=True,
            send_date=None,
            extra={},
        ))
        player_acc.clear()
        turn_index += 1

    for i, raw_line in enumerate(lines[1:], start=2):
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        try:
            msg = json.loads(raw_line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"第 {i} 行 JSON 解析失败：{exc}") from exc

        name     = str(msg.get("name") or "")
        is_user  = bool(msg.get("is_user", False))
        mes      = str(msg.get("mes") or "").strip()
        send_date = msg.get("send_date")
        extra    = dict(msg.get("extra") or {})

        if not mes:
            continue  # 空消息跳过

        mes = mes[:_MAX_MES_BYTES]

        if is_user:
            # 用户消息累积（允许连续几条 user 发言合并成一个 player_input）
            player_acc.append(mes)
        else:
            # GM/char 回复：配对一个 player_input（若无则留空）
            player_input = "\n".join(player_acc)[:_MAX_MES_BYTES] if player_acc else ""
            player_acc.clear()
            commits.append(_make_commit(
                turn_index=turn_index,
                player_input=player_input,
                gm_output=mes,
                name=name,
                is_user=False,
                send_date=send_date,
                extra=extra,
            ))
            turn_index += 1

    # 收尾：若最后几条都是 user 发言（没 char 回复），单独建 commit
    flush_player()

    if not commits:
        raise ValueError("JSONL 不含有效消息（mes 均为空）")

    return header, commits


def _make_commit(
    turn_index: int,
    player_input: str,
    gm_output: str,
    name: str,
    is_user: bool,
    send_date: Any,
    extra: dict,
) -> dict[str, Any]:
    preview = (gm_output or player_input)[:120]
    return {
        "turn_index": turn_index,
        "kind": "round",
        "title": "",
        "message": "",
        "summary": "",
        "content_preview": preview,
        "player_input": player_input,
        "gm_output": gm_output,
        "metadata": {
            "tavern_imported": True,
            "speaker_name": name,
            "is_user": is_user,
            "send_date": send_date,
            "extra": extra,
        },
        "state_snapshot": {},
        "object_hash": "",
        "tree_hash": "",
        "state_path": "",
    }


def chat_to_save_payload(
    header: dict[str, Any],
    commits: list[dict[str, Any]],
    script_id: int | None = None,
    title: str | None = None,
    character_card_id: int | None = None,
) -> dict[str, Any]:
    """把解析出的 header + commits 封装成 save_io.import_save 所需的 payload。

    export_version=1  +  save  +  commits  +  refs(空)  +  messages(空)

    酒馆模式:盖上 save_kind='tavern',让 import_save 走无剧本 lane(script_id=NULL),
    并透传 tavern_character_card_id 让导入的存档绑定到对应角色卡。
    """
    char_name = header.get("character_name") or "Tavern Chat"
    save_title = title or f"[酒馆导入] {char_name}"
    save: dict[str, Any] = {
        "title": save_title,
        "script_id": script_id,
        "save_kind": "tavern",
        "state_snapshot": {
            "tavern_imported": True,
            "user_name": header.get("user_name"),
            "character_name": header.get("character_name"),
            "create_date": header.get("create_date"),
        },
    }
    if character_card_id is not None:
        save["tavern_character_card_id"] = int(character_card_id)
    return {
        "export_version": 1,
        "exported_at": 0,
        "save": save,
        "commits": commits,
        "refs": [],
        "messages": [],
        "memories": [],
    }


def save_to_chat_jsonl(save_id: int, user_id: int | None = None) -> str:
    """导出存档为 SillyTavern JSONL 聊天记录(决策2:与 parse_chat_jsonl 互为镜像,
    文本无损往返)。

    安全:传 user_id 时在数据层强制归属(where id=%s and user_id=%s),不再仅依赖调用方
    先行鉴权 —— 防未来新增调用方漏掉 _require_tavern_save 导致泄漏他人对话。

    - 第 0 行 header:{"user_name", "character_name", "create_date":""}
      user_name 取 state_snapshot.player.name(兜底 "User");
      character_name 取 state_snapshot.tavern.character.name(兜底存档 title)。
    - 之后每条 branch_commit(按 turn_index 升序,跳过 turn-0 root)展开:
      player_input 非空 → 一行 user 消息;gm_output 非空 → 一行 character 消息。
      一个 round commit 可产出 0 / 1 / 2 行。
    """
    from .db import connect, init_db

    init_db()
    with connect() as db:
        if user_id is not None:
            save = db.execute(
                "select id, title, state_snapshot from game_saves where id = %s and user_id = %s",
                (int(save_id), int(user_id)),
            ).fetchone()
        else:
            save = db.execute(
                "select id, title, state_snapshot from game_saves where id = %s",
                (int(save_id),),
            ).fetchone()
        if not save:
            raise ValueError("存档不存在")
        commits = db.execute(
            """
            select turn_index, kind, player_input, gm_output
            from branch_commits
            where save_id = %s
            order by turn_index asc, id asc
            """,
            (int(save_id),),
        ).fetchall() or []

    snap = save.get("state_snapshot") or {}
    if not isinstance(snap, dict):
        snap = {}
    player = snap.get("player") if isinstance(snap.get("player"), dict) else {}
    tavern = snap.get("tavern") if isinstance(snap.get("tavern"), dict) else {}
    character = tavern.get("character") if isinstance(tavern.get("character"), dict) else {}

    user_name = str((player or {}).get("name") or "User") or "User"
    character_name = (
        str((character or {}).get("name") or "").strip()
        or str(save.get("title") or "").strip()
        or "Character"
    )

    lines: list[str] = []
    lines.append(json.dumps(
        {"user_name": user_name, "character_name": character_name, "create_date": ""},
        ensure_ascii=False,
    ))

    for c in commits:
        # 跳过 turn-0 root(seed_tree 的根 commit:无玩家输入/无 GM 输出,或 kind='root')
        if str(c.get("kind") or "") == "root":
            continue
        if int(c.get("turn_index") or 0) == 0 and not (c.get("player_input") or c.get("gm_output")):
            continue
        pin = str(c.get("player_input") or "").strip()
        gout = str(c.get("gm_output") or "").strip()
        if pin:
            lines.append(json.dumps(
                {"name": user_name, "is_user": True, "mes": pin, "send_date": ""},
                ensure_ascii=False,
            ))
        if gout:
            lines.append(json.dumps(
                {"name": character_name, "is_user": False, "mes": gout, "send_date": ""},
                ensure_ascii=False,
            ))

    return "\n".join(lines)
