#!/usr/bin/env python3
"""GUI-only launcher.

This file imports only the GUI module and standard-library Path/argparse. It
does not import GStreamer, FFmpeg helpers, or TI/Raspberry Pi code.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from ui import MockBackend, run


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the Rasp UI without media playback"
    )
    parser.add_argument("--media-dir", type=Path, default=Path("gui-media"))
    parser.add_argument("--fullscreen", action="store_true")
    args = parser.parse_args()
    return run(args.media_dir, args.fullscreen, MockBackend())


if __name__ == "__main__":
    raise SystemExit(main())
