"""core.feature_flags — 每用户【引擎特性开关】(默认开),复用 user_preferences JSONB。

用户拍板:GM 流水线/存档知识库等引擎特性的开关权交给用户、默认启用、统一在「模块模型」里控制。
单一来源:每个特性一个偏好键 `<key>.enabled`(true / false / 未设)。解析顺序:
    用户偏好(显式 true/false)  >  环境变量(全局,默认 "1"=开)  >  内置默认开
偏好未设 = 跟随环境(默认开)。任何读取失败静默退回环境默认,绝不破回合。

特性键 = 前端 agent-modules.js 的 FEATURES[].key,前后端同名(单一真相),前端经
`POST /api/me/preference` 写 `{"<key>.enabled": true/false}`,后端这里读同一键。
"""
from __future__ import annotations

import os

# key -> (env_var, env_default)。env_default 全 "1":用户要求默认启用(全局默认开,用户可逐项关)。
_FEATURES: dict[str, tuple[str, str]] = {
    "ctx_tiered": ("RPG_CTX_TIERED", "1"),          # 分层上下文缓存(司命)
    "recorder_unified": ("RPG_RECORDER_UNIFIED", "1"),  # 史官三合一
    "narrator_slim": ("RPG_NARRATOR_SLIM", "1"),    # 文宗精简(去工具循环)
    "rag_gate": ("RPG_RAG_GATE", "1"),              # 司命 RAG 检索闸
    "kb_state": ("RPG_KB_STATE", "1"),              # 存档知识库 DB 化
    "anchor_pace": ("RPG_ANCHOR_PACE", "1"),        # 锚点节奏(限速/窗口/intro/死亡失效)
    "episodic_recall": ("RPG_EPISODIC_RECALL", "0"),  # 永恒记忆·对玩家游戏历史语义召回(默认关,验后开)
    "consequence_ledger": ("RPG_CONSEQUENCE_LEDGER", "0"),  # 后果账本(承诺/欠债到期回响,默认关,验后开)
    "world_heartbeat": ("RPG_WORLD_HEARTBEAT", "0"),  # 世界心跳(玩家不在场时的世界侧小事,默认关,验后开)
    "channel_fallback": ("RPG_CHANNEL_FALLBACK", "0"),  # 跨渠道 fallback(主渠道重试耗尽切备用凭据渠道,默认关,验后开)
    "npc_agenda": ("RPG_NPC_AGENDA", "0"),  # NPC 议程(当下活跃 NPC 的意图/态度,默认关,验后开)
}

_FALSY = ("0", "false", "no", "off", "")


def _env_on(key: str) -> bool:
    env_var, env_default = _FEATURES[key]
    return os.environ.get(env_var, env_default).strip().lower() not in _FALSY


def feature_enabled(key: str, user_id: int | None = None) -> bool:
    """特性是否对该用户开启。用户偏好优先,未设跟随环境(默认开)。

    user_id=None → 仅看环境默认(无用户上下文的深层/批处理路径)。
    """
    if key not in _FEATURES:
        return False
    if user_id is not None:
        try:
            from core.request_cache import get_user_prefs_cached
            v = get_user_prefs_cached(int(user_id)).get(f"{key}.enabled")
            if v is not None:
                return bool(v)
        except Exception:
            pass
    return _env_on(key)


def feature_enabled_for_save(key: str, save_id: int | None, db: object | None = None) -> bool:
    """save 维度入口:从 save_id 反查 owner user_id 再判(供无 user 上下文的锚点深层路径用)。

    db 给定则复用连接查 owner(零额外连接);否则自开只读查。查不到 owner → 退回环境默认。
    """
    uid = _owner_uid(save_id, db)
    return feature_enabled(key, uid)


def _owner_uid(save_id: int | None, db: object | None = None) -> int | None:
    if not save_id:
        return None
    try:
        if db is not None:
            r = db.execute("select user_id from game_saves where id = %s", (int(save_id),)).fetchone()
            return int(r["user_id"]) if r and r.get("user_id") is not None else None
        from platform_app.db import connect
        with connect() as _db:
            r = _db.execute("select user_id from game_saves where id = %s", (int(save_id),)).fetchone()
            return int(r["user_id"]) if r and r.get("user_id") is not None else None
    except Exception:
        return None


def feature_keys() -> list[str]:
    return list(_FEATURES.keys())
