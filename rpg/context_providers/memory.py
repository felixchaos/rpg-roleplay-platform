"""
MemoryProvider — 通用记忆层。所有 manifest 都应该启用。
不区分小说/模组：facts / pinned / abilities / resources / notes 等等都是会话级数据。

A6 新增：消费 MemorySettings 配置
  - token_budget          : 截断注入总 token（字符 // 2 估算）
  - bucket_pinned_enabled : 跳过 pinned 桶
  - bucket_world_enabled  : 跳过 world 类（main_quest / objective / facts / notes）
  - bucket_character_enabled: 跳过 character 类（abilities / resources）
  - recall_depth          : 每桶最多召回条目数
  - auto_archive_after_turns / summary_window: 由 _maybe_auto_archive() 触发归档
"""
from __future__ import annotations

from .base import ContextContribution, ContextProvider
from .registry import register_provider


def _estimate_tokens(text: str) -> int:
    """粗估 token 数（汉字约 1 token/字，英文约 4 chars/token，统一 // 2 作保守估算）。"""
    return max(1, len(text) // 2)


def _maybe_auto_archive(state, ms) -> None:
    """检查是否需要触发自动归档。

    规则：当前 turn 数能被 summary_window 整除，且 turn >= auto_archive_after_turns，
    则把 memory.items 中 turn < (current_turn - auto_archive_after_turns) 的条目
    标记为 archived=True（不删除，只排除出上下文注入）。

    无 DB 依赖，纯内存操作，state.save() 由调用方负责。
    """
    try:
        data = getattr(state, "data", state) or {}
        current_turn = int(data.get("turn", 0))
        if current_turn <= 0:
            return
        if current_turn % ms.summary_window != 0:
            return
        if current_turn < ms.auto_archive_after_turns:
            return
        cutoff_turn = current_turn - ms.auto_archive_after_turns
        items = (data.get("memory") or {}).get("items") or []
        changed = False
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("archived"):
                continue
            item_turn = int(item.get("turn", 0))
            if item_turn < cutoff_turn and item.get("status") != "archived":
                item["archived"] = True
                changed = True
        # 同步到 legacy buckets：从 facts / notes / pinned / abilities / resources 中
        # 移除已归档条目。采用 set 比对，保持顺序。
        if changed:
            archived_texts = {
                item["text"]
                for item in items
                if isinstance(item, dict) and item.get("archived")
            }
            mem = data.get("memory") or {}
            for bucket in ("facts", "notes", "pinned", "abilities", "resources"):
                bucket_list = mem.get(bucket)
                if isinstance(bucket_list, list):
                    mem[bucket] = [t for t in bucket_list if t not in archived_texts]
    except Exception:
        pass  # 归档失败不影响正常出牌


class MemoryProvider(ContextProvider):
    id = "memory"

    def collect(self, state, manifest, demand, services) -> ContextContribution:
        # ── 读取 MemorySettings ───────────────────────────────────────────────
        ms = None
        user_id = getattr(services, "user_id", None)
        if user_id is not None:
            try:
                from platform_app.settings import get_memory_settings
                ms = get_memory_settings(int(user_id))
            except Exception:
                pass
        if ms is None:
            from schemas.memory import MemorySettings
            ms = MemorySettings()  # 全默认

        # ── 触发自动归档（只做内存标记，不 save，调用方在 chat 结束后 save）──
        _maybe_auto_archive(state, ms)

        # ── 读取 memory 数据 ──────────────────────────────────────────────────
        m = (getattr(state, "data", state) or {}).get("memory") or {}
        depth = ms.recall_depth
        lines: list[str] = []
        token_used = 0
        budget = ms.token_budget

        def _add_line(line: str) -> bool:
            """尝试追加一行，超 budget 返回 False。"""
            nonlocal token_used
            cost = _estimate_tokens(line)
            if token_used + cost > budget:
                return False
            lines.append(line)
            token_used += cost
            return True

        # ── bucket_world_enabled: main_quest / current_objective / facts / notes ─
        if ms.bucket_world_enabled:
            if m.get("main_quest"):
                _add_line(f"主线：{m['main_quest']}")
            if m.get("current_objective"):
                _add_line(f"当前目标：{m['current_objective']}")
            for item in (m.get("facts") or [])[:depth]:
                if not _add_line(f"事实：{item}"):
                    break
            for item in (m.get("notes") or [])[:depth]:
                if not _add_line(f"笔记：{item}"):
                    break

        # ── bucket_character_enabled: abilities / resources ───────────────────
        if ms.bucket_character_enabled:
            for item in (m.get("abilities") or [])[:depth]:
                if not _add_line(f"能力：{item}"):
                    break
            for item in (m.get("resources") or [])[:depth]:
                if not _add_line(f"资源：{item}"):
                    break

        # ── bucket_pinned_enabled: pinned ─────────────────────────────────────
        if ms.bucket_pinned_enabled:
            for item in (m.get("pinned") or [])[:depth]:
                if not _add_line(f"固定记忆：{item}"):
                    break

        # ── hypotheses（归属 world 类，随 bucket_world_enabled 开关）────────────
        if ms.bucket_world_enabled:
            active_hypos = [
                it for it in (m.get("items") or [])
                if isinstance(it, dict)
                and it.get("kind") == "hypothesis"
                and it.get("status") == "active"
                and not it.get("archived")
            ]
            for h in active_hypos[:depth]:
                if not _add_line(f"未确认推测：{h.get('text', '')}"):
                    break

        text = "\n".join(lines) or "（暂无长期记忆）"
        layer = self.make_layer(
            "memory", "长期记忆", text,
            sticky=False, priority=60,
        )
        return ContextContribution(
            provider_id=self.id,
            kind="memory",
            priority=60,
            facts=lines[:3],
            layers=[layer],
            tokens_estimate=token_used,
            debug={
                "memory_mode": m.get("mode"),
                "items_count": len(m.get("items") or []),
                "token_used": token_used,
                "token_budget": budget,
                "recall_depth": depth,
                "bucket_pinned": ms.bucket_pinned_enabled,
                "bucket_world": ms.bucket_world_enabled,
                "bucket_character": ms.bucket_character_enabled,
            },
        )


register_provider(MemoryProvider())
