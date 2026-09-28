"""platform_app.api.script_edit._shared —— 拆包共享的单一 router 实例 + 跨资源族写入辅助。

各资源族子模块 `from ._shared import router[, _require_owner, _write_commit]` 后用
`@router.<verb>(...)` 注册端点;`__init__.py` import 全部子模块触发装配,再把这同一个
router 暴露给 `platform_app.api`(`from .script_edit import router`)。这样装配结果与拆分
前的单文件逐端点一致(共享同一 APIRouter 实例)。

_require_owner / _write_commit 是被 fork / 版本控制 / 世界书 / canon / 锚点 多个子模块共用
的写入辅助,故与 router 同居本模块(单一真相源,避免跨子模块循环 import);生产侧
`from platform_app.api.script_edit import _write_commit` 经 __init__ 门面 re-export 不变。
"""
from __future__ import annotations

import json
from functools import partial

from fastapi import APIRouter
from psycopg.types.json import Jsonb

from ...perms import script_owned

router = APIRouter()

# commit payload 常带整行快照(before/after),里面有 created_at / updated_at 等 datetime。
# Jsonb 默认 json.dumps 遇到 datetime 直接抛 TypeError → 整个写请求 500(canon PUT/POST
# 一直这样挂着,因为 canon 的快照列带 created_at)。在唯一落点统一兜成字符串。
_commit_payload_dumps = partial(json.dumps, ensure_ascii=False, default=str)

def _require_owner(db, script_id: int, user_id: int):
    """确认 user 是 script owner，不是则 raise ValueError。

    严格 owner SQL 收敛到 perms.script_owned;但保留本函数特有的两段区分性文案
    (「剧本不存在」vs「必须 fork 后才能编辑」)—— 故非 owner 时再查一次存在性以选文案
    (仅失败分支多一次查询,正常路径单查)。
    """
    owned = script_owned(db, script_id, user_id)
    if owned:
        return owned
    exists = db.execute("SELECT owner_id FROM scripts WHERE id = %s", (script_id,)).fetchone()
    if not exists:
        raise ValueError("剧本不存在")
    raise ValueError("必须 fork 后才能编辑（当前用户不是该剧本 owner）")


def _write_commit(
    db,
    *,
    script_id: int,
    user_id: int,
    kind: str,
    message: str,
    payload: dict,
    is_checkpoint: bool = False,
) -> int:
    """写入一条 script_commit，更新 scripts.head_commit_id，返回新 commit id。"""
    # 取当前 head 作 parent
    head = db.execute(
        "SELECT head_commit_id FROM scripts WHERE id = %s",
        (script_id,),
    ).fetchone()
    parent_id = int(head["head_commit_id"]) if head and head["head_commit_id"] else None

    row = db.execute(
        """
        INSERT INTO script_commits
          (script_id, parent_commit_id, author_user_id, message, kind, payload, is_checkpoint)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id
        """,
        (script_id, parent_id, user_id, message, kind,
         Jsonb(payload, dumps=_commit_payload_dumps), is_checkpoint),
    ).fetchone()
    commit_id = int(row["id"])

    db.execute(
        "UPDATE scripts SET head_commit_id = %s, updated_at = now() WHERE id = %s",
        (commit_id, script_id),
    )
    return commit_id
