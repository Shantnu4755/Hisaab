import uuid
from pathlib import Path

from app.services.run_service import RunService
from tests.helpers import BANK_HEADER, LEDGER_HEADER, upload

BANK = "\n".join(
    [BANK_HEADER, "2026-01-05,100.00,UTR1", "2026-01-06,200.00,UTR2", '2026-01-10,"1,500.00",UTR3']
)
LEDGER = "\n".join(
    [LEDGER_HEADER, "2026-01-05,100.00,UTR1", "2026-01-07,200.00,LDG2", "2026-02-01,999.00,LDG9"]
)


def _service(app) -> RunService:
    return RunService(Path(app.config["APP_CONFIG"].data_dir))


def _process_next(app) -> uuid.UUID:
    service = _service(app)
    run_id = service.claim_next()
    service.process(run_id)
    return run_id


def test_upload_queues_a_pending_run(client, app):
    resp = upload(client, BANK, LEDGER, bank_name="hdfc-jan.csv")
    assert resp.status_code == 202
    run = resp.get_json()["data"]
    assert (run["status"], run["bank_file"], run["ledger_file"]) == ("PENDING", "hdfc-jan.csv", "ledger.csv")
    folder = Path(app.config["APP_CONFIG"].data_dir) / "runs" / run["id"]
    assert (folder / "bank.csv").read_text() == BANK
    assert [r["id"] for r in client.get("/runs").get_json()["data"]] == [run["id"]]


def test_upload_needs_two_csv_files(client):
    resp = client.post("/runs", data={}, content_type="multipart/form-data")
    assert resp.status_code == 422
    assert upload(client, BANK, LEDGER, ledger_name="notes.txt").status_code == 422


def test_missing_column_returns_422_and_queues_nothing(client, app):
    runs_dir = Path(app.config["APP_CONFIG"].data_dir) / "runs"
    folders_before = set(runs_dir.iterdir()) if runs_dir.exists() else set()
    resp = upload(client, BANK, "entry_date,amount\n2026-01-05,100.00\n")
    assert resp.status_code == 422
    error = resp.get_json()["error"]
    assert error["message"] == "ledger.csv is missing columns"
    assert error["details"]["missing"] == ["reference"]
    assert client.get("/runs").get_json()["data"] == []
    assert set(runs_dir.iterdir()) == folders_before  # the rejected upload left no files behind


def test_upload_over_the_size_limit_is_a_json_413(client, app):
    app.config["MAX_CONTENT_LENGTH"] = 200
    try:
        resp = upload(client, BANK * 5, LEDGER)
    finally:
        app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024
    assert resp.status_code == 413
    assert resp.get_json()["error"]["code"] == "request_entity_too_large"


def test_end_to_end_run(client, app):
    run_id = upload(client, BANK, LEDGER).get_json()["data"]["id"]
    assert str(_process_next(app)) == run_id

    run = client.get(f"/runs/{run_id}").get_json()["data"]
    assert run["status"] == "DONE"
    assert (run["matched_exact"], run["matched_fuzzy"], run["unmatched_bank"], run["unmatched_ledger"]) == (
        1,
        1,
        1,
        1,
    )
    assert (run["bank_rows"], run["ledger_rows"]) == (3, 3)
    assert run["match_rate"] == 0.6667
    assert run["finished_at"] is not None
    assert not (Path(app.config["APP_CONFIG"].data_dir) / "runs" / run_id).exists()  # files removed

    bank_rows = client.get(f"/runs/{run_id}/exceptions?side=bank").get_json()["data"]
    assert bank_rows == [
        {"side": "bank", "txn_date": "2026-01-10", "amount_paise": 150000, "reference": "UTR3"}
    ]
    ledger_rows = client.get(f"/runs/{run_id}/exceptions?side=ledger&limit=1").get_json()["data"]
    assert [r["reference"] for r in ledger_rows] == ["LDG9"]
    assert client.get(f"/runs/{run_id}/exceptions?side=ledger&offset=1").get_json()["data"] == []

    csv = client.get(f"/runs/{run_id}/exceptions.csv?side=ledger")
    assert csv.mimetype == "text/csv"
    assert "attachment" in csv.headers["Content-Disposition"]
    assert csv.get_data(as_text=True).splitlines() == [
        "entry_date,amount,reference",
        "2026-02-01,999.00,LDG9",
    ]


def test_bad_rows_mark_the_run_failed(client, app):
    run_id = upload(client, BANK_HEADER + "\n05/01/2026,100.00,UTR1\n", LEDGER).get_json()["data"]["id"]
    _process_next(app)
    run = client.get(f"/runs/{run_id}").get_json()["data"]
    assert run["status"] == "FAILED"
    assert run["error"].startswith("CSV could not be parsed")
    assert run["match_rate"] is None


def test_unknown_run_and_bad_side(client):
    missing = uuid.uuid4()
    assert client.get(f"/runs/{missing}").status_code == 404
    assert client.get(f"/runs/{missing}/exceptions").status_code == 404
    assert client.get(f"/runs/{missing}/exceptions.csv").status_code == 404
    assert client.get(f"/runs/{missing}/exceptions?side=both").status_code == 422
