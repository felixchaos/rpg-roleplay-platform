from __future__ import annotations

from pathlib import Path
from typing import Any

from ..library import safe_filename
from ._base import (
    MAX_SCRIPT_UPLOAD_BYTES,
    MAX_UPLOAD_CHUNK_BYTES,
    UPLOAD_CHUNK_ROOT,
)


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
