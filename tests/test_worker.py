import threading
import uuid
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import text

from app.extensions import db
from app.models.recon import ReconRun
from app.services.run_service import RunService
from app.worker.worker import ReconWorker
from tests.helpers import BANK_HEADER, LEDGER_HEADER, upload, write_csv


def _service(app) -> RunService:
    return RunService(Path(app.config["APP_CONFIG"].data_dir))


def _status(run_id) -> str:
    with db.session() as s:
        return s.execute(text("SELECT status FROM recon_runs WHERE id = :id"), {"id": run_id}).scalar_one()


def test_worker_processes_a_run_then_stops(client, app):
    resp = upload(client, f"{BANK_HEADER}\n2026-01-05,1.00,A", f"{LEDGER_HEADER}\n2026-01-05,1.00,A")
    run_id = resp.get_json()["data"]["id"]
    service = _service(app)
    worker = ReconWorker(service, poll_interval=0.01)
    real_process = service.process

    def process_then_stop(run_uuid):
        real_process(run_uuid)
        worker._request_stop()

    with (
        patch.object(service, "process", side_effect=process_then_stop),
        patch("app.worker.worker.signal.signal"),
    ):
        worker.run()
    assert _status(run_id) == "DONE"


def test_two_workers_never_claim_the_same_run(app):
    with db.session() as s:
        for n in range(8):
            s.add(ReconRun(bank_file=f"b{n}.csv", ledger_file=f"l{n}.csv"))
    service = _service(app)
    claimed: list[list[uuid.UUID]] = [[], []]
    barrier = threading.Barrier(2)

    def drain(bucket: list[uuid.UUID]) -> None:
        barrier.wait()
        while (run_id := service.claim_next()) is not None:
            bucket.append(run_id)

    threads = [threading.Thread(target=drain, args=(bucket,)) for bucket in claimed]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert set(claimed[0]).isdisjoint(claimed[1])
    assert len(claimed[0]) + len(claimed[1]) == 8


def test_housekeeping_requeues_stale_runs_and_deletes_old_ones(app):
    with db.session() as s:
        stale = ReconRun(bank_file="b.csv", ledger_file="l.csv", status="RUNNING")
        busy = ReconRun(bank_file="b.csv", ledger_file="l.csv", status="RUNNING")
        old = ReconRun(bank_file="b.csv", ledger_file="l.csv", status="DONE")
        s.add_all([stale, busy, old])
        s.flush()
        s.execute(
            text("UPDATE recon_runs SET started_at = now() - interval '31 minutes' WHERE id = :id"),
            {"id": stale.id},
        )
        s.execute(
            text("UPDATE recon_runs SET started_at = now() - interval '5 minutes' WHERE id = :id"),
            {"id": busy.id},
        )
        s.execute(
            text("UPDATE recon_runs SET created_at = now() - interval '8 days' WHERE id = :id"),
            {"id": old.id},
        )
        s.execute(
            text("INSERT INTO recon_exceptions (run_id, side, reference) VALUES (:id, 'bank', 'X')"),
            {"id": old.id},
        )
        ids = (stale.id, busy.id, old.id)
    assert _service(app).housekeeping() == (1, 1)
    assert (_status(ids[0]), _status(ids[1])) == ("PENDING", "RUNNING")
    with db.session() as s:
        assert s.execute(text("SELECT count(*) FROM recon_exceptions")).scalar_one() == 0  # cascade


def test_worker_survives_errors(app):
    service = _service(app)
    worker = ReconWorker(service, poll_interval=0.01)
    worker._stopping.set()  # makes the 5 s back-off return immediately
    with patch.object(service, "claim_next", side_effect=RuntimeError("db down")):
        assert worker._claim() is None
    with patch.object(service, "housekeeping", side_effect=RuntimeError("db down")):
        worker._housekeeping()  # logged, not raised

    worker._stopping.clear()
    run_id = uuid.uuid4()
    calls = {"n": 0}

    def claim():
        calls["n"] += 1
        if calls["n"] > 1:
            worker._request_stop()
            return None
        return run_id

    with (
        patch.object(service, "claim_next", side_effect=claim),
        patch.object(service, "process", side_effect=RuntimeError("disk full")),
        patch("app.worker.worker.signal.signal"),
    ):
        worker.run()  # the failing run is logged and the loop carries on
    assert calls["n"] == 2


def test_cli_queues_a_run_from_local_files(app, tmp_path, monkeypatch):
    from app import cli

    bank = write_csv(tmp_path / "big-bank.csv", BANK_HEADER, ["2026-01-05,1.00,A"])
    ledger = write_csv(tmp_path / "big-ledger.csv", LEDGER_HEADER, ["2026-01-05,1.00,A"])
    monkeypatch.setenv("DATABASE_URL", str(db.engine.url.render_as_string(hide_password=False)))
    monkeypatch.setenv("DATA_DIR", app.config["APP_CONFIG"].data_dir)
    cli.main(["create-run", "--bank", str(bank), "--ledger", str(ledger)])
    with db.session() as s:
        row = s.execute(text("SELECT status, bank_file FROM recon_runs")).one()
    assert tuple(row) == ("PENDING", "big-bank.csv")


def test_worker_entry_point(monkeypatch):
    from app.worker import __main__ as entry

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@localhost/db")
    with patch.object(entry, "db") as fake_db, patch.object(entry, "ReconWorker") as fake_worker:
        entry.main()
    fake_db.init_app.assert_called_once()
    fake_worker.return_value.run.assert_called_once()
