from __future__ import annotations

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


def redacted_url(url: str) -> str:
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    if "@" not in rest:
        return url
    return f"{scheme}://***@{rest.split('@', 1)[1]}"


def expose(row: dict | None) -> dict | None:
    if row is None:
        return None
    data = dict(row)
    if data.get("public_id") is not None:
        data["uid"] = str(data["public_id"])
    return data


def limit_value(value: int | str | None, default: int = DEFAULT_LIMIT, maximum: int = MAX_LIMIT) -> int:
    try:
        parsed = int(value) if value is not None else default
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, maximum))


def cursor_id(value: str | int | None) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def page_payload(rows: list[dict], limit: int) -> dict:
    has_more = len(rows) > limit
    visible = rows[:limit]
    next_cursor = str(visible[-1]["id"]) if has_more and visible else None
    return {
        "items": [expose(row) for row in visible],
        "page": {
            "limit": limit,
            "next_cursor": next_cursor,
            "has_more": has_more,
        },
    }
