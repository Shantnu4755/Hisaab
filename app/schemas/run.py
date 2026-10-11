import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field

from ..models.recon import RunStatus

Side = Literal["bank", "ledger"]


class RunFilter(BaseModel):
    limit: int = Field(default=20, ge=1, le=100)
    offset: int = Field(default=0, ge=0)


class ExceptionFilter(BaseModel):
    side: Side = "bank"
    limit: int = Field(default=100, ge=1, le=10_000)  # the UI's virtualized table pulls big pages
    offset: int = Field(default=0, ge=0)


class ExportFilter(BaseModel):
    side: Side = "bank"


class RunOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    status: RunStatus
    bank_file: str
    ledger_file: str
    bank_rows: int | None
    ledger_rows: int | None
    matched_exact: int | None
    matched_fuzzy: int | None
    unmatched_bank: int | None
    unmatched_ledger: int | None
    duration_ms: int | None
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @computed_field
    @property
    def match_rate(self) -> float | None:
        """Share of bank rows that found a ledger row."""
        if not self.bank_rows or self.matched_exact is None or self.matched_fuzzy is None:
            return None
        return round((self.matched_exact + self.matched_fuzzy) / self.bank_rows, 4)

    @computed_field
    @property
    def rows_per_second(self) -> int | None:
        if not self.duration_ms or self.bank_rows is None or self.ledger_rows is None:
            return None
        return int((self.bank_rows + self.ledger_rows) / (self.duration_ms / 1000))


class ExceptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    side: Side
    txn_date: date | None
    amount_paise: int | None
    reference: str | None
