from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Env-driven config. Names match .env.example (case-insensitive)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://postgres:devpassword@localhost:5432/b3"
    redis_url: str = "redis://localhost:6379/0"
    uploads_dir: str = "./uploads"
    secret_key: str = "dev-secret-change-me"
    environment: str = "development"
    frontend_url: str = "http://localhost:3000"
    allowed_origins: str = "http://localhost:3000"
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""

    @property
    def origins(self) -> list[str]:
        return [o.strip() for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def psycopg_dsn(self) -> str:
        # procrastinate speaks psycopg, not asyncpg — drop the SQLAlchemy driver suffix.
        return self.database_url.replace("+asyncpg", "")

    @property
    def is_dev(self) -> bool:
        return self.environment == "development"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
