"""
crypto_utils.py — 用户级 API key 加密存储

设计：
- 主密钥 RPG_MASTER_KEY 来自环境变量（64 位 hex）
- 派生密钥：HKDF(RPG_MASTER_KEY, salt=user_id, info=api_id) → 32 字节
- 实际加密：AES-256-GCM，每条记录独立 12 字节 nonce
- 输出格式：nonce(12) || ciphertext || tag(16) → bytea
- 第一次使用时若无 RPG_MASTER_KEY，生成一个并打印警告（仅本地模式）

威胁模型：
- 防御：拖库 → 只拿到密文，没主密钥解不开
- 防御：API 误返回 raw key 字段（始终走 decrypt_for_user，调用方拿到的就是明文 key，但写代码时不要往响应里塞 raw_key 字段）
- 不防御：管理员能看 RPG_MASTER_KEY + DB → 当然能解。需要 KMS/HSM 才能进一步隔离
"""
from __future__ import annotations

import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from core.logging import get_logger

log = get_logger(__name__)

_MASTER_KEY_ENV = "RPG_MASTER_KEY"
# utils/crypto.py 在 rpg/utils/ 下，platform_data/ 在 rpg/ 下，需要上溯两级
_FALLBACK_KEY_FILE = Path(__file__).parent.parent / "platform_data" / "master.key"
_NONCE_LEN = 12


def _get_master_key() -> bytes:
    raw = os.environ.get(_MASTER_KEY_ENV, "").strip()
    if raw:
        try:
            key = bytes.fromhex(raw)
            if len(key) == 32:
                return key
            return _stretch_to_32(raw.encode("utf-8"))
        except ValueError:
            return _stretch_to_32(raw.encode("utf-8"))

    # 本地模式回退：从文件读已生成的 key
    if _FALLBACK_KEY_FILE.exists():
        try:
            return bytes.fromhex(_FALLBACK_KEY_FILE.read_text(encoding="utf-8").strip())
        except Exception:
            pass

    # 首次使用：生成新 key 并落盘（仅本地模式合理；服务器部署必须显式设置）
    _FALLBACK_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_bytes(32)
    _FALLBACK_KEY_FILE.write_text(key.hex(), encoding="utf-8")
    os.chmod(_FALLBACK_KEY_FILE, 0o600)
    log.warning(
        f"[CRYPTO] 未设置 {_MASTER_KEY_ENV} 环境变量，已生成本地主密钥 → {_FALLBACK_KEY_FILE}\n"
        f"         生产部署请设置 RPG_MASTER_KEY=<32 字节 hex>"
    )
    return key


def _stretch_to_32(seed: bytes) -> bytes:
    """如果主密钥不是 32 字节，用 HKDF 拉伸到 32 字节"""
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=b"rpg-master-stretch",
        info=b"v1",
    ).derive(seed)


def _derive_user_key(user_id: int, api_id: str) -> bytes:
    """从主密钥派生 (user_id, api_id) 专属密钥"""
    master = _get_master_key()
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=str(int(user_id)).encode("utf-8"),
        info=f"api:{api_id}".encode(),
    ).derive(master)


# ── 加密 / 解密 ──────────────────────────────────────────────────────
def encrypt_api_key(plaintext: str, user_id: int, api_id: str) -> bytes:
    """返回 nonce || ciphertext || tag 的 bytea，可直接 INSERT。"""
    if not plaintext:
        return b""
    key = _derive_user_key(user_id, api_id)
    aes = AESGCM(key)
    nonce = secrets.token_bytes(_NONCE_LEN)
    aad = f"user={user_id}&api={api_id}".encode()
    ct = aes.encrypt(nonce, plaintext.encode("utf-8"), aad)
    return nonce + ct  # ct 已含 16 字节 tag 在末尾


def decrypt_api_key(blob: bytes | memoryview | None, user_id: int, api_id: str) -> str:
    """解密；任何失败都返回空串（让调用方走 fallback，不抛异常给上层）"""
    if not blob:
        return ""
    raw = bytes(blob)
    if len(raw) < _NONCE_LEN + 16:
        return ""
    nonce = raw[:_NONCE_LEN]
    ct = raw[_NONCE_LEN:]
    aad = f"user={user_id}&api={api_id}".encode()
    try:
        key = _derive_user_key(user_id, api_id)
        return AESGCM(key).decrypt(nonce, ct, aad).decode("utf-8")
    except Exception:
        return ""


def health_check() -> dict:
    """诊断：主密钥来源 + 加解密往返"""
    source = "env" if os.environ.get(_MASTER_KEY_ENV) else "file"
    try:
        sample = "test-key-123"
        blob = encrypt_api_key(sample, 999, "test_api")
        ok = decrypt_api_key(blob, 999, "test_api") == sample
        return {"ok": ok, "master_key_source": source, "roundtrip": ok}
    except Exception as e:
        return {"ok": False, "master_key_source": source, "error": str(e)}
