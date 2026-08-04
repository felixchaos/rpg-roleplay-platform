"""Sign in with Apple identity-token 校验的安全单测。

只测 verify_apple_identity_token(纯密码学 + claim 校验,不碰 DB):用本地 RSA 密钥伪造
Apple 风格的 JWT,monkeypatch JWKS 客户端返回该公钥,断言合法令牌通过、各类非法令牌被拒。
"""
import hashlib
import time

import pytest

jwt = pytest.importorskip("jwt")
from cryptography.hazmat.primitives.asymmetric import rsa  # noqa: E402

from platform_app import auth as _auth  # noqa: E402


def _keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return priv, priv.public_key()


def _make_token(priv, *, sub="apple-sub-123", aud=None, iss=None, email="x@y.com",
                nonce=None, exp_delta=600, drop=None):
    now = int(time.time())
    payload = {
        "iss": iss or _auth.APPLE_ISSUER,
        "aud": aud or _auth.APPLE_AUDIENCE,
        "sub": sub,
        "iat": now,
        "exp": now + exp_delta,
        "email": email,
        "email_verified": "true",
    }
    if nonce is not None:
        payload["nonce"] = nonce
    for k in (drop or []):
        payload.pop(k, None)
    return jwt.encode(payload, priv, algorithm="RS256", headers={"kid": "testkid"})


class _FakeSigningKey:
    def __init__(self, key):
        self.key = key


class _FakeJWKClient:
    def __init__(self, pub):
        self._pub = pub

    def get_signing_key_from_jwt(self, token):
        return _FakeSigningKey(self._pub)


@pytest.fixture
def signer(monkeypatch):
    priv, pub = _keypair()
    monkeypatch.setattr(_auth, "_get_apple_jwk_client", lambda: _FakeJWKClient(pub))
    return priv


def test_valid_token_passes(signer):
    raw_nonce = "abc-123"
    nh = hashlib.sha256(raw_nonce.encode()).hexdigest()
    token = _make_token(signer, nonce=nh, email="A@B.COM")
    out = _auth.verify_apple_identity_token(token, raw_nonce)
    assert out["sub"] == "apple-sub-123"
    assert out["email"] == "a@b.com"          # 归一化小写
    assert out["email_verified"] is True


def test_valid_without_nonce(signer):
    out = _auth.verify_apple_identity_token(_make_token(signer))
    assert out["sub"] == "apple-sub-123"


def test_wrong_audience_rejected(signer):
    with pytest.raises(ValueError):
        _auth.verify_apple_identity_token(_make_token(signer, aud="com.evil.app"))


def test_wrong_issuer_rejected(signer):
    with pytest.raises(ValueError):
        _auth.verify_apple_identity_token(_make_token(signer, iss="https://evil.example.com"))


def test_expired_rejected(signer):
    with pytest.raises(ValueError):
        _auth.verify_apple_identity_token(_make_token(signer, exp_delta=-10))


def test_missing_sub_rejected(signer):
    with pytest.raises(ValueError):
        _auth.verify_apple_identity_token(_make_token(signer, drop=["sub"]))


def test_bad_signature_rejected(monkeypatch):
    # token 用密钥 A 签名,但 JWKS 返回密钥 B 的公钥 → 验签必须失败
    priv_a, _ = _keypair()
    _, pub_b = _keypair()
    monkeypatch.setattr(_auth, "_get_apple_jwk_client", lambda: _FakeJWKClient(pub_b))
    with pytest.raises(ValueError):
        _auth.verify_apple_identity_token(_make_token(priv_a))


def test_nonce_mismatch_rejected(signer):
    token = _make_token(signer, nonce="deadbeefnotmatching")
    with pytest.raises(ValueError):
        _auth.verify_apple_identity_token(token, "the-real-nonce")


def test_empty_token_rejected(signer):
    with pytest.raises(ValueError):
        _auth.verify_apple_identity_token("")


def test_alg_none_rejected(signer):
    # 经典攻击:alg=none 无签名令牌 — 必须被拒(algorithms 限定 RS256)。
    import base64
    import json

    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=").decode()

    now = int(time.time())
    forged = b64({"alg": "none", "typ": "JWT"}) + "." + b64({
        "iss": _auth.APPLE_ISSUER, "aud": _auth.APPLE_AUDIENCE, "sub": "evil",
        "iat": now, "exp": now + 600,
    }) + "."
    with pytest.raises(ValueError):
        _auth.verify_apple_identity_token(forged)
