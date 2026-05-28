"""state._mixins.pending — pending writes / pending questions mixin。

承载:
- _pop_pending_write          (按 id / index 弹出 pending_write)
- approve_pending_write       (审批通过 → 调用 apply_state_write_typed)
- reject_pending_write        (拒绝 → 写 audit_log)
- add_pending_question        (GM 询问玩家入队)
- expire_stale_gm_questions   (玩家新一轮时过期旧 GM 询问)
- clear_pending_question      (玩家回答 / 跳过)

注: user_locks / time_jump / _scan_worldline_validation 等留在 GameState 主 class
(它们与 timeline / worldline 强耦合,不适合搬出去)。
"""
from __future__ import annotations

import secrets
from datetime import datetime

from state.parsers import _clean_item, _parse_question
from state.permissions import _normalize_permission_mode


class PendingMixin:
    """pending writes / pending questions 管理。"""

    def _pop_pending_write(self, *, id: str | None = None, index: int | None = None) -> dict | None:
        """按 id 优先 / index fallback 弹出 pending_write。两者都不命中返回 None。"""
        permissions = self.data.setdefault("permissions", {})
        pending = permissions.setdefault("pending_writes", [])
        if id:
            for i, item in enumerate(pending):
                if str(item.get("id", "")) == str(id):
                    return pending.pop(i)
            return None
        if index is not None and 0 <= int(index) < len(pending):
            return pending.pop(int(index))
        return None

    def approve_pending_write(self, index: int | None = None, *, id: str | None = None) -> str:
        item = self._pop_pending_write(id=id, index=index)
        if item is None:
            return "待审写入不存在"
        path = str(item.get("path", ""))
        # Bug 5：直接传 typed value，不走 spec 字符串往返；防止 list/dict 被 str() 污染。
        result = self.apply_state_write_typed(
            path=path,
            value=item.get("value"),
            source=f"{item.get('source', 'gm')}:approved",
            append=bool(item.get("append")),
            overwrite=bool(item.get("overwrite")),
            force=True,
        )
        # Bug 5 (retest硬要求 #3/#4)：memory.resources 是 inventory 的派生层。
        # 任何对 memory.resources 的审批写入完成后，立刻从 canonical
        # player_character.inventory 重写一遍 —— 防止 GM 的待审值与 canonical
        # 不一致时产生"两条 Torch"这种数据病。
        if path == "memory.resources" and (self.data.get("player_character") or {}).get("inventory"):
            try:
                self.sync_resources_from_inventory()
            except Exception:
                pass
        return result

    def reject_pending_write(self, index: int | None = None, *, id: str | None = None) -> str:
        item = self._pop_pending_write(id=id, index=index)
        if item is None:
            return "待审写入不存在"
        permissions = self.data.setdefault("permissions", {})
        permissions.setdefault("audit_log", []).append({
            "ts": datetime.now().isoformat(timespec="seconds"),
            "path": item.get("path", ""),
            "value": item.get("value", ""),
            "source": f"{item.get('source', 'gm')}:rejected",
            "mode": _normalize_permission_mode(permissions.get("mode", "full_access")),
            "turn": self.data.get("turn", 0),
        })
        permissions["audit_log"] = permissions["audit_log"][-200:]
        return f"状态写入拒绝：{item.get('path', '')}"

    def add_pending_question(self, text: str, source: str = "gm", options: list | None = None) -> bool:
        if options is None:
            question, parsed_options = _parse_question(text)
        else:
            question = _clean_item(text)
            parsed_options = [_clean_item(str(x)) for x in options if _clean_item(str(x))]
        if not question:
            return False
        permissions = self.data.setdefault("permissions", {})
        questions = permissions.setdefault("pending_questions", [])
        import secrets as _secrets
        item = {
            "id": _secrets.token_urlsafe(8),
            "question": question,
            "options": parsed_options[:4],
            "source": source,
            "turn": self.data.get("turn", 0),
        }
        # 比较时忽略 id（防止"同样的问题"被重复 push）
        def _same(a, b):
            return (a.get("question") == b.get("question")
                    and a.get("options") == b.get("options"))
        if not any(_same(item, q) for q in questions):
            questions.append(item)
            permissions["pending_questions"] = questions[-8:]
            return True
        return False

    def expire_stale_gm_questions(self, current_turn: int | None = None, reason: str = "next_turn") -> int:
        """玩家进入新一轮(发新 chat 消息)时,把**未回答的旧 GM 询问**过期掉。

        旧版 bug:GM 在 turn N 发询问 ("如何利用井口脱险?"),玩家不点选项直接打字
        "投降" 推进到 turn N+1;GM 在 N+1 再发新询问 → UI "2 项待确认" 同时挂两个,
        玩家很困扰。

        新行为:每次新 chat 处理前,把 turn < current_turn 且 source.startswith("gm")
        / source=="rules_engine" 等系统询问标记过期,从 pending_questions 移除,
        转到 audit_log 留痕。玩家显式回答 / clear 的不受影响(已经从列表 pop 掉)。

        玩家自己 add 的 (source 不含 gm/rules_engine) 不动 (玩家发的笔记 / 提问)。

        返回:过期了几条。
        """
        permissions = self.data.setdefault("permissions", {})
        questions = permissions.setdefault("pending_questions", [])
        if not questions:
            return 0
        cur = int(current_turn if current_turn is not None else self.data.get("turn", 0) or 0)
        keep: list[dict] = []
        expired: list[dict] = []
        # 哪些 source 算系统询问 → 新一轮自动过期
        system_sources = ("gm", "rules_engine", "curator", "curator:clarify", "extractor", "set_parser")
        for q in questions:
            q_turn = int(q.get("turn") or 0)
            q_source = str(q.get("source") or "")
            is_system = any(q_source == s or q_source.startswith(s + ":") for s in system_sources)
            if is_system and q_turn < cur:
                expired.append(q)
            else:
                keep.append(q)
        if not expired:
            return 0
        permissions["pending_questions"] = keep
        # audit
        audit = permissions.setdefault("audit_log", [])
        from datetime import datetime as _dt
        audit.append({
            "ts": _dt.now().isoformat(timespec="seconds"),
            "kind": "pending_questions_expired",
            "source": "expire_stale_gm_questions",
            "reason": reason,
            "current_turn": cur,
            "expired_count": len(expired),
            "expired": [
                {"id": q.get("id"), "turn": q.get("turn"), "source": q.get("source"),
                 "question": (q.get("question") or "")[:80]}
                for q in expired
            ],
        })
        if len(audit) > 200:
            permissions["audit_log"] = audit[-200:]
        return len(expired)

    def clear_pending_question(self, index: int | None = None, *, id: str | None = None, choice: str | None = None) -> dict | None:
        """同 _pop_pending_write：按 id 优先，index fallback。
        choice：玩家选择的答案，写进 audit_log 留痕（默认 None = 强制跳过）。
        """
        permissions = self.data.setdefault("permissions", {})
        questions = permissions.setdefault("pending_questions", [])
        popped = None
        if id:
            for i, q in enumerate(questions):
                if str(q.get("id", "")) == str(id):
                    popped = questions.pop(i)
                    break
        elif index is not None and 0 <= int(index) < len(questions):
            popped = questions.pop(int(index))
        if popped is not None:
            permissions.setdefault("audit_log", []).append({
                "ts": datetime.now().isoformat(timespec="seconds"),
                "kind": "question_answered",
                "question": popped.get("question", ""),
                "choice": choice or "(skipped)",
                "source": popped.get("source", "gm"),
                "turn": self.data.get("turn", 0),
            })
            permissions["audit_log"] = permissions["audit_log"][-200:]
        return popped

