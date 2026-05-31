"""platform_app.email — Resend API 邮件发送客户端。

用法:
    from platform_app.email import send_verification_email
    send_verification_email("user@example.com", "123456")

环境变量:
    RESEND_API_KEY   (必须)  Resend API 密钥
    RESEND_FROM      (可选)  发件人地址，默认 Stellatrix Labs <noreply@stellatrix.icu>

降级行为:
    RESEND_API_KEY 未配置时抛出 EmailSendError（不静默忽略）。
    调用方应在测试/开发环境捕获该异常并打印验证码到 log。
"""
from __future__ import annotations

import os

import httpx

RESEND_API_KEY: str = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM: str = os.environ.get(
    "RESEND_FROM", "Stellatrix Labs <noreply@stellatrix.icu>"
)


class EmailSendError(Exception):
    """邮件发送失败。"""


def send_verification_email(to: str, code: str, lang: str = "zh-CN") -> None:
    """向 `to` 发送验证码邮件。

    Args:
        to:   收件人邮箱（已规范化）
        code: 6 位验证码明文
        lang: 语言偏好，以 'zh' 开头则中文优先
    """
    if not RESEND_API_KEY:
        raise EmailSendError(
            "RESEND_API_KEY not configured — verification email cannot be sent"
        )

    is_zh = lang.lower().startswith("zh")
    subject = (
        "你的注册验证码 / Your verification code"
        if is_zh
        else "Your verification code — Stellatrix RPG"
    )
    body_zh = (
        f"你的 Stellatrix RPG 验证码是：{code}\n\n"
        "10 分钟内有效。\n"
        "如非你本人操作，请忽略此邮件。"
    )
    body_en = (
        f"Your Stellatrix RPG verification code: {code}\n\n"
        "Valid for 10 minutes. Ignore if you did not request this."
    )
    text = body_zh + "\n---\n" + body_en

    resp = httpx.post(
        "https://api.resend.com/emails",
        headers={
            "Authorization": f"Bearer {RESEND_API_KEY}",
            "Content-Type": "application/json",
        },
        json={"from": RESEND_FROM, "to": [to], "subject": subject, "text": text},
        timeout=10,
    )
    if resp.status_code >= 400:
        raise EmailSendError(f"Resend API {resp.status_code}: {resp.text[:300]}")
