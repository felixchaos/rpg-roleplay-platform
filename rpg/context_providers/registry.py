"""
rpg.context_providers.registry — Provider 注册表 + ContentPack manifest 解析。

context_agent 通过 registry 查 manifest.context_providers 里声明的 id，
分别调用对应 ContextProvider.collect()。

Manifest 的来源优先级：
  1. state.content_pack（玩家或系统显式指定）
  2. state.scene.module_manifest（模组开始时由 rules_bridge.start_module 写入）
     → 自动归到 module_adventure
  3. script_id 存在 → novel_adaptation legacy 默认
  4. 否则 → freeform 最小默认

向后兼容：旧柏林存档（没有 module_id，但有 script_id 或 history）走 novel_adaptation。
"""
from __future__ import annotations

import copy
from typing import Any, Optional

from .base import ContextContribution, ContextProvider, Demand, ProviderServices

# ── 注册表 ───────────────────────────────────────────────────────

_REGISTRY: dict[str, ContextProvider] = {}


def register_provider(provider: ContextProvider) -> None:
    """注册一个 provider。重复注册同 id 会覆盖（方便测试）。"""
    if not provider.id:
        raise ValueError("provider 必须有非空 id")
    _REGISTRY[provider.id] = provider


def get_provider(provider_id: str) -> ContextProvider | None:
    return _REGISTRY.get(provider_id)


def available_providers() -> list[str]:
    return sorted(_REGISTRY.keys())


# ── 默认 Manifest ────────────────────────────────────────────────

DEFAULT_NOVEL_MANIFEST: dict = {
    "id": "__legacy_novel__",
    "kind": "novel_adaptation",
    "ruleset": "none",
    "context_providers": [
        "novel_timeline",
        "novel_retrieval",
        "novel_characters",
        "novel_worldbook",
        "memory",
        "worldline",
        "recent_chat",
        # task 107E: 双时间线 — 历史摘要 + 剧本未来
        "runtime_phase_digests",
        "script_phase_anticipation",
    ],
    "retrieval_policy": {
        "allow_script_retrieval": True,
        "allow_chapter_facts": True,
    },
    "gm_policy": {
        "mode": "novel_gm",
        "must_obey_rules_result": False,
        "no_unverified_hard_state_write": False,
    },
}


DEFAULT_MODULE_MANIFEST: dict = {
    "id": "__module_default__",
    "kind": "module_adventure",
    "ruleset": "5e_compatible",
    "context_providers": [
        "module_scene",
        "module_encounter",
        "module_worldbook",
        "rules",
        "memory",
        "worldline",
        "recent_chat",
        # task 107E: 长游戏历史摘要 (剧本预期 module 没有, 不加)
        "runtime_phase_digests",
    ],
    "retrieval_policy": {
        "allow_script_retrieval": False,
        "allow_chapter_facts": False,
    },
    "gm_policy": {
        "mode": "adventure_gm",
        "must_obey_rules_result": True,
        "no_unverified_hard_state_write": True,
    },
}


DEFAULT_FREEFORM_MANIFEST: dict = {
    "id": "__freeform__",
    "kind": "freeform",
    "ruleset": "none",
    "context_providers": [
        "memory",
        "worldline",
        "recent_chat",
        # task 107E: 自由模式也启用历史摘要
        "runtime_phase_digests",
    ],
    "retrieval_policy": {
        "allow_script_retrieval": False,
        "allow_chapter_facts": False,
    },
    "gm_policy": {
        "mode": "freeform_gm",
        "must_obey_rules_result": False,
        "no_unverified_hard_state_write": False,
    },
}


# ── ContentPack Manifest 解析 ────────────────────────────────────

def resolve_content_pack(state, script_id: int | None = None) -> dict:
    """根据当前 session state 推断 active ContentPack manifest。

    返回 manifest dict，必含 kind / context_providers / retrieval_policy / gm_policy。
    永远返回一个有效 manifest（即便 state 残缺）。
    """
    data = getattr(state, "data", state) or {}

    # 1. state.content_pack 显式指定（最高优先级）
    explicit = data.get("content_pack")
    if isinstance(explicit, dict) and explicit.get("context_providers"):
        return _normalize_manifest(explicit)

    # 2. state.scene.module_manifest（模组开局写入；优先级高于 script）
    scene = data.get("scene") or {}
    module_manifest = scene.get("module_manifest") or {}
    if scene.get("module_id") or module_manifest.get("id"):
        # 模组路径：优先用模组自带的 context_providers / retrieval_policy / gm_policy；
        # 缺什么补什么（兜底用 DEFAULT_MODULE_MANIFEST）。
        merged = copy.deepcopy(DEFAULT_MODULE_MANIFEST)
        merged["id"] = module_manifest.get("id") or scene.get("module_id") or merged["id"]
        merged["title"] = module_manifest.get("name") or module_manifest.get("name_cn") or merged.get("title")
        # 加载完整 module.json（包含 context_providers / retrieval_policy / gm_policy 等）
        full = _load_full_module_manifest(merged["id"])
        if full:
            for key in ("kind", "ruleset", "context_providers", "retrieval_policy",
                        "gm_policy", "title", "tagline"):
                if full.get(key) is not None:
                    merged[key] = full[key]
        return _normalize_manifest(merged)

    # 3. script_id 存在 → novel adaptation legacy 默认
    if script_id:
        manifest = copy.deepcopy(DEFAULT_NOVEL_MANIFEST)
        manifest["id"] = f"script:{script_id}"
        return _normalize_manifest(manifest)

    # 4. 老存档兼容：history 有内容也按 novel_adaptation 走（保持柏林等无 script_id 存档不破坏）
    if data.get("history"):
        manifest = copy.deepcopy(DEFAULT_NOVEL_MANIFEST)
        manifest["id"] = "__legacy_save__"
        return _normalize_manifest(manifest)

    # 5. 兜底
    return copy.deepcopy(DEFAULT_FREEFORM_MANIFEST)


def _normalize_manifest(m: dict) -> dict:
    """补齐 manifest 必填字段，避免 provider 因 None 崩。"""
    out = copy.deepcopy(m)
    out.setdefault("kind", "freeform")
    out.setdefault("ruleset", "none")
    out.setdefault("context_providers", [])
    out.setdefault("retrieval_policy", {})
    out.setdefault("gm_policy", {})
    return out


def _load_full_module_manifest(module_id: str) -> dict | None:
    """从 rpg/modules/<id>/module.json 加载完整 manifest（含 context_providers 等）。"""
    try:
        import modules as _module_registry
        bundle = _module_registry.load_module(module_id)
        return bundle.get("manifest") or {}
    except Exception:
        return None


# ── 调度入口 ─────────────────────────────────────────────────────

def run_providers(
    state,
    manifest: dict,
    demand: Demand,
    services: ProviderServices,
) -> tuple[list[ContextContribution], list[str]]:
    """按 manifest.context_providers 顺序运行每个 provider。

    返回 (contributions, used_ids)。任何 provider 异常都被吞掉并写入
    warnings；不会让单个 provider 失败拖垮整个流程。
    """
    out: list[ContextContribution] = []
    used: list[str] = []
    for pid in manifest.get("context_providers") or []:
        provider = _REGISTRY.get(pid)
        if not provider:
            out.append(ContextContribution(
                provider_id=pid, applied=False,
                warnings=[f"未注册的 provider: {pid}"],
                debug={"missing": True},
            ))
            continue
        try:
            if not provider.applies(state, manifest, demand):
                out.append(ContextContribution.skipped(pid, "applies()=False"))
                continue
            contrib = provider.collect(state, manifest, demand, services)
            if contrib is None:
                contrib = ContextContribution.skipped(pid, "collect() 返回 None")
            else:
                contrib.provider_id = pid  # 保持 id 一致
            out.append(contrib)
            if contrib.applied:
                used.append(pid)
        except Exception as exc:
            out.append(ContextContribution(
                provider_id=pid, applied=False,
                warnings=[f"provider 异常：{type(exc).__name__}: {exc}"],
                debug={"error": str(exc)},
            ))
    return out, used
