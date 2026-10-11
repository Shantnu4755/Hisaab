import logging
import signal
import threading

from ..liveness import mark_alive
from ..services.run_service import RunService

logger = logging.getLogger(__name__)


class ReconWorker:
    """Claims PENDING runs one at a time (SKIP LOCKED, so several workers can run safely)."""

    HOUSEKEEPING_EVERY = 600  # loop iterations: about every 10 minutes when idle

    def __init__(self, service: RunService, poll_interval: float = 1.0) -> None:
        self.service = service
        self.poll_interval = poll_interval
        self._stopping = threading.Event()

    def run(self) -> None:
        signal.signal(signal.SIGTERM, self._request_stop)
        signal.signal(signal.SIGINT, self._request_stop)
        logger.info("worker_started")
        loops = 0
        while not self._stopping.is_set():
            mark_alive()
            if loops % self.HOUSEKEEPING_EVERY == 0:
                self._housekeeping()
            loops += 1
            run_id = self._claim()
            if run_id is None:
                self._stopping.wait(self.poll_interval)
                continue
            try:
                self.service.process(run_id)
            except Exception:
                logger.exception(
                    "process_failed", extra={"code": str(run_id)}
                )  # left RUNNING; re-queued later
        logger.info("worker_stopped")

    def _claim(self):
        try:
            return self.service.claim_next()
        except Exception:
            logger.exception("claim_failed")
            self._stopping.wait(5)  # database hiccup: back off, do not crash
            return None

    def _housekeeping(self) -> None:
        try:
            self.service.housekeeping()
        except Exception:
            logger.exception("housekeeping_failed")

    def _request_stop(self, *_: object) -> None:
        logger.info("shutdown_requested")
        self._stopping.set()  # the current run finishes, then the loop exits
