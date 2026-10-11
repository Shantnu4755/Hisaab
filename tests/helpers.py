import io
from pathlib import Path

BANK_HEADER = "txn_date,amount,reference"
LEDGER_HEADER = "entry_date,amount,reference"


def write_csv(path: Path, header: str, rows: list[str]) -> Path:
    path.write_text("\n".join([header, *rows]) + "\n")
    return path


def upload(client, bank: str, ledger: str, bank_name: str = "bank.csv", ledger_name: str = "ledger.csv"):
    data = {
        "bank_file": (io.BytesIO(bank.encode()), bank_name),
        "ledger_file": (io.BytesIO(ledger.encode()), ledger_name),
    }
    return client.post("/runs", data=data, content_type="multipart/form-data")
