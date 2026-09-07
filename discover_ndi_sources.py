"""
Phase 0 checkpoint script.

Goal: confirm your phone (running the NDI Camera app) is discoverable
as an NDI source on your local network, before writing any ingest or
fault-classification code.

Setup before running this:
    1. pip install cyndilib
    2. Install the NDI Camera app (NewTek) on your phone.
    3. Make sure your phone and this machine are on the SAME Wi-Fi network.
    4. Open the app on your phone and start streaming.

Run:
    python discover_ndi_sources.py

Expected output: your phone should appear in the list of source names,
e.g. something like "MY-PHONE (NDI Camera)".
"""

import time

from cyndilib.finder import Finder


def discover_sources(wait_seconds: float = 5.0, max_attempts: int = 6) -> list[str]:
    """
    Poll the network for NDI sources.

    We retry a few times with a short wait between attempts because NDI
    discovery relies on network broadcast/mDNS and the first check often
    comes back empty even when a source is live -- this is normal, not
    a sign something is broken.
    """
    with Finder() as finder:
        finder.open()

        for attempt in range(1, max_attempts + 1):
            print(f"[attempt {attempt}/{max_attempts}] waiting for sources "
                  f"(timeout={wait_seconds}s)...")

            found_change = finder.wait_for_sources(timeout=wait_seconds)
            source_names = finder.get_source_names()

            if source_names:
                print(f"Found {len(source_names)} source(s):")
                for name in source_names:
                    print(f"  - {name}")
                return source_names

            if not found_change:
                print("  no change detected, no sources yet.")

        return []


if __name__ == "__main__":
    print("Searching for NDI sources on the local network...\n")
    sources = discover_sources()

    if not sources:
        print(
            "\nNo NDI sources found. Checklist before troubleshooting further:\n"
            "  1. Is your phone on the SAME Wi-Fi network as this machine?\n"
            "  2. Is the NDI Camera app open and actively streaming (not just installed)?\n"
            "  3. Some networks block mDNS/multicast between devices (common on\n"
            "     public/guest Wi-Fi, and on some routers with 'client isolation'\n"
            "     or 'AP isolation' enabled) -- try a personal hotspot or home\n"
            "     network if this is the case.\n"
            "  4. On Linux, source discovery depends on Avahi/mDNS services being\n"
            "     available -- confirm avahi-daemon is installed and running.\n"
        )
    else:
        print(
            f"\nSuccess: {len(sources)} source(s) discoverable. "
            "You're clear to move to Phase 1 (frame capture)."
        )
        time.sleep(0.1)  # let stdout flush cleanly before Finder context closes
