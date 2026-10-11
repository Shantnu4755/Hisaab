from pathlib import Path

import pytest

from app.exceptions import ValidationError
from app.services.reconciler import Reconciler
from scripts.generate_data import generate
from tests.helpers import BANK_HEADER, LEDGER_HEADER, write_csv


def _run(tmp_path: Path, bank_rows: list[str], ledger_rows: list[str], bank_header: str = BANK_HEADER):
    bank = write_csv(tmp_path / "bank.csv", bank_header, bank_rows)
    ledger = write_csv(tmp_path / "ledger.csv", LEDGER_HEADER, ledger_rows)
    return Reconciler().run(bank, ledger)


def _counts(result) -> tuple[int, int, int, int]:
    return (
        result.matched_exact,
        result.matched_fuzzy,
        result.unmatched_bank.height,
        result.unmatched_ledger.height,
    )


# --- PDF "Tests to write" ---------------------------------------------------------------


def test_exact_match(tmp_path):
    result = _run(tmp_path, ["2026-01-05,100.00,UTR1"], ["2026-01-05,100.00,UTR1"])
    assert _counts(result) == (1, 0, 0, 0)


@pytest.mark.parametrize(
    ("ledger_date", "fuzzy"),
    [("2026-01-07", 1), ("2026-01-03", 1), ("2026-01-08", 0), ("2026-01-02", 0)],
)
def test_date_off_by_two_days_matches_off_by_three_does_not(tmp_path, ledger_date, fuzzy):
    result = _run(tmp_path, ["2026-01-05,100.00,BANK-REF"], [f"{ledger_date},100.00,LEDGER-REF"])
    assert _counts(result) == (0, fuzzy, 1 - fuzzy, 1 - fuzzy)


def test_duplicate_ledger_rows_never_match_twice(tmp_path):
    exact = _run(tmp_path, ["2026-01-05,100.00,UTR1"], ["2026-01-05,100.00,UTR1", "2026-01-05,100.00,UTR1"])
    assert _counts(exact) == (1, 0, 0, 1)
    fuzzy = _run(tmp_path, ["2026-01-05,100.00,UTR1"], ["2026-01-06,100.00,X1", "2026-01-04,100.00,X2"])
    assert _counts(fuzzy) == (0, 1, 0, 1)


def test_missing_column_is_a_validation_error(tmp_path):
    with pytest.raises(ValidationError) as err:
        _run(tmp_path, ["2026-01-05,100.00"], [], bank_header="txn_date,amount")
    assert err.value.details["missing"] == ["reference"]


@pytest.mark.parametrize(
    ("bank_amount", "ledger_amount"), [('"1,23,456.78"', "123456.78"), ('"123,456.78"', "123456.78")]
)
def test_amounts_with_commas_parse(tmp_path, bank_amount, ledger_amount):
    result = _run(tmp_path, [f"2026-01-05,{bank_amount},UTR1"], [f"2026-01-05,{ledger_amount},UTR1"])
    assert _counts(result) == (1, 0, 0, 0)


# --- correctness details ---------------------------------------------------------------------


def test_repeated_rows_pair_one_to_one(tmp_path):
    row = "2026-01-05,100.00,NEFT"
    result = _run(tmp_path, [row, row, row], [row, row])
    assert _counts(result) == (2, 0, 1, 0)


def test_references_ignore_case_and_spaces(tmp_path):
    result = _run(tmp_path, ["2026-01-05,100.00, utr9 "], ["2026-01-05,100.00,UTR9"])
    assert result.matched_exact == 1


def test_money_is_stored_in_paise(tmp_path):
    result = _run(tmp_path, ['2026-01-05,"12,345.6",ONLY-BANK'], [])
    assert result.unmatched_bank.to_dicts()[0]["amount_paise"] == 1_234_560


@pytest.mark.parametrize("bad_row", ["05/01/2026,100.00,UTR1", "2026-01-05,abc,UTR1"])
def test_unparseable_values_are_a_validation_error(tmp_path, bad_row):
    with pytest.raises(ValidationError, match="CSV could not be parsed"):
        _run(tmp_path, [bad_row], [])


def test_blank_cells_end_up_unmatched(tmp_path):
    result = _run(tmp_path, [",100.00,UTR1", "2026-01-05,,UTR2"], ["2026-01-05,100.00,UTR1"])
    # the exact pass keys on reference + amount, so the blank-date UTR1 row still matches exactly;
    # the blank-amount row can match nothing
    assert _counts(result) == (1, 0, 1, 0)
    leftover = result.unmatched_bank.to_dicts()[0]
    assert (leftover["reference"], leftover["amount_paise"]) == ("UTR2", None)


def test_generated_data_reconciles_to_the_known_mix(tmp_path):
    expected = generate(3000, tmp_path, seed=7)
    result = Reconciler().run(tmp_path / "bank.csv", tmp_path / "ledger.csv")
    assert _counts(result) == (
        expected["exact"],
        expected["fuzzy"],
        expected["bank_only"],
        expected["ledger_only"],
    )
    assert result.bank_rows == expected["exact"] + expected["fuzzy"] + expected["bank_only"]
