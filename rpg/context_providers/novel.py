"""
Novel providers — 只在 manifest.kind == 'novel_adaptation' 时启用。

把原来 context_agent.py / context_engine.py 里硬编码的小说专用逻辑
（timeline_filter_for_label / retrieve_context / character_cards / worldbook）
下沉到 4 个独立 provider：

- NovelTimelineProvider     — 原著章节锚点
- NovelRetrievalProvider    — script-scoped 检索 / ChapterFact / source snippets
- NovelCharactersProvider   — 激活角色卡
- NovelWorldbookProvider    — 激活世界书条目

模组（module_adventure）不启用这些，所以 Ash Mine 不会再混入小说残渣。
"""
from __future__ import annotations

from .base import ContextContribution, ContextProvider, Demand, ProviderServices
from .registry import register_provider


def _is_novel_manifest(manifest) -> bool:
    return manifest.get("kind") == "novel_adaptation"


def _allow_retrieval(manifest) -> bool:
    pol = manifest.get("retrieval_policy") or {}
    return bool(pol.get("allow_script_retrieval", True))


def _allow_chapter_facts(manifest) -> bool:
    pol = manifest.get("retrieval_policy") or {}
    return bool(pol.get("allow_chapter_facts", True))


class NovelTimelineProvider(ContextProvider):
    """注入小说时间线锚点。仅 novel_adaptation 启用。"""
    id = "novel_timeline"

    def applies(self, state, manifest, demand) -> bool:
        if not super().applies(state, manifest, demand):
            return False
        return _is_novel_manifest(manifest)

    def collect(self, state, manifest, demand, services) -> ContextContribution:
        data = getattr(state, "data", state) or {}
        world = data.get("world") or {}
        timeline = world.get("timeline") or {}
        pending = timeline.get("pending_jump") or {}
        label = pending.get("to") or world.get("time", "")

        anchor: dict = {}
        if services.timeline_filter_fn and label:
            try:
                anchor = services.timeline_filter_fn(label) or {}
            except Exception as exc:
                anchor = {"error": str(exc)}

        lines: list[str] = []
        lines.append(f"【时间线】当前 label：{label or '（无）'}")
        if pending:
            lines.append(f"【待确认跳跃】{pending.get('from', '')} → {pending.get('to', '')}")
        if anchor.get("anchor_chapter"):
            lines.append(
                f"【原著锚点】第 {anchor.get('anchor_chapter')} 章，"
                f"窗口 {anchor.get('chapter_min')}-{anchor.get('chapter_max')}"
            )
        elif label:
            lines.append("【原著锚点】未精确命中")
        text = "\n".join(lines)
        layer = self.make_layer(
            "novel_timeline", "时间线事务", text,
            sticky=True, priority=70,
        )
        return ContextContribution(
            provider_id=self.id,
            kind="novel_timeline",
            priority=70,
            layers=[layer],
            tokens_estimate=len(text) // 2,
            debug={"label": label, "anchor": anchor, "pending_jump": pending},
        )


class NovelRetrievalProvider(ContextProvider):
    """script-scoped 章节 / 摘要 / source snippet 检索。仅 novel_adaptation 启用。"""
    id = "novel_retrieval"

    def applies(self, state, manifest, demand) -> bool:
        if not super().applies(state, manifest, demand):
            return False
        return _is_novel_manifest(manifest) and _allow_retrieval(manifest)

    def collect(self, state, manifest, demand, services) -> ContextContribution:
        if not services.retrieve_fn:
            return ContextContribution.skipped(self.id, "no retrieve_fn injected")

        query = (demand.retrieval_query if demand else "") or ""
        try:
            text = services.retrieve_fn(
                query,
                state=state,
                user_id=services.user_id,
                script_id=services.script_id,
            )
        except Exception as exc:
            return ContextContribution(
                provider_id=self.id, applied=False,
                warnings=[f"retrieve_fn 异常：{exc}"],
                debug={"error": str(exc)},
            )
        if not text:
            return ContextContribution.skipped(self.id, "no retrieval content")
        try:
            state.set_last_retrieval(text)
        except Exception:
            pass

        layer = self.make_layer(
            "novel_retrieval", "检索参考（原著 / ChapterFact）", text,
            sticky=False, priority=40,
        )
        return ContextContribution(
            provider_id=self.id,
            kind="novel_retrieval",
            priority=40,
            layers=[layer],
            retrieval_items=[{"text": text}],
            tokens_estimate=len(text) // 2,
            debug={"query": query, "chars": len(text)},
        )


class NovelCharactersProvider(ContextProvider):
    """激活角色卡。仅 novel_adaptation 启用。委托给 context_engine 的现有 helper。"""
    id = "novel_characters"

    def applies(self, state, manifest, demand) -> bool:
        if not super().applies(state, manifest, demand):
            return False
        return _is_novel_manifest(manifest)

    def collect(self, state, manifest, demand, services) -> ContextContribution:
        # 委托给 context_engine 现有的 character cards 逻辑（避免重新实现 NPC 卡选）。
        try:
            from context_engine import (
                _active_character_cards,
                _load_characters,
                _player_card,
                _recent_text,
                _strip_card_text,
            )
        except Exception as exc:
            return ContextContribution(
                provider_id=self.id, applied=False,
                warnings=[f"import context_engine failed: {exc}"],
            )
        data = getattr(state, "data", state) or {}
        try:
            chars = _load_characters(script_id=services.script_id, book_id=services.book_id)
            history = state.history_messages()
            scan_text = "\n".join([
                (demand.player_intent if demand else "") or "",
                _recent_text(history),
                data.get("player", {}).get("current_location", ""),
                data.get("world", {}).get("time", ""),
                "\n".join(data.get("world", {}).get("known_events") or []),
                data.get("memory", {}).get("current_objective", ""),
            ])
            player_card = _player_card(state, chars)
            npc_cards = _active_character_cards(scan_text, chars, player_card.get("name", ""))
        except Exception as exc:
            return ContextContribution(
                provider_id=self.id, applied=False,
                warnings=[f"load characters failed: {exc}"],
            )
        layers = []
        if player_card.get("text"):
            layers.append(self.make_layer(
                "player_card", "玩家角色卡", player_card["text"],
                sticky=True, priority=88,
                source=player_card.get("name", ""),
            ))
        if npc_cards:
            layers.append(self.make_layer(
                "npc_cards", "当前角色卡（NPC）",
                "\n\n".join(c["text"] for c in npc_cards),
                sticky=False, priority=78,
                items=[_strip_card_text(c) for c in npc_cards],
            ))
        if not layers:
            return ContextContribution.skipped(self.id, "no cards loaded")
        return ContextContribution(
            provider_id=self.id,
            kind="novel_characters",
            priority=80,
            layers=layers,
            tokens_estimate=sum(len(lyr["content"]) for lyr in layers) // 2,
            debug={"cards_count": len(npc_cards)},
        )


class NovelWorldbookProvider(ContextProvider):
    """激活世界书条目。仅 novel_adaptation 启用。"""
    id = "novel_worldbook"

    def applies(self, state, manifest, demand) -> bool:
        if not super().applies(state, manifest, demand):
            return False
        return _is_novel_manifest(manifest)

    def collect(self, state, manifest, demand, services) -> ContextContribution:
        try:
            from context_engine import (
                _active_worldbook,
                _load_world,
                _recent_text,
                _strip_worldbook_text,
            )
        except Exception as exc:
            return ContextContribution(
                provider_id=self.id, applied=False,
                warnings=[f"import context_engine failed: {exc}"],
            )
        data = getattr(state, "data", state) or {}
        try:
            world = _load_world()
            history = state.history_messages()
            scan_text = "\n".join([
                (demand.player_intent if demand else "") or "",
                _recent_text(history),
                data.get("player", {}).get("current_location", ""),
                data.get("world", {}).get("time", ""),
            ])
            entries = _active_worldbook(scan_text, world, state,
                                        script_id=services.script_id,
                                        book_id=services.book_id)
        except Exception as exc:
            return ContextContribution(
                provider_id=self.id, applied=False,
                warnings=[f"load worldbook failed: {exc}"],
            )
        if not entries:
            return ContextContribution.skipped(self.id, "no worldbook entries")
        content = "\n\n".join(e.get("text", "") for e in entries)
        layer = self.make_layer(
            "novel_worldbook", "激活世界书", content,
            sticky=False, priority=72,
            items=[_strip_worldbook_text(e) for e in entries],
        )
        return ContextContribution(
            provider_id=self.id,
            kind="novel_worldbook",
            priority=72,
            layers=[layer],
            tokens_estimate=len(content) // 2,
            debug={"entries_count": len(entries)},
        )


register_provider(NovelTimelineProvider())
register_provider(NovelRetrievalProvider())
register_provider(NovelCharactersProvider())
register_provider(NovelWorldbookProvider())
