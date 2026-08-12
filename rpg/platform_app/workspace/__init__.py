"""
platform_app.workspace — 存档工作区 / 初始快照构建(包化)。

原单文件 workspace.py(1280 行)按职责拆为子包;本 __init__ 是薄门面,逐名 re-export
各子模块的全部顶层名(含下划线名与顶层 import 进来的名),让全仓 `workspace.X` /
`from platform_app import workspace` 均零改动。所有生产调用与两处 monkeypatch 目标
(create_save / ensure_default)都走本门面上的属性查找,拆包后命中一致。

── 2026-07-15 拆包说明(纯机械搬家,逐字复制,零行为变化)────────────────────
  listing.py   — 列表/概览/就绪度只读面:ensure_default(遗留档 backfill 兜底)+ overview
                 + scripts / scripts_page + saves / saves_page + save_detail
                 + _readiness_for_scripts / _empty_readiness + _read_state_snapshot + 列清单常量
  snapshot.py  — 新存档初始 state 快照构建(⚠️出生点/进度信号病灶区):
                 _build_initial_snapshot(写 worldline.progress_chapter)+ _apply_script_opening
                 + _scrub_berlin_default + inline 元数据正则 + BERLIN 默认态常量
  creation.py  — 存档创建编排:create_save / create_tavern_save + _seed_kb_at_creation
                 + _ingest_character_book(初始 state 委托 snapshot._build_initial_snapshot)

铁律:本门面 re-export 是同一函数对象引用;patch("platform_app.workspace.create_save")
与 setattr(workspace, "ensure_default", ...) 都设在本包对象上,消费方按 workspace.X 属性
查找 → 命中补丁,与拆包前完全一致。函数体内 lazy 相对 import 已加深一层(. -> ..)。
"""
from __future__ import annotations

# —— 原单文件顶层 import 头,逐条搬来(相对 import 深一层)以保持 workspace.<name> name parity ——
import re
from typing import Any

from psycopg.types.json import Jsonb

from core.logging import get_logger
from state import SAVE_FILE
from state.core import _extract_secret_sections, _strip_secret_sections

from .. import branches, runtime
from ..db import connect, cursor_id, expose, init_db, limit_value, page_payload
from ..db import status as db_status
from ..perms import script_readable
from ..security import public_user
from .creation import (
    _ingest_character_book,
    _seed_kb_at_creation,
    create_save,
    create_tavern_save,
)

# —— 子模块全部顶层名 re-export(生产引用面零改动)——
from .listing import (
    _READINESS_KEYS,
    _SAVE_LIST_COLUMNS,
    _empty_readiness,
    _read_state_snapshot,
    _readiness_for_scripts,
    ensure_default,
    overview,
    save_detail,
    saves,
    saves_page,
    scripts,
    scripts_page,
)
from .snapshot import (
    _DEFAULT_BERLIN_LOC,
    _DEFAULT_BERLIN_OBJECTIVE_FRAG,
    _DEFAULT_BERLIN_PHASE,
    _DEFAULT_BERLIN_TIME,
    _OPENING_LOCATION_RE,
    _OPENING_OBJECTIVE_RE,
    _OPENING_TIME_RE,
    _apply_script_opening,
    _build_initial_snapshot,
    _has_opening_meta,
    _is_doc_title_only,
    _scrub_berlin_default,
)

log = get_logger(__name__)
