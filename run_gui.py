#!/usr/bin/env python3
"""GUI-only launcher.

This file imports only the GUI module, the USB monitor (which has no hard
third-party dependencies) and standard-library Path/argparse. It does not
import GStreamer, FFmpeg helpers, or TI/Raspberry Pi code.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ui import MockBackend, run
from usb_monitor import UsbMonitor


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the Rasp UI without media playback"
    )
    parser.add_argument("--media-dir", type=Path, default=Path("gui-media"))
    parser.add_argument("--fullscreen", action="store_true")
    parser.add_argument(
        "--no-usb-monitor",
        action="store_true",
        help="disable USB plug/unplug detection",
    )
    args = parser.parse_args()
    monitor = None if args.no_usb_monitor else UsbMonitor()
    return run(args.media_dir, args.fullscreen, MockBackend(), None, monitor)


if __name__ == "__main__":
    raise SystemExit(main())
