"""agents.image_gen.dashscope — DashScope (阿里百炼) image-generation adapter.

DashScope uses an asynchronous task pattern:

1. SUBMIT — POST /api/v1/services/aigc/image-generation/generation
   Headers:
     Authorization: Bearer {api_key}
     Content-Type: application/json
     X-DashScope-Async: enable
   Body (new messages API, recommended for wan2.x / wanx2.x models):
     {
       "model": "wan2.7-image-pro",
       "input": {
         "messages": [
           {"role": "user", "content": [{"text": "<prompt>"}]}
         ]
       },
       "parameters": {
         "size": "1024*1024",
         "n": 1,
         "watermark": false
       }
     }
   Response:
     {
       "output": {"task_id": "<id>", "task_status": "PENDING"},
       "request_id": "..."
     }

   Older wanx models (wanx-v1, wanx2.0-t2i-*) use the legacy endpoint and
   input.prompt format — we detect them by model name prefix and route
   appropriately.

2. POLL — GET /api/v1/tasks/{task_id}
   Header: Authorization: Bearer {api_key}
   Poll until output.task_status is a terminal value.

   Terminal statuses: SUCCEEDED, FAILED, CANCELED, UNKNOWN

3. EXTRACT image URL from completed task:
   - New API: output.choices[0].message.content[0].image
   - Legacy API: output.results[0].url  (or output.results[0].b64_json)

4. Download URL → bytes (or decode b64).

Polling: fixed 3-second sleep, max 60 polls (= 3 minutes total).
"""
from __future__ import annotations

import time
from typing import Any

import httpx

from agents.image_gen.base import ImageGenError, decode_b64, download_url
from core.outbound import safe_httpx_client

# ── Endpoint constants ──────────────────────────────────────────────────────

_BASE = "https://dashscope.aliyuncs.com"
_API_VERSION = "v1"

# New messages-based API (wan2.x, wanx2.x models with generation capability)
_SUBMIT_NEW = f"{_BASE}/api/{_API_VERSION}/services/aigc/image-generation/generation"

# Legacy prompt-based API (wanx-v1, older wanx models)
_SUBMIT_LEGACY = f"{_BASE}/api/{_API_VERSION}/services/aigc/text2image/image-synthesis"

# Task polling URL template
_POLL_TEMPLATE = f"{_BASE}/api/{_API_VERSION}/tasks/{{task_id}}"

# Polling config
_POLL_INTERVAL_SECONDS = 3
_POLL_MAX_ATTEMPTS = 60  # 3 min max
_TERMINAL_STATUSES = {"SUCCEEDED", "FAILED", "CANCELED", "UNKNOWN"}

# HTTP timeouts
_CONNECT_TIMEOUT = 10.0
_REQUEST_TIMEOUT = 30.0


def _is_legacy_model(model: str) -> bool:
    """Return True for older wanx models that use the legacy input.prompt API."""
    m = model.lower()
    # wanx-v1, wanx-v2, wanx2.0-*, wanx2.1-t2i-turbo / plus / lite
    return m.startswith("wanx-") or m.startswith("wanx2.")


def _build_new_body(model: str, prompt: str, params: dict) -> dict[str, Any]:
    """Build request body for the new messages-based API."""
    body: dict[str, Any] = {
        "model": model,
        "input": {
            "messages": [
                {
                    "role": "user",
                    "content": [{"text": prompt}],
                }
            ]
        },
    }
    # Forward supported parameters
    parameters: dict[str, Any] = {}
    for field in ("size", "n", "seed", "watermark", "style", "steps"):
        if field in params:
            parameters[field] = params[field]
    if "size" in parameters:
        # dashscope 尺寸格式是 W*H(星号);UI/工具传的 1024x1024 需转换。比例(含:)无法直接用,丢弃。
        _sz = str(parameters["size"]).replace("X", "x")
        parameters["size"] = _sz.replace("x", "*") if ":" not in _sz else None
        if not parameters["size"]:
            parameters.pop("size", None)
    if parameters:
        body["parameters"] = parameters
    return body


def _build_legacy_body(model: str, prompt: str, params: dict) -> dict[str, Any]:
    """Build request body for the legacy input.prompt API."""
    body: dict[str, Any] = {
        "model": model,
        "input": {"prompt": prompt},
    }
    if "negative_prompt" in params:
        body["input"]["negative_prompt"] = params["negative_prompt"]
    parameters: dict[str, Any] = {}
    for field in ("size", "n", "seed", "style", "steps"):
        if field in params:
            parameters[field] = params[field]
    if "size" in parameters:
        # dashscope 尺寸格式是 W*H(星号);UI/工具传的 1024x1024 需转换。比例(含:)无法直接用,丢弃。
        _sz = str(parameters["size"]).replace("X", "x")
        parameters["size"] = _sz.replace("x", "*") if ":" not in _sz else None
        if not parameters["size"]:
            parameters.pop("size", None)
    if parameters:
        body["parameters"] = parameters
    return body


def _submit(
    endpoint: str,
    body: dict[str, Any],
    api_key: str,
) -> str:
    """Submit image generation task.  Returns task_id string."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    try:
        with safe_httpx_client(timeout=_REQUEST_TIMEOUT) as client:
            resp = client.post(
                endpoint,
                json=body,
                headers=headers,
            )
    except httpx.TimeoutException as exc:
        raise ImageGenError(f"dashscope: submit timed out ({exc})") from exc
    except Exception as exc:
        raise ImageGenError(f"dashscope: submit network error ({exc})") from exc

    if resp.status_code not in (200, 202):
        try:
            detail = resp.json()
        except Exception:
            detail = resp.text[:500]
        raise ImageGenError(
            f"dashscope: submit HTTP {resp.status_code}: {detail}"
        )

    try:
        payload = resp.json()
    except Exception as exc:
        raise ImageGenError(f"dashscope: submit invalid JSON: {exc}") from exc

    task_id = (payload.get("output") or {}).get("task_id")
    if not task_id:
        raise ImageGenError(f"dashscope: no task_id in submit response: {payload}")

    return str(task_id)


def _poll(task_id: str, api_key: str) -> dict[str, Any]:
    """Poll task until terminal status.  Returns the full completed response dict."""
    poll_url = _POLL_TEMPLATE.format(task_id=task_id)
    headers = {"Authorization": f"Bearer {api_key}"}

    for attempt in range(_POLL_MAX_ATTEMPTS):
        # Fixed interval sleep (except on the very first check we wait too)
        if attempt > 0:
            time.sleep(_POLL_INTERVAL_SECONDS)

        try:
            with safe_httpx_client(timeout=_REQUEST_TIMEOUT) as client:
                resp = client.get(
                    poll_url,
                    headers=headers,
                )
        except httpx.TimeoutException as exc:
            raise ImageGenError(
                f"dashscope: poll attempt {attempt + 1} timed out ({exc})"
            ) from exc
        except Exception as exc:
            raise ImageGenError(
                f"dashscope: poll attempt {attempt + 1} network error ({exc})"
            ) from exc

        if resp.status_code != 200:
            try:
                detail = resp.json()
            except Exception:
                detail = resp.text[:500]
            raise ImageGenError(
                f"dashscope: poll HTTP {resp.status_code}: {detail}"
            )

        try:
            payload = resp.json()
        except Exception as exc:
            raise ImageGenError(
                f"dashscope: poll invalid JSON on attempt {attempt + 1}: {exc}"
            ) from exc

        output = payload.get("output") or {}
        status = str(output.get("task_status") or "")

        if status in _TERMINAL_STATUSES:
            if status == "SUCCEEDED":
                return payload
            raise ImageGenError(
                f"dashscope: task {task_id} ended with status={status}: {output}"
            )

        # PENDING / RUNNING — keep waiting
        # (subsequent waits are handled at the top of the loop)

    raise ImageGenError(
        f"dashscope: task {task_id} did not complete after "
        f"{_POLL_MAX_ATTEMPTS} polls ({_POLL_MAX_ATTEMPTS * _POLL_INTERVAL_SECONDS}s)"
    )


def _extract_urls(payload: dict[str, Any], is_legacy: bool) -> list[str]:
    """Extract image URLs (or b64 strings) from a SUCCEEDED poll response.

    New API shape:
        output.choices[0].message.content[0].image  (URL string)

    Legacy API shape:
        output.results[0].url                        (URL string)
        output.results[0].b64_json                   (optional b64)
    """
    output = payload.get("output") or {}

    if not is_legacy:
        # New messages-based API
        choices = output.get("choices") or []
        urls: list[str] = []
        for choice in choices:
            msg = (choice.get("message") or {})
            content = msg.get("content") or []
            for part in content:
                if isinstance(part, dict):
                    img = part.get("image")
                    if img:
                        urls.append(img)
        if urls:
            return urls
        # Some new-API models also put results at output.results
        # (fall through to legacy extraction as fallback)

    # Legacy API (and fallback for new API)
    results = output.get("results") or []
    legacy_urls: list[str] = []
    for item in results:
        if isinstance(item, dict):
            url = item.get("url")
            b64 = item.get("b64_json")
            if url:
                legacy_urls.append(url)
            elif b64:
                legacy_urls.append(f"__b64__{b64}")
    if legacy_urls:
        return legacy_urls

    raise ImageGenError(
        f"dashscope: cannot find image URLs in SUCCEEDED response: {payload}"
    )


def generate(
    prompt: str,
    params: dict,
    *,
    api_id: str,
    model: str,
    api_key: str,
    base_url: str | None = None,
) -> list[bytes]:
    """Async submit → poll → download for DashScope image generation.

    Args:
        prompt:    Text prompt for image generation.
        params:    Optional provider parameters forwarded to DashScope
                   (size, n, seed, watermark, style, negative_prompt, steps).
        api_id:    Canonical provider id ("dashscope") — informational only.
        model:     Model id string (e.g. "wan2.7-image-pro", "wanx2.1-t2i-turbo").
        api_key:   DashScope API key (Bearer token).
        base_url:  Unused for DashScope (endpoint is fixed); reserved for interface
                   compatibility.  Ignored.
    Returns:
        list[bytes] — one element per generated image.
    Raises:
        ImageGenError on any provider, network, or timeout failure.
    """
    legacy = _is_legacy_model(model)

    if legacy:
        endpoint = _SUBMIT_LEGACY
        body = _build_legacy_body(model, prompt, params)
    else:
        endpoint = _SUBMIT_NEW
        body = _build_new_body(model, prompt, params)

    task_id = _submit(endpoint, body, api_key)
    completed = _poll(task_id, api_key)
    raw_urls = _extract_urls(completed, is_legacy=legacy)

    result: list[bytes] = []
    for raw in raw_urls:
        if raw.startswith("__b64__"):
            result.append(decode_b64(raw[len("__b64__"):]))
        else:
            result.append(download_url(raw))

    if not result:
        raise ImageGenError(
            f"dashscope: task {task_id} SUCCEEDED but no image data extracted"
        )

    return result
