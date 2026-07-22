from procrastinate import App, PsycopgConnector

from platform_app.config import settings

# Postgres-backed queue (spec 01: Redis is cache-only, jobs live in Postgres).
# Constructing the App does not open a connection — the pool opens on app.open_async().
app = App(connector=PsycopgConnector(conninfo=settings.psycopg_dsn))


@app.task(name="ping")
async def ping(name: str = "world") -> str:
    return f"pong: {name}"
