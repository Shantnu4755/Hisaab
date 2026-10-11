from flask import Flask

from .health import HealthView
from .runs import RunDetailView, RunExceptionsCsvView, RunExceptionsView, RunListView


def register_routes(app: Flask) -> None:
    app.add_url_rule("/health", view_func=HealthView.as_view("health"))
    # project-specific routes are added below this line
    data_dir = app.config["APP_CONFIG"].data_dir
    app.add_url_rule("/runs", view_func=RunListView.as_view("runs", data_dir=data_dir))
    app.add_url_rule("/runs/<uuid:run_id>", view_func=RunDetailView.as_view("run_detail", data_dir=data_dir))
    app.add_url_rule(
        "/runs/<uuid:run_id>/exceptions",
        view_func=RunExceptionsView.as_view("run_exceptions", data_dir=data_dir),
    )
    app.add_url_rule(
        "/runs/<uuid:run_id>/exceptions.csv",
        view_func=RunExceptionsCsvView.as_view("run_exceptions_csv", data_dir=data_dir),
    )
