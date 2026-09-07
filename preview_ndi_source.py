"""
Phase 0 checkpoint (visual confirmation).

Goal: pull one actual video frame from the first discovered NDI source
and save it as a .jpg -- so you can open it and SEE whether it's really
your phone's camera feed, rather than just trusting a source name.

Setup before running this:
    pip install cyndilib pillow

Run:
    python preview_ndi_source.py

Output: a file named "ndi_preview.jpg" in the same folder. Open it to
confirm it shows your phone's camera view.
"""

import sys
import time

from cyndilib.finder import Finder
from cyndilib.receiver import Receiver
from cyndilib.video_frame import VideoFrameSync
from cyndilib.wrapper.ndi_recv import RecvColorFormat, RecvBandwidth
from PIL import Image


OUTPUT_FILE = "ndi_preview.jpg"


def wait_for_first_frame(receiver: Receiver, video_frame: VideoFrameSync,
                          timeout_seconds: float = 10.0) -> bool:
    """
    The first few frames from an NDI receiver often contain no data yet
    while the connection warms up. Keep capturing until we get a frame
    with real pixel data, or give up after timeout_seconds.
    """
    deadline = time.time() + timeout_seconds

    while time.time() < deadline:
        receiver.frame_sync.capture_video()
        resolution = video_frame.get_resolution()

        if min(resolution) > 0 and video_frame.get_data_size() > 0:
            return True

        time.sleep(0.05)

    return False


def main() -> None:
    with Finder() as finder:
        finder.open()

        print("Searching for NDI sources...")
        has_source = finder.wait_for_sources(timeout=5.0)
        source_names = finder.get_source_names()

        if not source_names:
            print("No NDI sources found. Run discover_ndi_sources.py first "
                  "to confirm discovery is working.")
            sys.exit(1)

        # Use the first discovered source. If you have multiple sources
        # later, print source_names and pick the index you want.
        source_name = source_names[0]
        source_obj = finder.get_source(source_name)
        print(f"Connecting to: {source_name}")

        receiver = Receiver(
            color_format=RecvColorFormat.RGBX_RGBA,
            bandwidth=RecvBandwidth.highest,
            recv_name="phase0_preview",
        )
        video_frame = VideoFrameSync()
        receiver.frame_sync.set_video_frame(video_frame)
        receiver.set_source(source_obj)

        print("Waiting for receiver to connect...")
        connect_deadline = time.time() + 15.0
        while not receiver.is_connected() and time.time() < connect_deadline:
            time.sleep(0.05)

        if not receiver.is_connected():
            print("Receiver never connected. Check that the source is still "
                  "active and try again.")
            sys.exit(1)

        print("Waiting for first real video frame (this can take a few seconds)...")
        got_frame = wait_for_first_frame(receiver, video_frame)

        if not got_frame:
            print(
                "Timed out waiting for a real frame. This usually means the "
                "source is discoverable but not actually streaming video "
                "right now -- check that the NDI Camera app is still open "
                "and actively broadcasting on your phone."
            )
            sys.exit(1)

        width, height = video_frame.get_resolution()
        pixel_array = video_frame.get_array()

        print(f"Received frame: {width}x{height}")

        # RGBX_RGBA gives us 4 channels (RGB + unused alpha); drop the
        # alpha channel and save as a normal RGB jpeg.
        image = Image.fromarray(pixel_array, mode="RGBA").convert("RGB")
        image.save(OUTPUT_FILE)

        print(f"\nSaved preview frame to: {OUTPUT_FILE}")
        print("Open it now -- if it shows your phone's live camera view, "
              "you're fully confirmed and clear to move to Phase 1.")


if __name__ == "__main__":
    main()
