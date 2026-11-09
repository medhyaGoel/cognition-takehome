import os
from dataclasses import dataclass

_DEV_ISSUER = "http://localhost:8080/realms/internal-tools"


@dataclass(frozen=True)
class Settings:
    environment: str
    # Runtime connections use least-privilege roles that are subject to row-level security.
    database_url: str
    worker_database_url: str
    # Schema owner: migrations, role bootstrap, connector sync. Never used to serve requests.
    migration_database_url: str
    # The client's system of record (read-only). Stand-in: the `source-db` compose service.
    source_database_url: str
    oidc_issuer: str
    oidc_audience: str
    oidc_jwks_url: str
    oidc_web_client_id: str
    workflows_path: str

    @property
    def is_production(self) -> bool:
        return self.environment == "production"


def _env(name: str, dev_default: str, environment: str) -> str:
    value = os.environ.get(name)
    if value:
        return value
    if environment == "production":
        raise RuntimeError(f"{name} must be set when APP_ENV=production")
    return dev_default


def load_settings() -> Settings:
    env = os.environ.get("APP_ENV", "development")
    issuer = _env("OIDC_ISSUER", _DEV_ISSUER, env)
    return Settings(
        environment=env,
        database_url=_env(
            "DATABASE_URL", "postgresql+psycopg://app_api:app_api_dev@localhost:5432/app", env
        ),
        worker_database_url=_env(
            "WORKER_DATABASE_URL",
            "postgresql+psycopg://app_worker:app_worker_dev@localhost:5432/app",
            env,
        ),
        migration_database_url=_env(
            "MIGRATION_DATABASE_URL", "postgresql+psycopg://app:app@localhost:5432/app", env
        ),
        source_database_url=_env(
            "SOURCE_DATABASE_URL",
            "postgresql+psycopg://tools_reader:tools_reader_dev@localhost:5433/core_payments",
            env,
        ),
        oidc_issuer=issuer,
        oidc_audience=_env("OIDC_AUDIENCE", "internal-tools-api", env),
        oidc_jwks_url=_env("OIDC_JWKS_URL", f"{issuer}/protocol/openid-connect/certs", env),
        oidc_web_client_id=_env("OIDC_WEB_CLIENT_ID", "internal-tools-web", env),
        workflows_path=os.environ.get("WORKFLOWS_PATH", "../config/workflows.yaml"),
    )


settings = load_settings()
