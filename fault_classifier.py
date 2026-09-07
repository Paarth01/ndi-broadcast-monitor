"""
Phase 2: Fault classification engine.

Takes the FrameMetrics captured by the ingest layer and classifies faults
into Priority 1/2/3 severity, following the same ETSI TR 101 290-style
model used in the original (simulated) Signal Health Monitor project.

Deliberately kept separate from cyndilib/NDI entirely -- this module only
knows about plain FrameMetrics data, so it can be fully unit-tested with
synthetic data and run in CI without any real NDI source.

Signal faults and timing faults are classified separately and then
merged, matching the "dedicated sync-health classifier isolating timing
faults from signal faults" design from the earlier project.
"""

import statistics
import time
from dataclasses import dataclass
from enum import Enum


class Severity(str, Enum):
    P1 = "P1"  # critical -- signal is fundamentally broken
    P2 = "P2"  # significant -- degraded but still delivering some signal
    P3 = "P3"  # minor -- worth logging, not urgent


@dataclass
class FrameMetrics:
    timestamp: float
    width: int
    height: int


@dataclass
class FaultEvent:
    source_name: str
    severity: Severity
    fault_type: str      # "source_loss" | "frame_drop" | "resolution_mismatch" | "timing_jitter"
    message: str
    detected_at: float


# Thresholds -- kept as named constants so they're easy to tune later
# based on real-world observation, rather than buried as magic numbers.
SEVERE_DROP_RATE = 0.5
MODERATE_DROP_RATE = 0.1

# Jitter severity is tiered by how large the worst gap is relative to
# the median interval. A single "2x = P2" threshold treats a 200ms
# hiccup the same as a multi-second stall, which hides genuinely
# critical stalls (e.g. the process being suspended) inside routine
# noise. P1 catches gaps large enough that the source was effectively
# unavailable for a real stretch of time, not just briefly late.
JITTER_RATIO_P1 = 20.0   # e.g. median ~34ms -> gap of 680ms+, or worse for slower sources
JITTER_RATIO_P2 = 2.0
JITTER_RATIO_P3 = 1.5
JITTER_ABSOLUTE_P1_MS = 2000.0  # any gap over 2 seconds is P1 regardless of ratio


def classify_signal_faults(source_name: str, frames: list[FrameMetrics],
                            frames_attempted: int) -> list[FaultEvent]:
    """
    Faults about *whether* frames arrived and *what* they contained:
    total loss, drop rate, and resolution stability.
    """
    now = time.time()
    faults: list[FaultEvent] = []

    if frames_attempted == 0:
        return faults

    if not frames:
        faults.append(FaultEvent(
            source_name=source_name,
            severity=Severity.P1,
            fault_type="source_loss",
            message="No frames received -- source is unreachable.",
            detected_at=now,
        ))
        return faults  # nothing else to check without any frames

    drop_rate = 1 - (len(frames) / frames_attempted)
    if drop_rate >= SEVERE_DROP_RATE:
        faults.append(FaultEvent(
            source_name=source_name,
            severity=Severity.P1,
            fault_type="frame_drop",
            message=f"Severe frame drop: {drop_rate:.0%} of attempted frames lost.",
            detected_at=now,
        ))
    elif drop_rate >= MODERATE_DROP_RATE:
        faults.append(FaultEvent(
            source_name=source_name,
            severity=Severity.P2,
            fault_type="frame_drop",
            message=f"Moderate frame drop: {drop_rate:.0%} of attempted frames lost.",
            detected_at=now,
        ))
    elif drop_rate > 0:
        faults.append(FaultEvent(
            source_name=source_name,
            severity=Severity.P3,
            fault_type="frame_drop",
            message=f"Minor frame drop: {drop_rate:.0%} of attempted frames lost.",
            detected_at=now,
        ))

    resolutions = {(f.width, f.height) for f in frames}
    if len(resolutions) > 1:
        res_list = ", ".join(f"{w}x{h}" for w, h in sorted(resolutions))
        faults.append(FaultEvent(
            source_name=source_name,
            severity=Severity.P2,
            fault_type="resolution_mismatch",
            message=f"Resolution changed mid-capture: saw {res_list}.",
            detected_at=now,
        ))

    return faults


def classify_timing_faults(source_name: str, frames: list[FrameMetrics]) -> list[FaultEvent]:
    """
    Faults about *when* frames arrived, independent of whether they
    arrived at all or what they contained. Kept separate from signal
    faults so timing instability doesn't get conflated with actual
    signal loss.
    """
    now = time.time()
    faults: list[FaultEvent] = []

    if len(frames) < 3:
        # Not enough samples to meaningfully judge timing consistency.
        return faults

    intervals_ms = [
        (frames[i + 1].timestamp - frames[i].timestamp) * 1000
        for i in range(len(frames) - 1)
    ]
    median_interval = statistics.median(intervals_ms)

    if median_interval <= 0:
        return faults

    max_interval = max(intervals_ms)
    ratio = max_interval / median_interval

    if max_interval >= JITTER_ABSOLUTE_P1_MS or ratio >= JITTER_RATIO_P1:
        faults.append(FaultEvent(
            source_name=source_name,
            severity=Severity.P1,
            fault_type="timing_jitter",
            message=(
                f"Critical stall: worst gap was {max_interval:.1f}ms "
                f"vs a {median_interval:.1f}ms median interval -- source was "
                f"effectively unavailable for a real stretch of time, not "
                f"just briefly late."
            ),
            detected_at=now,
        ))
    elif ratio >= JITTER_RATIO_P2:
        faults.append(FaultEvent(
            source_name=source_name,
            severity=Severity.P2,
            fault_type="timing_jitter",
            message=(
                f"Significant timing jitter: worst gap was {max_interval:.1f}ms "
                f"vs a {median_interval:.1f}ms median interval."
            ),
            detected_at=now,
        ))
    elif ratio >= JITTER_RATIO_P3:
        faults.append(FaultEvent(
            source_name=source_name,
            severity=Severity.P3,
            fault_type="timing_jitter",
            message=(
                f"Minor timing jitter: worst gap was {max_interval:.1f}ms "
                f"vs a {median_interval:.1f}ms median interval."
            ),
            detected_at=now,
        ))

    return faults


def classify_source(source_name: str, frames: list[FrameMetrics],
                     frames_attempted: int) -> list[FaultEvent]:
    """
    Full classification for one source: signal faults + timing faults,
    combined. This is the main entry point the ingest service calls.
    """
    return (
        classify_signal_faults(source_name, frames, frames_attempted)
        + classify_timing_faults(source_name, frames)
    )


def worst_severity(faults: list[FaultEvent]) -> Severity | None:
    """Returns the highest-priority (most severe) fault present, if any."""
    if not faults:
        return None
    order = {Severity.P1: 0, Severity.P2: 1, Severity.P3: 2}
    return min((f.severity for f in faults), key=lambda s: order[s])
