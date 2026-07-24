from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Env-driven config. Names match .env.example (case-insensitive)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://postgres:devpassword@localhost:5432/b3"
    uploads_dir: str = "./uploads"
    artifacts_dir: str = "./artifacts"
    # Where taxcore's code + interpreter live. Empty => b3run's host defaults
    # (../taxcore and its .venv). The image sets these to /opt/taxcore.
    taxcore_dir: str = ""
    taxcore_python: str = ""
    # Same pattern for the backtest engine. Empty => enginerun's host defaults
    # (../engine and its .venv). The image sets these to /opt/engine.
    engine_dir: str = ""
    engine_python: str = ""
    # DSN the engine subprocess reads (ENGINE_DATABASE_URL) for the market plane.
    # Plain psycopg-style URL (no +asyncpg); host is `postgres` inside containers.
    engine_database_url: str = "postgresql://postgres:devpassword@localhost:5432/b3"
    secret_key: str = "dev-secret-change-me"
    environment: str = "development"
    frontend_url: str = "http://localhost:3000"
    allowed_origins: str = "http://localhost:3000"
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    # One recurring price per paid tier (see billing.LADDER). Empty => that tier
    # can't be bought; /billing/checkout 400s instead of half-creating a session.
    stripe_price_investidor: str = ""
    stripe_price_trader: str = ""

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
