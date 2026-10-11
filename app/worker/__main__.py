"""Reconciliation worker: python -m app.worker (same image as the API, different command)."""

from pathlib import Path

from ..config import Config
from ..extensions import db
from ..logging_setup import setup_logging
from ..services.run_service import RunService
from .worker import ReconWorker


def main() -> None:
    config = Config.from_env()
    setup_logging(config.log_level)
    db.init_app(config.database_url)
    ReconWorker(RunService(Path(config.data_dir))).run()


if __name__ == "__main__":
    main()
