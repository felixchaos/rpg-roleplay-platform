"""platform_app.api.script_edit —— schema v44 剧本 fork / Git 版本控制 / 手动编辑(包化)。

原单文件(1633 行)按资源族拆为子包;本 __init__ 是薄门面:import 全部子模块触发装配
(各子模块 `from ._shared import router` 后用 `@router.<verb>` 注册,共享同一 APIRouter
实例),再逐名 re-export 原模块的全部公开名(含 router / 全部 api_* 端点 / 下划线辅助与常量),
让 `from platform_app.api.script_edit import router`(以及生产侧 `import _write_commit`、
测试侧 `import _anchor_update_sets / api_undo_chapter_edit`)与既有引用零改动。

endpoints:
  POST   /api/scripts/{script_id}/fork
  GET    /api/scripts/{script_id}/commits
  POST   /api/scripts/{script_id}/pin
  POST   /api/scripts/{script_id}/unpin
  PUT    /api/scripts/{script_id}/worldbook/{entry_id}
  POST   /api/scripts/{script_id}/worldbook
  DELETE /api/scripts/{script_id}/worldbook/{entry_id}
  POST   /api/scripts/{script_id}/worldbook/batch   (批量 delete/enable/disable/set_priority)
  PUT    /api/scripts/{script_id}/canon-entities/{logical_key}
  POST   /api/scripts/{script_id}/canon-entities
  DELETE /api/scripts/{script_id}/canon-entities/{logical_key}
  PUT    /api/scripts/{script_id}/anchors/{anchor_id}
  POST   /api/scripts/{script_id}/anchors
  DELETE /api/scripts/{script_id}/anchors/{anchor_id}
  POST   /api/scripts/{script_id}/checkout/{commit_id}

── 2026-07-14 拆包说明(纯机械搬家,零行为变化)────────────────────────────
_shared.py    — 共享的单一 router 实例 + _require_owner / _write_commit
fork.py       — 剧本 fork(整本复制)
versioning.py — commits log + 章节/世界书/角色卡撤销恢复(undo/history/restore)+ checkout
sharing.py    — pin / unpin(含 _VALID_SHARING_MODES)
worldbook.py  — 世界书写侧 CRUD + 批量(含 _WB_BATCH_ACTIONS)
canon.py      — canon 实体写侧 CRUD
anchors.py    — 时间线锚点写侧 CRUD(含 _anchor_update_sets)
writing.py    — 写作规范 + 审稿问题
search.py     — 全书检索
agent_doc.py  — 编辑器文档暂存
"""
from __future__ import annotations

# 原顶层 import 的名字(测试/调用方可能以 module.X 形式引用)—— 保持可见
import json as _json  # noqa: F401
from typing import Any  # noqa: F401

from psycopg.types.json import Jsonb  # noqa: F401

from ...db import connect  # noqa: F401
from ...perms import script_owned  # noqa: F401
from .._deps import json_response, require_user  # noqa: F401
from ._shared import _require_owner, _write_commit, router
from .agent_doc import api_agent_doc_upload
from .anchors import (
    _anchor_update_sets,
    api_anchor_add,
    api_anchor_delete,
    api_anchor_update,
)
from .canon import (
    api_canon_add,
    api_canon_delete,
    api_canon_update,
)
from .fork import api_fork_script
from .search import api_script_search
from .sharing import (
    _VALID_SHARING_MODES,
    api_pin_script,
    api_unpin_script,
)
from .versioning import (
    _UNDO_SPEC,
    api_chapter_history,
    api_chapter_restore,
    api_chapter_undoable,
    api_checkout_commit,
    api_list_commits,
    api_undo_chapter_edit,
    api_undo_edit,
)
from .worldbook import (
    _WB_BATCH_ACTIONS,
    api_worldbook_add,
    api_worldbook_batch,
    api_worldbook_delete,
    api_worldbook_update,
)
from .writing import (
    api_clear_writing_issues,
    api_dismiss_writing_issue,
    api_get_writing_rules,
    api_list_writing_issues,
    api_put_writing_rules,
)

__all__ = ["router"]
