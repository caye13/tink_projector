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
    parser.add_argument(
        "--display",
        default="auto",
        help=(
            "video output: auto (default), panel, dpi, hdmi, dp, "
            "or connector e.g. DPI-1. On PC auto = panel; "
            "on Pi 4 auto = DPI projector when connected."
        ),
    )
    parser.add_argument(
        "--list-displays",
        action="store_true",
        help="print detected DRM/GDK outputs and exit",
    )
    args = parser.parse_args()
    if args.list_displays:
        from display_output import main as display_main

        raise SystemExit(display_main(["--list", "--display", args.display]))
    from display_output import resolve_target

    target = resolve_target(args.display)
    monitor = None if args.no_usb_monitor else UsbMonitor()
    return run(
        args.media_dir,
        args.fullscreen,
        MockBackend(),
        None,
        monitor,
        target,
    )


if __name__ == "__main__":
    raise SystemExit(main())
