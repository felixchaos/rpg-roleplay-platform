"""
user_models.py — 每用户的模型 overlay(安全隔离)

背景:model_apis / model_entries 是全局共享的 admin 策展菜单。用户通过
/api/models/remote/sync 用自己的 API Key 拉到的「本账号可见模型」是**私有**的:
- 一个用户的 OpenAI 账号可见的模型 ≠ 另一个用户的;
- 用户自建中转站(自定义 base_url provider)更是只属于该用户。

历史 bug:remote/sync 把这些写进全局 catalog,导致一个用户的 provider/模型
泄露进所有人(含 admin)的模型选择器。本模块把它们存进 user_model_entries,
只在该用户自己的 catalog 视图里 merge(见 model_registry.apply_user_overlay)。

入口:
- replace_synced_models(user_id, api_id, models)  覆盖某 provider 的同步结果
- load_overlay(user_id) -> {api_id: [model dict, ...]}  读该用户全部 overlay
"""
from __future__ import annotations

from typing import Any

from psycopg.types.json import Jsonb

from .db import connect, init_db
from .user_credentials import normalize_api_id


def _norm_model(m: dict[str, Any]) -> dict[str, Any] | None:
    real = str(m.get("real_name") or m.get("id") or "").strip()
    if not real:
        return None
    model_id = str(m.get("id") or real).strip()
    return {
        "id": model_id,
        "real_name": real,
        "display_name": str(m.get("display_name") or real).strip(),
        "enabled": bool(m.get("enabled", True)),
        "capabilities": list(m.get("capabilities") or ["text", "streaming"]),
    }


def replace_synced_models(user_id: int, api_id: str, models: list[dict[str, Any]]) -> int:
    """用 remote/sync 结果覆盖该用户某 provider 的 overlay 模型清单。

    Returns: 写入的模型条数。
    """
    if not user_id:
        return 0
    canonical = normalize_api_id(api_id) or (api_id or "").strip()
    if not canonical:
        return 0
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for m in models or []:
        norm = _norm_model(m if isinstance(m, dict) else {})
        if not norm or norm["id"] in seen:
            continue
        seen.add(norm["id"])
        rows.append(norm)
    init_db()
    with connect() as db:
        # 保留用户已设的可见性:re-sync 前读旧 enabled-by-model,新清单里**沿用**用户的选择
        # (否则每次同步都把用户隐藏的模型重新打开 → 用户「启用 provider 但只留几个模型」永远无效)。
        prev: dict[str, bool] = {}
        for er in db.execute(
            "select model_id, enabled from user_model_entries where user_id = %s and api_id = %s",
            (int(user_id), canonical),
        ).fetchall() or []:
            prev[er["model_id"]] = bool(er["enabled"])
        # 覆盖语义:先清该 (user, api_id) 旧 overlay,再写新清单。
        # ⚠️ 只清 source='synced' —— 用户**手填**的模型(source='manual')必须留下。
        # 有的 provider 没有 /models 接口(火山方舟 Agent Plan 订阅套餐恒 404),模型只能手填;
        # 若这里连它一起删,用户每点一次「拉取模型」就把自己填的清空一次。
        db.execute(
            "delete from user_model_entries "
            "where user_id = %s and api_id = %s and coalesce(source, 'synced') <> 'manual'",
            (int(user_id), canonical),
        )
        for r in rows:
            # 旧的若被用户隐藏(enabled=false)则保留隐藏;新模型沿用同步默认(通常 true)。
            keep_enabled = prev.get(r["id"], r["enabled"])
            db.execute(
                """
                insert into user_model_entries
                  (user_id, api_id, model_id, real_name, display_name, enabled, capabilities)
                values (%s, %s, %s, %s, %s, %s, %s)
                on conflict (user_id, api_id, model_id) do update set
                  real_name = excluded.real_name,
                  display_name = excluded.display_name,
                  enabled = excluded.enabled,
                  capabilities = excluded.capabilities,
                  updated_at = now()
                  -- source 不动:同名模型若已被用户手填过,保持 'manual',
                  -- 免得同步一次就把它降级成可被下次同步清掉的 'synced'。
                """,
                (
                    int(user_id), canonical, r["id"], r["real_name"],
                    r["display_name"], keep_enabled, Jsonb(r["capabilities"]),
                ),
            )
    return len(rows)


def upsert_manual_model(user_id: int, api_id: str, model: dict[str, Any]) -> dict[str, Any] | None:
    """把**用户自己填的**一个模型写进他的 overlay(user_model_entries, source='manual')。

    这是「provider 没有 /models 接口」时的唯一途径(如火山方舟 Agent Plan 订阅套餐,
    /models 恒 404)。与 model_registry.upsert_model 的区别是关键的:
      · upsert_model      → 写**全局 catalog**,admin-only,写进去所有人都看得见;
      · 本函数            → 只写**当前用户**的 overlay,任何用户都能用,别人看不到。
    (前科:v1.76.0 的「添加模型」按钮误接了前者,普通用户直接撞「需要管理员权限」。)

    source='manual' 让它在下次「拉取远程模型」时不被覆盖(见 replace_synced_models)。
    返回写入后的模型 dict;参数不合法返回 None。
    """
    if not user_id:
        return None
    canonical = normalize_api_id(api_id) or (api_id or "").strip()
    norm = _norm_model(model if isinstance(model, dict) else {})
    if not canonical or not norm:
        return None
    init_db()
    with connect() as db:
        db.execute(
            """
            insert into user_model_entries
              (user_id, api_id, model_id, real_name, display_name, enabled, capabilities, source)
            values (%s, %s, %s, %s, %s, %s, %s, 'manual')
            on conflict (user_id, api_id, model_id) do update set
              real_name = excluded.real_name,
              display_name = excluded.display_name,
              enabled = excluded.enabled,
              capabilities = excluded.capabilities,
              source = 'manual',
              updated_at = now()
            """,
            (int(user_id), canonical, norm["id"], norm["real_name"],
             norm["display_name"], bool(norm["enabled"]), Jsonb(norm["capabilities"])),
        )
    return {**norm, "api_id": canonical, "source": "manual"}


def delete_overlay_model(user_id: int, api_id: str, model: str) -> int:
    """删除该用户 overlay 里的一个模型(手填的删掉就没了;同步来的下次同步会回来)。"""
    if not user_id or not model:
        return 0
    canonical = normalize_api_id(api_id) or (api_id or "").strip()
    if not canonical:
        return 0
    init_db()
    with connect() as db:
        cur = db.execute(
            "delete from user_model_entries where user_id = %s and api_id = %s "
            "and (model_id = %s or real_name = %s)",
            (int(user_id), canonical, str(model), str(model)),
        )
        return int(getattr(cur, "rowcount", 0) or 0)


def set_overlay_model_enabled(user_id: int, api_id: str, model: str, enabled: bool) -> int:
    """设置该用户某同步模型(overlay)的可见性(enabled)。model 可传 model_id 或 real_name。

    用户(含 admin)用此隐藏自己同步来的单个模型(如 openrouter 几百个里只留几个);
    与全局 /api/models/visibility 不同 —— 那个写全局 model_entries,管不到 per-user overlay。
    Returns: 受影响行数(0 = 该模型不在用户 overlay 里)。
    """
    if not user_id or not model:
        return 0
    canonical = normalize_api_id(api_id) or (api_id or "").strip()
    if not canonical:
        return 0
    init_db()
    with connect() as db:
        rows = db.execute(
            "update user_model_entries set enabled = %s, updated_at = now() "
            "where user_id = %s and api_id = %s and (model_id = %s or real_name = %s) "
            "returning model_id",
            (bool(enabled), int(user_id), canonical, str(model), str(model)),
        ).fetchall()
    return len(rows or [])


def load_overlay(user_id: int) -> dict[str, list[dict[str, Any]]]:
    """读该用户全部 overlay,按 api_id 分组。无则返回空 dict。"""
    if not user_id:
        return {}
    try:
        init_db()
        with connect() as db:
            rows = db.execute(
                """
                select api_id, model_id, real_name, display_name, enabled, capabilities
                from user_model_entries
                where user_id = %s
                order by api_id, model_id
                """,
                (int(user_id),),
            ).fetchall()
    except Exception:
        return {}
    by_api: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_api.setdefault(normalize_api_id(r["api_id"]) or r["api_id"], []).append({
            "id": r["model_id"],
            "real_name": r["real_name"],
            "display_name": r["display_name"],
            "enabled": bool(r["enabled"]),
            "capabilities": list(r.get("capabilities") or ["text", "streaming"]),
            # synced=True 标记「这是用户同步来的 overlay 模型」:前端据此把可见性 toggle 路由到
            # per-user 端点(/api/me/models/visibility)而非全局端点。
            "synced": True,
        })
    return by_api
