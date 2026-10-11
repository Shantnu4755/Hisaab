import uuid
from pathlib import Path

from flask import Response, request, stream_with_context

from ..exceptions import ValidationError
from ..schemas.run import ExceptionFilter, ExportFilter, RunFilter
from ..services.run_service import RunService
from .base import BaseView


class RunListView(BaseView):
    def __init__(self, data_dir: str) -> None:
        self.service = RunService(Path(data_dir))

    def get(self):
        return {
            "data": [r.model_dump(mode="json") for r in self.service.recent(self.parse_query(RunFilter))]
        }, 200

    def post(self):
        bank = request.files.get("bank_file")
        ledger = request.files.get("ledger_file")
        if not bank or not ledger:
            raise ValidationError("Both bank_file and ledger_file are required")
        for f in (bank, ledger):
            if not (f.filename or "").lower().endswith(".csv"):
                raise ValidationError(f"{f.filename} is not a .csv file")
        run = self.service.create(bank, ledger)
        return {"data": run.model_dump(mode="json")}, 202


class RunDetailView(BaseView):
    def __init__(self, data_dir: str) -> None:
        self.service = RunService(Path(data_dir))

    def get(self, run_id: uuid.UUID):
        return {"data": self.service.get(run_id).model_dump(mode="json")}, 200


class RunExceptionsView(BaseView):
    def __init__(self, data_dir: str) -> None:
        self.service = RunService(Path(data_dir))

    def get(self, run_id: uuid.UUID):
        rows = self.service.exceptions(run_id, self.parse_query(ExceptionFilter))
        return {"data": [r.model_dump(mode="json") for r in rows]}, 200


class RunExceptionsCsvView(BaseView):
    def __init__(self, data_dir: str) -> None:
        self.service = RunService(Path(data_dir))

    def get(self, run_id: uuid.UUID):
        side = self.parse_query(ExportFilter).side
        lines = self.service.export_csv(run_id, side)
        filename = f"hisaab-{str(run_id)[:8]}-{side}-exceptions.csv"
        return Response(
            stream_with_context(lines),
            mimetype="text/csv",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
