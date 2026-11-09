import os
from dataclasses import dataclass

_DEV_ONLY_SECRET = "dev-only-insecure-jwt-secret-do-not-use-in-prod"  # noqa: S105


@dataclass(frozen=True)
class Settings:
    environment: str
    database_url: str
    jwt_secret: str
    jwt_issuer: str = "internal-tools-platform"
    jwt_audience: str = "internal-tools"
    jwt_ttl_seconds: int = 3600
    workflows_path: str = "config/workflows.yaml"

    @property
    def dev_login_enabled(self) -> bool:
        return self.environment != "production"


def load_settings() -> Settings:
    environment = os.environ.get("APP_ENV", "development")
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        if environment == "production":
            raise RuntimeError("JWT_SECRET must be set when APP_ENV=production")
        secret = _DEV_ONLY_SECRET
    return Settings(
        environment=environment,
        database_url=os.environ.get(
            "DATABASE_URL", "postgresql+psycopg://app:app@localhost:5432/app"
        ),
        jwt_secret=secret,
        workflows_path=os.environ.get("WORKFLOWS_PATH", "../config/workflows.yaml"),
    )


settings = load_settings()
