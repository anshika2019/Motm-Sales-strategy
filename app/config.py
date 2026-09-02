from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Loaded from environment variables / a local .env file.

    There is no JWT secret here: this project's Supabase instance signs
    tokens with an asymmetric key (ES256), so app/dependencies/auth.py
    verifies tokens against Supabase's public JWKS endpoint
    (derived from supabase_url) instead of a shared secret.

    supabase_service_role_key is used server-side by POST /auth/login to
    call Supabase's auth API, and reserved for future Supabase Admin API use
    (e.g. inviting/deleting auth users). It must never be returned in an API
    response.
    """

    supabase_url: str
    supabase_service_role_key: SecretStr
    database_url: str
    gemini_api_key: SecretStr
    openai_api_key: SecretStr
    # Deployed frontend origin (e.g. https://motm-sales.vercel.app), added to
    # the CORS allow-list alongside localhost. Unset in local dev.
    frontend_origin: str | None = None
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
