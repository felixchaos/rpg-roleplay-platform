"""安全回归 — 用户 API key 加密(AES-256-GCM + AAD 隔离 + 生产主密钥闸)。"""
import importlib
import os

import pytest


def _crypto_with(env):
    for k, v in env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    import utils.crypto as c
    importlib.reload(c)
    return c


def test_roundtrip_and_aad_cross_user_isolation():
    c = _crypto_with({"RPG_REQUIRE_AUTH": "1", "RPG_MASTER_KEY": "a" * 64})
    blob = c.encrypt_api_key("sk-secret-123", 5, "anthropic")
    assert c.decrypt_api_key(blob, 5, "anthropic") == "sk-secret-123"
    # AAD 绑定 user+api:换 user / 换 api 解不开(返空)
    assert c.decrypt_api_key(blob, 9, "anthropic") == ""
    assert c.decrypt_api_key(blob, 5, "openai") == ""
    # 密文无明文残留
    assert b"sk-secret" not in blob


def test_production_without_master_key_refuses():
    c = _crypto_with({"RPG_REQUIRE_AUTH": "1", "RPG_MASTER_KEY": None})
    with pytest.raises(RuntimeError):
        c.encrypt_api_key("sk", 1, "anthropic")
    # 复原,避免污染其它测试
    _crypto_with({"RPG_MASTER_KEY": "a" * 64})


if __name__ == "__main__":
    test_roundtrip_and_aad_cross_user_isolation()
    test_production_without_master_key_refuses()
    print("OK")
