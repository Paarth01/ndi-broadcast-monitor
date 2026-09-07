"""
Phase 1 + 2: batch ingest script (real NDI capture).

For each discovered NDI source, capture a batch of frames, run them
through the fault classifier, and write both the raw health metrics
and the classified faults into Redis.

The cyndilib-independent logic (SourceProvider, SourceHealthReport,
build_health_report, write_to_redis, constants) lives in ingest_core.py
so it can be imported and tested without cyndilib/NDI at all -- this
file only adds the real, hardware-dependent implementation on top.

Setup:
    pip install cyndilib redis
    (and have a local Redis server running -- see project README)

Run:
    python ingest_batch.py
"""

import time

from cyndilib.finder import Finder
from cyndilib.receiver import Receiver
from cyndilib.video_frame import VideoFrameSync
from cyndilib.wrapper.ndi_recv import RecvColorFormat, RecvBandwidth
import redis

from fault_classifier import FrameMetrics, worst_severity
from ingest_core import (
    SourceProvider,
    SourceHealthReport,
    FRAMES_PER_SOURCE,
    CAPTURE_TIMEOUT_SECONDS,
    REDIS_HOST,
    REDIS_PORT,
    REDIS_KEY_PREFIX,
    build_health_report,
    write_to_redis,
)


class CyndilibSourceProvider(SourceProvider):
    def __init__(self, finder: Finder):
        self._finder = finder

    def discover_sources(self) -> list[str]:
        self._finder.wait_for_sources(timeout=5.0)
        return self._finder.get_source_names()

    def capture_batch(self, source_name: str, num_frames: int) -> list[FrameMetrics]:
        source_obj = self._finder.get_source(source_name)

        # Build the receiver empty first, register a VideoFrameSync
        # explicitly, and only then attach the source. Passing the
        # source directly into the Receiver() constructor was found to
        # leave the connection in a bad state that caused a native
        # access-violation crash on the first capture_video() call --
        # this order matches cyndilib's own documented examples and
        # avoids that.
        receiver = Receiver(
            color_format=RecvColorFormat.RGBX_RGBA,
            bandwidth=RecvBandwidth.highest,
            recv_name=f"ingest_{source_name}",
        )
        video_frame = VideoFrameSync()
        receiver.frame_sync.set_video_frame(video_frame)
        receiver.set_source(source_obj)

        connect_deadline = time.time() + 15.0
        while not receiver.is_connected() and time.time() < connect_deadline:
            time.sleep(0.05)

        if not receiver.is_connected():
            # Source discoverable but not actually connectable -- treat
            # as zero frames captured rather than crashing or hanging.
            return []

        # Pace captures to the source's real frame rate. Without this,
        # capture_video() is called far faster than new frames actually
        # arrive, so we'd mostly re-read the same buffered frame -- which
        # looks like a perfect 0ms interval but isn't measuring anything
        # real, and would hide genuine frame drops from the classifier.
        frame_rate = video_frame.get_frame_rate()
        pace_seconds = (1.0 / frame_rate) if frame_rate > 0 else 0.033

        captured: list[FrameMetrics] = []
        for _ in range(num_frames):
            if not receiver.is_connected():
                break

            deadline = time.time() + CAPTURE_TIMEOUT_SECONDS
            got_frame = False

            while time.time() < deadline:
                receiver.frame_sync.capture_video()
                resolution = video_frame.get_resolution()

                if min(resolution) > 0 and video_frame.get_data_size() > 0:
                    captured.append(FrameMetrics(
                        timestamp=time.time(),
                        width=resolution[0],
                        height=resolution[1],
                    ))
                    got_frame = True
                    break

                time.sleep(0.02)

            if not got_frame:
                # This attempt failed -- counts against the drop rate,
                # but we keep going to try the remaining frames.
                continue

            # Wait roughly one frame's worth of time before the next
            # capture, so successive reads correspond to genuinely
            # distinct frames instead of the same buffered one.
            time.sleep(pace_seconds)

        return captured


def main() -> None:
    redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    try:
        redis_client.ping()
    except redis.exceptions.ConnectionError:
        print(f"Could not connect to Redis at {REDIS_HOST}:{REDIS_PORT}. "
              "Make sure a Redis server is running before retrying.")
        return

    with Finder() as finder:
        finder.open()
        provider = CyndilibSourceProvider(finder)

        print("Discovering NDI sources...")
        source_names = provider.discover_sources()

        if not source_names:
            print("No NDI sources found. Confirm OBS + DistroAV outputs "
                  "are active before running this script.")
            return

        print(f"Found {len(source_names)} source(s): {source_names}\n")

        for source_name in source_names:
            print(f"Capturing {FRAMES_PER_SOURCE} frames from: {source_name}")
            frames = provider.capture_batch(source_name, FRAMES_PER_SOURCE)
            report = build_health_report(source_name, frames, FRAMES_PER_SOURCE)
            write_to_redis(redis_client, report)

            severity = worst_severity(report.faults)
            status = "healthy" if severity is None else severity.value

            print(
                f"  status={status}  "
                f"captured={report.frames_captured}/{report.frames_attempted}  "
                f"resolution={report.resolution}  "
                f"avg_interval={report.avg_interval_ms}ms"
            )
            for fault in report.faults:
                print(f"    - [{fault.severity.value}] {fault.fault_type}: {fault.message}")
            print()

    print("Done. Health reports + faults written to Redis under "
          f"'{REDIS_KEY_PREFIX}:<source_name>' and "
          f"'{REDIS_KEY_PREFIX}:<source_name>:faults' keys.")


if __name__ == "__main__":
    main()
