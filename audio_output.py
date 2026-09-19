#!/usr/bin/env python3
"""Audio output detection and selection (USB speaker hotplug, Pi -> PC).

Split of responsibilities, answering "is audio handled by the OS or the
program?":

- **Detection is the OS's job.** The kernel's ``snd-usb-audio`` driver binds
  a USB speaker automatically (no GPIO/I2C involvement), and PipeWire (Pi OS
  Bookworm default) or PulseAudio exposes it as a playback sink. Nothing in
  this repo needs to enumerate USB buses.
- **Routing is the app's job.** GStreamer's ``autoaudiosink`` follows the
  *system default* sink, but freshly plugged sinks do not always become the
  default (WirePlumber policy). To guarantee a just-plugged USB speaker gets
  the sound, this module enumerates sinks and lets the player re-target.

Strategy, first available wins:

1. ``Gst.DeviceMonitor`` (``Audio/Sink``) — works with PipeWire, PulseAudio
   and ALSA providers; already part of the GStreamer stack this app needs.
   Its ``device-added``/``device-removed`` signals give real hotplug events
   on the GTK main loop.
2. ``pactl list short sinks`` — PulseAudio/PipeWire fallback.
3. ``/proc/asound/cards`` — bare-ALSA fallback (no sound server running).

Import-safe: ``gi`` is imported lazily; without GStreamer the module still
parses ``pactl`` / ``/proc/asound``. Everything returns ``[]`` instead of
raising when audio is unavailable (e.g. headless CI).

Usage::

    sinks = list_audio_sinks()
    monitor = AudioMonitor(on_change=...)
    backend = monitor.start()          # "gstreamer" | "polling" | "disabled"

    choice = resolve_audio_preference("auto", sinks)   # prefers USB
    backend.set_audio_device(choice)
"""

from __future__ import annotations

import re
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass
class AudioSink:
    """One playback output (speaker / headphone jack / HDMI audio).

    ``name`` is a stable human identifier (GDK/pactl description). GST
    pipewire device names (``pipewiredeviceN``) shift between monitor
    instantiations, so they are never used as identity.
    """

    name: str  # stable id: display name, pulse sink name, or ALSA "hw:N"
    description: str
    is_usb: bool = False
    gst_device: object | None = None  # Gst.Device when found via DeviceMonitor
    pulse_name: str | None = None  # pactl sink name for `pulsesink device=`

    def label(self) -> str:
        return f"{self.description or self.name}{' [USB]' if self.is_usb else ''}"


OnChangeCallback = Callable[[list[AudioSink]], None]

_ALSA_CARDS = Path("/proc/asound/cards")


def _looks_usb(text: str) -> bool:
    lowered = text.lower()
    return "usb" in lowered


def _sinks_from_gstreamer() -> list[AudioSink]:
    try:
        import gi

        gi.require_version("Gst", "1.0")
        from gi.repository import Gst

        if not Gst.is_initialized():
            Gst.init(None)
    except (ImportError, ValueError):
        return []
    try:
        monitor = Gst.DeviceMonitor()
        monitor.add_filter("Audio/Sink", None)
        monitor.start()
        devices = monitor.get_devices()
        monitor.stop()
    except Exception:
        return []
    sinks: list[AudioSink] = []
    for device in devices or []:
        try:
            gst_name = device.get_name() or ""
            display = device.get_display_name() or gst_name
            if not display:
                continue
            sinks.append(
                AudioSink(
                    name=display,  # stable across monitor instantiations
                    description=display,
                    is_usb=_looks_usb(f"{gst_name} {display}"),
                    gst_device=device,
                )
            )
        except Exception:
            continue
    return sinks


def _sinks_from_pactl() -> list[AudioSink]:
    try:
        proc = subprocess.run(
            ["pactl", "list", "short", "sinks"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    sinks: list[AudioSink] = []
    for line in proc.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) < 5:
            continue
        sink_name = fields[1].strip()
        state = fields[4].strip().lower() if len(fields) > 4 else ""
        if not sink_name or state == "suspended":
            continue
        sinks.append(
            AudioSink(
                name=sink_name,  # stable pactl id, e.g. alsa_output.usb-...
                description=sink_name.replace("alsa_output.", ""),
                is_usb=_looks_usb(sink_name),
                pulse_name=sink_name,
            )
        )
    return sinks


def _sinks_from_alsa_proc() -> list[AudioSink]:
    """Parse /proc/asound/cards: bare-ALSA fallback (no sound server)."""
    try:
        text = _ALSA_CARDS.read_text(errors="replace")
    except OSError:
        return []
    sinks: list[AudioSink] = []
    lines = text.splitlines()
    for index, line in enumerate(lines):
        match = re.match(r"^\s*(\d+)\s*\[([^\]]+)\]", line)
        if not match:
            continue
        card_index = match.group(1)
        card_name = match.group(2).strip()
        description = ""
        if index + 1 < len(lines):
            description = lines[index + 1].strip(" -")
        sinks.append(
            AudioSink(
                name=f"hw:{card_index}",
                description=f"{card_name} — {description}" if description else card_name,
                is_usb=_looks_usb(f"{card_name} {description}"),
            )
        )
    return sinks


def list_audio_sinks() -> list[AudioSink]:
    """Enumerate playback sinks. GStreamer -> pactl -> /proc/asound.

    GStreamer devices (display names) and pactl sinks (pulse names) describe
    the same outputs; entries are merged by description so each sink keeps
    both a ``gst_device`` (for ``create_element``) and a ``pulse_name`` (for
    ``pulsesink device=``).
    """
    merged: dict[str, AudioSink] = {}
    for source in (_sinks_from_gstreamer, _sinks_from_pactl, _sinks_from_alsa_proc):
        try:
            sinks = source()
        except Exception:
            sinks = []
        for sink in sinks:
            key = sink.name.strip().lower()
            existing = merged.get(key)
            if existing is None:
                merged[key] = sink
                continue
            # enrich, keep the first (higher-priority) source's name
            if existing.gst_device is None and sink.gst_device is not None:
                existing.gst_device = sink.gst_device
            if existing.pulse_name is None and sink.pulse_name is not None:
                existing.pulse_name = sink.pulse_name
            if len(sink.description) > len(existing.description):
                existing.description = sink.description
            existing.is_usb = existing.is_usb or sink.is_usb
    result = list(merged.values())
    return sorted(result, key=lambda s: s.name.lower())


def find_audio_sink(
    preference: str, sinks: list[AudioSink] | None = None
) -> AudioSink | None:
    """Resolve a ``--audio-device`` value against available sinks.

    - ``"default"`` → ``None`` (let autoaudiosink follow the OS default).
    - ``"usb"`` → first USB sink.
    - ``"hdmi"`` → first sink whose name/description mentions hdmi.
    - anything else → case-insensitive substring match against
      ``name`` + ``description``.
    """
    pref = (preference or "").strip().lower()
    if pref in {"", "default", "os", "system"}:
        return None
    if sinks is None:
        sinks = list_audio_sinks()
    if pref == "usb":
        for sink in sinks:
            if sink.is_usb:
                return sink
        return None
    if pref == "hdmi":
        for sink in sinks:
            if "hdmi" in f"{sink.name} {sink.description}".lower():
                return sink
        return None
    for sink in sinks:
        blob = f"{sink.name} {sink.description}".lower()
        if pref in blob:
            return sink
    return None


def find_audio_sink_by_name(name: str | None) -> AudioSink | None:
    """Lookup by stable id: display name, pulse sink name, or ALSA ``hw:N``."""
    if not name:
        return None
    try:
        sinks = list_audio_sinks()
    except Exception:
        return None
    wanted = name.strip().lower()
    for sink in sinks:
        if sink.name.lower() == wanted:
            return sink
    for sink in sinks:
        if sink.pulse_name and sink.pulse_name.lower() == wanted:
            return sink
    return None


def resolve_audio_preference(
    preference: str = "auto", sinks: list[AudioSink] | None = None
) -> str | None:
    """Return the sink ``name`` to pin, or ``None`` for OS default.

    - ``auto`` → prefer a USB sink (the Pi USB-speaker case), else OS default.
    - ``default`` → always OS default.
    - ``usb``/``hdmi``/substring → :func:`find_audio_sink`.
    """
    pref = (preference or "auto").strip().lower()
    if pref == "default":
        return None
    if sinks is None:
        sinks = list_audio_sinks()
    if pref == "auto":
        for sink in sinks:
            if sink.is_usb:
                return sink.name
        return None
    found = find_audio_sink(pref, sinks)
    return found.name if found is not None else None


def _emit_on_gtk_thread(
    callback: OnChangeCallback, sinks: list[AudioSink]
) -> None:
    try:
        from gi.repository import GLib
    except ImportError:
        callback(sinks)
        return
    try:
        GLib.idle_add(callback, sinks)
    except Exception:
        callback(sinks)


class AudioMonitor:
    """Hotplug monitor for audio sinks (USB speaker plug/unplug events).

    ``on_change`` receives the current ``list[AudioSink]`` whenever a sink
    appears, disappears, or the list changes; invoked on the GTK main thread
    when ``gi`` is available.
    """

    def __init__(
        self,
        on_change: OnChangeCallback | None = None,
        poll_interval: float = 3.0,
        use_gstreamer: bool = True,
    ) -> None:
        self.on_change = on_change
        self.poll_interval = max(1.0, poll_interval)
        self.use_gstreamer = use_gstreamer
        self.backend: str = "disabled"
        self._gst_monitor: object | None = None
        self._handlers: list[int] = []
        self._stop_event = threading.Event()
        self._poll_thread: threading.Thread | None = None
        self._last_signature: tuple[str, ...] | None = None

    @property
    def current_sinks(self) -> list[AudioSink]:
        return list_audio_sinks()

    def set_on_change(self, callback: OnChangeCallback | None) -> None:
        self.on_change = callback

    def _signature(self, sinks: list[AudioSink]) -> tuple[str, ...]:
        return tuple(sorted(s.name for s in sinks))

    def _notify(self, sinks: list[AudioSink]) -> None:
        if self.on_change is None:
            return
        _emit_on_gtk_thread(self.on_change, sinks)

    def _snapshot_and_notify(self, *, force: bool = False) -> bool:
        sinks = list_audio_sinks()
        signature = self._signature(sinks)
        if force or signature != self._last_signature:
            self._last_signature = signature
            self._notify(sinks)
            return True
        return False

    def _try_start_gstreamer(self) -> bool:
        if not self.use_gstreamer:
            return False
        try:
            import gi

            gi.require_version("Gst", "1.0")
            from gi.repository import Gst
        except (ImportError, ValueError):
            return False
        try:
            if not Gst.is_initialized():
                Gst.init(None)
            monitor = Gst.DeviceMonitor()
            monitor.add_filter("Audio/Sink", None)
            handler_added = monitor.connect(
                "device-added", lambda *_args: self._snapshot_and_notify(force=True)
            )
            handler_removed = monitor.connect(
                "device-removed", lambda *_args: self._snapshot_and_notify(force=True)
            )
            monitor.start()
        except Exception:
            return False
        self._gst_monitor = monitor
        self._handlers = [handler_added, handler_removed]
        return True

    def _stop_gstreamer(self) -> None:
        monitor = self._gst_monitor
        if monitor is not None:
            try:
                for handler_id in self._handlers:
                    monitor.disconnect(handler_id)
                monitor.stop()
            except Exception:
                pass
        self._handlers.clear()
        self._gst_monitor = None

    def _start_polling(self) -> None:
        self._stop_event.clear()
        self._last_signature = self._signature(list_audio_sinks())

        def _loop() -> None:
            while not self._stop_event.wait(self.poll_interval):
                try:
                    self._snapshot_and_notify()
                except Exception:
                    continue

        thread = threading.Thread(target=_loop, name="audio-monitor", daemon=True)
        self._poll_thread = thread
        thread.start()

    def _stop_polling(self) -> None:
        self._stop_event.set()
        thread = self._poll_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._poll_thread = None

    def start(self) -> str:
        """Start monitoring. Returns backend name. Safe to call twice."""
        if self.backend != "disabled":
            return self.backend
        self._last_signature = self._signature(list_audio_sinks())
        if self._try_start_gstreamer():
            # Poll pactl as backup so sink-name-level changes (server restarts)
            # are caught even if the device provider misses them.
            self._start_polling()
            self.backend = "gstreamer+polling"
            return self.backend
        if _has_pactl():
            self._start_polling()
            self.backend = "polling"
            return self.backend
        self.backend = "disabled"
        return self.backend

    def stop(self) -> None:
        """Stop monitoring. Safe to call when not started."""
        self._stop_gstreamer()
        self._stop_polling()
        self.backend = "disabled"


def _has_pactl() -> bool:
    try:
        proc = subprocess.run(
            ["pactl", "info"], capture_output=True, timeout=3
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python audio_output.py [--audio auto|default|usb|NAME] [--watch]``."""
    import argparse
    import time

    parser = argparse.ArgumentParser(description="List/playback audio sinks")
    parser.add_argument(
        "--audio",
        default="auto",
        help="auto (default), default, usb, hdmi, or sink name substring",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="print sinks every 2 s (Ctrl-C to stop); simulates hotplug checks",
    )
    args = parser.parse_args(argv)

    if args.watch:
        try:
            while True:
                sinks = list_audio_sinks()
                print(f"[{time.strftime('%H:%M:%S')}] sinks: {len(sinks)}")
                for sink in sinks:
                    print(f"  {sink.name} -> {sink.label()}")
                time.sleep(2)
        except KeyboardInterrupt:
            return 0

    sinks = list_audio_sinks()
    print("sinks:")
    for sink in sinks:
        print(f"  {sink.name} -> {sink.label()}")
    if not sinks:
        print("  (none found — no sound server/ALSA cards)")
    choice = resolve_audio_preference(args.audio, sinks)
    print(f"preference {args.audio!r} -> {choice or 'OS default (autoaudiosink)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
