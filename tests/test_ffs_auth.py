"""Verify guest-token uniqueness and the bearer binding used by FFS."""
import asyncio
import hashlib
import importlib.util
from pathlib import Path

from Crypto.PublicKey import RSA
from fastapi import HTTPException
import jwt
import pytest

from app.config import settings
from app.services.ffs import owner_id


def load(name, relative):
    path = Path(__file__).resolve().parents[1] / relative
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_same_guest_subject_gets_distinct_verified_ffs_owners(tmp_path, monkeypatch):
    key = RSA.generate(2048)
    private, public = tmp_path / "private.pem", tmp_path / "public.pem"
    private.write_bytes(key.export_key())
    private.chmod(0o600)
    public.write_bytes(key.public_key().export_key())
    monkeypatch.setattr(settings, "jwt_private_key_path", str(private))
    monkeypatch.setattr(settings, "jwt_public_key_path", str(public))
    token_api = load("ffs_test_token_api", "app/routers/token.py")
    auth = load("ffs_test_jwt_auth", "app/auth/jwt_auth.py")

    async def run():
        a, b = await token_api.create_auth_token(), await token_api.create_auth_token()
        user_a, user_b = await auth.get_current_user(a.token), await auth.get_current_user(b.token)
        assert user_a["sub"] == user_b["sub"] == "guest:anon"
        assert a.token != b.token
        assert user_a["auth_token_id"] == hashlib.sha256(a.token.encode()).hexdigest()
        assert owner_id(user_a) != owner_id(user_b)
        claims_a = jwt.decode(a.token, key.public_key().export_key(), algorithms=["RS256"])
        claims_b = jwt.decode(b.token, key.public_key().export_key(), algorithms=["RS256"])
        assert claims_a["jti"] != claims_b["jti"]
        forged = jwt.encode(claims_a, RSA.generate(2048).export_key(), algorithm="RS256")
        with pytest.raises(HTTPException) as error:
            await auth.get_current_user(forged)
        assert error.value.status_code == 401

    asyncio.run(run())
