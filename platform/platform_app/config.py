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
    # Wall-clock cap on any taxcore/engine subprocess. Generous — a full-history
    # backtest is the slow case — but bounded, so a wedged child can't hold a
    # request thread or one of the worker's two slots forever.
    subprocess_timeout_s: int = 600
    # Ingestion runs on its own clock: a first full COTAHIST backfill is hours of
    # download + parse, so the per-request cap above would kill it. Bounded anyway
    # so a stalled B3 download cannot hold the ingest worker forever.
    ingest_timeout_s: int = 6 * 60 * 60
    # Cron for the scheduled market-data refresh (UTC — procrastinate schedules in
    # UTC). 06:00 UTC = 03:00 America/Sao_Paulo, comfortably after B3 has published
    # the day's COTAHIST and outside the hours anyone is running backtests.
    ingest_prices_cron: str = "0 6 * * *"
    # Earliest COTAHIST year the scheduled refresh covers. The pipeline's own floor
    # is 1994, but the engine's strategies default to start_date=2005-01-01, so the
    # decade before that is downloaded and parsed to serve nothing. Bounding it also
    # keeps the very first run — which has no local archive at all — tractable on a
    # 2-core box using the pure-Python parser. Lower it if a strategy ever needs
    # deeper history; the pipeline is idempotent and will just fetch the gap.
    ingest_start_year: int = 2005
    # Fundamentals move on a filing calendar, not a daily one — weekly is plenty.
    ingest_fundamentals_cron: str = "0 7 * * 0"
    environment: str = "development"
    frontend_url: str = "http://localhost:3000"
    allowed_origins: str = "http://localhost:3000"
    google_oauth_client_id: str = ""
    google_oauth_client_secret: str = ""
    stripe_secret_key: str = ""
    stripe_webhook_secret: str = ""
    # One recurring price per sellable module (billing.SELLABLE — the field name
    # is `stripe_price_<module id>`, which is how billing.price_for finds it).
    # Empty => that module can't be bought; /billing/checkout 400s instead of
    # half-creating a session.
    stripe_price_irpf: str = ""
    stripe_price_mercado: str = ""
    stripe_price_indice: str = ""
    stripe_price_bench: str = ""
    stripe_price_empresarial: str = ""

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


settings = Settings()

# The signing key fails open: `SECRET_KEY: ${SECRET_KEY}` in compose interpolates a
# missing var to "", pydantic reads the env var as present-and-empty and so takes ""
# over the default, and PyJWT happily signs *and verifies* HS256 with a zero-length
# key. The stack then boots green while anyone can mint a token for any tenant_id.
# The same key signs OAuth `state`, so it is not only session forgery.
_UNSAFE_SECRETS = {"", "change-me", "dev-secret-change-me"}
if not settings.is_dev and settings.secret_key in _UNSAFE_SECRETS:
    raise RuntimeError(
        "SECRET_KEY is unset or still the placeholder — refusing to start outside "
        "development. Generate one with: python -c 'import secrets;print(secrets.token_urlsafe(64))'"
    )
