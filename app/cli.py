"""Admin commands: python -m app.cli create-run --bank PATH --ledger PATH

Queues a run from files already on disk, for benchmarks larger than the 50 MB upload limit.
"""

import argparse
import json
import logging
from pathlib import Path

from .config import Config
from .extensions import db
from .logging_setup import setup_logging
from .services.run_service import RunService

logger = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-run", help="queue a reconciliation run from local files")
    create.add_argument("--bank", type=Path, required=True)
    create.add_argument("--ledger", type=Path, required=True)
    args = parser.parse_args(argv)

    config = Config.from_env()
    setup_logging(config.log_level)
    db.init_app(config.database_url)
    run = RunService(Path(config.data_dir)).create_from_paths(args.bank, args.ledger)
    logger.info("run_created", extra={"code": json.dumps({"id": str(run.id), "status": run.status.value})})


if __name__ == "__main__":
    main()
