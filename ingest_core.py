"""
Phase 5: cyndilib-independent ingest logic.

Split out of ingest_batch.py specifically so this module can be
imported -- and its logic fully tested -- in environments with no
cyndilib/NDI runtime at all, like GitHub Actions CI. Nothing in this
file touches cyndilib, NDI, or any hardware.

ingest_batch.py imports from here and adds the real CyndilibSourceProvider
implementation on top.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
import time

import redis as redis_lib
from fault_classifier import (
    FrameMetrics,
    FaultEvent,
    classify_source,
    worst_severity,
)


FRAMES_PER_SOURCE = 30          # how many frames to sample per source
CAPTURE_TIMEOUT_SECONDS = 2.0   # max wait per individual frame attempt
REDIS_HOST = "localhost"
REDIS_PORT = 6379
REDIS_KEY_PREFIX = "source_health"


@dataclass
class SourceHealthReport:
    source_name: str
    frames_attempted: int
    frames_captured: int
    resolution: str
    avg_interval_ms: float
    faults: list[FaultEvent]


class SourceProvider(ABC):
    """
    Abstraction over "where frames come from".

    CyndilibSourceProvider (in ingest_batch.py) is the real
    implementation. FixtureSourceProvider (in fixture_source_provider.py)
    replays recorded/synthetic data for tests and CI.
    """

    @abstractmethod
    def discover_sources(self) -> list[str]:
        ...

    @abstractmethod
    def capture_batch(self, source_name: str, num_frames: int) -> list[FrameMetrics]:
        ...


def build_health_report(source_name: str, frames: list[FrameMetrics],
                         frames_attempted: int) -> SourceHealthReport:
    """
    Bundles raw metrics with the classified faults for this source.
    Status is derived FROM the faults (worst_severity), not computed
    separately -- so there's a single source of truth for what counts
    as a problem, instead of two logic paths that could disagree.
    """
    faults = classify_source(source_name, frames, frames_attempted)

    if frames:
        resolution = f"{frames[-1].width}x{frames[-1].height}"
        if len(frames) >= 2:
            intervals = [
                (frames[i + 1].timestamp - frames[i].timestamp) * 1000
                for i in range(len(frames) - 1)
            ]
            avg_interval_ms = round(sum(intervals) / len(intervals), 2)
        else:
            avg_interval_ms = 0.0
    else:
        resolution = "unknown"
        avg_interval_ms = 0.0

    return SourceHealthReport(
        source_name=source_name,
        frames_attempted=frames_attempted,
        frames_captured=len(frames),
        resolution=resolution,
        avg_interval_ms=avg_interval_ms,
        faults=faults,
    )


def write_to_redis(client: "redis_lib.Redis", report: SourceHealthReport) -> None:
    severity = worst_severity(report.faults)
    status = "healthy" if severity is None else severity.value

    key = f"{REDIS_KEY_PREFIX}:{report.source_name}"
    client.hset(key, mapping={
        "status": status,
        "frames_attempted": report.frames_attempted,
        "frames_captured": report.frames_captured,
        "resolution": report.resolution,
        "avg_interval_ms": report.avg_interval_ms,
        "fault_count": len(report.faults),
        "last_checked": time.time(),
    })

    # Faults themselves go in a separate list key so history isn't lost
    # on the next overwrite of the summary hash above.
    faults_key = f"{REDIS_KEY_PREFIX}:{report.source_name}:faults"
    client.delete(faults_key)  # this run's faults replace the last run's
    for fault in report.faults:
        client.rpush(
            faults_key,
            f"[{fault.severity.value}] {fault.fault_type}: {fault.message}",
        )
