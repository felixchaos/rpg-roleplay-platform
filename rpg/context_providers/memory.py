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

# token 粗估收敛到权威 context_engine._utils._estimate_tokens(带 `or ""` 防御)。
# 注:本地旧版 `len(text)//2` 无防御,text=None 会 TypeError —— 收口顺带消除该潜在缺陷,
# 属行为强化(非纯等价);非 None 输入两版结果逐位相同。
from context_engine._utils import _estimate_tokens


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
            # 永不自动归档的桶 = 角色卡式【持久状态】+ 玩家权威手写输入:
            #   · notes(手记)/ pinned(固定记忆):玩家手写/显式固定的权威输入。
            #   · abilities(能力)/ resources(资源/物品/货币):角色卡式持久状态 —— 角色不会
            #     因为过了几十回合就「忘掉」一项能力或丢掉背包物品。手动添加的尤其荒谬
            #     (行者无疆反馈「手动添加的能力随剧情推动消失、条目减少」的根因)。
            # 只有 facts(叙事流水,高频累积、有上下文预算压力)才自动归档。
            if item.get("legacy_bucket") in ("notes", "pinned", "abilities", "resources"):
                continue
            item_turn = int(item.get("turn", 0))
            if item_turn < cutoff_turn and item.get("status") != "archived":
                item["archived"] = True
                changed = True
        # 同步到 legacy buckets：仅从 facts 移除已归档条目。**绝不碰 notes/pinned/abilities/resources**
        # (持久状态 + 玩家权威);且这里按文本 set 比对,若把它们纳入,一条与某归档事实文本恰好
        # 相同的能力/资源/手记会被误删(文本碰撞)。上面的豁免已保证这些桶的 item 不会被标 archived,
        # 此处仅剥 facts 是双保险。
        if changed:
            archived_texts = {
                item["text"]
                for item in items
                if isinstance(item, dict) and item.get("archived")
            }
            mem = data.get("memory") or {}
            for bucket in ("facts",):
                bucket_list = mem.get(bucket)
                if isinstance(bucket_list, list):
                    mem[bucket] = [t for t in bucket_list if t not in archived_texts]
    except Exception:
        pass  # 归档失败不影响正常出牌


def _restore_persistent_buckets(state) -> None:
    """自愈:把此前被(旧)auto-archive 误归档的 abilities/resources 条目救回 legacy bucket + 取消 archived。
    v1.34.1 起 abilities/resources 豁免归档(角色卡式持久状态),但**历史存档里已被移出 bucket 的**不会自己
    回来 —— 老玩家丢掉的能力/资源要恢复。只动 archived 的(auto-archive 是唯一置 archived 的路径);玩家显式
    删除的条目 remove_memory 已从 items 移除,不会被复活;superseded 的跳过。幂等,MemoryProvider 每回合调。"""
    try:
        data = getattr(state, "data", state) or {}
        mem = data.get("memory")
        if not isinstance(mem, dict):
            return
        items = mem.get("items") or []
        for bucket in ("abilities", "resources"):
            bucket_list = mem.get(bucket)
            if not isinstance(bucket_list, list):
                continue
            for item in items:
                if not isinstance(item, dict) or item.get("legacy_bucket") != bucket:
                    continue
                if item.get("status") == "superseded":
                    continue
                if item.get("archived"):
                    item["archived"] = False  # 取消误归档
                text = item.get("text")
                if text and text not in bucket_list:
                    bucket_list.append(text)  # 补回 bucket
    except Exception:
        pass  # 自愈失败不影响正常出牌


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

        # ── 自愈:恢复历史存档里被旧 auto-archive 误归档移出的 abilities/resources(持久状态)──
        _restore_persistent_buckets(state)
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

        # 记忆优先级提示:玩家手写的「笔记/固定记忆」权威且最新,与自动累积的「事实」冲突
        # 时一律以玩家手记为准(修「GM 拿事实库过时数值、不读玩家笔记」)。仅当确有手记时注入。
        if m.get("notes") or m.get("pinned"):
            _add_line(
                "【记忆优先级:带「笔记：」「固定记忆：」的是玩家手写、权威且最新;"
                "与「事实：」冲突时一律以笔记/固定记忆为准,数值/状态以玩家手记为最新。】"
            )

        # 窗口方向:各桶写入端是尾部 append(state.add_memory / apply_ops list 分支),
        # 「最近 depth 条」必须取尾([-depth:])。旧实现取头 → 桶超过 depth 后,新增的
        # 能力/资源/固定记忆/笔记/事实对 GM 永久不可见(notes/pinned/abilities/resources
        # 四桶归档豁免、无任何轮转,一错就是永久)。
        # ── bucket_world_enabled: main_quest / current_objective / facts / notes ─
        if ms.bucket_world_enabled:
            if m.get("main_quest"):
                _add_line(f"主线：{m['main_quest']}")
            if m.get("current_objective"):
                _add_line(f"当前目标：{m['current_objective']}")
            for item in (m.get("facts") or [])[-depth:]:
                if not _add_line(f"事实：{item}"):
                    break
            for item in (m.get("notes") or [])[-depth:]:
                if not _add_line(f"笔记：{item}"):
                    break

        # ── bucket_character_enabled: abilities / resources ───────────────────
        if ms.bucket_character_enabled:
            for item in (m.get("abilities") or [])[-depth:]:
                if not _add_line(f"能力：{item}"):
                    break
            for item in (m.get("resources") or [])[-depth:]:
                if not _add_line(f"资源：{item}"):
                    break

        # ── bucket_pinned_enabled: pinned ─────────────────────────────────────
        if ms.bucket_pinned_enabled:
            for item in (m.get("pinned") or [])[-depth:]:
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
