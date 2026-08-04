"""platform_app.policy_notice — DOC-02 / AUP-03 政策变更通知管理。

数据存于 app_config 表（不新增 DB 表）:
  key "policy_versions"       -> dict[slug, version_str]
  key "policy_pending_notices" -> list[NoticeRecord]

NoticeRecord schema:
  id             str  (uuid4)
  slug           str
  new_version    str
  summary        str
  effective_at   str  (ISO-8601 UTC)
  created_at     str
  dispatched_at  str | null
  activated_at   str | null
  recipients_sent  int | null
  recipients_total int | null

批量邮件: 每批 100 封,发完批次后 sleep 0.6s (Resend 100/min 限速)。
RESEND_API_KEY 未配置时降级写 WARNING 日志,不抛出异常(cron 不中断)。
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from psycopg.types.json import Jsonb

logger = logging.getLogger(__name__)

POLICY_SLUGS = [
    "privacy-policy",
    "terms-of-service",
    "acceptable-use-policy",
    "cookie-policy",
    "dmca-policy",
    "adult-content-disclaimer",
]

NOTICE_LEAD_TIME_DAYS = 30

# Resend 批次大小与批次间间隔
_BATCH_SIZE = 100
_BATCH_SLEEP_S = 0.65  # 100/min ≈ 1.67/s; 0.65s 留余量

_APP_CONFIG_VERSIONS_KEY = "policy_versions"
_APP_CONFIG_NOTICES_KEY = "policy_pending_notices"

LANDING_BASE = "https://play.stellatrix.icu/legal"

# 各 slug 的人类可读名称（双语）
_POLICY_NAMES: dict[str, dict[str, str]] = {
    "privacy-policy":         {"zh-CN": "隐私政策",           "en": "Privacy Policy"},
    "terms-of-service":       {"zh-CN": "服务条款",           "en": "Terms of Service"},
    "acceptable-use-policy":  {"zh-CN": "可接受使用政策",     "en": "Acceptable Use Policy"},
    "cookie-policy":          {"zh-CN": "Cookie 政策",        "en": "Cookie Policy"},
    "dmca-policy":            {"zh-CN": "DMCA 版权政策",      "en": "DMCA Policy"},
    "adult-content-disclaimer":{"zh-CN": "成人内容免责声明",   "en": "Adult Content Disclaimer"},
}


# ─────────────────────────── app_config helpers ──────────────────────────────

def _get_config(db, key: str) -> dict | list:
    row = db.execute(
        "select value from app_config where key = %s", (key,)
    ).fetchone()
    if row and row["value"] is not None:
        v = row["value"]
        return v if isinstance(v, (dict, list)) else {}
    return {} if key == _APP_CONFIG_VERSIONS_KEY else []


def _set_config(db, key: str, value: dict | list) -> None:
    db.execute(
        """insert into app_config(key, value) values(%s, %s)
           on conflict(key) do update set value = excluded.value, updated_at = now()""",
        (key, Jsonb(value)),
    )


# ─────────────────────────── public API ──────────────────────────────────────

def get_current_version(db, slug: str) -> str | None:
    """从 app_config.policy_versions 读当前版本号。"""
    versions: dict = _get_config(db, _APP_CONFIG_VERSIONS_KEY)  # type: ignore[assignment]
    return versions.get(slug)


def schedule_policy_change(
    db,
    slug: str,
    new_version: str,
    summary: str,
    effective_at: datetime | None = None,
) -> dict:
    """admin 创建待发通知。effective_at 默认 now() + 30d。

    Returns: notice record dict (含 id).
    """
    if slug not in POLICY_SLUGS:
        raise ValueError(f"未知 slug: {slug!r}")

    if effective_at is None:
        effective_at = datetime.now(UTC) + timedelta(days=NOTICE_LEAD_TIME_DAYS)

    notice_id = str(uuid.uuid4())
    now_str = datetime.now(UTC).isoformat()
    record = {
        "id": notice_id,
        "slug": slug,
        "new_version": new_version,
        "summary": summary,
        "effective_at": effective_at.isoformat(),
        "created_at": now_str,
        "dispatched_at": None,
        "activated_at": None,
        "recipients_sent": None,
        "recipients_total": None,
    }

    notices: list = _get_config(db, _APP_CONFIG_NOTICES_KEY)  # type: ignore[assignment]
    notices.append(record)
    _set_config(db, _APP_CONFIG_NOTICES_KEY, notices)

    logger.info(
        "policy_notice: scheduled %s → %s, effective_at=%s, id=%s",
        slug, new_version, effective_at.isoformat(), notice_id,
    )
    return record


def list_pending_notices(db) -> list[dict]:
    """返回 activated_at IS NULL 的所有 notice(已发邮件 + 未发邮件均返回)。"""
    notices: list = _get_config(db, _APP_CONFIG_NOTICES_KEY)  # type: ignore[assignment]
    return [n for n in notices if not n.get("activated_at")]


def _find_notice(notices: list, notice_id: str) -> tuple[int, dict] | tuple[None, None]:
    for i, n in enumerate(notices):
        if n.get("id") == notice_id:
            return i, n
    return None, None


def dispatch_notice(db, notice_id: str) -> dict:
    """向所有 email_verified=true 的用户发政策变更邮件。

    降级: RESEND_API_KEY 未配置时写 WARNING 日志,记录 recipients_total,
    dispatched_at 设置为 now 以防止重复触发。

    Returns: 更新后的 notice record。
    """
    import platform_app.email as _email_mod

    resend_api_key = _email_mod.RESEND_API_KEY
    resend_from = _email_mod.RESEND_FROM

    notices: list = _get_config(db, _APP_CONFIG_NOTICES_KEY)  # type: ignore[assignment]
    idx, notice = _find_notice(notices, notice_id)
    if notice is None:
        raise ValueError(f"notice not found: {notice_id!r}")

    slug = notice["slug"]
    new_version = notice["new_version"]
    summary = notice["summary"]
    effective_at_str = notice["effective_at"]

    # 拉收件人列表: email_verified=true 用户,取 email + 语言偏好
    rows = db.execute(
        """
        select u.email,
               coalesce(pe.default_language, 'zh-CN') as lang
        from   users u
        left   join profile_extras pe on pe.user_id = u.id
        where  u.email_verified = true
          and  u.deactivated_at is null
        """
    ).fetchall()

    recipients = [(r["email"], r["lang"]) for r in rows]
    total = len(recipients)

    if not resend_api_key:
        logger.warning(
            "policy_notice: RESEND_API_KEY not set — skipping email dispatch "
            "for notice %s (would send to %d recipients)",
            notice_id, total,
        )
        notices[idx]["dispatched_at"] = datetime.now(UTC).isoformat()
        notices[idx]["recipients_total"] = total
        notices[idx]["recipients_sent"] = 0
        _set_config(db, _APP_CONFIG_NOTICES_KEY, notices)
        return notices[idx]

    sent = 0
    for batch_start in range(0, total, _BATCH_SIZE):
        batch = recipients[batch_start: batch_start + _BATCH_SIZE]
        for addr, lang in batch:
            is_zh = lang.lower().startswith("zh")
            subject, body, html = _build_email_payload(slug, new_version, summary, effective_at_str, is_zh)
            try:
                resp = httpx.post(
                    "https://api.resend.com/emails",
                    headers={
                        "Authorization": f"Bearer {resend_api_key}",
                        "Content-Type": "application/json",
                    },
                    json={
                        "from": resend_from,
                        "to": [addr],
                        "subject": subject,
                        "text": body,
                        "html": html,
                    },
                    timeout=10,
                )
                if resp.status_code >= 400:
                    logger.warning(
                        "policy_notice: resend error %s for %s: %s",
                        resp.status_code, addr, resp.text[:200],
                    )
                else:
                    sent += 1
            except Exception:
                logger.exception("policy_notice: failed to send to %s", addr)

        if batch_start + _BATCH_SIZE < total:
            time.sleep(_BATCH_SLEEP_S)

    notices[idx]["dispatched_at"] = datetime.now(UTC).isoformat()
    notices[idx]["recipients_total"] = total
    notices[idx]["recipients_sent"] = sent
    _set_config(db, _APP_CONFIG_NOTICES_KEY, notices)

    logger.info(
        "policy_notice: dispatched notice %s — sent %d/%d", notice_id, sent, total
    )
    return notices[idx]


def activate_notice(db, notice_id: str) -> dict:
    """effective_at 已到时调用:把 policy_versions[slug] 更新为 new_version,标 activated_at。

    Returns: 更新后的 notice record。
    """
    notices: list = _get_config(db, _APP_CONFIG_NOTICES_KEY)  # type: ignore[assignment]
    idx, notice = _find_notice(notices, notice_id)
    if notice is None:
        raise ValueError(f"notice not found: {notice_id!r}")

    slug = notice["slug"]
    new_version = notice["new_version"]

    versions: dict = _get_config(db, _APP_CONFIG_VERSIONS_KEY)  # type: ignore[assignment]
    versions[slug] = new_version
    _set_config(db, _APP_CONFIG_VERSIONS_KEY, versions)

    notices[idx]["activated_at"] = datetime.now(UTC).isoformat()
    _set_config(db, _APP_CONFIG_NOTICES_KEY, notices)

    logger.info(
        "policy_notice: activated notice %s — %s is now %s",
        notice_id, slug, new_version,
    )
    return notices[idx]


# ─────────────────────────── email builder ───────────────────────────────────

def _build_email(
    slug: str,
    new_version: str,
    summary: str,
    effective_at_str: str,
    is_zh: bool,
) -> tuple[str, str]:
    """返回 (subject, text body) 双语邮件。"""
    names = _POLICY_NAMES.get(slug, {"zh-CN": slug, "en": slug})
    name_zh = names["zh-CN"]
    name_en = names["en"]
    url = f"{LANDING_BASE}/{slug}.html"

    # 尽量把 ISO 时间转换为易读格式
    try:
        dt = datetime.fromisoformat(effective_at_str)
        effective_display = dt.strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        effective_display = effective_at_str

    if is_zh:
        subject = f"[Stellatrix RPG] {name_zh}变更通知（{new_version}）"
        body = (
            f"你好,\n\n"
            f"Stellatrix RPG 即将更新{name_zh}({new_version})。\n\n"
            f"变更摘要:\n{summary}\n\n"
            f"生效时间: {effective_display}\n\n"
            f"查看完整政策: {url}\n\n"
            f"如有疑问,请联系 support@stellatrix.icu\n\n"
            f"---\n"
            f"[EN] Stellatrix RPG is updating its {name_en} ({new_version}).\n"
            f"Summary: {summary}\n"
            f"Effective: {effective_display}\n"
            f"Full policy: {url}"
        )
    else:
        subject = f"[Stellatrix RPG] {name_en} Update ({new_version})"
        body = (
            f"Hello,\n\n"
            f"Stellatrix RPG is updating its {name_en} ({new_version}).\n\n"
            f"Summary of changes:\n{summary}\n\n"
            f"Effective date: {effective_display}\n\n"
            f"Read the full policy: {url}\n\n"
            f"For questions, contact support@stellatrix.icu\n\n"
            f"---\n"
            f"[ZH] Stellatrix RPG 即将更新{name_zh}({new_version})。\n"
            f"变更摘要: {summary}\n"
            f"生效时间: {effective_display}\n"
            f"完整政策: {url}"
        )

    return subject, body


def _build_email_payload(
    slug: str,
    new_version: str,
    summary: str,
    effective_at_str: str,
    is_zh: bool,
) -> tuple[str, str, str]:
    """返回 (subject, text body, html body)。"""
    from platform_app.email import build_policy_notice_email

    names = _POLICY_NAMES.get(slug, {"zh-CN": slug, "en": slug})
    name_zh = names["zh-CN"]
    name_en = names["en"]
    url = f"{LANDING_BASE}/{slug}.html"

    try:
        dt = datetime.fromisoformat(effective_at_str)
        effective_display = dt.strftime("%Y-%m-%d %H:%M UTC")
    except ValueError:
        effective_display = effective_at_str

    return build_policy_notice_email(
        name_zh=name_zh,
        name_en=name_en,
        new_version=new_version,
        summary=summary,
        effective_display=effective_display,
        url=url,
        is_zh=is_zh,
    )
