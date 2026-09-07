"""
Phase 3: continuous background ingest worker.

This replaces the one-off ingest_batch.py script with a loop that runs
forever in a background thread, started by the FastAPI app on startup.
It reuses the exact same SourceProvider / fault_classifier logic from
Phases 1 and 2 -- only the "run once and exit" wrapper changes.

Runs in a plain Python thread (not asyncio) because cyndilib's
capture_video() calls are blocking, synchronous, native calls -- running
them on the asyncio event loop would freeze the whole API.
"""

import threading
import time

import redis
from cyndilib.finder import Finder

from fault_classifier import classify_source, worst_severity
from ingest_batch import (
    CyndilibSourceProvider,
    FRAMES_PER_SOURCE,
    REDIS_HOST,
    REDIS_PORT,
    REDIS_KEY_PREFIX,
    build_health_report,
    write_to_redis,
)
import db


CYCLE_PAUSE_SECONDS = 1.0  # brief pause between full discovery+capture cycles


class IngestWorker:
    def __init__(self):
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.redis_client = redis.Redis(
            host=REDIS_HOST, port=REDIS_PORT, decode_responses=True
        )

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)

    def _run_loop(self) -> None:
        print("[ingest_worker] Starting continuous ingest loop...")

        with Finder() as finder:
            finder.open()
            provider = CyndilibSourceProvider(finder)

            while not self._stop_event.is_set():
                try:
                    source_names = provider.discover_sources()

                    for source_name in source_names:
                        if self._stop_event.is_set():
                            break

                        frames = provider.capture_batch(source_name, FRAMES_PER_SOURCE)
                        report = build_health_report(
                            source_name, frames, FRAMES_PER_SOURCE
                        )
                        write_to_redis(self.redis_client, report)

                        severity = worst_severity(report.faults)
                        status = "healthy" if severity is None else severity.value

                        # Persist to Postgres: source status always, but
                        # fault EVENTS only when something was actually
                        # detected, so the history table reflects real
                        # incidents rather than a row every single cycle.
                        db.upsert_source(source_name, status)
                        db.insert_fault_events(source_name, report.faults)

                except Exception as exc:
                    # A single bad cycle (e.g. a source briefly vanishing)
                    # should not kill the whole background thread.
                    print(f"[ingest_worker] Error during cycle: {exc}")

                time.sleep(CYCLE_PAUSE_SECONDS)

        print("[ingest_worker] Stopped.")
