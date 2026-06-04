from __future__ import annotations

from pathlib import Path
from typing import Any

from psycopg.types.json import Jsonb

from chapter_splitter import chapter_splitter

from .db import connect, expose, init_db, limit_value, page_payload
from .library import decode_upload, safe_filename, unique_path

BASE = Path(__file__).resolve().parents[1]
SCRIPT_ROOT = BASE / "platform_data" / "scripts"
UPLOAD_CHUNK_ROOT = BASE / "platform_data" / "upload_chunks"
from core.config import (
    script_upload_max_bytes as _script_upload_max_bytes,
)
from core.config import (
    upload_chunk_max_bytes as _upload_chunk_max_bytes,
)

MAX_SCRIPT_UPLOAD_BYTES = _script_upload_max_bytes()
MAX_UPLOAD_CHUNK_BYTES = _upload_chunk_max_bytes()  # 8MB / 块


# task 23：knowledge.sync_script_knowledge 的返回结果里常常嵌套 backend Row（dict-like）+ datetime
# 字段（created_at/updated_at）+ Decimal/UUID/bytes 等 jsonb 直接不能吃的类型。
# psycopg 的 Jsonb 默认走 json.dumps，遇到这些类型抛 TypeError，让整个 _run_sync_job 静默失败，
# 用户看到 import 200 OK 却没建知识库。这里统一兜底：递归走一遍替换为 JSON-safe 原语。
def _jsonify(value):
    """递归把不能直接 json.dumps 的类型转成 JSON-safe 原语。"""
    import datetime as _dt
    import decimal as _dec
    import uuid as _uuid
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, _dt.timedelta):
        return value.total_seconds()
    if isinstance(value, _dec.Decimal):
        # float 失真但 jsonb 不区分；如果要精确，改成 str(value)
        return float(value)
    if isinstance(value, _uuid.UUID):
        return str(value)
    if isinstance(value, (bytes, bytearray, memoryview)):
        try:
            return bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            import base64 as _b64
            return {"__bytes_b64__": _b64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, dict):
        return {str(k): _jsonify(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonify(v) for v in value]
    # psycopg Row / 其他 dict-like
    if hasattr(value, "keys") and callable(value.keys):
        try:
            return {str(k): _jsonify(value[k]) for k in value.keys()}
        except Exception:
            pass
    # 兜底：repr 而不是 raise，让 jsonb 至少能写
    return repr(value)


# ReDoS 防护：长度上限 + 禁止嵌套量词模式（(.+)+  (.*)*  ([^x]+)+ 等）
_NESTED_QUANTIFIER_RE = __import__("re").compile(r"\([^)]*[+*][^)]*\)[+*]")


def _validate_custom_pattern(pattern: str) -> None:
    """校验用户自定义正则，防止 ReDoS。"""
    import re as _re
    if len(pattern) > 200:
        raise ValueError("正则过长（上限 200 字符）")
    if _NESTED_QUANTIFIER_RE.search(pattern):
        raise ValueError("custom_pattern 含嵌套量词，可能导致 ReDoS，拒绝")
    try:
        _re.compile(pattern)
    except Exception as exc:
        raise ValueError(f"custom_pattern 不是合法正则：{exc}") from exc


def import_script(
    user_id: int,
    file_item: dict[str, Any] | None = None,
    *,
    split_rule: str = "auto",
    custom_pattern: str = "",
    title: str = "",
    upload_id: str = "",
) -> dict[str, Any]:
    """导入剧本。两种来源：
    - file_item: 单次 POST 的 base64（≤8MB 直接走这条）
    - upload_id: 已通过 init_upload + put_chunk + finish_upload 完成的分片
    """
    init_db()
    if upload_id:
        raw = _consume_upload_chunks(user_id, upload_id, peek=False)
        original_name = safe_filename(file_item.get("name") if file_item else None or Path(upload_id).name + ".txt")
    elif file_item:
        original_name = safe_filename(file_item.get("name") or "script.txt")
        raw = decode_upload(file_item)
    else:
        raise ValueError("请提供 file 或 upload_id")
    if len(raw) > MAX_SCRIPT_UPLOAD_BYTES:
        raise ValueError(f"剧本文件过大：{original_name}")

    text, encoding = chapter_splitter.decode_bytes(raw)
    cleaned = chapter_splitter.clean_text(text)
    if not cleaned:
        raise ValueError("剧本文本为空")

    script_title = (title or Path(original_name).stem or "未命名剧本").strip()[:160]

    # 自定义正则提前校验，避免坏正则被静默回退到 auto
    if (split_rule or "").strip() == "custom":
        if not (custom_pattern or "").strip():
            raise ValueError("split_rule=custom 时必须提供 custom_pattern")
        _validate_custom_pattern(custom_pattern)

    chapters, report = chapter_splitter.split_chapters_with_report(
        text,  # 传未清洗文本: with_report 内部 _normalize_encoding + sanitize 并计入 cleaning 报告
        split_rule=split_rule or "auto",
        custom_pattern=custom_pattern or "",
        source_name=original_name,
        title=script_title,
    )
    # 用户明确选了某种模式但实际走了另一种，要在报告里标出，并拒绝静默回退。
    # split_rule=custom 时,realize 出的 mode 是 'custom_pattern',应视为达成(原误判为不匹配 → 假拒绝)。
    _expected_modes = {split_rule, "auto"}
    if split_rule == "custom":
        _expected_modes.add("custom_pattern")
    if (split_rule or "auto") not in {"", "auto"} and report.get("mode") not in _expected_modes:
        raise ValueError(f"无法用 {split_rule} 规则切分该文本：实际只能用 {report.get('mode')}")
    if not chapters:
        raise ValueError("没有识别到可导入章节")

    user_dir = SCRIPT_ROOT / f"user_{user_id}"
    user_dir.mkdir(parents=True, exist_ok=True)
    target_path = unique_path(user_dir / original_name)
    target_path.write_bytes(raw)

    report = {
        **report,
        "encoding": encoding,
        "source_name": original_name,
        "storage_path": str(target_path.relative_to(BASE)),
    }
    total_words = sum(len(chapter.get("content") or "") for chapter in chapters)
    description = f"导入剧本 · {len(chapters)}章 · {report.get('mode_label', report.get('mode'))} · 置信 {report.get('confidence')}"

    with connect() as db:
        script = db.execute(
            """
            insert into scripts(owner_id, title, description, source_path, chapter_count, word_count,
                                 import_report, review_status)
            values (%s, %s, %s, %s, %s, %s, %s, 'unreviewed')
            returning *
            """,
            (user_id, script_title, description, str(target_path.relative_to(BASE)), len(chapters), total_words, Jsonb(report)),
        ).fetchone()
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
                    (
                        script["id"],
                        index,
                        str(chapter.get("title") or f"第{index}章")[:200],
                        str(chapter.get("content") or ""),
                        len(str(chapter.get("content") or "")),
                        str(chapter.get("volume_title") or ""),
                        str(chapter.get("source_marker") or ""),
                        float(report.get("confidence") or 0),
                        bool(chapter.get("is_author_note", False)),
                        bool(chapter.get("exclude_from_extraction", False)),
                        float(chapter.get("title_confidence", 1.0)),
                        str(chapter.get("content_descriptor") or ""),
                    )
                    for index, chapter in enumerate(chapters, start=1)
                ],
            )

    # phase_backend: 不再起 kind='knowledge_sync' 旧任务。
    # 上传完成就直接 schedule_full_import (kind='full_pipeline'),前端订阅 SSE 看真进度。
    # 老的 knowledge_sync 路径只在用户没配 LLM 凭证、或被显式 /knowledge/sync 调用时才走 fallback。
    # 这样 wizard 不再出现"toast 导入成功 → 任务静默死掉"的撕裂。
    try:
        from .import_pipeline import schedule_full_import
        sched = schedule_full_import(user_id, script["id"])
        job_id = sched.get("job_id")
        kind = "full_pipeline"
    except Exception as exc:
        # 没配 user LLM 凭证 / 别的 ValueError → 退到老的零 LLM 路径
        # (sync_script_knowledge 把 facts/cards 从词典聚合,不调 LLM)。
        # 不 silent swallow:把 exc 记到 import_report,前端能看到为什么走了 fallback。
        logger.warning(
            "import_script: schedule_full_import failed (%s), fallback to knowledge_sync",
            exc, exc_info=True,
        )
        job_id = _schedule_knowledge_sync(user_id, script["id"])
        kind = "knowledge_sync"
    return {
        "script": expose(script),
        "report": report,
        "knowledge": {
            "ok": True, "job_id": job_id, "status": "pending",
            "async": True, "kind": kind,
        },
        "preview": _chapter_preview(chapters),
    }


# ── 后台同步任务（DB 持久化 + 进程内执行）─────────────────────────
# B5: 状态从 import_jobs 表读写，避免 worker 重启或多进程下 _SYNC_STATE 丢失。
# 单一权威源：DB。in-process ThreadPoolExecutor 只是执行器。
#
# 三层保护防止重复跑同一任务：
# 1) 唯一索引 uq_import_jobs_active_per_script（migration v13）保证
#    (user_id, script_id, kind) 在 pending/running 状态下只能有一行
# 2) _schedule_knowledge_sync 用 INSERT ... ON CONFLICT DO NOTHING + RETURNING，
#    任何竞争方插入失败都回退到读 DB 拿现有 job_id
# 3) _run_sync_job 用 UPDATE ... WHERE status='pending' RETURNING 原子领取；
#    领取失败说明别的 worker 已经在跑（或已 done/failed），直接退出
import logging
import threading
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger(__name__)

_SYNC_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="script-sync")


MAX_ACTIVE_JOBS_PER_USER = 1
# 超过这个时长还在 running 视为 worker 崩溃，启动 recover 时回收
from core.config import (
    sync_heartbeat_seconds as _sync_heartbeat_seconds,
)
from core.config import (
    sync_stale_running_seconds as _sync_stale_running_seconds,
)

STALE_RUNNING_SECONDS = _sync_stale_running_seconds()
# heartbeat 刷新间隔（worker 跑长任务时定期更新 heartbeat_at）
SYNC_HEARTBEAT_SECONDS = _sync_heartbeat_seconds()


def _schedule_knowledge_sync(user_id: int, script_id: int) -> str:
    """触发后台同步（DB 持久化）。

    去重 + 限流：
    - 同 (user, script) 已有 pending/running → 返回老 job_id（依赖 uq_import_jobs_active_per_script 唯一索引兜底）
    - 同 user 跨 script 的活跃任务数 >= MAX_ACTIVE_JOBS_PER_USER → 拒绝

    并发安全：INSERT ... ON CONFLICT DO NOTHING + RETURNING 让两个进程同时进入也只能成功一个插入；
    失败方回查同 (user, script) 拿到对方的 job_id 返回。
    """
    import secrets

    from .db import connect, init_db
    init_db()
    job_id = f"ks_{script_id}_{secrets.token_hex(6)}"
    with connect() as db:
        # 限流（注意：此查询不在唯一索引保护内，是 advisory 的；竞争窗口的代价就是
        # 多挤进 1 个 job，对单用户场景可忽略；真要严格可用 advisory_lock）
        active_count_row = db.execute(
            """
            select count(*) as n from import_jobs
            where user_id = %s and kind = 'knowledge_sync'
              and status in ('pending', 'running')
              and (user_id, script_id) != (%s, %s)
            """,
            (user_id, user_id, script_id),
        ).fetchone()
        if int(active_count_row["n"]) >= MAX_ACTIVE_JOBS_PER_USER:
            raise ValueError(
                f"已有 {active_count_row['n']} 个同步任务在跑，"
                f"请等已有任务完成（每用户最多 {MAX_ACTIVE_JOBS_PER_USER} 个并发）"
            )

        # 原子去重：唯一索引 uq_import_jobs_active_per_script（partial unique index）
        # 保证同 (user_id, script_id, kind) 在 pending/running 状态下只能有一行。
        # PG 对 partial unique index 的 ON CONFLICT 需写 (cols) + WHERE 谓词（必须与索引谓词一致）。
        inserted = db.execute(
            """
            insert into import_jobs(job_id, user_id, script_id, kind, status, stage,
                                    stage_progress, stage_total, overall_progress, overall_total)
            values (%s, %s, %s, 'knowledge_sync', 'pending', 'pending', 0, 1, 0, 1)
            on conflict (user_id, script_id, kind)
              where status in ('pending', 'running')
              do nothing
            returning job_id
            """,
            (job_id, user_id, script_id),
        ).fetchone()
        if inserted:
            actual_job_id = inserted["job_id"]
        else:
            # 撞了：去查现有 active job
            row = db.execute(
                """
                select job_id from import_jobs
                where user_id = %s and script_id = %s and kind = 'knowledge_sync'
                  and status in ('pending', 'running')
                order by created_at desc limit 1
                """,
                (user_id, script_id),
            ).fetchone()
            if not row:
                # 极端竞争：唯一索引拒绝但 active 行又消失（被同时 done 了）。重试一次。
                inserted = db.execute(
                    """
                    insert into import_jobs(job_id, user_id, script_id, kind, status, stage,
                                            stage_progress, stage_total, overall_progress, overall_total)
                    values (%s, %s, %s, 'knowledge_sync', 'pending', 'pending', 0, 1, 0, 1)
                    on conflict (user_id, script_id, kind)
                      where status in ('pending', 'running')
                      do nothing
                    returning job_id
                    """,
                    (job_id, user_id, script_id),
                ).fetchone()
                if not inserted:
                    raise RuntimeError("无法插入 sync job 也无法读取已存在 job_id（请重试）")
                actual_job_id = inserted["job_id"]
            else:
                actual_job_id = row["job_id"]
    _SYNC_POOL.submit(_run_sync_job, actual_job_id)
    return actual_job_id


def _claim_pending_job(job_id: str) -> dict[str, Any] | None:
    """原子领取一个 pending 任务。
    UPDATE ... WHERE status='pending' RETURNING 一次完成判定 + 标记 + 取 owner 信息。
    返回 None 说明：任务不存在 / 已被别的 worker 领走 / 已 done/failed/cancelled。
    """
    from .db import connect
    with connect() as db:
        row = db.execute(
            """
            update import_jobs
            set status = 'running',
                started_at = coalesce(started_at, now()),
                heartbeat_at = now(),
                updated_at = now()
            where job_id = %s and status = 'pending'
            returning user_id, script_id, kind
            """,
            (job_id,),
        ).fetchone()
        return dict(row) if row else None


def _run_sync_job(job_id: str) -> None:
    """worker 入口：必须先 _claim_pending_job 原子领取，领不到直接退出。"""
    from psycopg.types.json import Jsonb

    from . import knowledge
    from .db import connect, init_db
    init_db()

    claim = _claim_pending_job(job_id)
    if not claim:
        # 已被别的 worker 领走 / 已结束 / 不存在；幂等返回
        logger.debug("sync job %s not pending, skip", job_id)
        return
    user_id = int(claim["user_id"])
    script_id = int(claim["script_id"])

    # 长任务 heartbeat：开一根后台线程，每 SYNC_HEARTBEAT_SECONDS 更新 heartbeat_at，
    # 让 stale-running 回收逻辑能区分活 worker 和死 worker。
    stop_heartbeat = threading.Event()

    # phase_backend: heartbeat 连续 3 次失败主动 abort,不再留 stale recover 兜底重跑。
    # 旧逻辑只 log.warning,worker 死了 DB 看不出来,recover 30 分钟后才回收。
    consecutive_hb_failures = {"n": 0}

    def _heartbeat_loop() -> None:
        while not stop_heartbeat.is_set():
            stop_heartbeat.wait(timeout=SYNC_HEARTBEAT_SECONDS)
            if stop_heartbeat.is_set():
                break
            try:
                with connect() as hb_db:
                    hb_db.execute(
                        "update import_jobs set heartbeat_at = now(), updated_at = now() "
                        "where job_id = %s and status = 'running'",
                        (job_id,),
                    )
                consecutive_hb_failures["n"] = 0
            except Exception:
                consecutive_hb_failures["n"] += 1
                logger.warning(
                    "heartbeat update failed for %s (consecutive=%d)",
                    job_id, consecutive_hb_failures["n"], exc_info=True,
                )
                if consecutive_hb_failures["n"] >= 3:
                    # DB 出问题超过 3 次,主动让主任务退出而不是 silently 跑下去
                    # (主任务的 ctl.update 也会跟着失败,后续 cancel/SSE 完全看不到)
                    logger.error(
                        "heartbeat consecutive 3 failures, abort job %s", job_id,
                    )
                    stop_heartbeat.set()
                    break

    hb_thread = threading.Thread(target=_heartbeat_loop, name=f"sync-hb-{job_id}", daemon=True)
    hb_thread.start()
    try:
        result = knowledge.sync_script_knowledge(user_id, script_id, rebuild=True)
        # phase_backend: result.partial_failures 非空 → done_with_errors,而非"假成功"。
        partial_failures = []
        if isinstance(result, dict):
            partial_failures = list(result.get("partial_failures") or [])
        final_status = "done_with_errors" if partial_failures else "done"
        error_text = ""
        if partial_failures:
            error_text = "; ".join(
                f"{p.get('stage', '?')}: {str(p.get('error', ''))[:100]}"
                for p in partial_failures
            )[:500]
        with connect() as db:
            db.execute(
                """
                update import_jobs
                set status = %s, stage = 'done',
                    stage_progress = 1, overall_progress = 1,
                    finished_at = now(), updated_at = now(),
                    usage_actual = %s,
                    warnings = %s,
                    error = case when %s = '' then error else %s end
                where job_id = %s
                """,
                # task 23：result 里可能含 datetime/date/Decimal/UUID/Row 等 jsonb 不能直接吃的对象
                # （如 sync_script_knowledge 把 book row 整个塞进结果时，含 created_at: datetime）。
                # 用 _jsonify 走一遍把它们转成 JSON-safe 字符串/原语，再喂 Jsonb。
                # 否则 psycopg 序列化时抛 TypeError，import 主路径已 200 但 sync 静默 failed → 用户以为知识库 OK 实际没建。
                (
                    final_status,
                    Jsonb(_jsonify({"result": result})),
                    Jsonb(_jsonify(partial_failures)),
                    error_text, error_text,
                    job_id,
                ),
            )
    except Exception as exc:
        logger.exception("sync job %s failed", job_id)
        with connect() as db:
            db.execute(
                """
                update import_jobs
                set status = 'failed', error = %s,
                    finished_at = now(), updated_at = now()
                where job_id = %s
                """,
                (str(exc)[:500], job_id),
            )
    finally:
        stop_heartbeat.set()


def recover_pending_sync_jobs(stale_running_seconds: int | None = None) -> dict[str, Any]:
    """启动时恢复 durable jobs。

    两类需要重新提交进线程池：
    1) status='pending' 但没有任何 worker 领走的（很可能是上次 crash 前已 schedule 但 submit 没完成）
    2) status='running' 但 heartbeat_at（或 started_at）超过 STALE_RUNNING_SECONDS 没更新的
       → 视为 worker 已死，原子回退到 pending，再丢回线程池
    返回：{recovered_pending: n, reclaimed_stale: n, resubmitted: [job_id...]}
    """
    from .db import connect, init_db
    init_db()
    stale_seconds = stale_running_seconds if stale_running_seconds is not None else STALE_RUNNING_SECONDS
    resubmitted: list[str] = []
    with connect() as db:
        # 1) stale running → 原子回 pending
        stale_rows = db.execute(
            """
            update import_jobs
            set status = 'pending',
                error = case when error = '' then 'reclaimed_after_stale' else error end,
                heartbeat_at = null,
                updated_at = now()
            where kind = 'knowledge_sync'
              and status = 'running'
              and coalesce(heartbeat_at, started_at, created_at)
                  < now() - make_interval(secs => %s)
            returning job_id
            """,
            (stale_seconds,),
        ).fetchall()
        reclaimed_stale = [r["job_id"] for r in stale_rows]
        # 2) 取所有 pending（含刚刚回退的）
        pending_rows = db.execute(
            """
            select job_id from import_jobs
            where kind = 'knowledge_sync' and status = 'pending'
            order by created_at asc
            """,
        ).fetchall()
        pending_job_ids = [r["job_id"] for r in pending_rows]

    # 在 with 外面 submit，避免持着 DB 连接 submit
    for jid in pending_job_ids:
        try:
            _SYNC_POOL.submit(_run_sync_job, jid)
            resubmitted.append(jid)
        except Exception:
            logger.warning("resubmit pending sync job %s failed", jid, exc_info=True)
    return {
        "ok": True,
        "recovered_pending": len(pending_job_ids) - len(reclaimed_stale),
        "reclaimed_stale": len(reclaimed_stale),
        "stale_job_ids": reclaimed_stale,
        "resubmitted": resubmitted,
    }


def get_sync_status(user_id: int, script_id: int) -> dict[str, Any]:
    """返回该剧本最近一次同步任务的状态（DB 单一源）。"""
    from .db import connect, init_db
    init_db()
    with connect() as db:
        row = db.execute(
            """
            select job_id, status, stage_progress, stage_total, overall_progress, overall_total,
                   started_at, finished_at, error, usage_actual, created_at
            from import_jobs
            where user_id = %s and script_id = %s and kind = 'knowledge_sync'
            order by created_at desc limit 1
            """,
            (user_id, script_id),
        ).fetchone()
    if not row:
        return {"ok": True, "status": "none", "script_id": script_id}
    progress_pct = 0
    if row["overall_total"] and row["overall_progress"] is not None:
        progress_pct = int(100 * int(row["overall_progress"]) / max(1, int(row["overall_total"])))
    out = {
        "job_id": row["job_id"],
        "user_id": user_id,
        "script_id": script_id,
        "status": row["status"],
        "progress": progress_pct,
        "started_at": row["started_at"].timestamp() if row["started_at"] else None,
        "finished_at": row["finished_at"].timestamp() if row["finished_at"] else None,
        "error": row["error"] or None,
    }
    usage = row.get("usage_actual") or {}
    if isinstance(usage, dict) and usage.get("result"):
        out["result_summary"] = {
            k: usage["result"].get(k)
            for k in ("documents", "chunks", "facts", "characters", "worldbook")
            if k in usage["result"]
        }
    return {"ok": True, **out}


def list_chapters(user_id: int, script_id: int, limit: int | str | None = None, cursor: str | None = None) -> dict[str, Any]:
    init_db()
    # 章节列表只回 180-char preview 元数据,放宽 limit 上限到 5000 — 给章节
    # 浏览 modal 一次拉完;500 万字小说约 1200 章,5000 cap 留 4x 余量
    page_limit = limit_value(limit, default=200, maximum=5000)
    before_index = _cursor_index(cursor)
    with connect() as db:
        script = db.execute("select * from scripts where id = %s and owner_id = %s", (script_id, user_id)).fetchone()
        if not script:
            raise ValueError("无权访问该剧本")
        rows = db.execute(
            """
            select id, public_id, chapter_index, title, word_count, volume_title,
                   left(content, 180) as content_preview, created_at, updated_at
            from script_chapters
            where script_id = %s and (%s::integer is null or chapter_index > %s)
            order by chapter_index asc
            limit %s
            """,
            (script_id, before_index, before_index, page_limit + 1),
        ).fetchall()
    payload = page_payload(rows, page_limit)
    if payload["items"]:
        payload["page"]["next_cursor"] = str(payload["items"][-1]["chapter_index"]) if payload["page"]["has_more"] else None
    payload["script"] = expose(script)
    return payload


def _chapter_preview(chapters: list[dict], limit: int = 8) -> list[dict[str, Any]]:
    return [
        {
            "chapter_index": index,
            "title": str(chapter.get("title") or f"第{index}章"),
            "volume_title": str(chapter.get("volume_title") or ""),
            "word_count": len(str(chapter.get("content") or "")),
            "content_preview": str(chapter.get("content") or "").replace("\n", " ")[:120],
        }
        for index, chapter in enumerate(chapters[:limit], start=1)
    ]


def _cursor_index(value: str | None) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


# ══════════════════════════════════════════════════════════════════════
#  Dry-run 预切（不入库）
# ══════════════════════════════════════════════════════════════════════
def preview_split(
    file_item: dict[str, Any] | None = None,
    *,
    split_rule: str = "auto",
    custom_pattern: str = "",
    upload_id: str = "",
    user_id: int | None = None,
    sample_limit: int = 20,
) -> dict[str, Any]:
    """前端调参用：返回切分预览但不入库。

    输入：file_item（base64 同 /api/scripts/import）或 upload_id（已分片上传完的文件）
    """
    if upload_id:
        raw = _consume_upload_chunks(user_id, upload_id, peek=True)
    elif file_item:
        raw = decode_upload(file_item)
    else:
        raise ValueError("需要 file 或 upload_id")
    if len(raw) > MAX_SCRIPT_UPLOAD_BYTES:
        raise ValueError("剧本文件过大")

    text, encoding = chapter_splitter.decode_bytes(raw)
    cleaned = chapter_splitter.clean_text(text)
    if not cleaned:
        raise ValueError("剧本文本为空")

    if (split_rule or "").strip() == "custom":
        if not (custom_pattern or "").strip():
            raise ValueError("split_rule=custom 时必须提供 custom_pattern")
        _validate_custom_pattern(custom_pattern)

    chapters, report = chapter_splitter.split_chapters_with_report(
        text, split_rule=split_rule or "auto",  # 传未清洗文本: with_report 内部清洗并计入 cleaning 报告
        custom_pattern=custom_pattern or "",
        source_name=str(file_item and file_item.get("name") or "preview.txt"),
        title="preview",
    )
    return {
        "ok": True,
        "encoding": encoding,
        "report": report,
        "total_chapters": len(chapters),
        "total_words": sum(len(c.get("content") or "") for c in chapters),
        "preview": _chapter_preview(chapters, limit=sample_limit),
    }


# ══════════════════════════════════════════════════════════════════════
#  删除剧本（连同 chapters / character_cards / worldbook / chapter_facts / saves 级联）
# ══════════════════════════════════════════════════════════════════════
def delete_script(user_id: int, script_id: int, *, force: bool = False) -> dict[str, Any]:
    """删除剧本。force=False 时拒绝删有 game_save 的剧本（防误删存档丢失）。"""
    init_db()
    with connect() as db:
        owned = db.execute(
            "select id, source_path from scripts where id = %s and owner_id = %s",
            (script_id, user_id),
        ).fetchone()
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
    知识库（chapter_facts/character_cards/worldbook）不动，需要时调一次 sync。
    """
    init_db()
    with connect() as db:
        script = db.execute(
            "select * from scripts where id = %s and owner_id = %s",
            (script_id, user_id),
        ).fetchone()
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
    chapter_splitter.clean_text(text)
    chapters, report = chapter_splitter.split_chapters_with_report(
        text, split_rule=split_rule or "auto",  # 传未清洗文本: with_report 内部清洗并计入 cleaning 报告
        custom_pattern=custom_pattern or "",
        source_name=Path(src).name, title=script.get("title") or "",
    )
    if not chapters:
        raise ValueError("重切结果为空")

    total_words = sum(len(c.get("content") or "") for c in chapters)
    with connect() as db:
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
    return {
        "ok": True, "script_id": script_id,
        "chapter_count": len(chapters), "word_count": total_words,
        "report": report,
        "knowledge_stale": True,  # 提示前端需要再触发一次 sync
        "review_status": "unreviewed",
    }


# ══════════════════════════════════════════════════════════════════════
#  分片上传（大文件 stream 到磁盘，避免 base64 撑爆内存）
# ══════════════════════════════════════════════════════════════════════
import json as _json
import secrets as _secrets
import time as _t

# ── 跨平台 meta.json 文件锁 ────────────────────────────────────────────────
# put_chunk 对同一 upload 的 meta.json 做 read-modify-write,需串行化。原实现用
# fcntl.flock(POSIX 跨进程锁),但 fcntl 是 Linux/macOS 专有,Windows 自托管下
# `import fcntl` 直接 ImportError → chunk 上传 500。这里按平台分发:
#   · POSIX:保持 fcntl.flock 跨进程语义(生产 workers≥2 不变)。
#   · Windows:fcntl 不存在 → 回退进程内 threading.Lock。Windows 自托管通常单进程,
#     且前端分片是串行 await,跨进程竞争实际不发生,进程内锁足够。
try:
    import fcntl as _fcntl

    def _lock_meta_file(fp) -> None:
        _fcntl.flock(fp.fileno(), _fcntl.LOCK_EX)

    def _unlock_meta_file(fp) -> None:
        _fcntl.flock(fp.fileno(), _fcntl.LOCK_UN)
except ImportError:  # Windows:无 fcntl,退化到进程内线程锁
    import threading as _threading

    _META_FALLBACK_LOCK = _threading.Lock()

    def _lock_meta_file(fp) -> None:
        _META_FALLBACK_LOCK.acquire()

    def _unlock_meta_file(fp) -> None:
        # _lock/_unlock 在 put_chunk 的 try/finally 内成对调用(同线程持锁),
        # 直接 release;极端兜底吞 RuntimeError(从未持锁时)。
        try:
            _META_FALLBACK_LOCK.release()
        except RuntimeError:
            pass


def init_upload(user_id: int, filename: str, total_bytes: int, total_chunks: int) -> dict[str, Any]:
    """开始一次分片上传，返回 upload_id。"""
    if not user_id:
        raise ValueError("分片上传需要登录用户")
    if total_bytes <= 0 or total_bytes > MAX_SCRIPT_UPLOAD_BYTES:
        raise ValueError(f"total_bytes 越界（最大 {MAX_SCRIPT_UPLOAD_BYTES}）")
    if total_chunks <= 0 or total_chunks > 4096:
        raise ValueError("total_chunks 越界（最大 4096）")
    upload_id = f"up_{user_id}_{_secrets.token_hex(8)}"
    user_dir = UPLOAD_CHUNK_ROOT / f"user_{user_id}" / upload_id
    user_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "upload_id": upload_id, "user_id": user_id,
        "filename": safe_filename(filename or "upload.bin"),
        "total_bytes": total_bytes, "total_chunks": total_chunks,
        "received_chunks": 0, "received_bytes": 0,
        "created_at": _t.time(),
    }
    (user_dir / "meta.json").write_text(_json.dumps(meta), encoding="utf-8")
    return meta


def put_chunk(user_id: int, upload_id: str, chunk_index: int, blob: bytes) -> dict[str, Any]:
    """写一块到磁盘。返回累计已收 chunks/bytes。"""
    user_dir = _upload_dir(user_id, upload_id)
    if len(blob) > MAX_UPLOAD_CHUNK_BYTES:
        raise ValueError(f"chunk 超过 {MAX_UPLOAD_CHUNK_BYTES} 字节")
    meta_path = user_dir / "meta.json"
    with open(meta_path, "r+") as fp:
        _lock_meta_file(fp)  # 跨平台:POSIX=fcntl 跨进程锁,Windows=进程内回退(见模块顶部)
        try:
            meta = _json.loads(fp.read())
            if chunk_index < 0 or chunk_index >= meta["total_chunks"]:
                raise ValueError("chunk_index 越界")
            if meta["received_bytes"] + len(blob) > meta["total_bytes"]:
                raise ValueError("累计字节超过 total_bytes 声明")
            chunk_path = user_dir / f"chunk_{chunk_index:04d}.bin"
            if chunk_path.exists():
                # 幂等：同 chunk_index 重传忽略大小调整
                meta["received_bytes"] -= chunk_path.stat().st_size
            chunk_path.write_bytes(blob)
            meta["received_bytes"] += len(blob)
            meta["received_chunks"] = sum(1 for _ in user_dir.glob("chunk_*.bin"))
            fp.seek(0)
            fp.truncate()
            fp.write(_json.dumps(meta))
        finally:
            _unlock_meta_file(fp)
    return meta


def finish_upload(user_id: int, upload_id: str) -> dict[str, Any]:
    """所有块到齐后，拼成最终文件。

    注意：这里不能删除 upload 目录。后续 preview/import 仍会用 upload_id 消费
    payload.bin；真正消费成功后由 _consume_upload_chunks(peek=False) 清理。
    """
    user_dir = _upload_dir(user_id, upload_id)
    meta = _read_meta(user_dir)
    if meta["received_chunks"] != meta["total_chunks"]:
        raise ValueError(f"分片未齐：{meta['received_chunks']}/{meta['total_chunks']}")
    if meta["received_bytes"] != meta["total_bytes"]:
        raise ValueError(f"字节不匹配：收到 {meta['received_bytes']} ≠ 声明 {meta['total_bytes']}")
    # 拼装
    payload_path = user_dir / "payload.bin"
    total_size = 0
    with open(payload_path, "wb") as out:
        for i in range(meta["total_chunks"]):
            p = user_dir / f"chunk_{i:04d}.bin"
            if not p.exists():
                raise ValueError(f"缺失 chunk {i}")
            data = p.read_bytes()
            total_size += len(data)
            out.write(data)
    for i in range(meta["total_chunks"]):
        (user_dir / f"chunk_{i:04d}.bin").unlink(missing_ok=True)
    meta["status"] = "finished"
    meta["finished_at"] = _t.time()
    meta["payload_bytes"] = total_size
    (user_dir / "meta.json").write_text(_json.dumps(meta), encoding="utf-8")
    return {
        "ok": True, "upload_id": upload_id, "filename": meta["filename"],
        "size": total_size,
    }


def cancel_upload(user_id: int, upload_id: str) -> dict[str, Any]:
    import shutil
    user_dir = _upload_dir(user_id, upload_id)
    if user_dir.exists():
        shutil.rmtree(user_dir, ignore_errors=True)
    return {"ok": True, "cancelled": True}


def _upload_dir(user_id: int, upload_id: str) -> Path:
    """安全：upload_id 必须以 up_<user_id>_ 开头 + 严格 slug 校验 + 解析后路径必须在用户分片根下。

    旧实现只看前缀，攻击者传 ``up_1_../../user_2/up_2_secret`` 可越权读/删他人分片目录。
    """
    import re as _re
    # 1) slug 校验：禁止任何分隔符 / 控制字符 / ..
    if not _re.fullmatch(r"up_\d+_[A-Za-z0-9_-]{1,64}", upload_id):
        raise ValueError("upload_id 格式非法")
    # 2) 前缀必须对应当前 user_id
    if not upload_id.startswith(f"up_{int(user_id)}_"):
        raise ValueError("无权访问该 upload_id")
    # 3) 解析后路径必须在该用户的分片根下（双保险，防止 OS 层符号链接欺骗）
    user_root = (UPLOAD_CHUNK_ROOT / f"user_{int(user_id)}").resolve()
    candidate = (user_root / upload_id).resolve()
    if user_root != candidate and user_root not in candidate.parents:
        raise ValueError("upload_id 路径越界")
    return candidate


def _read_meta(user_dir: Path) -> dict[str, Any]:
    meta_path = user_dir / "meta.json"
    if not meta_path.exists():
        raise ValueError("upload_id 不存在或已过期")
    return _json.loads(meta_path.read_text(encoding="utf-8"))


# ══════════════════════════════════════════════════════════════════════
#  章节手动编辑 / 合并 / 拆分
# ══════════════════════════════════════════════════════════════════════
def update_chapter(user_id: int, script_id: int, chapter_index: int, *,
                   title: str | None = None, content: str | None = None,
                   volume_title: str | None = None) -> dict[str, Any]:
    """编辑单章。title/content/volume_title 任一可传。"""
    init_db()
    with connect() as db:
        owned = db.execute(
            "select 1 from scripts where id = %s and owner_id = %s", (script_id, user_id),
        ).fetchone()
        if not owned:
            raise ValueError("无权访问该剧本")
        sets, params = [], []
        if title is not None:
            sets.append("title = %s")
            params.append(str(title)[:200])
        if content is not None:
            new_content = str(content)
            sets.append("content = %s")
            params.append(new_content)
            sets.append("word_count = %s")
            params.append(len(new_content))
        if volume_title is not None:
            sets.append("volume_title = %s")
            params.append(str(volume_title)[:200])
        if not sets:
            raise ValueError("没有要更新的字段")
        sets.append("updated_at = now()")
        params.extend([script_id, chapter_index])
        row = db.execute(
            f"update script_chapters set {', '.join(sets)} "
            f"where script_id = %s and chapter_index = %s returning *",
            tuple(params),
        ).fetchone()
        if not row:
            raise ValueError(f"章节 {chapter_index} 不存在")
        # 同步刷新 scripts.word_count
        total = db.execute(
            "select coalesce(sum(word_count),0) as n from script_chapters where script_id = %s",
            (script_id,),
        ).fetchone()
        db.execute(
            "update scripts set word_count = %s, updated_at = now() where id = %s",
            (int(total["n"]), script_id),
        )
    return {"ok": True, "chapter": expose(row)}


def merge_chapters(user_id: int, script_id: int, first_index: int,
                   *, separator: str = "\n\n") -> dict[str, Any]:
    """合并 first_index 和 first_index+1 两章。后面所有章节 index 减 1。"""
    init_db()
    with connect() as db:
        owned = db.execute(
            "select 1 from scripts where id = %s and owner_id = %s", (script_id, user_id),
        ).fetchone()
        if not owned:
            raise ValueError("无权访问该剧本")
        a = db.execute(
            "select * from script_chapters where script_id = %s and chapter_index = %s",
            (script_id, first_index),
        ).fetchone()
        b = db.execute(
            "select * from script_chapters where script_id = %s and chapter_index = %s",
            (script_id, first_index + 1),
        ).fetchone()
        if not a or not b:
            raise ValueError(f"需要章节 {first_index} 和 {first_index + 1} 都存在")

        merged_content = (a["content"] or "") + separator + (b["content"] or "")
        db.execute(
            """
            update script_chapters set
              content = %s, word_count = %s,
              updated_at = now()
            where id = %s
            """,
            (merged_content, len(merged_content), a["id"]),
        )
        db.execute("delete from script_chapters where id = %s", (b["id"],))
        # 后续章节 index 全部 -1
        db.execute(
            "update script_chapters set chapter_index = chapter_index - 1, updated_at = now() "
            "where script_id = %s and chapter_index > %s",
            (script_id, first_index + 1),
        )
        # 更新 scripts.chapter_count
        cnt = db.execute(
            "select count(*) as n, coalesce(sum(word_count),0) as w from script_chapters where script_id = %s",
            (script_id,),
        ).fetchone()
        db.execute(
            "update scripts set chapter_count = %s, word_count = %s, updated_at = now() where id = %s",
            (int(cnt["n"]), int(cnt["w"]), script_id),
        )
    return {"ok": True, "merged_into": first_index, "new_chapter_count": int(cnt["n"])}


def split_chapter(user_id: int, script_id: int, chapter_index: int,
                  *, split_at: int, new_title: str = "") -> dict[str, Any]:
    """按字符位置 split_at 把一章拆成两章。后续 index 全部 +1。"""
    init_db()
    if split_at <= 0:
        raise ValueError("split_at 必须 > 0")
    with connect() as db:
        owned = db.execute(
            "select 1 from scripts where id = %s and owner_id = %s", (script_id, user_id),
        ).fetchone()
        if not owned:
            raise ValueError("无权访问该剧本")
        ch = db.execute(
            "select * from script_chapters where script_id = %s and chapter_index = %s",
            (script_id, chapter_index),
        ).fetchone()
        if not ch:
            raise ValueError(f"章节 {chapter_index} 不存在")
        content = ch["content"] or ""
        if split_at >= len(content):
            raise ValueError(f"split_at ({split_at}) 超过章节长度 ({len(content)})")
        left_text = content[:split_at]
        right_text = content[split_at:]
        # 后续章节 index 全部 +1（腾位置）
        db.execute(
            "update script_chapters set chapter_index = chapter_index + 1, updated_at = now() "
            "where script_id = %s and chapter_index > %s",
            (script_id, chapter_index),
        )
        # 改原章为左半部分
        db.execute(
            "update script_chapters set content = %s, word_count = %s, updated_at = now() where id = %s",
            (left_text, len(left_text), ch["id"]),
        )
        # 插入右半为新章
        db.execute(
            """
            insert into script_chapters(
              script_id, chapter_index, title, content, word_count,
              volume_title, source_marker, confidence
            ) values (%s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (script_id, chapter_index + 1,
             str(new_title or (str(ch.get("title") or "") + "（下）"))[:200],
             right_text, len(right_text),
             ch.get("volume_title") or "", "manual_split",
             float(ch.get("confidence") or 0)),
        )
        cnt = db.execute(
            "select count(*) as n from script_chapters where script_id = %s",
            (script_id,),
        ).fetchone()
        db.execute(
            "update scripts set chapter_count = %s, updated_at = now() where id = %s",
            (int(cnt["n"]), script_id),
        )
    return {"ok": True, "split_at": split_at, "new_chapter_count": int(cnt["n"])}


def _consume_upload_chunks(user_id: int | None, upload_id: str, peek: bool = False) -> bytes:
    """preview/import 时读取已上传文件。peek=True 不删原文件。"""
    if not user_id:
        raise ValueError("缺 user_id")
    user_dir = _upload_dir(user_id, upload_id)
    meta = _read_meta(user_dir)
    if meta["received_chunks"] != meta["total_chunks"]:
        raise ValueError("分片未齐，无法消费")
    payload_path = user_dir / "payload.bin"
    if payload_path.exists():
        out = payload_path.read_bytes()
    else:
        out = bytearray()
        for i in range(meta["total_chunks"]):
            out.extend((user_dir / f"chunk_{i:04d}.bin").read_bytes())
        out = bytes(out)
    if not peek:
        import shutil
        shutil.rmtree(user_dir, ignore_errors=True)
    return bytes(out)


def cleanup_stale_upload_chunks(ttl_hours: int = 24, base_dir: Path | None = None) -> int:
    """清理超过 ttl_hours 的上传分片目录。返回清理的目录数。

    在 startup 时调用一次，以及 recover_pending_sync_jobs 附带调用。
    目录结构: base_dir/user_<id>/up_<id>_<token>/
    best-effort: 单个目录失败不影响其余目录。
    """
    import shutil

    if base_dir is None:
        base_dir = UPLOAD_CHUNK_ROOT
    if not base_dir.exists():
        return 0
    cutoff = _t.time() - (ttl_hours * 3600)
    cleaned = 0
    for user_dir in base_dir.glob("user_*"):
        if not user_dir.is_dir():
            continue
        for upload_dir in user_dir.glob("up_*"):
            if not upload_dir.is_dir():
                continue
            try:
                mtime = upload_dir.stat().st_mtime
                if mtime < cutoff:
                    shutil.rmtree(upload_dir, ignore_errors=True)
                    cleaned += 1
            except Exception:
                pass
    return cleaned
