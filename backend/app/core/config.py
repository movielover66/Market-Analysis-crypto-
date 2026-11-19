"""
Environment configuration and strict validation for TRADEIQ.
Supports both TRADEIQ canonical variable names AND backward-compatible variable names
from the user's previous Render deployment (ANGEL_PIN, SUPABASE_KEY, GEMINI_API_KEYS,
TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, FINNHUB_API_KEY, MARKETAUX_API_KEY).
Never logs or exposes secret values.
"""
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

try:
    from dotenv import load_dotenv
    _ROOT_ENV = Path(__file__).resolve().parents[3] / ".env"
    if _ROOT_ENV.exists():
        load_dotenv(_ROOT_ENV)
except ImportError:
    pass

VALID_ENVS = ("development", "staging", "production", "test")


def _parse_bool(val: str, default: bool = False) -> bool:
    if val is None or val.strip() == "":
        return default
    v = val.strip().lower()
    if v in ("1", "true", "yes", "on"):
        return True
    if v in ("0", "false", "no", "off"):
        return False
    raise ValueError(f"Invalid boolean environment value: {val!r}")


@dataclass(frozen=True)
class Settings:
    app_env: str = "development"
    demo_mode: bool = True
    allowed_origins: List[str] = field(default_factory=lambda: ["http://localhost:5173", "http://127.0.0.1:5173"])

    supabase_url: str = ""
    supabase_anon_key: str = ""
    supabase_service_role_key: str = ""
    supabase_jwt_secret: str = ""

    angel_api_key: str = ""
    angel_client_code: str = ""
    angel_password: str = ""
    angel_totp_secret: str = ""

    token_encryption_key: str = ""
    gemini_api_key: str = ""
    gemini_api_keys: List[str] = field(default_factory=list)
    gemini_model: str = "gemini-2.5-flash"

    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    finnhub_api_key: str = ""
    marketaux_api_key: str = ""

    internal_job_token: str = ""
    sentry_dsn: str = ""

    # --- Single-owner authentication ---------------------------------------------------
    # owner_user_id: the ONE identity allowed to use the private API. For Supabase tokens it
    #   must equal the owner's Supabase user UUID (`sub`). Empty => Supabase tokens are refused.
    # owner_password_hash + auth_jwt_secret: enable built-in owner login (POST /api/v1/auth/login).
    owner_user_id: str = ""
    owner_password_hash: str = ""
    auth_jwt_secret: str = ""
    access_token_ttl_minutes: int = 60
    enable_api_docs: bool = False

    @property
    def owner_id(self) -> str:
        """Identity every authenticated request is mapped to (never taken from the client)."""
        return self.owner_user_id or "owner"

    @property
    def local_auth_enabled(self) -> bool:
        return bool(self.owner_password_hash and self.auth_jwt_secret)

    @property
    def supabase_auth_enabled(self) -> bool:
        return bool(self.supabase_jwt_secret and self.owner_user_id)

    def validate_config(self) -> None:
        if self.app_env not in VALID_ENVS:
            raise ValueError(f"APP_ENV must be one of {VALID_ENVS}, got {self.app_env!r}")

        if not self.allowed_origins:
            raise ValueError("ALLOWED_ORIGINS cannot be empty")
        if "*" in self.allowed_origins:
            raise ValueError("Wildcard '*' in ALLOWED_ORIGINS is forbidden (list explicit origins)")

        if self.auth_jwt_secret and len(self.auth_jwt_secret) < 32:
            raise ValueError("AUTH_JWT_SECRET must be at least 32 characters")
        if self.owner_password_hash:
            from .password_hash import is_valid_hash_format
            if not is_valid_hash_format(self.owner_password_hash):
                raise ValueError("OWNER_PASSWORD_HASH is not a valid hash (generate it with: python -m app.core.password_hash)")
            if not self.auth_jwt_secret:
                raise ValueError("AUTH_JWT_SECRET is required when OWNER_PASSWORD_HASH is set")
        if self.auth_jwt_secret and self.supabase_jwt_secret and self.auth_jwt_secret == self.supabase_jwt_secret:
            raise ValueError("AUTH_JWT_SECRET must differ from SUPABASE_JWT_SECRET")
        if not 5 <= self.access_token_ttl_minutes <= 1440:
            raise ValueError("ACCESS_TOKEN_TTL_MINUTES must be between 5 and 1440")

        if self.app_env in ("staging", "production"):
            if not self.supabase_url or not self.supabase_jwt_secret:
                raise ValueError("SUPABASE_URL and SUPABASE_JWT_SECRET are required in staging/production")
            if not self.demo_mode and not all((self.angel_api_key, self.angel_client_code, self.angel_password, self.angel_totp_secret)):
                raise ValueError(
                    "DEMO_MODE is false in staging/production, but complete Angel One credentials "
                    "(ANGEL_API_KEY, ANGEL_CLIENT_CODE, ANGEL_PIN/ANGEL_PASSWORD, ANGEL_TOTP_SECRET) are not configured. "
                    "TRADEIQ never silently falls back to simulated data."
                )
            if not (self.local_auth_enabled or self.supabase_auth_enabled):
                raise ValueError(
                    "No owner authentication configured for staging/production. Set OWNER_PASSWORD_HASH + "
                    "AUTH_JWT_SECRET (built-in login) and/or OWNER_USER_ID (Supabase owner UUID)."
                )

    def safe_summary(self) -> dict:
        """Returns non-sensitive configuration metadata safe for logs and status endpoints."""
        return {
            "app_env": self.app_env,
            "demo_mode": self.demo_mode,
            "allowed_origins": self.allowed_origins,
            "supabase_configured": bool(self.supabase_url and (self.supabase_service_role_key or self.supabase_anon_key)),
            "angel_one_configured": bool(
                self.angel_api_key and self.angel_client_code and self.angel_password and self.angel_totp_secret
            ),
            "gemini_configured": bool(self.gemini_api_key or self.gemini_api_keys),
            "gemini_key_pool_count": len(self.gemini_api_keys),
            "telegram_configured": bool(self.telegram_bot_token and self.telegram_chat_id),
            "finnhub_configured": bool(self.finnhub_api_key),
            "marketaux_configured": bool(self.marketaux_api_key),
            "encryption_key_configured": bool(self.token_encryption_key),
            "owner_login_configured": self.local_auth_enabled,
            "supabase_owner_auth_configured": self.supabase_auth_enabled,
        }

    def __repr__(self) -> str:
        return f"Settings({self.safe_summary()!r})"


def load_settings_from_env(env: dict = None) -> Settings:
    src = env if env is not None else os.environ
    app_env = src.get("APP_ENV", "development").strip().lower() or "development"
    demo_default = app_env in ("development", "test")
    demo_mode = _parse_bool(src.get("DEMO_MODE", ""), default=demo_default)

    raw_origins = src.get("ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    origins = [o.strip() for o in raw_origins.split(",") if o.strip()]

    # Backward-compatible Supabase key resolution (supports SUPABASE_KEY from previous project)
    legacy_sb_key = src.get("SUPABASE_KEY", "").strip()
    sb_anon = src.get("SUPABASE_ANON_KEY", "").strip() or legacy_sb_key
    sb_service = src.get("SUPABASE_SERVICE_ROLE_KEY", "").strip() or legacy_sb_key

    # Backward-compatible Gemini multi-key pool resolution (supports GEMINI_API_KEYS + GEMINI_API_KEY)
    single_gemini = src.get("GEMINI_API_KEY", "").strip()
    multi_gemini_raw = src.get("GEMINI_API_KEYS", "").strip()
    combined_keys = [
        k.strip()
        for k in re.split(r"[,\s;]+", f"{multi_gemini_raw},{single_gemini}")
        if k.strip() and not k.strip().startswith("YOUR_")
    ]
    dedup_gemini_keys = list(dict.fromkeys(combined_keys))
    primary_gemini = single_gemini or (dedup_gemini_keys[0] if dedup_gemini_keys else "")

    raw_ttl = src.get("ACCESS_TOKEN_TTL_MINUTES", "").strip()
    try:
        ttl_minutes = int(raw_ttl) if raw_ttl else 60
    except ValueError:
        raise ValueError(f"ACCESS_TOKEN_TTL_MINUTES must be an integer, got {raw_ttl!r}")

    settings = Settings(
        app_env=app_env,
        demo_mode=demo_mode,
        allowed_origins=origins,
        supabase_url=src.get("SUPABASE_URL", "").strip().rstrip("/"),
        supabase_anon_key=sb_anon,
        supabase_service_role_key=sb_service,
        supabase_jwt_secret=src.get("SUPABASE_JWT_SECRET", "").strip(),
        angel_api_key=src.get("ANGEL_API_KEY", "").strip(),
        angel_client_code=src.get("ANGEL_CLIENT_CODE", "").strip(),
        angel_password=(src.get("ANGEL_PASSWORD", "") or src.get("ANGEL_PIN", "")).strip(),
        angel_totp_secret=src.get("ANGEL_TOTP_SECRET", "").strip(),
        token_encryption_key=src.get("TOKEN_ENCRYPTION_KEY", "").strip(),
        gemini_api_key=primary_gemini,
        gemini_api_keys=dedup_gemini_keys,
        gemini_model=src.get("GEMINI_MODEL", "gemini-2.5-flash").strip() or "gemini-2.5-flash",
        telegram_bot_token=src.get("TELEGRAM_BOT_TOKEN", "").strip(),
        telegram_chat_id=src.get("TELEGRAM_CHAT_ID", "").strip(),
        finnhub_api_key=src.get("FINNHUB_API_KEY", "").strip(),
        marketaux_api_key=src.get("MARKETAUX_API_KEY", "").strip(),
        internal_job_token=src.get("INTERNAL_JOB_TOKEN", "").strip(),
        sentry_dsn=src.get("SENTRY_DSN", "").strip(),
        owner_user_id=src.get("OWNER_USER_ID", "").strip(),
        owner_password_hash=src.get("OWNER_PASSWORD_HASH", "").strip(),
        auth_jwt_secret=src.get("AUTH_JWT_SECRET", "").strip(),
        access_token_ttl_minutes=ttl_minutes,
        enable_api_docs=_parse_bool(src.get("ENABLE_API_DOCS", ""), default=False),
    )
    settings.validate_config()
    return settings
