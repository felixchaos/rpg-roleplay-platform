"""
runtime_phase_digests.py — task 107E (part 1/2)

Save 级 runtime phase digest provider — 把当前 save 已经摘要好的历史阶段塞进 GM context,
让 GM 在长游戏 (100+ turn) 中能"想起 100 turn 前发生的事",而不是只看最近 6 轮 + state。

数据源: save_phase_digests 表 (107A schema, 107D LLM 摘要写入)。

规则:
  · 取当前 save 最近 3 个 closed phase + 1 个 open phase 的 digest
  · 每个 digest 渲染成简短一段 (turn 范围 + 标题 + summary + 关键事件 + 关键 NPC)
  · 长游戏时 priority 高 (跟 fact_groups 同级), 短游戏 (turn ≤ 30) 自动 skipped
"""
from __future__ import annotations

from .base import ContextContribution, ContextProvider, Demand, ProviderServices
from .registry import register_provider

# 最大返回的 phase 数 (避免长游戏拉太多)
MAX_PHASES = 4
# 单 phase 渲染上限 (字符)
PER_PHASE_BUDGET = 450


class RuntimePhaseDigestProvider(ContextProvider):
    """注入 save 级 phase 摘要,GM 思考历史的参考。"""
    id = "runtime_phase_digests"

    def applies(self, state, manifest, demand) -> bool:
        # 默认开 — 没 save_id 或没 digest 时 collect 自己会 skipped
        return True

    def collect(self, state, manifest, demand, services) -> ContextContribution:
        save_id = getattr(services, "save_id", None)
        if not save_id:
            return ContextContribution.skipped(self.id, "no save_id in services")

        try:
            phases = _load_recent_phases(int(save_id), limit=MAX_PHASES)
        except Exception as exc:
            return ContextContribution.skipped(self.id, f"db error: {exc!r}")

        if not phases:
            return ContextContribution.skipped(self.id, "no phase digests yet")

        # 过滤掉空摘要 (digest 还没由 LLM 生成)
        phases = [p for p in phases if (p.get("summary") or "").strip()]
        if not phases:
            return ContextContribution.skipped(self.id, "phases exist but all summaries empty")

        text = _render_phases(phases)
        layer = self.make_layer(
            "runtime_phase_digests",
            "已发生历史摘要(本存档)",
            text,
            sticky=False,
            priority=48,  # 仅次于 fact_groups (50), 高于 hypotheses (32)
        )
        return ContextContribution(
            provider_id=self.id,
            kind="runtime_history",
            priority=48,
            layers=[layer],
            facts=[
                f"phase {p['phase_index']}: turn {p['turn_start']}-{p['turn_end']} "
                f"({p.get('phase_label') or '无标题'})"
                for p in phases
            ],
            tokens_estimate=len(text) // 2,
            debug={"phase_count": len(phases), "save_id": save_id},
        )


def _load_recent_phases(save_id: int, limit: int = 4) -> list[dict]:
    """拉 save 最近 limit 个 phase (按 phase_index 倒序拉, 然后反转给 LLM 时间正序)。"""
    from platform_app.db import connect, init_db
    init_db()
    with connect() as db:
        rows = db.execute(
            """
            select id, phase_index, turn_start, turn_end, story_time_label, phase_label,
                   summary, key_events, key_npcs, key_locations, key_decisions, emotion_arc,
                   status
            from save_phase_digests
            where save_id = %s
            order by phase_index desc
            limit %s
            """,
            (save_id, limit),
        ).fetchall()
    out = [dict(r) for r in rows]
    out.reverse()  # 时间正序: 早 phase 在前, 当前 open phase 在后
    return out


def _render_phases(phases: list[dict]) -> str:
    """把 phase digest 列表渲染成 GM 看的简短文本。"""
    parts: list[str] = []
    for i, p in enumerate(phases):
        last = (i == len(phases) - 1)
        status_tag = "进行中" if p.get("status") == "open" else "已结束"
        head = f"# Phase {p['phase_index']} (turn {p['turn_start']}-{p['turn_end']} · {status_tag})"
        label = (p.get("phase_label") or "").strip()
        story_time = (p.get("story_time_label") or "").strip()
        if label or story_time:
            head += f" — {label}"
            if story_time:
                head += f" · {story_time}"

        summary = (p.get("summary") or "").strip()
        events = p.get("key_events") or []
        npcs = p.get("key_npcs") or []
        decisions = p.get("key_decisions") or []
        emotion = (p.get("emotion_arc") or "").strip()

        block = [head, summary]
        if events:
            evt_lines = []
            for ev in events[:3]:  # 只取 top 3
                if isinstance(ev, dict):
                    t = ev.get("turn", "?")
                    s = (ev.get("summary") or ev.get("desc") or "").strip()
                    if s:
                        evt_lines.append(f"  · t{t}: {s[:100]}")
            if evt_lines:
                block.append("关键事件:")
                block.extend(evt_lines)
        if npcs and last:  # 最后一个 phase 时附带 NPC 状态
            npc_lines = []
            for n in npcs[:4]:
                if isinstance(n, dict):
                    nm = (n.get("name") or "").strip()
                    role = (n.get("role") or "").strip()
                    if nm:
                        npc_lines.append(f"  · {nm}" + (f" ({role})" if role else ""))
            if npc_lines:
                block.append("活跃 NPC:")
                block.extend(npc_lines)
        if decisions and last:
            dec_lines = []
            for d in decisions[:2]:
                if isinstance(d, dict):
                    ch = (d.get("choice") or "").strip()
                    if ch:
                        dec_lines.append(f"  · {ch[:60]}")
            if dec_lines:
                block.append("关键决定:")
                block.extend(dec_lines)
        if emotion and last:
            block.append(f"情感弧线: {emotion[:80]}")

        chunk = "\n".join(block)
        # 单 phase 预算 cap
        if len(chunk) > PER_PHASE_BUDGET:
            chunk = chunk[: PER_PHASE_BUDGET - 3] + "..."
        parts.append(chunk)

    return "\n\n".join(parts)


register_provider(RuntimePhaseDigestProvider())
