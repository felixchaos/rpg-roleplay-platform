"""core.feature_flags — 每用户【引擎特性开关】(默认开),复用 user_preferences JSONB。

用户拍板:GM 流水线/存档知识库等引擎特性的开关权交给用户、默认启用、统一在「模块模型」里控制。
单一来源:每个特性一个偏好键 `<key>.enabled`(true / false / 未设)。解析顺序:
    用户偏好(显式 true/false)  >  环境变量(全局,默认 "1"=开)  >  内置默认开
偏好未设 = 跟随环境(默认开)。任何读取失败静默退回环境默认,绝不破回合。

特性键 = 前端 agent-modules.js 的 FEATURES[].key,前后端同名(单一真相),前端经
`POST /api/me/preference` 写 `{"<key>.enabled": true/false}`,后端这里读同一键。

⚠️ **用户偏好不是访问控制**(08-01 反馈:「为什么普通用户可以看到 rath」)。
`POST /api/me/preference` 收任意键、无白名单,于是任何登录用户都能自己 POST 一句
`{"rath_experiment.enabled": true}` 把「默认关的灰度特性」给自己打开 —— 前端那两道
(导航隐藏 / 路由守卫)只是藏 UI,绕过成本就是一次 POST,而后端这道闸读的正是用户
自己能写的那张表。于是「三道防线」实际上一道都不成立。

分界不在「默认开还是默认关」,而在**这个开关控制什么**:

  · **引擎开关**(绝大多数)—— 只影响这个用户自己这局怎么跑(分层缓存、召回深度、
    NPC 议程…)。用户偏好照旧优先;按人灰度(金丝雀)也靠它,不能锁。
  · **访问闸**(`_ACCESS_GATED`)—— 决定能不能进某个界面/功能面。用户偏好一律
    **忽略**,只认环境变量;谁能用由真正的角色闸决定(RATH 路由另有 is_admin)。

加新特性时的判据:**这个开关决定「能不能进某个页面/管理面」吗?**
是 → 进 `_ACCESS_GATED`;只是改自己这局的跑法 → 不用管。
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
    "kb_hash_keys": ("RPG_KB_HASH_KEYS", "0"),  # KB 事件内容哈希键+seq(方案A,默认关,验后开;关闭后已转换档下回合自愈回 legacy)
    "rath_experiment": ("RPG_RATH_EXPERIMENT", "0"),  # RATH·搖光观测台(离线世界实验,默认关,验后开)
}

# 访问闸:这些键决定「能不能进某个界面/功能面」,**用户偏好一律忽略**(见模块头)。
# 其余键都是只影响自己这局的引擎开关 —— 用户偏好照旧优先,按人灰度也靠它
# (线上就有靠偏好跑的金丝雀,一刀切锁死会把它们静默关掉)。
_ACCESS_GATED: frozenset[str] = frozenset({
    "rath_experiment",   # RATH·搖光观测台:灰度期仅管理员,前端 adminOnly + 路由 AdminGuard 同向
})

# 前端「模块模型」页面里列给用户自己控制的键(agent-modules.js FEATURES)。
# 与 _ACCESS_GATED 必须无交集 —— 一个键不能既摆在用户面前又声称是访问闸。
_USER_TOGGLABLE: frozenset[str] = frozenset({
    "ctx_tiered", "recorder_unified", "narrator_slim", "rag_gate", "kb_state",
    "anchor_pace", "consequence_ledger", "world_heartbeat", "channel_fallback",
    "npc_agenda",
})

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
    # 访问闸:用户偏好不参与判定(用户偏好不是访问控制,见模块头)。
    if user_id is not None and key not in _ACCESS_GATED:
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


def rejected_feature_keys(payload: dict) -> set[str]:
    """从一份 preference payload 里挑出**用户无权自设**的特性开关键。

    只拦**访问闸**那几个键;引擎开关(用户自己这局怎么跑)与其它偏好一律放行 ——
    线上有靠偏好跑的按人灰度,拦它们就是把金丝雀掐了。
    """
    out: set[str] = set()
    for k in (payload or {}):
        if not isinstance(k, str) or not k.endswith(".enabled"):
            continue
        feat = k[: -len(".enabled")]
        if feat in _ACCESS_GATED:
            out.add(k)
    return out
