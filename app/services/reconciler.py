import time
from dataclasses import dataclass
from pathlib import Path

import polars as pl

from ..exceptions import ValidationError

BANK_DATE_COLUMN = "txn_date"
LEDGER_DATE_COLUMN = "entry_date"


@dataclass(frozen=True)
class ReconResult:
    matched_exact: int
    matched_fuzzy: int
    unmatched_bank: pl.DataFrame
    unmatched_ledger: pl.DataFrame
    duration_ms: int
    bank_rows: int
    ledger_rows: int


class Reconciler:
    """Pass 1: same reference and amount. Pass 2: same amount, date within N days."""

    def __init__(self, date_tolerance_days: int = 2) -> None:
        self.tolerance = f"{date_tolerance_days}d"

    @staticmethod
    def check_columns(path: Path, date_col: str) -> None:
        """Reads only the header, so the API can reject a bad file before queueing a run."""
        required = {date_col, "amount", "reference"}
        try:
            header = set(pl.read_csv(path, n_rows=0).columns)
        except pl.exceptions.PolarsError as exc:
            raise ValidationError(f"{path.name} could not be parsed", {"reason": str(exc)[:300]}) from exc
        if not required <= header:
            raise ValidationError(
                f"{path.name} is missing columns",
                {"missing": sorted(required - header), "expected": sorted(required)},
            )

    def _load(self, path: Path, date_col: str) -> pl.LazyFrame:
        required = {date_col, "amount", "reference"}
        self.check_columns(path, date_col)
        return (
            pl.scan_csv(path, schema_overrides={c: pl.Utf8 for c in required})
            .select(
                pl.col(date_col).str.strip_chars().str.to_date("%Y-%m-%d").alias("txn_date"),
                # money as integer paise: no floating point errors in comparisons
                (pl.col("amount").str.replace_all(",", "").cast(pl.Float64) * 100)
                .round(0)
                .cast(pl.Int64)
                .alias("amount_paise"),
                pl.col("reference").str.strip_chars().str.to_uppercase().alias("reference"),
            )
            .with_row_index("row_id")
        )

    def run(self, bank_path: Path, ledger_path: Path) -> ReconResult:
        started = time.perf_counter()
        try:
            bank = self._load(bank_path, BANK_DATE_COLUMN).collect()
            ledger = self._load(ledger_path, LEDGER_DATE_COLUMN).collect()
        except pl.exceptions.PolarsError as exc:
            raise ValidationError("CSV could not be parsed", {"reason": str(exc)[:300]}) from exc

        # Pass 1: exact match, one-to-one. Repeats of the same (reference, amount) are numbered on
        # both sides and joined on that number too, so n identical rows pair with n identical rows
        # deterministically, without an n x m join.
        # Only keys and row ids go into the join: carrying every column through ~1M matched pairs
        # roughly doubled peak memory.
        keys = ["reference", "amount_paise"]
        occurrence = pl.int_range(pl.len()).over(keys).alias("occurrence")
        exact = (
            bank.select("row_id", *keys)
            .with_columns(occurrence)
            .join(
                ledger.select("row_id", *keys).with_columns(occurrence),
                on=[*keys, "occurrence"],
                how="inner",
                suffix="_l",
            )
            .select("row_id", "row_id_l")
        )
        bank_left = bank.join(exact.select("row_id"), on="row_id", how="anti")
        ledger_left = ledger.join(exact.select(pl.col("row_id_l").alias("row_id")), on="row_id", how="anti")

        # Pass 2: same amount, nearest date within tolerance (rows with a blank date or amount cannot match)
        matchable = pl.col("txn_date").is_not_null() & pl.col("amount_paise").is_not_null()
        fuzzy = (
            bank_left.filter(matchable)
            .sort("txn_date")
            .join_asof(
                ledger_left.filter(matchable).sort("txn_date"),
                on="txn_date",
                by="amount_paise",
                strategy="nearest",
                tolerance=self.tolerance,
                suffix="_l",
                check_sortedness=False,  # sorted above; polars cannot verify it per `by` group
            )
            .filter(pl.col("row_id_l").is_not_null())
            .unique(subset=["row_id_l"], keep="first", maintain_order=True)
        )
        bank_final = bank_left.join(fuzzy.select("row_id"), on="row_id", how="anti")
        ledger_final = ledger_left.join(
            fuzzy.select(pl.col("row_id_l").alias("row_id")), on="row_id", how="anti"
        )

        return ReconResult(
            matched_exact=exact.height,
            matched_fuzzy=fuzzy.height,
            unmatched_bank=bank_final.sort("row_id").drop("row_id"),
            unmatched_ledger=ledger_final.sort("row_id").drop("row_id"),
            duration_ms=int((time.perf_counter() - started) * 1000),
            bank_rows=bank.height,
            ledger_rows=ledger.height,
        )
