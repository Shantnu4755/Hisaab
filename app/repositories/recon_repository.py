from __future__ import annotations  # keeps annotations lazy (see Karya: methods named like builtins)

import uuid
from collections.abc import Iterator

import polars as pl
from sqlalchemy import delete, func, insert, select, text, update
from sqlalchemy.orm import Session

from ..models.recon import ReconException, ReconRun, RunStatus

BATCH = 5000

CLAIM_SQL = text("""
    UPDATE recon_runs SET status = 'RUNNING', started_at = now()
     WHERE id = (
            SELECT id FROM recon_runs
             WHERE status = 'PENDING'
             ORDER BY created_at
             LIMIT 1
             FOR UPDATE SKIP LOCKED
     )
 RETURNING id
""")


class ReconRepository:
    def save_exceptions(self, s: Session, run_id: uuid.UUID, side: str, frame: pl.DataFrame) -> None:
        for chunk in frame.iter_slices(n_rows=BATCH):
            rows = [dict(r, run_id=run_id, side=side) for r in chunk.to_dicts()]
            if rows:
                s.execute(insert(ReconException), rows)

    def add_run(self, s: Session, run: ReconRun) -> None:
        s.add(run)

    def get_run(self, s: Session, run_id: uuid.UUID) -> ReconRun | None:
        return s.get(ReconRun, run_id)

    def recent_runs(self, s: Session, limit: int, offset: int) -> list[ReconRun]:
        stmt = select(ReconRun).order_by(ReconRun.created_at.desc()).limit(limit).offset(offset)
        return list(s.execute(stmt).scalars())

    def claim_next(self, s: Session) -> uuid.UUID | None:
        return s.execute(CLAIM_SQL).scalar_one_or_none()

    def complete(self, s: Session, run_id: uuid.UUID, summary: dict) -> None:
        values = {**summary, "status": RunStatus.DONE.value, "finished_at": func.now(), "error": None}
        s.execute(update(ReconRun).where(ReconRun.id == run_id).values(**values))

    def fail(self, s: Session, run_id: uuid.UUID, error: str) -> None:
        values = {"status": RunStatus.FAILED.value, "finished_at": func.now(), "error": error[:1000]}
        s.execute(update(ReconRun).where(ReconRun.id == run_id).values(**values))

    def exceptions(
        self, s: Session, run_id: uuid.UUID, side: str, limit: int, offset: int
    ) -> list[ReconException]:
        stmt = (
            select(ReconException)
            .where(ReconException.run_id == run_id, ReconException.side == side)
            .order_by(ReconException.id)
            .limit(limit)
            .offset(offset)
        )
        return list(s.execute(stmt).scalars())

    def iter_exceptions(self, s: Session, run_id: uuid.UUID, side: str) -> Iterator[ReconException]:
        """Server-side cursor: streams any number of rows in constant memory."""
        stmt = (
            select(ReconException)
            .where(ReconException.run_id == run_id, ReconException.side == side)
            .order_by(ReconException.id)
            .execution_options(yield_per=BATCH)
        )
        yield from s.scalars(stmt)

    def requeue_stale(self, s: Session, minutes: int) -> int:
        """Runs left RUNNING by a crashed worker go back to PENDING (a run saves in one transaction)."""
        stmt = (
            update(ReconRun)
            .where(
                ReconRun.status == RunStatus.RUNNING.value,
                ReconRun.started_at < func.now() - func.make_interval(0, 0, 0, 0, 0, minutes),
            )
            .values(status=RunStatus.PENDING.value, started_at=None)
        )
        return s.execute(stmt).rowcount

    def delete_older_than(self, s: Session, days: int) -> list[uuid.UUID]:
        """Retention for the free VM's disk; exceptions go with their run (ON DELETE CASCADE)."""
        stmt = (
            delete(ReconRun)
            .where(ReconRun.created_at < func.now() - func.make_interval(0, 0, 0, days))
            .returning(ReconRun.id)
        )
        return list(s.execute(stmt).scalars())
