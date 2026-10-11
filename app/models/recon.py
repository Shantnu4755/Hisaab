import enum
import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import Base


class RunStatus(str, enum.Enum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"


class ReconRun(Base):
    __tablename__ = "recon_runs"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING', 'RUNNING', 'DONE', 'FAILED')", name="ck_recon_runs_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(16), default=RunStatus.PENDING.value, index=True)
    bank_file: Mapped[str] = mapped_column(String(255))  # original upload names, for display
    ledger_file: Mapped[str] = mapped_column(String(255))
    bank_rows: Mapped[int | None] = mapped_column(Integer)
    ledger_rows: Mapped[int | None] = mapped_column(Integer)
    matched_exact: Mapped[int | None] = mapped_column(Integer)
    matched_fuzzy: Mapped[int | None] = mapped_column(Integer)
    unmatched_bank: Mapped[int | None] = mapped_column(Integer)
    unmatched_ledger: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReconException(Base):
    """One unmatched row. Columns are nullable: a row with a blank cell can never match, so it lands here."""

    __tablename__ = "recon_exceptions"
    __table_args__ = (Index("ix_recon_exceptions_run_side_id", "run_id", "side", "id"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("recon_runs.id", ondelete="CASCADE"))
    side: Mapped[str] = mapped_column(String(8))  # bank | ledger
    txn_date: Mapped[date | None] = mapped_column(Date)
    amount_paise: Mapped[int | None] = mapped_column(BigInteger)
    reference: Mapped[str | None] = mapped_column(Text)
