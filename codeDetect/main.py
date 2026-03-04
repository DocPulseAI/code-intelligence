import asyncio
import atexit
import json
import logging
import os
import signal
import threading
from typing import Any

from flask import Flask, jsonify

from service_bus import Epic1ServiceBusWorker

LOG = logging.getLogger("epic1.main")
if not logging.getLogger().handlers:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


async def process_message(payload: dict) -> dict:
    """
    EPIC-1 business logic stub.
    Replace this body with real impact analysis.
    """
    return {
        "epic": "epic1",
        "status": "processed",
        "input": payload,
    }


class BackgroundAsyncRunner:
    def __init__(self, worker: Epic1ServiceBusWorker) -> None:
        self.worker = worker
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stop_event: asyncio.Event | None = None
        self._started = threading.Event()
        self._lock = threading.Lock()

    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return

            self._thread = threading.Thread(target=self._run_loop, name="epic1-servicebus-listener", daemon=True)
            self._thread.start()
            self._started.wait(timeout=10)
            LOG.info(json.dumps({"event": "background_runner_started"}, separators=(",", ":")))

    def _run_loop(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._stop_event = asyncio.Event()
        self._started.set()

        try:
            self._loop.run_until_complete(self.worker.run(self._stop_event))
        except Exception as exc:
            LOG.error(
                json.dumps({"event": "background_runner_crashed", "error": str(exc)}, separators=(",", ":"))
            )
        finally:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            self._loop.close()

    def stop(self, timeout: float = 30.0) -> None:
        with self._lock:
            if not self._thread:
                return

            if self._loop and self._stop_event:
                self._loop.call_soon_threadsafe(self._stop_event.set)

            self._thread.join(timeout=timeout)
            if self._thread.is_alive():
                LOG.warning(json.dumps({"event": "background_runner_stop_timeout"}, separators=(",", ":")))
            else:
                LOG.info(json.dumps({"event": "background_runner_stopped"}, separators=(",", ":")))

            self._thread = None
            self._loop = None
            self._stop_event = None
            self._started.clear()


app = Flask(__name__)
worker = Epic1ServiceBusWorker(process_message=process_message)
runner = BackgroundAsyncRunner(worker)
_listener_started = False


def start_listener() -> None:
    global _listener_started
    if _listener_started:
        return
    runner.start()
    _listener_started = True


@app.before_request
def ensure_listener_started() -> None:
    start_listener()


@app.get("/health")
def health() -> Any:
    return jsonify({"status": "ok", "service": "epic1-consumer"}), 200


@app.get("/")
def root() -> Any:
    consume_queue = os.getenv("EPIC1_QUEUE_NAME", "epic1-impact").strip() or "epic1-impact"
    produce_queue = os.getenv("NEXT_QUEUE_NAME", "epic2-generate").strip() or "epic2-generate"
    return jsonify(
        {
            "service": "DocPulse EPIC-1",
            "queues": {
                "consume": consume_queue,
                "produce": produce_queue,
            },
        }
    ), 200


def _shutdown_handler(signum: int, _frame: Any) -> None:
    LOG.info(json.dumps({"event": "signal_received", "signal": signum}, separators=(",", ":")))
    runner.stop()


signal.signal(signal.SIGTERM, _shutdown_handler)
signal.signal(signal.SIGINT, _shutdown_handler)
atexit.register(runner.stop)


if __name__ == "__main__":
    # Container-friendly default bind/port.
    app.run(host="0.0.0.0", port=8000)
