"""platform_app.image_jobs — Phase 1-C: 生图异步 job 编排。

职责：
  enqueue_image_generation(...)  — 建 ai_images 记录 + 入 chat_postproc_tasks 队列
  handle_image_gen(payload)      — worker handler（由 run_postproc_worker 注册）
  _notify_image_ready(...)       — SSE 回推 image_ready{image_id, url, kind}

worker 集成：在 scripts/run_postproc_worker.py 的 TASK_HANDLERS 里加：
    from platform_app.image_jobs import handle_image_gen
    TASK_HANDLERS["image_gen"] = handle_image_gen
并在 platform_app/postproc_queue.py 的 TASK_KINDS 里追加 "image_gen"。
（整合时由主代理完成，此文件独立可编译）。

Phase 3 增量：
  - enqueue_image_generation 加 save_id 参数，透传 create_image_record + payload。
  - 每日配额(确定性)：起点查该 user 近 24h ai_images 行数(非 failed)，
    ≥ RPG_IMAGE_DAILY_CAP(env,默认 50) 则不入队，返回 quota_exceeded。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any

log = logging.getLogger(__name__)

# ── 入队 ────────────────────────────────────────────────────────────────

_INSERT_SQL = """
INSERT INTO chat_postproc_tasks
    (user_id, save_id, commit_id, task_kind, payload, status, scheduled_at)
VALUES
    (%(user_id)s, %(save_id)s, NULL, 'image_gen',
     %(payload)s::jsonb, 'pending', now())
"""

# image 任务没有 game save 归属；用固定占位符满足 NOT NULL 约束。
_IMAGE_SAVE_PLACEHOLDER = "image_job"


def enqueue_image_generation(
    user_id: int,
    prompt: str,
    kind: str,
    *,
    api_id: str | None = None,
    model: str | None = None,
    origin: str = "api_direct",
    extra: dict[str, Any] | None = None,
    attach: dict[str, Any] | None = None,
    save_id: str | None = None,
    message_index: int | None = None,
) -> dict[str, Any]:
    """建 ai_images 记录(status='pending')，入 postproc_queue，返回 {image_id, status}.

    api_id / model 未传时用用户偏好回退（image_gen 能力优先，可后续细化）。
    origin 透传到 payload 以便 worker 计费/审计区分来源。
    attach: 可选附着目标，如 {"type": "script_cover", "script_id": 42}，
            worker 完成后把 url 写回目标表（带 ownership 校验）。
    save_id: 可选，关联游戏存档 ID（Phase 3）。

    每日配额(确定性)：
      查该 user 近 24h ai_images 行数(status != 'failed')，
      ≥ RPG_IMAGE_DAILY_CAP(env,默认 50) 则不建记录、不入队，
      返回 {"error": "quota_exceeded", "status": "failed", "image_id": None}。
    """
    from platform_app.api.images import create_image_record
    from platform_app.db import connect

    # ── 每日配额检查（确定性）─────────────────────────────────────────────
    daily_cap: int = int(os.getenv("RPG_IMAGE_DAILY_CAP", "50"))
    try:
        with connect() as _db:
            row = _db.execute(
                """
                select count(*) as cnt from ai_images
                 where user_id = %s
                   and status <> 'failed'
                   and created_at > now() - interval '24 hours'
                """,
                (int(user_id),),
            ).fetchone()
        count_24h: int = int(row["cnt"]) if row else 0
    except Exception as _quota_exc:
        # 配额查询失败时宽松放行（避免 DB 故障导致所有生图被拦截）
        log.warning("[image_jobs] quota check failed (allowing): %s", _quota_exc)
        count_24h = 0

    if count_24h >= daily_cap:
        log.warning(
            "[image_jobs] quota_exceeded user=%s count_24h=%d cap=%d",
            user_id, count_24h, daily_cap,
        )
        return {"error": "quota_exceeded", "status": "failed", "image_id": None}

    # model/api_id 解析：未传则复用偏好回退
    _api_id = api_id
    _model = model
    if not _api_id or not _model:
        try:
            from core.llm_backend import resolve_preferred_api, resolve_preferred_model
            if not _api_id:
                _api_id = resolve_preferred_api(user_id, pref_key="image_gen.api_id") or api_id
            if not _model:
                _model = resolve_preferred_model(user_id, pref_key="image_gen.model_real_name") or model
        except Exception as _pref_exc:
            log.debug("[image_jobs] pref resolve skipped: %s", _pref_exc)

    # 1. 建 ai_images 行（含 save_id）
    image_id = create_image_record(
        user_id=int(user_id),
        kind=kind,
        prompt=prompt,
        api_id=_api_id,
        model=_model,
        params=extra or {},
        save_id=save_id or None,
        message_index=message_index,
    )

    # 2. 入 chat_postproc_tasks
    payload: dict[str, Any] = {
        "image_id": image_id,
        "user_id": int(user_id),
        "prompt": prompt,
        "kind": kind,
        "api_id": _api_id,
        "model": _model,
        "origin": origin,
        "extra": extra or {},
        "save_id": save_id or None,
    }
    if attach is not None:
        payload["attach"] = attach

    with connect() as db:
        db.execute(_INSERT_SQL, {
            "user_id": int(user_id),
            "save_id": _IMAGE_SAVE_PLACEHOLDER,
            "payload": json.dumps(payload, ensure_ascii=False),
        })
        try:
            db.execute("SELECT pg_notify('chat_postproc_new', %s)", (str(user_id),))
        except Exception as _notify_exc:
            log.warning("[image_jobs] NOTIFY failed (worker will poll): %s", _notify_exc)

    log.info("[image_jobs] enqueued image_id=%s user=%s kind=%s origin=%s save_id=%s",
             image_id, user_id, kind, origin, save_id)
    return {"image_id": image_id, "status": "pending"}


def wait_for_image(image_id: int, *, timeout_s: float = 90.0, poll_s: float = 1.5) -> dict[str, Any]:
    """阻塞轮询 ai_images 直到终态(done/failed/cancelled)或超时,返回 {status,url,error}。

    闭环用(用户:生图后 LLM 不知道好没好):generate_image 在 LLM 自主路径上【确定性】等真实
    结果,把成功/失败回灌进 agentic 工具循环,而非返回「已入队」回执。后处理在独立进程,故用
    DB 轮询(跨进程可靠,不依赖 in-process 队列)。轮询跑在 GM 工作线程(asyncio.to_thread 桥接),
    time.sleep 不阻塞事件循环、SSE 照常存活。超时返回当前(pending/generating)状态,调用方优雅收尾。
    """
    import time

    from platform_app.db import connect
    deadline = time.monotonic() + timeout_s
    last: dict[str, Any] = {"status": "pending", "url": "", "error": ""}
    while True:
        try:
            with connect() as db:
                row = db.execute(
                    "select status, url, error from ai_images where id = %s", (int(image_id),)
                ).fetchone()
            if row:
                last = {
                    "status": row.get("status") or "pending",
                    "url": row.get("url") or "",
                    "error": row.get("error") or "",
                }
                if last["status"] in ("done", "failed", "cancelled"):
                    return last
        except Exception as exc:
            log.debug("[image_jobs] wait_for_image poll error: %s", exc)
        if time.monotonic() >= deadline:
            return last
        time.sleep(poll_s)


# ── Worker handler ───────────────────────────────────────────────────────

async def handle_image_gen(payload: dict[str, Any]) -> None:
    """postproc_worker 调用的 handler，在独立进程内跑。

    步骤：
    1. update_image_record(id, 'generating')
    2. resolve_api_key(user_id, api_id) — 缺 key 标 failed
    3. generate_image_bytes(prompt, params, api_id, model, api_key, base_url) — Agent B
    4. store_image(bytes) — Agent A
    5. update_image_record(id, 'done', url=...)
    6. SSE emit image_ready
    7. attach 写回目标（如有）
    """
    from platform_app.api.images import store_image, update_image_record

    image_id: int = int(payload.get("image_id") or 0)
    user_id: int = int(payload.get("user_id") or 0)
    prompt: str = str(payload.get("prompt") or "")
    kind: str = str(payload.get("kind") or "chat")
    api_id: str | None = payload.get("api_id") or None
    model: str | None = payload.get("model") or None
    extra: dict[str, Any] = payload.get("extra") or {}
    attach: dict[str, Any] | None = payload.get("attach") or None

    if not image_id or not user_id:
        log.warning("[image_jobs] handle_image_gen: missing image_id or user_id in payload")
        return

    # 1. mark generating
    try:
        update_image_record(image_id, "generating")
    except Exception as exc:
        log.warning("[image_jobs] update generating failed image_id=%s: %s", image_id, exc)

    # 2. resolve key
    api_key: str = ""
    base_url: str = ""
    if api_id:
        try:
            from platform_app.user_credentials import resolve_api_key
            cred = resolve_api_key(user_id, api_id)
            api_key = cred.get("key") or ""
            base_url = cred.get("base_url_override") or ""
        except Exception as exc:
            log.warning("[image_jobs] resolve_api_key failed image_id=%s: %s", image_id, exc)

    from model_aliases import normalize_api_id as _norm_api
    _is_vertex = _norm_api(api_id) == "vertex_ai"
    if not api_key and not _is_vertex:
        # vertex_ai 走平台/用户 Service Account(core.vertex_sa),无 BYOK key 字符串,放行
        _fail(image_id, "credentials_required")
        return

    # 3. generate
    try:
        from agents.image_gen.dispatch import generate_image_bytes  # type: ignore[import]
        size: str | None = extra.get("size") or None
        params: dict[str, Any] = {k: v for k, v in extra.items() if k != "ref"}
        if size:
            params["size"] = size

        raw_results = await asyncio.to_thread(
            generate_image_bytes,
            prompt=prompt,
            params=params,
            api_id=api_id,
            model=model,
            api_key=api_key,
            base_url=base_url,
            user_id=user_id,
        )
    except Exception as exc:
        log.exception("[image_jobs] generate_image_bytes failed image_id=%s", image_id)
        _fail(image_id, f"generation_error: {exc}")
        return

    if not raw_results:
        _fail(image_id, "generation_error: empty result")
        return

    # 4. store
    try:
        url = store_image(raw_results[0], user_id=user_id, kind=kind)
    except Exception as exc:
        log.exception("[image_jobs] store_image failed image_id=%s", image_id)
        _fail(image_id, f"store_error: {exc}")
        return

    # 5. update done —— 但若生成期间被用户取消,则丢弃结果(不覆盖 cancelled→done、不写回附着)
    try:
        from platform_app.db import connect as _connect
        with _connect() as _cdb:
            _cur = _cdb.execute("select status from ai_images where id = %s", (image_id,)).fetchone()
        if _cur and str(_cur.get("status") or "") == "cancelled":
            log.info("[image_jobs] image_id=%s 生图完成前已被用户取消,丢弃结果", image_id)
            return
    except Exception as _cexc:
        log.warning("[image_jobs] cancel-check DB error image_id=%s, 跳过写回以防覆盖取消: %s", image_id, _cexc)
        return
    try:
        update_image_record(image_id, "done", url=url)
    except Exception as exc:
        log.warning("[image_jobs] update done failed image_id=%s: %s", image_id, exc)

    # 5b. 登记 user_assets（失败只 log，不影响生图结果）
    try:
        from platform_app.assets_registry import register_asset  # lazy import
        # storage_key = "ai_images/{filename}"，从 url 末段解析文件名
        _img_filename = url.rstrip("/").rsplit("/", 1)[-1]
        _storage_key = "ai_images/" + _img_filename
        # ref_kind / ref_id 根据 attach 目标决定
        _ref_kind: str | None = None
        _ref_id: int | None = None
        if attach:
            _atype = str(attach.get("type") or "")
            if _atype == "card_avatar":
                _ref_kind = "card"
                _ref_id = int(attach.get("id") or attach.get("card_id") or 0) or None
            elif _atype == "script_cover":
                _ref_kind = "script"
                _ref_id = int(attach.get("id") or attach.get("script_id") or 0) or None
            elif _atype == "persona_image":
                _ref_kind = "card"
                _ref_id = int(attach.get("id") or attach.get("card_id") or 0) or None
            elif _atype == "user_avatar":
                _ref_kind = "user"
                _ref_id = int(user_id)
        register_asset(
            user_id=int(user_id),
            kind="ai_image",
            storage_key=_storage_key,
            url=url,
            source="image_gen",
            ref_kind=_ref_kind,
            ref_id=_ref_id,
            mime="image/png",
            meta={"prompt": prompt, "model": model or ""},
        )
    except Exception as _reg_exc:
        log.warning("[image_jobs] register_asset failed image_id=%s: %s", image_id, _reg_exc)

    # 6. SSE push
    _notify_image_ready(user_id=user_id, image_id=image_id, url=url, kind=kind)
    log.info("[image_jobs] done image_id=%s user=%s url=%s", image_id, user_id, url)

    # 7. attach 写回目标（如有）。写回失败不再静默:图片本身已生成并入图库,但用户
    # 的原始诉求(设为头像/人设图)没有兑现——只 log 会表现为「提示成功、刷新后头像
    # 悄悄回旧图」的不可定位故障(横扫记债项)。翻转 ai_images 为 failed 带原因,
    # 阻塞轮询端(wait_for_image)与任务浮窗都能如实呈现。
    if attach:
        # 把 prompt 注入 attach，供 persona_image 分支写入 prompt_snapshot
        attach_with_prompt = {**attach, "prompt_snapshot": attach.get("prompt_snapshot") or prompt}
        if not _attach_image_to_target(user_id=user_id, url=url, attach=attach_with_prompt):
            _fail(image_id, "attach_failed: 生成成功但目标写回失败(无权限或目标不存在);图片已存入图库")


def _attach_image_to_target(
    *,
    user_id: int,
    url: str,
    attach: dict[str, Any],
) -> bool:
    """生图完成后把 url 写回目标表，带 ownership 校验。

    attach 结构：
      user_avatar  — {"type": "user_avatar"}
      card_avatar  — {"type": "card_avatar",  "card_id": int}
      script_cover — {"type": "script_cover", "script_id": int}

    返回写回是否成功;不抛异常。False 由调用方翻转 ai_images 为 failed 呈现给用户
    (此前只 log = 「提示成功但头像悄悄没变」的不可定位故障)。
    """
    from platform_app.db import connect

    attach_type: str = str(attach.get("type") or "")
    try:
        with connect() as db:
            if attach_type == "user_avatar":
                result = db.execute(
                    "update users set avatar_url = %s where id = %s",
                    (url, int(user_id)),
                )
                log.info(
                    "[image_jobs] attach user_avatar user=%s url=%s", user_id, url
                )
                return result.rowcount > 0

            elif attach_type == "card_avatar":
                card_id = int(attach.get("id") or attach.get("card_id") or 0)
                if not card_id:
                    log.warning("[image_jobs] attach card_avatar missing card_id user=%s", user_id)
                    return False
                script_id = int(attach.get("script_id") or 0)
                if script_id:
                    # NPC 卡(user_id=NULL,挂 script_id):owner 走 scripts.owner_id
                    from platform_app.perms import script_owned
                    if not script_owned(db, script_id, int(user_id)):
                        log.warning("[image_jobs] attach card_avatar(npc) script owner failed script_id=%s user=%s", script_id, user_id)
                        return False
                    result = db.execute(
                        "update character_cards set avatar_path = %s where id = %s and script_id = %s",
                        (url, card_id, script_id),
                    )
                else:
                    result = db.execute(
                        """
                        update character_cards
                           set avatar_path = %s
                         where id = %s and user_id = %s
                        """,
                        (url, card_id, int(user_id)),
                    )
                if result.rowcount == 0:
                    log.warning(
                        "[image_jobs] attach card_avatar ownership failed card_id=%s user=%s",
                        card_id, user_id,
                    )
                    return False
                log.info(
                    "[image_jobs] attach card_avatar card_id=%s user=%s url=%s",
                    card_id, user_id, url,
                )
                return True

            elif attach_type == "script_cover":
                script_id = int(attach.get("id") or attach.get("script_id") or 0)
                if not script_id:
                    log.warning("[image_jobs] attach script_cover missing script_id user=%s", user_id)
                    return False
                result = db.execute(
                    """
                    update scripts
                       set cover_image_url = %s
                     where id = %s and owner_id = %s
                    """,
                    (url, script_id, int(user_id)),
                )
                if result.rowcount == 0:
                    log.warning(
                        "[image_jobs] attach script_cover ownership failed script_id=%s user=%s",
                        script_id, user_id,
                    )
                    return False
                log.info(
                    "[image_jobs] attach script_cover script_id=%s user=%s url=%s",
                    script_id, user_id, url,
                )
                return True

            elif attach_type == "persona_image":
                # Phase 4：人设图历史写入
                # attach = {"type":"persona_image","id":card_id,"persona_hash":str,"source":str}
                card_id = int(attach.get("id") or attach.get("card_id") or 0)
                if not card_id:
                    log.warning("[image_jobs] attach persona_image missing card_id user=%s", user_id)
                    return False
                persona_hash: str = str(attach.get("persona_hash") or "")
                source: str = str(attach.get("source") or "manual")
                # payload prompt_snapshot
                prompt_snapshot: str = ""  # resolved from outer scope via closure — not available here;
                # we log what we have; the outer handle_image_gen wraps us with no locals.
                # Callers that need prompt_snapshot should pass it in attach.
                prompt_snapshot = str(attach.get("prompt_snapshot") or "")

                # ownership 校验。card_type 显式限定 pc/persona:人设图是用户卡专属能力
                # (与 generate-persona-image 端点同谓词);此前只靠 NPC 卡 user_id 恰为
                # NULL 的巧合挡门,语义模糊且功能扩展时会复现 403 家族(横扫记债项)。
                owned = db.execute(
                    "select row_version from character_cards where id = %s and user_id = %s"
                    " and card_type in ('pc', 'persona')",
                    (card_id, int(user_id)),
                ).fetchone()
                if not owned:
                    log.warning(
                        "[image_jobs] attach persona_image ownership failed card_id=%s user=%s",
                        card_id, user_id,
                    )
                    return False
                card_row_version: int = int(owned.get("row_version") or 1)

                # ① 翻转 is_current：该卡所有历史行置 false
                db.execute(
                    "update card_persona_images set is_current = false where card_id = %s",
                    (card_id,),
                )
                # ② 插入新历史行（is_current=true）
                db.execute(
                    """
                    insert into card_persona_images
                        (card_id, image_url, persona_hash, card_row_version,
                         source, status, is_current, prompt_snapshot)
                    values (%s, %s, %s, %s, %s, 'done', true, %s)
                    """,
                    (card_id, url, persona_hash, card_row_version, source, prompt_snapshot),
                )
                # ③ 更新角色卡头像
                result = db.execute(
                    "update character_cards set avatar_path = %s where id = %s and user_id = %s",
                    (url, card_id, int(user_id)),
                )
                if result.rowcount == 0:
                    log.warning(
                        "[image_jobs] attach persona_image avatar_path update 0 rows card_id=%s user=%s",
                        card_id, user_id,
                    )
                    return False
                log.info(
                    "[image_jobs] attach persona_image card_id=%s user=%s url=%s",
                    card_id, user_id, url,
                )
                return True

            else:
                log.warning(
                    "[image_jobs] _attach_image_to_target unknown type=%s user=%s",
                    attach_type, user_id,
                )
                return False
    except Exception as exc:
        log.warning(
            "[image_jobs] _attach_image_to_target failed type=%s user=%s: %s",
            attach_type, user_id, exc,
        )
        return False


# ── Phase 4 helpers ─────────────────────────────────────────────────────────

def list_persona_images(user_id: int, card_id: int) -> list[dict[str, Any]]:
    """返回指定卡的人设图历史，按 created_at 倒序。owner 校验。

    返回字段：id, image_url, persona_hash, card_row_version, source, is_current, created_at。
    """
    from platform_app.db import connect

    with connect() as db:
        # card_type 显式限定 pc/persona(人设图家族统一谓词,与 generate-persona-image 一致)
        owned = db.execute(
            "select 1 from character_cards where id = %s and user_id = %s"
            " and card_type in ('pc', 'persona')",
            (int(card_id), user_id),
        ).fetchone()
        if not owned:
            raise ValueError("card 不存在或无权访问")
        rows = db.execute(
            """
            select id, image_url, persona_hash, card_row_version,
                   source, is_current, created_at
              from card_persona_images
             where card_id = %s
             order by created_at desc
            """,
            (int(card_id),),
        ).fetchall()
    return [
        {
            "id": int(r["id"]),
            "image_url": r["image_url"],
            "persona_hash": r["persona_hash"],
            "card_row_version": int(r["card_row_version"]),
            "source": r["source"],
            "is_current": bool(r["is_current"]),
            "created_at": str(r["created_at"]),
        }
        for r in rows
    ]


def set_current_persona_image(user_id: int, card_id: int, image_id: int) -> dict[str, Any]:
    """将指定历史人设图设为当前（is_current=true），更新卡头像 avatar_path。owner 校验。

    步骤：
    1. 校验 image_id 归属该卡且卡归属 user。
    2. 该卡所有历史行 is_current → false。
    3. 指定 image_id is_current → true。
    4. character_cards.avatar_path 设为该图 url。
    """
    from platform_app.db import connect

    with connect() as db:
        # 校验 card 归属 user(card_type 限定同 list_persona_images)
        card_row = db.execute(
            "select id from character_cards where id = %s and user_id = %s"
            " and card_type in ('pc', 'persona')",
            (int(card_id), user_id),
        ).fetchone()
        if not card_row:
            raise ValueError("card 不存在或无权访问")
        # 校验 image 归属该卡
        img_row = db.execute(
            "select image_url from card_persona_images where id = %s and card_id = %s",
            (int(image_id), int(card_id)),
        ).fetchone()
        if not img_row:
            raise ValueError("人设图不存在或不属于该卡")
        target_url: str = str(img_row["image_url"])

        # 翻转 is_current
        db.execute(
            "update card_persona_images set is_current = false where card_id = %s",
            (int(card_id),),
        )
        db.execute(
            "update card_persona_images set is_current = true where id = %s",
            (int(image_id),),
        )
        # 同步 avatar_path
        db.execute(
            "update character_cards set avatar_path = %s where id = %s and user_id = %s",
            (target_url, int(card_id), user_id),
        )
    return {"ok": True, "card_id": int(card_id), "image_id": int(image_id), "image_url": target_url}


def _fail(image_id: int, reason: str) -> None:
    """标记 ai_images 为 failed，记录 error。"""
    try:
        from platform_app.api.images import update_image_record
        update_image_record(image_id, "failed", error=reason)
    except Exception as exc:
        log.warning("[image_jobs] _fail update_record failed image_id=%s: %s", image_id, exc)
    log.warning("[image_jobs] image_id=%s failed reason=%s", image_id, reason)


def _notify_image_ready(
    *,
    user_id: int,
    image_id: int,
    url: str,
    kind: str,
) -> None:
    """经 SSE 事件总线发 image_ready{image_id, url, kind}。

    worker 是独立进程，没有 FastAPI event-loop 的 SSE 订阅者 —— _local_emit 会找不到
    任何队列，但 Redis 广播路径会把事件跨进程推给主 FastAPI worker 的订阅者。
    无 Redis 时事件静默丢失（前端可靠性退化到轮询，不影响生图正确性）。
    """
    try:
        from state_event_bus import emit as _emit
        _emit(user_id, "image", "ready", {
            "image_id": image_id,
            "url": url,
            "kind": kind,
        })
    except Exception as exc:
        log.debug("[image_jobs] SSE emit skipped: %s", exc)
