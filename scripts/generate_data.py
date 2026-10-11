"""Generate synthetic bank and ledger CSVs for Hisaab benchmarks and demos.

    python scripts/generate_data.py --rows 1000000 --out /tmp/hisaab-bench

Mix per row (deterministic for a given seed): ~93% exact matches, ~3% fuzzy matches (same amount,
booked 1 or 2 days apart under another reference), ~2% only in the bank, ~2% only in the ledger.
About one amount in five is written with thousands separators, to exercise comma parsing.
"""

import argparse
import csv
import json
import random
import sys
from datetime import date, timedelta
from pathlib import Path

START = date(2026, 1, 1)


def _amount(paise: int, rng: random.Random) -> str:
    rupees = paise / 100
    return f"{rupees:,.2f}" if rng.random() < 0.2 else f"{rupees:.2f}"


def generate(rows: int, out_dir: Path, seed: int = 42) -> dict[str, int]:
    rng = random.Random(seed)
    expected = {"exact": 0, "fuzzy": 0, "bank_only": 0, "ledger_only": 0}
    bank: list[tuple[str, str, str]] = []
    ledger: list[tuple[str, str, str]] = []
    for n in range(rows):
        day = START + timedelta(days=rng.randrange(270))
        paise = rng.randrange(100, 10_000_000)  # Rs 1 to Rs 1,00,000
        reference = f"UTR{n:010d}"
        kind = rng.random()
        if kind < 0.02:
            bank.append((day.isoformat(), _amount(paise, rng), reference))
            expected["bank_only"] += 1
        elif kind < 0.04:
            ledger.append((day.isoformat(), _amount(paise, rng), reference))
            expected["ledger_only"] += 1
        elif kind < 0.07:
            booked = day + timedelta(days=rng.choice([-2, -1, 1, 2]))
            bank.append((day.isoformat(), _amount(paise, rng), reference))
            ledger.append((booked.isoformat(), _amount(paise, rng), f"LDG{n:010d}"))
            expected["fuzzy"] += 1
        else:
            bank.append((day.isoformat(), _amount(paise, rng), reference))
            ledger.append((day.isoformat(), _amount(paise, rng), reference))
            expected["exact"] += 1
    rng.shuffle(ledger)  # a ledger is never in statement order

    out_dir.mkdir(parents=True, exist_ok=True)
    for name, header, data in (
        ("bank.csv", ["txn_date", "amount", "reference"], bank),
        ("ledger.csv", ["entry_date", "amount", "reference"], ledger),
    ):
        with (out_dir / name).open("w", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(header)
            writer.writerows(data)
    return expected


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--rows", type=int, default=1_000_000)
    parser.add_argument("--out", type=Path, default=Path("data"))
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    expected = generate(args.rows, args.out, args.seed)
    sys.stdout.write(json.dumps({"out": str(args.out), "expected": expected}) + "\n")


if __name__ == "__main__":
    main()
