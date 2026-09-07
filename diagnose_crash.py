"""
Diagnostic script -- prints progress at every single step so we can see
EXACTLY where the crash happens. Run this instead of ingest_batch.py
for now.
"""

import sys
import time

from cyndilib.finder import Finder
from cyndilib.receiver import Receiver
from cyndilib.video_frame import VideoFrameSync
from cyndilib.wrapper.ndi_recv import RecvColorFormat, RecvBandwidth


def log(msg: str) -> None:
    print(msg, flush=True)


def main() -> None:
    with Finder() as finder:
        finder.open()
        log("Finder opened.")

        finder.wait_for_sources(timeout=5.0)
        source_names = finder.get_source_names()
        log(f"Sources found: {source_names}")

        if not source_names:
            log("No sources -- stopping.")
            return

        source_name = source_names[0]
        log(f"Using source: {source_name}")

        source_obj = finder.get_source(source_name)
        log("Got source object.")

        receiver = Receiver(
            color_format=RecvColorFormat.RGBX_RGBA,
            bandwidth=RecvBandwidth.highest,
            recv_name="diagnostic_receiver",
        )
        log("Receiver created (no source yet).")

        video_frame = VideoFrameSync()
        receiver.frame_sync.set_video_frame(video_frame)
        log("VideoFrameSync created and registered.")

        receiver.set_source(source_obj)
        log("Source set on receiver -- connection should now begin.")

        log("Waiting for receiver to connect...")
        connect_deadline = time.time() + 15.0
        while not receiver.is_connected() and time.time() < connect_deadline:
            time.sleep(0.05)

        if not receiver.is_connected():
            log("Receiver never connected -- stopping before capture_video().")
            return

        log("Receiver is connected.")
        log("Using registered video_frame reference.")

        for i in range(10):
            if not receiver.is_connected():
                log(f"Receiver disconnected before iteration {i} -- stopping.")
                break

            log(f"--- Loop iteration {i} ---")
            receiver.frame_sync.capture_video()
            log(f"  capture_video() call {i} returned.")

            resolution = video_frame.get_resolution()
            log(f"  resolution: {resolution}")

            data_size = video_frame.get_data_size()
            log(f"  data_size: {data_size}")

            time.sleep(0.1)

        log("Loop finished without crashing.")


if __name__ == "__main__":
    main()
