"""GStreamer playback backend.

This is deliberately separate from ui.py so GUI-only development never imports
GStreamer.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib, Gtk


class GStreamerBackend:
    def __init__(self) -> None:
        Gst.init(None)
        self.pipeline = Gst.ElementFactory.make("playbin", "player")
        self.sink = Gst.ElementFactory.make("gtksink", "video-sink")
        if self.pipeline is None or self.sink is None:
            raise RuntimeError(
                "GStreamer or gtksink is unavailable. Install the full media packages."
            )

        self.pipeline.set_property("video-sink", self.sink)
        widget = self.sink.get_property("widget")
        if widget is None:
            raise RuntimeError("GStreamer gtksink widget is unavailable.")
        self.video_widget = widget
        self.video_widget.set_hexpand(True)
        self.video_widget.set_vexpand(True)
        self.on_finished: Callable[[], None] | None = None

        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_message)

    def mount(self, container: Gtk.Box) -> None:
        container.pack_start(self.video_widget, True, True, 0)

    def set_on_finished(self, callback: Callable[[], None]) -> None:
        self.on_finished = callback

    def play(self, video: Path) -> None:
        self.pipeline.set_property("uri", Gst.filename_to_uri(str(video.resolve())))
        self.pipeline.set_state(Gst.State.PLAYING)

    def stop(self) -> None:
        self.pipeline.set_state(Gst.State.NULL)

    def toggle_pause(self) -> None:
        state = self.pipeline.get_state(0).state
        self.pipeline.set_state(
            Gst.State.PLAYING if state == Gst.State.PAUSED else Gst.State.PAUSED
        )

    def seek(self, seconds: int) -> None:
        self.pipeline.seek_simple(
            Gst.Format.TIME,
            Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
            seconds * Gst.SECOND,
        )

    def _on_message(self, _bus, message) -> None:
        if message.type == Gst.MessageType.EOS:
            if self.on_finished is not None:
                GLib.idle_add(self.on_finished)
        elif message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            print(f"GStreamer error: {error}\n{debug or ''}")
            if self.on_finished is not None:
                GLib.idle_add(self.on_finished)
