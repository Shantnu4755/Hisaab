import os
from dataclasses import dataclass


class ConfigError(RuntimeError):
    """Raised at startup when required configuration is missing."""


def _require(name: str) -> str:
    value = os.getenv(name)
    if not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


@dataclass(frozen=True)
class Config:
    app_name: str
    database_url: str
    log_level: str
    debug: bool
    data_dir: str = "/data"  # uploaded CSVs; a Docker volume shared by API and worker

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            app_name=os.getenv("APP_NAME", "app"),
            database_url=_require("DATABASE_URL"),
            log_level=os.getenv("LOG_LEVEL", "INFO"),
            debug=os.getenv("DEBUG", "false").lower() == "true",
            data_dir=os.getenv("DATA_DIR", "/data"),
        )
