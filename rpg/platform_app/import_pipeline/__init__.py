"""
import_pipeline.py — 拆书流水线（多阶段 + DB 进度 + 取消 + 预算）

整体流程：
  1. chunks        — 文本切块入 document_chunks
  2. facts         — 规则 ChapterFact 入 chapter_facts
  3. entities      — 高频人物名提取（不调 LLM，靠词频）
  4. cards         — LLM 给 top N 人物生成人设卡（可关）
  5. worldbook     — LLM 提取地点/势力/概念入世界书（可关）

每阶段：
  - 进度落 import_jobs.stage_progress / overall_progress
  - 每个 chunk 检查 cancel_requested，true → 标 cancelled 退出
  - usage_actual 累加真实 token / cost
"""
from __future__ import annotations

# ── 2026-07-14 拆包说明(纯机械搬家,零行为变化)────────────────────────────
# 原单文件 import_pipeline.py(3209 行)按既有语义分段拆为子包;本 __init__ 是
# 薄门面,逐名 re-export 原模块的全部顶层名(含下划线名与顶层 import 进来的名),
# 全仓 `import_pipeline.X` / `from platform_app.import_pipeline import X` 均零改动。
#   errors.py            — 凭证缺失异常类
#   control.py           — estimate_budget + JobController
#   stages_llm.py        — 提取模型解析/凭证预检 + LLM 微任务阶段 + _parse_json
#   stages_core.py       — 确定性阶段 + canon/anchors/embeddings 阶段
#   runner.py            — STAGES/并发信号量/公共入口/完整流水线 worker/收尾兜底
#   rebuild_modules.py   — 单模块 rebuild 函数
#   rebuild_registry.py  — REBUILD_MODULES 注册表 + 嵌入预检 helpers
#   rebuild_worker.py    — _run_module_rebuild worker
#   rebuild_scheduler.py — estimate_module_rebuild + schedule_module_rebuild
# 注意:mutable 全局(_RUNNING/_QUEUE_DEPTH/_QUEUE_LOCK/信号量)统一住在 runner.py,
# 本门面上的 _QUEUE_DEPTH 是 import 时快照,运行期真值以 runner._QUEUE_DEPTH 为准
# (全仓无外部读者,仅为 dir() 兼容保留)。

# 原顶层 import 的名字(测试/调用方可能以 import_pipeline.X 形式引用)——保持可见
import json
import re
import secrets
import threading
from collections import Counter
from typing import Any

from psycopg.types.json import Jsonb

from ..db import connect, expose, init_db
from ..perms import script_owned
from core.llm_backend import DEFAULT_FALLBACK_API, DEFAULT_FALLBACK_MODEL
from model_aliases import credential_storage_api_id, normalize_api_id

from .errors import (
    MissingEmbeddingCredentialError,
    MissingUserCredentialError,
)
from .control import (
    JobController,
    estimate_budget,
)
from .stages_llm import (
    _backfill_unphased_with_even_split,
    _credential_api_id_for,
    _even_split_phases,
    _has_user_llm_credential,
    _parse_json,
    _require_user_llm_credential,
    _resolve_extractor_llm,
    _stage_cards,
    _stage_npc_voices,
    _stage_story_phase_llm,
    _stage_worldbook,
    require_user_llm_credential,
)
from .stages_core import (
    _ENTITY_SCAN_CHAPTERS,
    _backfill_chapter_facts_events_from_canon,
    _count_canon_and_anchors,
    _final_stage_status,
    _rerank_cards_by_canon_importance,
    _stage_canon_extract,
    _stage_chunks,
    _stage_embeddings,
    _stage_entities,
    _stage_facts,
    _stage_phase_digests,
)
from .runner import (
    STAGES,
    _IMPORT_GLOBAL_SEM,
    _IMPORT_SEM_CAPACITY,
    _IMPORT_SEM_NAME,
    _QUEUE_DEPTH,
    _QUEUE_LOCK,
    _RUNNING,
    _TERMINAL_STATUSES,
    _finalize_cancelled,
    _redis_sem_acquire,
    _redis_sem_init,
    _redis_sem_release,
    _run_pipeline,
    cancel_job,
    finalize_job_if_unterminated,
    get_job_status,
    list_jobs,
    reap_zombie_import_jobs,
    schedule_full_import,
    summarize_job_result,
    wait_for_import_job,
)
from .rebuild_modules import (
    rebuild_cards_from_canon,
    rebuild_cards_with_llm,
    rebuild_chunks_from_db,
    rebuild_facts_from_db,
    rebuild_worldbook_with_llm,
)
from .rebuild_registry import (
    REBUILD_MODULES,
    _embedding_preflight_or_raise,
    _embedding_prereq,
    normalize_rebuild_module,
)
from .rebuild_worker import (
    _count,
    _run_module_rebuild,
)
from .rebuild_scheduler import (
    estimate_module_rebuild,
    schedule_module_rebuild,
)
