"""core.llm_backend — LLM backend resolver helpers (共享给 agents/)。

抽出来的目的: command_agent / extractor / 其他 agent 不再各自实现。

用法示例:
    from core.llm_backend import (
        resolve_preferred_model as _resolve_preferred_model,
        resolve_preferred_api  as _resolve_preferred_api,
        detect_default_api     as _detect_default_api,
    )
"""
from __future__ import annotations

from typing import Optional


# 全局「便宜 vertex 默认」单一真源 —— 各调用点(command_agent / set_parser / _harness /
# extractor / acceptance_verifier / import_pipeline / tavern_cards / GameMaster …)的兜底
# 默认统一引用这两个常量,消除 2.5/3.5 漂移。
#
# 值 = 项目现行『当前默认』(model_probe.py:58 标注 gemini-3.5-flash『2026-05-19 当前默认』,
# 且全部姊妹调用点的硬编码兜底都是它)。**故意不读 DEFAULT_MODEL_CATALOG["selected"]**:那是
# 「全局 selected_model」模板(live DB 里是 anthropic/claude-opus-4-7),语义=平台默认主模型,
# 与这里的「便宜 vertex 子代理兜底」是两个不同概念。把兜底锚到 selected 会让最后兜底随主模型
# 漂成 2.5/opus,与 gemini-3.5-flash 的现行默认不一致(即上一轮引入的 blocker)。
DEFAULT_FALLBACK_API = "vertex_ai"
DEFAULT_FALLBACK_MODEL = "gemini-3.5-flash"


def detect_default_api() -> str:
    """启动时检测可用 backend: 优先 vertex_ai (SA 文件), 然后 anthropic (env key)."""
    import os as _os
    from pathlib import Path as _Path

    sa_path = _Path(__file__).parent.parent / "vertex_sa.json"
    if sa_path.exists():
        return "vertex_ai"
    if _os.environ.get("ANTHROPIC_API_KEY"):
        return "anthropic"
    return "vertex_ai"  # 默认仍兜底 vertex,失败时调用方走 fallback


def first_user_model(user_id: Optional[int], api_id: str | None = None) -> tuple[str, str] | None:
    """Return the first model backed by this user's own credential.

    Production must not fall back to the global selected model if the user has
    not configured that provider. This helper keeps the default path BYOK-only.
    """
    if not user_id:
        return None
    try:
        from model_registry import load_catalog_for_user, normalize_api_id
        from platform_app.db import connect, init_db

        target_api = normalize_api_id(api_id) if api_id else ""
        init_db()
        with connect() as db:
            rows = db.execute(
                """
                select api_id
                from user_api_credentials
                where user_id = %s and enabled = true and length(encrypted_key) > 0
                """,
                (int(user_id),),
            ).fetchall()
        credential_api_ids = {normalize_api_id(row["api_id"]) for row in rows}
        # 每用户视图:含该用户同步到的模型 + 自建中转站,BYOK 默认才能命中
        catalog = load_catalog_for_user(int(user_id))

        # 用户的 GM 模型偏好 = 他们实际在用、已验证可用的模型。优先返回它(在有凭证的
        # provider 下)。否则盲取"第一个 enabled" 会撞上 catalog 里排最前的过期/已下线模型
        # —— 例如平台 vertex provider 的首个模型 gemini-1.5-pro-002 早已 404 NOT_FOUND,
        # 导致子代理(身份生成 / phase compact 等)对没设专用偏好的用户一律失败。
        gm_pref = None
        try:
            with connect() as _dbp:
                _pr = _dbp.execute(
                    "select preferences->>'gm.model_real_name' as m from user_preferences where user_id = %s",
                    (int(user_id),),
                ).fetchone()
            gm_pref = (_pr or {}).get("m") if _pr else None
        except Exception:
            gm_pref = None
        if gm_pref:
            for api in catalog.get("apis", []):
                aid = normalize_api_id(api.get("id") or api.get("api_id"))
                if target_api and aid != target_api:
                    continue
                if aid not in credential_api_ids:
                    continue
                for model in api.get("models", []) or []:
                    if model.get("enabled") is False:
                        continue
                    rn = model.get("real_name") or model.get("id")
                    if rn and str(rn) == str(gm_pref):
                        return aid, str(rn)

        # 回退:有凭证 provider 的第一个 enabled 模型(原逻辑)
        for api in catalog.get("apis", []):
            aid = normalize_api_id(api.get("id") or api.get("api_id"))
            if target_api and aid != target_api:
                continue
            if aid not in credential_api_ids:
                continue
            for model in api.get("models", []) or []:
                if model.get("enabled") is False:
                    continue
                real_name = model.get("real_name") or model.get("id")
                if real_name:
                    return aid, str(real_name)
        # 兜底:用户有凭证但 provider 既不在全局 catalog、也没同步模型(自定义中转站,
        # 如 火山口/gg)→ 上面 catalog 循环匹配不到 → 之前返回 None,导致所有 BYOK 守卫
        # 无从回退、子代理落 vertex 失败。这里用"第一个启用凭证 + 用户 gm 模型偏好"兜底:
        # 玩家既然在用这个自定义 provider 玩,gm.model_real_name 偏好就是可用的模型名。
        with connect() as db2:
            cred = db2.execute(
                "select api_id from user_api_credentials where user_id=%s and enabled=true "
                "and length(encrypted_key)>0 order by updated_at desc",
                (int(user_id),),
            ).fetchall()
            pref = db2.execute(
                "select preferences->>'gm.model_real_name' as m from user_preferences where user_id=%s",
                (int(user_id),),
            ).fetchone()
        pref_model = (pref or {}).get("m") if pref else None
        for c in cred:
            aid = normalize_api_id(c["api_id"])
            if target_api and aid != target_api:
                continue
            if pref_model:
                return aid, str(pref_model)
    except Exception:
        return None
    return None


def _model_in_catalog(user_id: int, model_real_name: str) -> bool:
    """用户视图 catalog 里是否存在该 model_real_name。
    替代已删除的 KNOWN_OFFLINE_MODELS 黑名单:用"是否在真实 catalog 里"校验偏好有效性。
    任何异常视为"不确定,允许通过"(返回 True),避免过度拦截。
    """
    try:
        from model_registry import load_catalog_for_user
        catalog = load_catalog_for_user(int(user_id))
        for api in catalog.get("apis", []):
            for m in api.get("models", []) or []:
                rn = m.get("real_name") or m.get("id")
                if rn and str(rn) == str(model_real_name):
                    return True
        return False
    except Exception:
        return True  # 查询失败 → 保守放行


def resolve_preferred_model(
    user_id: Optional[int],
    pref_key: str = "set_parser.model_real_name",
) -> Optional[str]:
    """从用户偏好推断该用户应该用的 model。

    Args:
        user_id:  用户 ID，None 时直接返回 None。
        pref_key: user_preferences.preferences 字典里的键名，
                  不同 agent 使用不同命名空间，如:
                  - command_agent: "set_parser.model_real_name"
                  - extractor:     "extractor.model_real_name"

    内部使用 request-scoped cache（core.request_cache），一个请求内
    相同 user_id 只查一次 DB；非请求上下文每次直接查。

    catalog 校验:取到偏好的 model_real_name 后，用 load_catalog_for_user 验证该模型
    是否存在于用户视图 catalog 里；不存在则视为无效偏好（下线/迁移），回退到
    first_user_model(user_id)。替代已删除的 KNOWN_OFFLINE_MODELS 黑名单职责。
    """
    if not user_id:
        return None
    try:
        from core.request_cache import get_user_prefs_cached

        prefs = get_user_prefs_cached(int(user_id))
        model_name = prefs.get(pref_key) or None
        if not model_name:
            return None
        # catalog 存在性校验:偏好的模型不在用户 catalog 里 → 回退
        if not _model_in_catalog(int(user_id), model_name):
            result = first_user_model(int(user_id))
            return result[1] if result else None
        return model_name
    except Exception:
        return None


def resolve_preferred_api(
    user_id: Optional[int],
    pref_key: str = "set_parser.api_id",
) -> Optional[str]:
    """从用户偏好推断该用户应该用的 API provider。

    Args:
        user_id:  用户 ID，None 时直接返回 None。
        pref_key: user_preferences.preferences 字典里的键名，
                  不同 agent 使用不同命名空间，如:
                  - command_agent: "set_parser.api_id"
                  - extractor:     "extractor.api_id"

    内部使用 request-scoped cache，同一请求内 user_id 相同时复用
    preferences dict，不重复 SELECT。

    catalog 校验:若对应 model_real_name 偏好不在 catalog 里（已由
    resolve_preferred_model 判为无效），api_id 偏好也应一并回退。
    model_key 由调用方命名空间推断（将 pref_key 的 api_id 替换为 model_real_name）。
    """
    if not user_id:
        return None
    try:
        from core.request_cache import get_user_prefs_cached

        prefs = get_user_prefs_cached(int(user_id))
        api_id = prefs.get(pref_key) or None
        if not api_id:
            return None
        # 同步校验对应 model 偏好是否有效（model key = 同命名空间下的 model_real_name）
        model_key = pref_key.replace("api_id", "model_real_name")
        model_name = prefs.get(model_key) or None
        if model_name and not _model_in_catalog(int(user_id), model_name):
            result = first_user_model(int(user_id))
            return result[0] if result else None
        return api_id
    except Exception:
        return None


def _provider_usable_strict(user_id: int, api_id: str) -> bool:
    """三态核心:用户是否实际可用此 provider(有自己配的凭证)。

    单一真源(上提自 import_pipeline._has_user_llm_credential):
      · vertex_ai(含 kind=vertex_ai 的别名)→ 用户上传过 BYOK Service Account
      · 其它 → user_api_credentials 里有非空 key

    **不吞异常**:可用性可被确定时返回 True/False;凭证/SA 校验发生瞬时异常(DB 连接、
    解密失败等)时**向上抛**,代表「不可判定」。由调用方决定如何处理这一第三态:
      · user_can_use_provider(公开 bool 包装)→ 捕获后返回 False(保守拦截,沿用旧契约,
        供 model_probe / import_pipeline 薄委托)。
      · guard_byok_usable(BYOK 守卫)→ 捕获后保留已解析模型(不回退),与被收编的原四处
        内联守卫语义一致(可用性判定本身出错时不回退)。
    """
    from model_registry import api_kind
    # vertex:直接查凭证(不经 has_user_sa,它会把异常吞成 False、抹掉「不可判定」第三态)。
    if api_kind(api_id) == "vertex_ai" or api_id == "vertex_ai":
        from platform_app.user_credentials import get_credential
        cred = get_credential(int(user_id), "AgentPlatform")
        return bool(cred and cred.get("key"))
    from platform_app.user_credentials import get_credential
    cred = get_credential(int(user_id), api_id)
    return bool(cred and cred.get("key"))


def user_can_use_provider(user_id: Optional[int], api_id: str) -> bool:
    """该用户是否实际可用此 provider(有自己配的凭证)。公开 bool 契约。

    薄包装 _provider_usable_strict:任何异常(含「不可判定」第三态)视为 → False
    (保守:宁可触发回退也不放行不可用 provider)。供 model_probe / import_pipeline
    的薄委托,以及不需要区分「不可判定」的调用方使用。
    """
    if not user_id:
        return False
    try:
        return _provider_usable_strict(int(user_id), api_id)
    except Exception:
        return False


def guard_byok_usable(
    user_id: Optional[int],
    api_id: str | None,
    model: str | None,
    *,
    allow_override: bool = False,
) -> tuple[str, str]:
    """BYOK 守卫:解析出的 (api_id, model) 用户实际不可用时,回退到用户配过 key 的第一个模型。

    收编四处逐字内联守卫(app.py 主 GM / app.py 控制台助手 / _harness 子代理 /
    command_agent /set)。语义:
      · allow_override=True(调用方显式传了 override)→ 不触发守卫,原样返回。
      · 否则:有 user_id + 有 user_default(first_user_model) + api_id 与 user_default 不同
        + 该 provider 用户不可用(user_can_use_provider)→ 返回 user_default。
      · 其余情况原样返回 (api_id, model)。

    返回值始终是 (str, str)。
    """
    api_id = str(api_id or "")
    model = str(model or "")
    if allow_override:
        return api_id, model
    if not (user_id and api_id):
        return api_id, model
    user_default = first_user_model(user_id)
    if not user_default:
        return api_id, model
    if api_id == user_default[0]:
        return api_id, model
    # 与原四处内联守卫一致:可用性判定本身出错时**不**回退(保守保留已解析模型)。
    # 这里必须调三态核心 _provider_usable_strict —— 它在「不可判定」时**抛**,使下面的
    # except 真正生效(usable=True → 不回退)。若改调 user_can_use_provider,后者已把异常
    # 吞成 False,except 永不触发=死代码,且会把瞬时异常误判为「不可用」而回退,违反契约。
    try:
        usable = _provider_usable_strict(int(user_id), api_id)
    except Exception:
        usable = True
    if not usable:
        return user_default[0], user_default[1]
    return api_id, model


__all__ = [
    "DEFAULT_FALLBACK_API",
    "DEFAULT_FALLBACK_MODEL",
    "detect_default_api",
    "first_user_model",
    "resolve_preferred_model",
    "resolve_preferred_api",
    "user_can_use_provider",
    "guard_byok_usable",
]
