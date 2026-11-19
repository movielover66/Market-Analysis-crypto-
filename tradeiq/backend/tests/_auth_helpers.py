"""Shared test helpers: a fully-configured single-owner Settings object and owner auth headers.
Test-only values — nothing here is a real credential."""
from app.core.auth import issue_owner_token
from app.core.config import Settings
from app.core.password_hash import hash_password

OWNER_PASSWORD = "test-owner-password-for-unit-tests-only"
AUTH_JWT_SECRET = "unit-test-auth-signing-secret-0123456789abcdef"
SUPABASE_JWT_SECRET = "unit-test-supabase-jwt-secret-0123456789abcd"
OWNER_UUID = "11111111-1111-1111-1111-111111111111"
OTHER_UUID = "22222222-2222-2222-2222-222222222222"

_HASH = hash_password(OWNER_PASSWORD)  # computed once per test process


def owner_settings(**overrides) -> Settings:
    base = dict(
        app_env="test",
        demo_mode=True,
        allowed_origins=["http://localhost:5173"],
        owner_user_id=OWNER_UUID,
        owner_password_hash=_HASH,
        auth_jwt_secret=AUTH_JWT_SECRET,
        supabase_jwt_secret=SUPABASE_JWT_SECRET,
    )
    base.update(overrides)
    return Settings(**base)


def owner_headers(settings: Settings) -> dict:
    token, _ = issue_owner_token(settings)
    return {"Authorization": f"Bearer {token}"}
