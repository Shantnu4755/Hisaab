import csv
import io
import logging
import shutil
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from ..exceptions import AppError, NotFoundError
from ..extensions import db
from ..models.recon import ReconRun, RunStatus
from ..repositories.recon_repository import ReconRepository
from ..schemas.run import ExceptionFilter, ExceptionOut, RunFilter, RunOut
from .reconciler import BANK_DATE_COLUMN, LEDGER_DATE_COLUMN, Reconciler

logger = logging.getLogger(__name__)

STALE_RUN_MINUTES = 30
RETENTION_DAYS = 7


class Upload(Protocol):
    """What the service needs from an uploaded file (werkzeug's FileStorage fits)."""

    filename: str | None

    def save(self, dst: str | Path) -> None: ...


class RunService:
    def __init__(self, data_dir: Path, reconciler: Reconciler | None = None) -> None:
        self.data_dir = data_dir
        self.reconciler = reconciler or Reconciler()
        self.runs = ReconRepository()

    # --- API side ------------------------------------------------------------------
    def create(self, bank: Upload, ledger: Upload) -> RunOut:
        run_id = uuid.uuid4()
        folder = self._folder(run_id)
        folder.mkdir(parents=True)
        try:
            bank.save(folder / "bank.csv")
            ledger.save(folder / "ledger.csv")
            self._check_headers(folder)
        except Exception:
            self._remove_files(run_id)  # nothing queued, nothing left on disk
            raise
        return self._queue(run_id, _display_name(bank.filename), _display_name(ledger.filename))

    def create_from_paths(self, bank_path: Path, ledger_path: Path) -> RunOut:
        """For the CLI benchmark: files larger than the 50 MB upload limit."""
        run_id = uuid.uuid4()
        folder = self._folder(run_id)
        folder.mkdir(parents=True)
        try:
            shutil.copyfile(bank_path, folder / "bank.csv")
            shutil.copyfile(ledger_path, folder / "ledger.csv")
            self._check_headers(folder)
        except Exception:
            self._remove_files(run_id)
            raise
        return self._queue(run_id, bank_path.name, ledger_path.name)

    def recent(self, f: RunFilter) -> list[RunOut]:
        with db.session() as s:
            return [RunOut.model_validate(r) for r in self.runs.recent_runs(s, f.limit, f.offset)]

    def get(self, run_id: uuid.UUID) -> RunOut:
        with db.session() as s:
            return RunOut.model_validate(self._get_or_404(s, run_id))

    def exceptions(self, run_id: uuid.UUID, f: ExceptionFilter) -> list[ExceptionOut]:
        with db.session() as s:
            self._get_or_404(s, run_id)
            rows = self.runs.exceptions(s, run_id, f.side, f.limit, f.offset)
            return [ExceptionOut.model_validate(r) for r in rows]

    def export_csv(self, run_id: uuid.UUID, side: str) -> Iterator[str]:
        """Checks the run now (so a 404 is raised before streaming starts), then streams CSV lines."""
        with db.session() as s:
            self._get_or_404(s, run_id)
        date_header = BANK_DATE_COLUMN if side == "bank" else LEDGER_DATE_COLUMN

        def lines() -> Iterator[str]:
            buffer = io.StringIO()
            writer = csv.writer(buffer)
            writer.writerow([date_header, "amount", "reference"])
            with db.session() as s:
                for row in self.runs.iter_exceptions(s, run_id, side):
                    amount = f"{row.amount_paise / 100:.2f}" if row.amount_paise is not None else ""
                    writer.writerow([row.txn_date or "", amount, row.reference or ""])
                    if buffer.tell() > 64_000:
                        yield buffer.getvalue()
                        buffer.seek(0)
                        buffer.truncate()
            yield buffer.getvalue()

        return lines()

    # --- worker side ---------------------------------------------------------------
    def claim_next(self) -> uuid.UUID | None:
        with db.session() as s:
            return self.runs.claim_next(s)

    def process(self, run_id: uuid.UUID) -> None:
        folder = self._folder(run_id)
        try:
            result = self.reconciler.run(folder / "bank.csv", folder / "ledger.csv")
            with db.session() as s:
                self.runs.save_exceptions(s, run_id, "bank", result.unmatched_bank)
                self.runs.save_exceptions(s, run_id, "ledger", result.unmatched_ledger)
                self.runs.complete(
                    s,
                    run_id,
                    {
                        "matched_exact": result.matched_exact,
                        "matched_fuzzy": result.matched_fuzzy,
                        "unmatched_bank": result.unmatched_bank.height,
                        "unmatched_ledger": result.unmatched_ledger.height,
                        "duration_ms": result.duration_ms,
                        "bank_rows": result.bank_rows,
                        "ledger_rows": result.ledger_rows,
                    },
                )
            logger.info("run_done", extra={"code": str(run_id)})
        except AppError as exc:  # a bad file: the uploader's problem, recorded on the run
            reason = f"{exc.message}: {exc.details}" if exc.details else exc.message
            self._fail(run_id, reason)
        except Exception as exc:
            logger.exception("run_failed", extra={"code": str(run_id)})
            self._fail(run_id, f"{type(exc).__name__}: {exc}")
        # reached only when the outcome is stored; if storing failed, the files stay for a retry
        self._remove_files(run_id)

    def housekeeping(self) -> tuple[int, int]:
        """Re-queue runs a crashed worker left RUNNING; delete runs past retention. Returns counts."""
        with db.session() as s:
            requeued = self.runs.requeue_stale(s, STALE_RUN_MINUTES)
            deleted = self.runs.delete_older_than(s, RETENTION_DAYS)
        for run_id in deleted:
            self._remove_files(run_id)
        if requeued or deleted:
            logger.info("housekeeping", extra={"code": f"requeued={requeued} deleted={len(deleted)}"})
        return requeued, len(deleted)

    # --- helpers -------------------------------------------------------------------
    def _queue(self, run_id: uuid.UUID, bank_name: str, ledger_name: str) -> RunOut:
        with db.session() as s:
            run = ReconRun(
                id=run_id, status=RunStatus.PENDING.value, bank_file=bank_name, ledger_file=ledger_name
            )
            self.runs.add_run(s, run)
            s.flush()
            logger.info("run_queued", extra={"code": str(run_id)})
            return RunOut.model_validate(run)

    def _fail(self, run_id: uuid.UUID, reason: str) -> None:
        with db.session() as s:
            self.runs.fail(s, run_id, reason)
        logger.warning("run_marked_failed", extra={"code": str(run_id), "error_message": reason[:300]})

    def _get_or_404(self, s, run_id: uuid.UUID) -> ReconRun:
        run = self.runs.get_run(s, run_id)
        if run is None:
            raise NotFoundError("Run not found", {"run_id": str(run_id)})
        return run

    def _check_headers(self, folder: Path) -> None:
        self.reconciler.check_columns(folder / "bank.csv", BANK_DATE_COLUMN)
        self.reconciler.check_columns(folder / "ledger.csv", LEDGER_DATE_COLUMN)

    def _folder(self, run_id: uuid.UUID) -> Path:
        return self.data_dir / "runs" / str(run_id)

    def _remove_files(self, run_id: uuid.UUID) -> None:
        try:
            shutil.rmtree(self._folder(run_id))
        except FileNotFoundError:
            pass
        except OSError:
            logger.exception("remove_files_failed", extra={"code": str(run_id)})


def _display_name(filename: str | None) -> str:
    return Path(filename or "upload.csv").name[:255]
