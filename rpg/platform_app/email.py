"""platform_app.email — Resend API email client with branded HTML templates."""
from __future__ import annotations

import os
from html import escape

import httpx

RESEND_API_KEY: str = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM: str = os.environ.get(
    "RESEND_FROM", "Stellatrix Labs <noreply@stellatrix.icu>"
)


class EmailSendError(Exception):
    """Email delivery failed."""


def _public_base_url() -> str:
    return os.environ.get("PUBLIC_BASE_URL", "https://rpg-roleplay.stellatrix.icu").rstrip("/")


def _preheader(text: str) -> str:
    return (
        '<div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;'
        'mso-hide:all;font-size:1px;line-height:1px;">'
        f"{escape(text)}"
        "</div>"
    )


def _email_css() -> str:
    return """
    <style>
      :root { color-scheme: dark; supported-color-schemes: dark; }
      body, table, td, a { -webkit-text-size-adjust: 100%; -ms-text-size-adjust: 100%; }
      table, td { mso-table-lspace: 0pt; mso-table-rspace: 0pt; }
      img { -ms-interpolation-mode: bicubic; border: 0; outline: none; text-decoration: none; }
      body { margin: 0 !important; padding: 0 !important; width: 100% !important; background: #131211; }
      .sr-body {
        margin: 0; padding: 0; background: #131211; color: #ebe7df;
        font-family: "Noto Sans SC", "PingFang SC", "Microsoft YaHei", -apple-system, BlinkMacSystemFont, "Segoe UI", Arial, sans-serif;
      }
      .sr-shell { width: 100%; background: #131211; padding: 28px 12px; }
      .sr-card {
        width: 100%; max-width: 560px; background: #211f1d; border: 1px solid #36322d;
        border-radius: 8px; box-shadow: 0 18px 48px -18px rgba(0,0,0,.55), 0 2px 6px rgba(0,0,0,.25);
      }
      .sr-mark {
        width: 42px; height: 42px; border-radius: 8px; background: #282623;
        border: 1px solid #36322d; color: #c96442; text-align: center;
        font: 700 18px/42px "Noto Serif SC", Georgia, serif;
      }
      .sr-brand {
        color: #ebe7df; font-family: "Noto Serif SC", Georgia, serif;
        font-size: 25px; line-height: 1.2; letter-spacing: 0; font-weight: 600;
      }
      .sr-eyebrow {
        color: #c96442; font-size: 12px; line-height: 1.4; font-weight: 700;
        text-transform: uppercase; letter-spacing: .08em;
      }
      .sr-title {
        color: #ebe7df; font-family: "Noto Serif SC", Georgia, serif;
        font-size: 22px; line-height: 1.32; font-weight: 600; letter-spacing: 0;
      }
      .sr-text { color: #c8c2b7; font-size: 15px; line-height: 1.72; letter-spacing: 0; }
      .sr-muted { color: #968f85; font-size: 13px; line-height: 1.65; letter-spacing: 0; }
      .sr-panel { background: #1a1817; border: 1px solid #36322d; border-radius: 8px; }
      .sr-code {
        color: #ebe7df; font-family: "JetBrains Mono", SFMono-Regular, Menlo, Consolas, monospace;
        font-size: 34px; line-height: 1.2; font-weight: 600; letter-spacing: .32em; text-align: center;
      }
      .sr-button {
        display: inline-block; background: #c96442; color: #fff7f1 !important; border-radius: 6px;
        padding: 12px 18px; font-size: 14px; line-height: 1; font-weight: 700; text-decoration: none;
      }
      .sr-link { color: #d97955 !important; text-decoration: none; font-weight: 700; }
      .sr-rule { height: 1px; line-height: 1px; background: #36322d; }
      @media screen and (max-width: 600px) {
        .sr-shell { padding: 16px 8px !important; }
        .sr-card { border-radius: 8px !important; }
        .sr-pad { padding: 22px 18px !important; }
        .sr-brand { font-size: 22px !important; }
        .sr-title { font-size: 20px !important; }
        .sr-code { font-size: 30px !important; letter-spacing: .24em !important; }
      }
    </style>
    """


def _render_email(
    *,
    preheader: str,
    eyebrow: str,
    title: str,
    lead_html: str,
    main_html: str,
    secondary_html: str = "",
    footer_html: str = "",
) -> str:
    support_url = f"{_public_base_url()}/Login.html"
    return f"""<!doctype html>
<html>
<head>
  <meta http-equiv="Content-Type" content="text/html; charset=utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <meta name="color-scheme" content="dark">
  <meta name="supported-color-schemes" content="dark">
  {_email_css()}
</head>
<body class="sr-body" style="margin:0;padding:0;background:#131211;color:#ebe7df;">
{_preheader(preheader)}
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="sr-shell" style="width:100%;background:#131211;padding:28px 12px;">
  <tr>
    <td align="center">
      <table role="presentation" width="560" cellpadding="0" cellspacing="0" class="sr-card" style="width:100%;max-width:560px;background:#211f1d;border:1px solid #36322d;border-radius:8px;">
        <tr>
          <td class="sr-pad" style="padding:28px 28px 24px;">
            <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td width="54" valign="top">
                  <div class="sr-mark" style="width:42px;height:42px;border-radius:8px;background:#282623;border:1px solid #36322d;color:#c96442;text-align:center;font:700 18px/42px Georgia,serif;">R</div>
                </td>
                <td valign="top">
                  <div class="sr-brand" style="color:#ebe7df;font-family:Georgia,serif;font-size:25px;line-height:1.2;font-weight:600;">RPG Roleplay</div>
                  <div class="sr-muted" style="color:#968f85;font-size:13px;line-height:1.65;">Stellatrix Labs</div>
                </td>
              </tr>
            </table>

            <div style="height:26px;line-height:26px;">&nbsp;</div>
            <div class="sr-eyebrow" style="color:#c96442;font-size:12px;line-height:1.4;font-weight:700;text-transform:uppercase;letter-spacing:.08em;">{escape(eyebrow)}</div>
            <div style="height:7px;line-height:7px;">&nbsp;</div>
            <div class="sr-title" style="color:#ebe7df;font-family:Georgia,serif;font-size:22px;line-height:1.32;font-weight:600;">{escape(title)}</div>
            <div style="height:12px;line-height:12px;">&nbsp;</div>
            <div class="sr-text" style="color:#c8c2b7;font-size:15px;line-height:1.72;">{lead_html}</div>
            <div style="height:20px;line-height:20px;">&nbsp;</div>
            {main_html}
            {secondary_html}
            <div style="height:24px;line-height:24px;">&nbsp;</div>
            <div class="sr-rule" style="height:1px;line-height:1px;background:#36322d;">&nbsp;</div>
            <div style="height:14px;line-height:14px;">&nbsp;</div>
            <div class="sr-muted" style="color:#968f85;font-size:13px;line-height:1.65;">
              {footer_html or f'如果你没有请求此邮件，可以安全忽略。<br>If you did not request this email, you can safely ignore it.'}
              <br><a class="sr-link" style="color:#d97955;text-decoration:none;font-weight:700;" href="{support_url}">rpg-roleplay.stellatrix.icu</a>
            </div>
          </td>
        </tr>
      </table>
    </td>
  </tr>
</table>
</body>
</html>"""


def _send_resend(to: str, subject: str, text: str, html: str) -> None:
    if not RESEND_API_KEY:
        raise EmailSendError("RESEND_API_KEY not configured — email cannot be sent")

    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={
            "from": RESEND_FROM,
            "to": [to],
            "subject": subject,
            "text": text,
            "html": html,
        },
        timeout=10,
    )
    if resp.status_code >= 400:
        raise EmailSendError(f"Resend API {resp.status_code}: {resp.text[:300]}")


def build_verification_email(code: str, lang: str = "zh-CN") -> tuple[str, str, str]:
    is_zh = lang.lower().startswith("zh")
    subject = (
        "你的注册验证码 / Your verification code"
        if is_zh
        else "Your verification code — Stellatrix RPG"
    )
    text = (
        f"你的 Stellatrix RPG 验证码是：{code}\n\n"
        "10 分钟内有效。\n"
        "如非你本人操作，请忽略此邮件。\n\n"
        "---\n"
        f"Your Stellatrix RPG verification code: {code}\n\n"
        "Valid for 10 minutes. Ignore if you did not request this."
    )
    lead = (
        "请输入以下 6 位验证码完成注册。验证码会在 <strong style=\"color:#ebe7df;\">10 分钟</strong>后失效。"
        if is_zh
        else "Enter this 6-digit code to finish registration. It expires in <strong style=\"color:#ebe7df;\">10 minutes</strong>."
    )
    main = f"""
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="sr-panel" style="width:100%;background:#1a1817;border:1px solid #36322d;border-radius:8px;">
        <tr><td style="padding:22px 14px;">
          <div class="sr-code" style="color:#ebe7df;font-family:SFMono-Regular,Menlo,Consolas,monospace;font-size:34px;line-height:1.2;font-weight:600;letter-spacing:.32em;text-align:center;">{escape(code)}</div>
        </td></tr>
      </table>
    """
    secondary = """
      <div style="height:18px;line-height:18px;">&nbsp;</div>
      <div class="sr-muted" style="color:#968f85;font-size:13px;line-height:1.65;">
        为了保护账号安全，请不要把验证码转发给任何人。<br>
        For your security, never forward this code to anyone.
      </div>
    """
    html = _render_email(
        preheader=f"验证码 {code}，10 分钟内有效",
        eyebrow="Verification Code",
        title="你的注册验证码" if is_zh else "Your verification code",
        lead_html=lead,
        main_html=main,
        secondary_html=secondary,
    )
    return subject, text, html


def send_verification_email(to: str, code: str, lang: str = "zh-CN") -> None:
    """Send a registration verification code."""
    subject, text, html = build_verification_email(code, lang)
    _send_resend(to, subject, text, html)


def build_password_reset_email(to: str, token: str, lang: str = "zh-CN") -> tuple[str, str, str]:
    is_zh = lang.lower().startswith("zh")
    link = f"{_public_base_url()}/Login.html#reset?token={token}"
    subject = "重置你的密码 / Reset your password" if is_zh else "Reset your password — Stellatrix RPG"
    text = (
        f"点击以下链接重置你的 Stellatrix RPG 密码（30 分钟内有效）：\n{link}\n\n"
        "如果这不是你本人的操作，请忽略此邮件。你的密码不会被更改。\n\n"
        "---\n"
        f"Click the link below to reset your Stellatrix RPG password (valid for 30 minutes):\n{link}\n\n"
        "If you did not request this, ignore this email. Your password will not be changed."
    )
    lead = (
        "我们收到了重置密码请求。这个链接会在 <strong style=\"color:#ebe7df;\">30 分钟</strong>后失效。"
        if is_zh
        else "We received a password reset request. This link expires in <strong style=\"color:#ebe7df;\">30 minutes</strong>."
    )
    main = f"""
      <table role="presentation" cellpadding="0" cellspacing="0">
        <tr><td>
          <a class="sr-button" style="display:inline-block;background:#c96442;color:#fff7f1;border-radius:6px;padding:12px 18px;font-size:14px;line-height:1;font-weight:700;text-decoration:none;" href="{escape(link)}">
            {'重置密码' if is_zh else 'Reset password'}
          </a>
        </td></tr>
      </table>
      <div style="height:16px;line-height:16px;">&nbsp;</div>
      <div class="sr-muted" style="color:#968f85;font-size:13px;line-height:1.65;word-break:break-all;">
        {escape(link)}
      </div>
    """
    html = _render_email(
        preheader="Stellatrix RPG 密码重置链接" if is_zh else "Stellatrix RPG password reset link",
        eyebrow="Password Reset",
        title="重置你的密码" if is_zh else "Reset your password",
        lead_html=lead,
        main_html=main,
    )
    return subject, text, html


def send_password_reset_email(to: str, token: str, lang: str = "zh-CN") -> None:
    """Send a password reset link."""
    subject, text, html = build_password_reset_email(to, token, lang)
    _send_resend(to, subject, text, html)


def build_policy_notice_email(
    *,
    name_zh: str,
    name_en: str,
    new_version: str,
    summary: str,
    effective_display: str,
    url: str,
    is_zh: bool,
) -> tuple[str, str, str]:
    subject = (
        f"[Stellatrix RPG] {name_zh}变更通知（{new_version}）"
        if is_zh
        else f"[Stellatrix RPG] {name_en} Update ({new_version})"
    )
    if is_zh:
        text = (
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
        title = f"{name_zh}变更通知"
        lead = f"Stellatrix RPG 即将更新 <strong style=\"color:#ebe7df;\">{escape(name_zh)}</strong>。"
    else:
        text = (
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
        title = f"{name_en} Update"
        lead = f"Stellatrix RPG is updating its <strong style=\"color:#ebe7df;\">{escape(name_en)}</strong>."

    main = f"""
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="sr-panel" style="width:100%;background:#1a1817;border:1px solid #36322d;border-radius:8px;">
        <tr><td style="padding:18px;">
          <div class="sr-eyebrow" style="color:#c96442;font-size:12px;line-height:1.4;font-weight:700;text-transform:uppercase;letter-spacing:.08em;">Version {escape(new_version)}</div>
          <div style="height:8px;line-height:8px;">&nbsp;</div>
          <div class="sr-text" style="color:#c8c2b7;font-size:15px;line-height:1.72;">{escape(summary).replace(chr(10), '<br>')}</div>
          <div style="height:12px;line-height:12px;">&nbsp;</div>
          <div class="sr-muted" style="color:#968f85;font-size:13px;line-height:1.65;">Effective: {escape(effective_display)}</div>
        </td></tr>
      </table>
      <div style="height:18px;line-height:18px;">&nbsp;</div>
      <a class="sr-button" style="display:inline-block;background:#c96442;color:#fff7f1;border-radius:6px;padding:12px 18px;font-size:14px;line-height:1;font-weight:700;text-decoration:none;" href="{escape(url)}">
        {'查看完整政策' if is_zh else 'Read full policy'}
      </a>
    """
    html = _render_email(
        preheader=f"{title} · {new_version}",
        eyebrow="Policy Notice",
        title=title,
        lead_html=lead,
        main_html=main,
        footer_html="如有疑问，请联系 support@stellatrix.icu。<br>For questions, contact support@stellatrix.icu.",
    )
    return subject, text, html
