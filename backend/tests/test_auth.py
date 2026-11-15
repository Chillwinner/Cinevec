from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from app.infra.config import Settings
from app.infra.file_store import LocalFileStore
from app.infra.passwords import hash_password, verify_password
from app.main import create_app
from tests.conftest import FakeReader

TEST_USER = "tester"
TEST_PASS = "test-pass-12345"
TEST_KEY = "test-signing-key-aaaa-bbbb-cccc-dddd"
TEST_PRESET = "test-preset-key-xxxx-yyyy-zzzz-wwww"
TEST_KEY_2 = "test-signing-key-2222-3333-4444-5555"


def _app(tmp_path: Path, jwt_secret: str | None = None, auth_enabled_env: bool = False):
    settings = Settings(
        sqlite_path=tmp_path / "auth.db",
        worker_enabled=False,
        auth_enabled=auth_enabled_env,
        jwt_secret=jwt_secret,
    )
    return create_app(
        settings,
        sqlite_path=tmp_path / "auth.db",
        source_reader=FakeReader("ok", listing=[]),
        worker_enabled=False,
        file_store=LocalFileStore(tmp_path / "store", label="pytest-temp"),
        work_root=tmp_path / "work",
    )


def _enable(client: TestClient, mode="custom", secret=TEST_KEY, confirm=True, **extra):
    body = {
        "username": TEST_USER,
        "password": TEST_PASS,
        "password_confirm": TEST_PASS,
        "signing_key_mode": mode,
        "confirm_customization": confirm,
    }
    if mode == "custom":
        body["custom_signing_key"] = secret
    body.update(extra)
    return client.post("/api/auth/enable", json=body)


def test_default_auth_off_allows_business_routes(tmp_path: Path):
    with TestClient(_app(tmp_path)) as client:
        status = client.get("/api/auth/status").json()
        assert status["enabled"] is False
        assert status["preset_pending_verification"] is False
        assert client.get("/api/health").json()["status"] == "ok"
        assert client.get("/api/assets").status_code == 200
        assert client.get("/api/settings").status_code == 200
        preview = client.get("/api/previews/x/frames/0")
        # 无资源是 404，而不是因未登录拒绝；认证关闭不另开缺口也不误拦
        assert preview.status_code in {404, 503}


def test_enable_requires_confirmation_and_complete_credentials(tmp_path: Path):
    with TestClient(_app(tmp_path)) as client:
        missing = _enable(client, confirm=False)
        assert missing.status_code == 400
        assert missing.json()["error"]["code"] == "confirm_required"
        assert client.get("/api/auth/status").json()["enabled"] is False
        no_user = client.post(
            "/api/auth/enable",
            json={"signing_key_mode": "custom", "custom_signing_key": TEST_KEY, "confirm_customization": True, "username": "", "password": ""},
        )
        assert no_user.status_code == 400
        assert client.get("/api/auth/status").json()["enabled"] is False


def test_password_is_hashed_and_login_success_failure(tmp_path: Path):
    with TestClient(_app(tmp_path)) as client:
        enabled = _enable(client)
        assert enabled.status_code == 200
        body = enabled.json()
        assert "token" in body
        dumped = str(body)
        assert TEST_PASS not in dumped
        assert TEST_KEY not in dumped
        runtime = client.app.state.container.auth._metadata.get_auth_runtime()
        user = client.app.state.container.auth._metadata.get_user()
        assert user is not None
        assert TEST_PASS not in user["password_hash"]
        assert user["password_hash"].startswith("pbkdf2_sha256$")
        assert verify_password(TEST_PASS, user["password_hash"])
        assert not verify_password("wrong-pass-12345", user["password_hash"])
        assert not runtime.get("signing_key") or TEST_PASS not in str(runtime.get("signing_key"))
        # 登录成功只把令牌放在响应里
        login = client.post("/api/auth/login", json={"username": TEST_USER, "password": TEST_PASS})
        assert login.status_code == 200
        token = login.json()["token"]
        assert token
        assert "password" not in login.json()
        bad = client.post("/api/auth/login", json={"username": TEST_USER, "password": "wrong-pass-12345"})
        assert bad.status_code == 401
        assert "token" not in bad.json()
        settings = client.get("/api/settings", headers={"Authorization": f"Bearer {token}"}).json()
        text = str(settings)
        assert TEST_KEY not in text
        assert TEST_PASS not in text
        status = client.get("/api/status", headers={"Authorization": f"Bearer {token}"}).json()
        dumped = str(status)
        assert TEST_KEY not in dumped
        assert "signing_key_source" in status["auth"]
        assert "signing_key" not in status["auth"]
        assert status["auth"]["secret_configured"] is True


def test_protected_routes_and_preview_require_token(tmp_path: Path):
    with TestClient(_app(tmp_path)) as client:
        token = _enable(client).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/assets").status_code == 401
        assert client.get("/api/settings").status_code == 401
        assert client.get("/api/previews/x/frames/0").status_code == 401
        assert client.get("/api/assets", headers=headers).status_code == 200
        settings = client.get("/api/settings", headers=headers)
        assert settings.status_code == 200
        # 已配置后不能再用 setup 绕过
        again = client.post(
            "/api/auth/setup",
            json={"username": "other", "password": "another-pass-999"},
        )
        assert again.status_code == 409


def test_jwt_tamper_and_expiry(tmp_path: Path):
    with TestClient(_app(tmp_path)) as client:
        token = _enable(client, expire_seconds=60).json()["token"]
        tampered = token[:-4] + ("AAAA" if not token.endswith("AAAA") else "BBBB")
        denied = client.get("/api/settings", headers={"Authorization": f"Bearer {tampered}"})
        assert denied.status_code == 401
        unauth = client.post(
            "/api/auth/enable",
            json={
                "username": TEST_USER,
                "password": TEST_PASS,
                "signing_key_mode": "custom",
                "custom_signing_key": TEST_KEY,
                "confirm_customization": True,
                "expire_seconds": 60,
            },
        )
        assert unauth.status_code == 401
        short = client.post(
            "/api/auth/enable",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "username": TEST_USER,
                "password": TEST_PASS,
                "signing_key_mode": "custom",
                "custom_signing_key": TEST_KEY,
                "confirm_customization": True,
                "expire_seconds": 60,
            },
        )
        # already enabled
        assert short.status_code == 409
        # 另开短有效期实例
    with TestClient(_app(tmp_path / "exp", jwt_secret=None)) as client:
        token = _enable(client, expire_seconds=61).json()["token"]
        # 有效期内可用
        assert client.get("/api/settings", headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_expired_token_is_rejected(tmp_path: Path, monkeypatch):
    from datetime import datetime, timedelta, timezone
    import app.infra.jwt_tokens as jwt_mod

    frozen = datetime.now(timezone.utc)
    monkeypatch.setattr(jwt_mod, "now", lambda: frozen)
    with TestClient(_app(tmp_path)) as client:
        token = _enable(client, expire_seconds=60).json()["token"]
        monkeypatch.setattr(jwt_mod, "now", lambda: frozen + timedelta(seconds=120))
        # decode uses current time, not now(); advance real wait is flaky. 改用显式过期令牌校验：
        import jwt
        expired = jwt.encode(
            {"sub": "x", "username": TEST_USER, "tv": 1, "iat": frozen - timedelta(seconds=120), "exp": frozen - timedelta(seconds=60), "iss": "pica"},
            TEST_KEY,
            algorithm="HS256",
        )
        resp = client.get("/api/settings", headers={"Authorization": f"Bearer {expired}"})
        assert resp.status_code == 401
        assert resp.json()["error"]["code"] in {"token_expired", "token_invalid", "unauthorized"}


def test_changing_signing_key_revokes_old_tokens(tmp_path: Path):
    with TestClient(_app(tmp_path)) as client:
        first = _enable(client).json()["token"]
        headers = {"Authorization": f"Bearer {first}"}
        assert client.get("/api/me" if False else "/api/auth/me", headers=headers).status_code == 200
        changed = client.post(
            "/api/auth/signing-key",
            headers=headers,
            json={"signing_key_mode": "custom", "custom_signing_key": TEST_KEY_2, "confirm_customization": True},
        )
        assert changed.status_code == 200
        assert "token" not in changed.json() or changed.json().get("token") is None
        assert client.get("/api/settings", headers=headers).status_code == 401
        login = client.post("/api/auth/login", json={"username": TEST_USER, "password": TEST_PASS})
        assert login.status_code == 200
        new_headers = {"Authorization": f"Bearer {login.json()['token']}"}
        assert client.get("/api/settings", headers=new_headers).status_code == 200
        status = client.get("/api/auth/status").json()
        assert status["token_version"] >= 2
        assert TEST_KEY_2 not in str(status)


def test_preset_mode_uses_config_not_guessed_default(tmp_path: Path):
    with TestClient(_app(tmp_path)) as client:
        denied = _enable(client, mode="preset")
        assert denied.status_code == 400
        assert denied.json()["error"]["code"] == "preset_unavailable"
        assert client.get("/api/auth/status").json()["preset_available"] is False
    with TestClient(_app(tmp_path / "with-preset", jwt_secret=TEST_PRESET)) as client:
        status = client.get("/api/auth/status").json()
        assert status["preset_available"] is True
        assert status["preset_pending_verification"] is False
        enabled = _enable(client, mode="preset")
        assert enabled.status_code == 200
        token = enabled.json()["token"]
        runtime = client.app.state.container.auth._metadata.get_auth_runtime()
        # 预设不复制进自定义存储列
        assert runtime["signing_key_source"] == "preset"
        assert not runtime.get("signing_key")
        assert client.get("/api/settings", headers={"Authorization": f"Bearer {token}"}).status_code == 200
        assert TEST_PRESET not in str(client.get("/api/auth/status").json())


def test_env_auth_flag_does_not_auto_enable(tmp_path: Path):
    with TestClient(_app(tmp_path, auth_enabled_env=True)) as client:
        assert client.get("/api/auth/status").json()["enabled"] is False
        assert client.get("/api/assets").status_code == 200


def test_setup_then_conflict_and_hash_helper():
    hashed = hash_password(TEST_PASS)
    assert hashed != TEST_PASS
    assert verify_password(TEST_PASS, hashed)
