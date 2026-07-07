"""kb/alias.py — canon 实体别名的确定性归并(运行时名字治理)。

群反馈(斗破档):①GM 写 relationships.云芝 与 relationships.云韵 = 人物面板同人两卡
(云芝=云韵化名,canon aliases 数据本来是对的,写入点没查);②GM 给原创路人起名薅了
后文角色名(韩枫/紫妍)。归并/识别都必须是代码缝,不指望 LLM(确定性铁律)。
"""
from __future__ import annotations

import logging
from functools import lru_cache

log = logging.getLogger(__name__)


@lru_cache(maxsize=4096)
def _alias_to_canonical(script_id: int, name: str) -> str:
    """名字命中某 canon 实体的 aliases(且非其主名)→返回主名;否则返回原名。
    失败原样返回(非致命)。lru 缓存:同剧本同名只查一次。"""
    if not script_id or not name or len(name) < 2:
        return name
    try:
        from platform_app.db import connect
        with connect() as db:
            row = db.execute(
                "select name from kb_canon_entities "
                "where script_id = %s and name <> %s and aliases ? %s limit 1",
                (int(script_id), name, name),
            ).fetchone()
        return str(row["name"]) if row and row.get("name") else name
    except Exception:
        return name


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
