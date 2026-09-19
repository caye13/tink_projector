#!/usr/bin/env python3
"""Full TINK media-player launcher.

Wires the shared GUI (``ui.py``) to real playback:

- ``GStreamerBackend`` (playbin + gtksink) for video/audio.
- ``media_backend.ThumbnailCache`` (ffmpeg) for library thumbnails.
- ``usb_monitor.UsbMonitor`` for USB stick hotplug (library auto-merge).
- ``display_output.resolve_target`` for output selection:

  - Linux PC: laptop panel / external HDMI/DP, windowed by default.
  - Raspberry Pi 4B + DLPDLCR230NPEVM: auto resolves to the 40-pin DPI
    connector (``DPI-1``, 1920x1080 RGB666 fullscreen) after the TI
    ``config.txt`` has been applied and the Pi rebooted.

Graceful degradation: if GStreamer or ``gtksink`` is missing, the launcher
falls back to ``MockBackend`` (GUI preview) instead of failing, and skips
thumbnails if ffmpeg is unavailable.

Audio: USB speakers/headphone jacks are detected by the OS kernel
(``snd-usb-audio``) and exposed by PipeWire/PulseAudio automatically. The
player enumerates sinks via :mod:`audio_output` and pins the chosen one for
each playback start — ``auto`` prefers the USB speaker so a Pi with one
plugged-in speaker never stays silent. The pin applies when playback starts;
the current video keeps its audio device until it ends.

Run::

    python media_player.py                      # full mode, auto output
    python media_player.py --media-dir ./media --fullscreen
    python media_player.py --display dpi        # force 40-pin projector
    python media_player.py --audio-device usb   # force USB speakers
    python media_player.py --gui-only           # GUI preview, no GStreamer
    python media_player.py --list-displays --list-audio
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Callable


def build_backends(gui_only: bool) -> tuple[object, Callable[[Path], Path | None] | None]:
    """Return (backend, thumbnail_provider) with graceful fallbacks."""
    from ui import MockBackend

    backend: object = None
    thumbnail_provider: Callable[[Path], Path | None] | None = None

    if not gui_only:
        try:
            from gstreamer_backend import GStreamerBackend

            backend = GStreamerBackend()
            print("Playback: GStreamer backend ready (playbin + gtksink)")
        except Exception as exc:
            print(f"Playback: GStreamer unavailable ({exc}); GUI preview only")
            backend = None

        try:
            from media_backend import ThumbnailCache

            thumbnail_provider = ThumbnailCache().create
        except Exception as exc:
            print(f"Thumbnails: unavailable ({exc}); using icons")

    if backend is None:
        backend = MockBackend()
    return backend, thumbnail_provider


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="TINK projector media player")
    parser.add_argument(
        "--media-dir",
        type=Path,
        default=Path("media"),
        help="folder to scan for videos; USB drives are merged automatically "
        "(default: ./media)",
    )
    parser.add_argument(
        "--fullscreen",
        action="store_true",
        help="start fullscreen (always true on the Pi DPI output)",
    )
    parser.add_argument(
        "--display",
        default="auto",
        help=(
            "video output: auto (default), panel, dpi, hdmi, dp, or a DRM "
            "connector e.g. DPI-1. On a PC auto = panel; on the Pi 4 auto = "
            "the 40-pin DPI projector when connected."
        ),
    )
    parser.add_argument(
        "--gui-only",
        action="store_true",
        help="skip GStreamer/ffmpeg and run the GUI preview (no video)",
    )
    parser.add_argument(
        "--no-usb-monitor",
        action="store_true",
        help="disable USB plug/unplug detection",
    )
    parser.add_argument(
        "--audio-device",
        default="auto",
        help=(
            "audio output: auto (default, prefers a plugged-in USB speaker), "
            "default (OS default sink), usb, hdmi, or a sink name substring. "
            "Re-routes live when sinks appear/disappear."
        ),
    )
    parser.add_argument(
        "--list-audio",
        action="store_true",
        help="print detected audio sinks and exit",
    )
    parser.add_argument(
        "--control-port",
        type=int,
        default=0,
        help=(
            "listen for UDP remote-control commands (back/enter/left/right/"
            "play...) on this port, e.g. --control-port 5005. Needed on the "
            "Pi because SSH keyboard input cannot reach the local seat."
        ),
    )
    parser.add_argument(
        "--list-displays",
        action="store_true",
        help="print detected DRM/GDK outputs and exit",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.list_displays:
        from display_output import main as display_main

        raise SystemExit(display_main(["--list", "--display", args.display]))
    if args.list_audio:
        from audio_output import main as audio_main

        raise SystemExit(audio_main(["--audio", args.audio_device]))

    from audio_output import (
        AudioMonitor,
        list_audio_sinks,
        resolve_audio_preference,
    )
    from display_output import resolve_target
    from ui import run
    from usb_monitor import UsbMonitor

    backend, thumbnail_provider = build_backends(args.gui_only)
    monitor = None if args.no_usb_monitor else UsbMonitor()
    target = resolve_target(args.display)

    if hasattr(backend, "set_audio_device"):
        initial_name = resolve_audio_preference(args.audio_device, list_audio_sinks())
        try:
            backend.set_audio_device(initial_name)  # type: ignore[union-attr]
            where = initial_name or "OS default"
            print(f"Audio: {where} ({args.audio_device})")
        except Exception as exc:
            print(f"Audio: could not set sink ({exc}); using OS default")

        pinned = args.audio_device.lower() not in {"", "auto"}

        def _on_audio_change(_sinks) -> None:
            """Re-pin the sink on hotplug; applied at the next play()."""
            try:
                sinks = list_audio_sinks()
                if pinned:
                    wanted = resolve_audio_preference(args.audio_device, sinks)
                else:
                    wanted = resolve_audio_preference("auto", sinks)
                name = getattr(backend, "audio_device", None)
                if wanted != name:
                    backend.set_audio_device(wanted)  # type: ignore[union-attr]
                    print(f"Audio: next playback uses {wanted or 'OS default'}")
            except Exception:
                pass

        audio_monitor = AudioMonitor(on_change=_on_audio_change)
        audio_monitor.start()

    window_holder: dict[str, object] = {}

    control_server = None
    if args.control_port:
        try:
            from control_server import ControlServer

            def _handle_command(command: str) -> bool:
                win = window_holder.get("window")
                if win is None:
                    return False
                return bool(win.dispatch_command(command))

            control_server = ControlServer(_handle_command, args.control_port)
        except Exception as exc:
            print(f"Control server: unavailable ({exc})")

    def _wire_window(win) -> None:
        window_holder["window"] = win
        if control_server is not None and control_server.start():
            print(f"Control server: UDP :{control_server.port} ready")

    return run(
        args.media_dir,
        args.fullscreen,
        backend,  # type: ignore[arg-type]
        thumbnail_provider,
        monitor,
        target,
        on_window=_wire_window,
    )


if __name__ == "__main__":
    raise SystemExit(main())
