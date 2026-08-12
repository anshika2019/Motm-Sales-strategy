from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Loaded from environment variables / a local .env file.

    supabase_service_role_key is intentionally loaded but not currently used
    by any endpoint — it's reserved for future Supabase Admin API calls
    (e.g. inviting/deleting auth users). It must never be returned in an API
    response.
    """

    supabase_url: str
    supabase_jwt_secret: SecretStr
    supabase_service_role_key: SecretStr
    database_url: str

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
