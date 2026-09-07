"""
Phase 5: fixture-based SourceProvider for tests and CI.

Implements the same SourceProvider interface as CyndilibSourceProvider,
but replays canned/synthetic FrameMetrics instead of talking to real
NDI hardware. This is what lets the ingest pipeline (build_health_report
+ write_to_redis) be tested end-to-end in GitHub Actions, which has no
NDI runtime and no real sources at all.
"""

from fault_classifier import FrameMetrics
from ingest_core import SourceProvider


class FixtureSourceProvider(SourceProvider):
    """
    scenarios: dict mapping source_name -> list[FrameMetrics] to return
    when that source is captured. discover_sources() returns the dict's
    keys.
    """

    def __init__(self, scenarios: dict[str, list[FrameMetrics]]):
        self._scenarios = scenarios

    def discover_sources(self) -> list[str]:
        return list(self._scenarios.keys())

    def capture_batch(self, source_name: str, num_frames: int) -> list[FrameMetrics]:
        frames = self._scenarios.get(source_name, [])
        return frames[:num_frames]


def make_healthy_frames(count: int = 30, start_time: float = 1000.0,
                         interval: float = 0.033, width: int = 1280,
                         height: int = 720) -> list[FrameMetrics]:
    """A clean, evenly-paced scenario -- what a healthy source looks like."""
    return [
        FrameMetrics(timestamp=start_time + i * interval, width=width, height=height)
        for i in range(count)
    ]


def make_degraded_frames(count: int = 20, start_time: float = 1000.0,
                          interval: float = 0.033) -> list[FrameMetrics]:
    """Fewer frames than a full batch -- simulates real frame drops."""
    return make_healthy_frames(count=count, start_time=start_time, interval=interval)
