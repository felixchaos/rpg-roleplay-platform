from __future__ import annotations

import base64
import binascii
import mimetypes
from pathlib import Path
from typing import Any

import fsspec

from .db import connect, init_db, limit_value

BASE = Path(__file__).resolve().parents[1]
LIBRARY_ROOT = BASE / "platform_data" / "library"
MAX_UPLOAD_BYTES = 64 * 1024 * 1024


def list_dir(user_id: int, rel_path: str = "", limit: int | str | None = None, cursor: str | None = None) -> dict[str, Any]:
    root = user_root(user_id)
    current = safe_path(root, rel_path)
    current.mkdir(parents=True, exist_ok=True)
    entries = []
    for item in sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        stat = item.stat()
        entries.append({
            "name": item.name,
            "path": str(item.relative_to(root)),
            "type": "directory" if item.is_dir() else "file",
            "size": stat.st_size,
            "mime": mimetypes.guess_type(item.name)[0] or "",
            "modified": int(stat.st_mtime),
        })
    if cursor:
        entries = [item for item in entries if item["path"] > cursor]
    page_limit = limit_value(limit)
    has_more = len(entries) > page_limit
    visible = entries[:page_limit]
    rel = str(current.relative_to(root)) if current != root else ""
    return {
        "engine": "fsspec-local",
        "path": rel,
        "entries": visible,
        "items": visible,
        "page": {
            "limit": page_limit,
            "next_cursor": visible[-1]["path"] if has_more and visible else None,
            "has_more": has_more,
        },
    }


def mkdir(user_id: int, rel_path: str) -> dict[str, Any]:
    root = user_root(user_id)
    target = safe_path(root, rel_path)
    fsspec.filesystem("file").makedirs(str(target), exist_ok=True)
    return list_dir(user_id, parent_rel(root, target))


def delete(user_id: int, rel_path: str) -> dict[str, Any]:
    root = user_root(user_id)
    target = safe_path(root, rel_path)
    if target == root:
        raise ValueError("不能删除库根目录")
    if not target.exists():
        raise FileNotFoundError(f"文件不存在: {rel_path}")
    fsspec.filesystem("file").rm(str(target), recursive=True)
    init_db()
    with connect() as db:
        db.execute("delete from assets where user_id = %s and rel_path = %s", (user_id, str(Path(rel_path))))
    return list_dir(user_id, parent_rel(root, target))


MAX_FILES_PER_REQUEST = 12


def upload(user_id: int, rel_dir: str, files: list[dict[str, Any]]) -> dict[str, Any]:
    root = user_root(user_id)
    target_dir = safe_path(root, rel_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    # 超量明确拒绝，不再静默截断
    if not isinstance(files, list) or not files:
        raise ValueError("files 必须是非空列表")
    if len(files) > MAX_FILES_PER_REQUEST:
        raise ValueError(f"单次最多上传 {MAX_FILES_PER_REQUEST} 个文件，本次提交 {len(files)}")
    fs = fsspec.filesystem("file")
    init_db()
    with connect() as db:
        for item in files:
            name = safe_filename(item.get("name") or "upload.bin")
            data = decode_upload(item)
            if len(data) > MAX_UPLOAD_BYTES:
                raise ValueError(f"文件过大：{name}")
            target = unique_path(target_dir / name)
            with fs.open(str(target), "wb") as f:
                f.write(data)
            mime = item.get("type") or mimetypes.guess_type(target.name)[0] or "application/octet-stream"
            db.execute(
                """
                insert into assets(user_id, name, rel_path, mime, kind, size)
                values (%s, %s, %s, %s, %s, %s)
                """,
                (user_id, target.name, str(target.relative_to(root)), mime, kind_for(mime, target.suffix), len(data)),
            )
    return list_dir(user_id, rel_dir)


def download_path(user_id: int, rel_path: str) -> Path:
    target = safe_path(user_root(user_id), rel_path)
    if not target.exists() or not target.is_file():
        raise ValueError("文件不存在")
    return target


def user_root(user_id: int) -> Path:
    root = LIBRARY_ROOT / f"user_{user_id}"
    root.mkdir(parents=True, exist_ok=True)
    return root


def safe_path(root: Path, rel_path: str) -> Path:
    root = root.resolve()
    target = (root / (rel_path or "")).resolve()
    if target != root and root not in target.parents:
        raise ValueError("非法路径")
    return target


def parent_rel(root: Path, target: Path) -> str:
    return str(target.parent.relative_to(root)) if target.parent != root else ""


def decode_upload(item: dict[str, Any]) -> bytes:
    encoded = str(item.get("base64") or item.get("content_base64") or item.get("contentBase64") or "")
    data_url = str(item.get("data_url") or item.get("dataUrl") or "")
    if "," in data_url:
        encoded = data_url.split(",", 1)[1]
    if not encoded:
        raise ValueError("上传内容为空")
    # 严格校验：validate=True 时遇到非法字符会抛 binascii.Error，
    # 避免畸形 base64（如 'aGVsbG8=%%%%')被静默截断后落盘成损坏文件。
    try:
        return base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("上传内容不是有效 base64") from exc


def safe_filename(name: str) -> str:
    # \u663e\u5f0f\u767d\u540d\u5355: ASCII \u5b57\u6bcd\u6570\u5b57\u3001\u5e38\u89c1\u5206\u9694\u7b26\u3001CJK \u6c49\u5b57; \u5176\u4f59\u66ff\u6362\u4e3a\u4e0b\u5212\u7ebf
    import re as _re
    stem = Path(name).name
    cleaned = _re.sub(r"[^A-Za-z0-9._\- \u4e00-\u9fff]", "_", stem)
    # \u62d2\u7edd\u4ee5 . \u5f00\u5934\u6216\u5168\u662f . \u7684\u6587\u4ef6\u540d\uff08\u9632\u6b62\u9690\u85cf\u6587\u4ef6/\u76f8\u5bf9\u8def\u5f84\u7a7f\u8d8a\uff09
    if not _re.search(r"[A-Za-z0-9\u4e00-\u9fff]", cleaned):
        cleaned = "untitled"
    return cleaned or "file.bin"


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    for index in range(2, 1000):
        candidate = path.with_name(f"{path.stem}-{index}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise ValueError("无法分配文件名")


def kind_for(mime: str, suffix: str) -> str:
    suffix = suffix.lower()
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if suffix in {".zip", ".rar", ".7z", ".tar", ".gz"}:
        return "archive"
    if suffix in {".md", ".txt", ".pdf", ".doc", ".docx", ".csv", ".json"}:
        return "document"
    return "file"
