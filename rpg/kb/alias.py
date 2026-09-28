"""kb/alias.py — canon 实体别名的确定性归并(运行时名字治理)。

群反馈(斗破档):①GM 写 relationships.云芝 与 relationships.云韵 = 人物面板同人两卡
(云芝=云韵化名,canon aliases 数据本来是对的,写入点没查);②GM 给原创路人起名薅了
后文角色名(韩枫/紫妍)。归并/识别都必须是代码缝,不指望 LLM(确定性铁律)。

缓存:同剧本同名只查一次库,但必须会失效 —— 以前是进程级 lru_cache 永不失效,编辑器删掉实体
或去掉某个别名后,GM 写关系仍按旧别名归并,直到 worker 重启。现在:
  · canon 写入点(编辑器 REST / 编辑器 agent / 复核页 / 提取)写完调 invalidate_alias_cache,
    本进程立即生效;
  · 其它 worker 收不到通知,靠 _ALIAS_TTL 过期自愈(与 gm_serving.context_inject 的
    constant 缓存同口径,30 秒)。
"""
from __future__ import annotations

import logging
import threading
import time

log = logging.getLogger(__name__)

_ALIAS_TTL = 30.0  # 秒:跨 worker 失效窗口上界
_ALIAS_MAX = 4096
_ALIAS_CACHE: dict[tuple[int, str], tuple[float, str]] = {}
_ALIAS_LOCK = threading.Lock()


def _now() -> float:
    return time.monotonic()


def invalidate_alias_cache(script_id: int | None = None) -> None:
    """canon 实体增删改后调:清本进程里这个剧本(不给=全部)的别名归并缓存。"""
    with _ALIAS_LOCK:
        if script_id is None:
            _ALIAS_CACHE.clear()
            return
        try:
            sid = int(script_id)
        except (TypeError, ValueError):
            _ALIAS_CACHE.clear()
            return
        for k in [k for k in _ALIAS_CACHE if k[0] == sid]:
            _ALIAS_CACHE.pop(k, None)


def _alias_to_canonical(script_id: int, name: str) -> str:
    """名字命中某 canon 实体的 aliases(且非其主名)→返回主名;否则返回原名。
    失败原样返回(非致命,且不缓存失败)。"""
    if not script_id or not name or len(name) < 2:
        return name
    key = (int(script_id), name)
    hit = _ALIAS_CACHE.get(key)
    if hit and (_now() - hit[0]) < _ALIAS_TTL:
        return hit[1]
    try:
        from platform_app.db import connect
        with connect() as db:
            row = db.execute(
                "select name from kb_canon_entities "
                "where script_id = %s and name <> %s and aliases ? %s limit 1",
                (int(script_id), name, name),
            ).fetchone()
        result = str(row["name"]) if row and row.get("name") else name
    except Exception:
        return name
    now = _now()
    with _ALIAS_LOCK:
        if len(_ALIAS_CACHE) >= _ALIAS_MAX:
            for k in [k for k, v in _ALIAS_CACHE.items() if now - v[0] >= _ALIAS_TTL]:
                _ALIAS_CACHE.pop(k, None)
            if len(_ALIAS_CACHE) >= _ALIAS_MAX:
                _ALIAS_CACHE.clear()
        _ALIAS_CACHE[key] = (now, result)
    return result


def canonical_name_for_save(save_id: int | None, name: str) -> str:
    """按存档所属剧本做别名归并。查不到剧本/任何失败=原名。"""
    n = str(name or "").strip()
    if not save_id or not n:
        return n
    try:
        from platform_app.db import connect
        with connect() as db:
            row = db.execute(
                "select script_id from game_saves where id = %s", (int(save_id),)).fetchone()
        sid = int((row or {}).get("script_id") or 0)
    except Exception:
        return n
    return _alias_to_canonical(sid, n) if sid else n
