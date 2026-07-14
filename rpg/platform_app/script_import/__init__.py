"""
platform_app.script_import — 剧本导入 / 章节编辑编排(包化)。

原单文件 script_import.py(1443 行)按语义分段拆为子包;本 __init__ 是薄门面,
逐名 re-export 各子模块的全部顶层名(含下划线名与顶层 import 进来的名),让全仓
`script_import.X` / `from platform_app.script_import import X` 均零改动。

── 2026-07-15 拆包说明(纯机械搬家,零行为变化)────────────────────────────
  _base.py      — BASE 根 + 上传大小上限常量(单一路径基座,消除子模块间循环 import)
  uploads.py    — 分片上传(init/put/finish/cancel/_upload_dir/_read_meta/
                  _consume_upload_chunks/cleanup)+ 跨平台 meta.json 文件锁
  sync_jobs.py  — 后台 knowledge_sync durable 任务(_SYNC_POOL + schedule/claim/run/
                  recover/get_status + _jsonify)
  chapters.py   — 章节读取/手动编辑/合并/拆分/删除 + 结构锁与负区两段式重排
  imports.py    — import_script / preview_split / _validate_custom_pattern / _chapter_preview
本文件另留 delete_script + resplit_script:二者是整本剧本生命周期操作、共用 BASE
与 source_path 越界守卫,且 resplit_script 被多个单测按名 monkeypatch 其依赖
(init_db/connect/script_owned/_lock_chapter_struct/_validate_custom_pattern/chapter_splitter),
留在门面模块使这些既有 patch 目标零改动(patch-where-defined:依赖过多者整体留 __init__)。

铁律:mutable 全局 _SYNC_POOL 住 sync_jobs.py、_META_FALLBACK_LOCK 住 uploads.py,
与各自读写方同居;本门面上的 re-export 是同一对象引用,patch.object(pool,...) 仍生效。
"""
from __future__ import annotations

import os  # 名字可见性:原单文件顶层 import(未使用),保留 name parity
import logging
from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from chapter_splitter import chapter_splitter

from ..db import connect, expose, init_db, limit_value, page_payload
from ..library import decode_upload, safe_filename, unique_path
from ..perms import script_owned

from ._base import (
    BASE,
    MAX_SCRIPT_UPLOAD_BYTES,
    MAX_UPLOAD_CHUNK_BYTES,
    SCRIPT_ROOT,
    UPLOAD_CHUNK_ROOT,
    _script_upload_max_bytes,
    _upload_chunk_max_bytes,
)
from .sync_jobs import (
    MAX_ACTIVE_JOBS_PER_USER,
    STALE_RUNNING_SECONDS,
    SYNC_HEARTBEAT_SECONDS,
    _SYNC_POOL,
    _claim_pending_job,
    _jsonify,
    _run_sync_job,
    _sync_heartbeat_seconds,
    _sync_stale_running_seconds,
    _schedule_knowledge_sync,
    get_sync_status,
    recover_pending_sync_jobs,
)
from .uploads import (
    _consume_upload_chunks,
    _json,
    _lock_meta_file,
    _read_meta,
    _secrets,
    _t,
    _unlock_meta_file,
    _upload_dir,
    cancel_upload,
    cleanup_stale_upload_chunks,
    finish_upload,
    init_upload,
    put_chunk,
)
from .chapters import (
    ChapterConflict,
    _CHAPTER_STRUCT_LOCK_NS,
    _cursor_index,
    _lock_chapter_struct,
    _renumber_contiguous,
    _restore_from_negative,
    _shift_to_negative,
    create_blank_script,
    create_chapter,
    delete_chapters,
    list_chapters,
    merge_chapters,
    split_chapter,
    update_chapter,
)
from .imports import (
    _NESTED_QUANTIFIER_RE,
    _chapter_preview,
    _validate_custom_pattern,
    import_script,
    preview_split,
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════════════════════════════
#  删除剧本（连同 chapters / character_cards / worldbook / chapter_facts / saves 级联）
# ══════════════════════════════════════════════════════════════════════
def delete_script(user_id: int, script_id: int, *, force: bool = False) -> dict[str, Any]:
    """删除剧本。force=False 时拒绝删有 game_save 的剧本（防误删存档丢失）。"""
    init_db()
    with connect() as db:
        owned = script_owned(db, script_id, user_id)
        if not owned:
            raise ValueError("无权访问该剧本")
        save_count = int(db.execute(
            "select count(*) as n from game_saves where script_id = %s", (script_id,)
        ).fetchone()["n"])
        if save_count and not force:
            raise ValueError(f"该剧本下有 {save_count} 个存档，需先删存档或传 force=true")
        # 级联：scripts CASCADE 删 script_chapters / books / character_cards / worldbook /
        # chapter_facts；game_saves 用户传 force 才会删
        if save_count and force:
            db.execute("delete from game_saves where script_id = %s", (script_id,))
        db.execute("delete from scripts where id = %s", (script_id,))
        # 顺手删源文件 — phase_backend: 失败 log.warning 并向调用方返 source_file_kept,
        # 不再 silent swallow,运维能从 import_jobs/log 看到孤儿文件残留
        source_file_kept = False
        kept_reason = ""
        src = (owned.get("source_path") or "").strip()
        if src:
            p = (BASE / src).resolve() if not Path(src).is_absolute() else Path(src).resolve()
            base_resolved = BASE.resolve()
            if base_resolved not in p.parents and p != base_resolved:
                source_file_kept = True
                kept_reason = "source_path 越界,拒绝删除"
                logger.warning(
                    "delete_script: %s out of BASE, keeping source file (script_id=%s)",
                    src, script_id,
                )
            else:
                try:
                    if p.exists() and p.is_file():
                        p.unlink()
                except Exception as exc:
                    source_file_kept = True
                    kept_reason = f"unlink failed: {exc}"
                    logger.warning(
                        "delete_script: unlink %s failed: %s",
                        p, exc, exc_info=True,
                    )
    return {
        "ok": True, "deleted": True, "id": script_id,
        "saves_deleted": save_count if force else 0,
        "source_file_kept": source_file_kept,
        "kept_reason": kept_reason,
    }


# ══════════════════════════════════════════════════════════════════════
#  重切（用新规则重切已导入剧本，保留 script + 存档关系，只换章节）
# ══════════════════════════════════════════════════════════════════════
def resplit_script(
    user_id: int, script_id: int,
    *, split_rule: str = "auto", custom_pattern: str = "",
) -> dict[str, Any]:
    """换规则重切已导入剧本。

    保留 scripts/game_saves 不动，重新生成 script_chapters 行。

    ⚠️ 知识库联动（不是"不动"，是会被清空）：documents / document_chunks /
    chapter_facts 三张表都对 script_chapters(id) 设了 on delete cascade 外键，
    下面 `delete from script_chapters` 一执行，挂在旧章节下的这三表行会被数据库
    级联物理删除，不是"过时"而是"已删空"。本函数会在 resplit 提交后，
    在同一调用内自动重建 document_chunks 与 chapter_facts（零 LLM、确定性、
    从新的 script_chapters 重新切块/重新抽取），让三表行数与新章节重新对齐；
    character_cards/worldbook 不受 script_chapters 级联影响，不在本函数重建范围内，
    仍按旧契约"需要时调一次 sync"。若自动重建失败（见返回体 facts_rebuilt），
    调用方仍可手动触发 /rebuild/chunks、/rebuild/chapter-facts 兜底。
    """
    init_db()
    with connect() as db:
        script = script_owned(db, script_id, user_id)
        if not script:
            raise ValueError("无权访问该剧本")
        src = (script.get("source_path") or "").strip()
        if not src:
            raise ValueError("剧本源文件路径丢失")
        p = (BASE / src).resolve() if not Path(src).is_absolute() else Path(src).resolve()
        if BASE.resolve() not in p.parents and p != BASE.resolve():
            raise ValueError("source_path 越界, 拒绝操作")
        if not p.exists():
            raise ValueError("剧本源文件不存在，无法重切")
        raw = p.read_bytes()

    if (split_rule or "").strip() == "custom":
        if not (custom_pattern or "").strip():
            raise ValueError("split_rule=custom 时必须提供 custom_pattern")
        _validate_custom_pattern(custom_pattern)

    text, encoding = chapter_splitter.decode_bytes(raw)
    cleaned = chapter_splitter.clean_text(text)
    chapters, report = chapter_splitter.split_chapters_with_report(
        text, split_rule=split_rule or "auto",  # 传未清洗文本: with_report 内部清洗并计入 cleaning 报告
        custom_pattern=custom_pattern or "",
        source_name=Path(src).name, title=script.get("title") or "",
    )
    if not chapters:
        raise ValueError("重切结果为空")

    total_words = sum(len(c.get("content") or "") for c in chapters)
    with connect() as db:
        _lock_chapter_struct(db, script_id)  # 与 split/merge 共用锁,避免重切与逐章编辑并发互撞
        db.execute("SAVEPOINT resplit_save")
        try:
            db.execute("delete from script_chapters where script_id = %s", (script_id,))
            with db.cursor() as cur:
                cur.executemany(
                    """
                    insert into script_chapters(
                      script_id, chapter_index, title, content, word_count,
                      volume_title, source_marker, confidence,
                      is_author_note, exclude_from_extraction, title_confidence, content_descriptor
                    )
                    values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (script_id, i, str(c.get("title") or f"第{i}章")[:200],
                         str(c.get("content") or ""), len(str(c.get("content") or "")),
                         str(c.get("volume_title") or ""), str(c.get("source_marker") or ""),
                         float(report.get("confidence") or 0),
                         bool(c.get("is_author_note", False)), bool(c.get("exclude_from_extraction", False)),
                         float(c.get("title_confidence", 1.0)), str(c.get("content_descriptor") or ""))
                        for i, c in enumerate(chapters, start=1)
                    ],
                )
            # 重切后章节边界变了 → KB 与新边界对不上 → 强制回 unreviewed,用户须重过复核
            db.execute(
                "update scripts set chapter_count = %s, word_count = %s, import_report = %s, "
                "review_status = 'unreviewed', reviewed_at = null, updated_at = now() where id = %s",
                (len(chapters), total_words, Jsonb({**report, "encoding": encoding, "resplit": True}), script_id),
            )
        except Exception:
            db.execute("ROLLBACK TO SAVEPOINT resplit_save")
            raise

    # resplit 的 delete+reinsert 已在上面的 `with connect()` 块内提交完成。
    # documents/document_chunks/chapter_facts 挂在旧 script_chapters 行下的数据
    # 已被外键级联清空（不是"过时"，是物理删除），这里立即用零 LLM 的确定性
    # 重建函数把它们对齐到新的 script_chapters。重建失败只降级、绝不让 resplit
    # 本身报错——resplit 的核心操作（换章节结构）已经成功了。
    facts_rebuilt = False
    chunks_rebuilt = False
    rebuild_error = ""
    try:
        from .. import import_pipeline
        chunks_result = import_pipeline.rebuild_chunks_from_db(user_id, script_id)
        chunks_rebuilt = bool(chunks_result.get("ok"))
        facts_result = import_pipeline.rebuild_facts_from_db(user_id, script_id)
        facts_rebuilt = bool(facts_result.get("ok"))
        if not (chunks_rebuilt and facts_rebuilt):
            rebuild_error = (
                f"chunks.ok={chunks_result.get('ok')} facts.ok={facts_result.get('ok')}"
            )
    except Exception as exc:
        rebuild_error = f"{type(exc).__name__}: {exc}"
        logger.warning(
            "resplit_script: 自动重建 document_chunks/chapter_facts 失败 "
            "(script_id=%s): %s", script_id, exc, exc_info=True,
        )

    return {
        "ok": True, "script_id": script_id,
        "chapter_count": len(chapters), "word_count": total_words,
        "report": report,
        # knowledge_stale 是旧字段名,语义具有误导性(暗示"数据还在只是过时"，
        # 实际是"数据已被外键级联删空后又自动重建")。没有发现任何调用方读取
        # 这个字段(grep 全仓库只有本文件在写),但保留它以防未知/外部消费方，
        # 新增 knowledge_cleared 才是准确描述当下发生的事。
        "knowledge_stale": True,
        "knowledge_cleared": True,  # 诚实字段: 三表旧行已被级联删空,而非只是"过时"
        "chunks_rebuilt": chunks_rebuilt,
        "facts_rebuilt": facts_rebuilt,
        "rebuild_error": rebuild_error,
        "review_status": "unreviewed",
    }
