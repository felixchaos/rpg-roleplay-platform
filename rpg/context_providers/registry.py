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
        # #18 复读修复: 不再注入 recent_chat 文本层 — 历史已由 master 以
        # state.history_messages() 作为结构化 messages[] 传给模型,文本层是
        # 第二份历史,会被模型当"待续写草稿"先复述后续写。novel.py 的
        # _recent_text 是检索 scan_text(选 NPC/世界书),非 prompt 注入,保留。
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
        # #18 复读修复: 历史走 messages[],去掉重复的 recent_chat 文本层。
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
        # #18 复读修复: 历史走 messages[],去掉重复的 recent_chat 文本层。
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


DEFAULT_TAVERN_MANIFEST: dict = {
    "id": "__tavern__",
    "kind": "tavern",
    "ruleset": "none",
    "context_providers": [
        # 角色卡 + persona + 卡内高优先级 system_prompt(复用 make_layer 高优先级层基建)
        "tavern_character",
        # 角色带持久记忆(决策4):记忆/关系 op 仍可写
        "memory",
        # 用户硬约束 / 高优先级引导(/set、user_variables)
        "worldline",
        # 无剧本:不含 script_phase_anticipation / runtime_phase_digests / 任何 script/anchor provider
    ],
    "retrieval_policy": {
        "allow_script_retrieval": False,
        "allow_chapter_facts": False,
    },
    "gm_policy": {
        "mode": "tavern_gm",
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
        normalized = _normalize_manifest(explicit)
        # 酒馆 v2(R2):绑定剧本后,给 tavern manifest 追加剧本检索 providers
        # (novel_retrieval / novel_characters / novel_worldbook,与 DEFAULT_NOVEL_MANIFEST
        # 同款,scoped 到该 script_id)+ 放开 allow_script_retrieval。**主动检索**:只在
        # agent 查询相关轮触发(provider 吃 demand.retrieval_query),不前缀强灌。
        gm_mode = (normalized.get("gm_policy") or {}).get("mode")
        if gm_mode == "tavern_gm":
            tavern = data.get("tavern") if isinstance(data.get("tavern"), dict) else {}
            bound_script_id = (tavern or {}).get("bound_script_id")
            if bound_script_id:
                merged = copy.deepcopy(normalized)
                providers = list(merged.get("context_providers") or [])
                for pid in ("novel_retrieval", "novel_characters", "novel_worldbook"):
                    if pid not in providers:
                        providers.append(pid)
                merged["context_providers"] = providers
                rp = dict(merged.get("retrieval_policy") or {})
                rp["allow_script_retrieval"] = True
                merged["retrieval_policy"] = rp
                merged["id"] = f"__tavern__:script:{int(bound_script_id)}"
                # gm_policy.mode 保持 tavern_gm(角色扮演引擎不变,只是多了原著可查)
                return _normalize_manifest(merged)
        return normalized

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
