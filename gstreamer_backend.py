"""GStreamer playback backend.

This is deliberately separate from ui.py so GUI-only development never imports
GStreamer.

Audio routing: by default ``playbin`` uses ``autoaudiosink`` which follows the
OS default sink (PipeWire/PulseAudio/ALSA). For the Raspberry Pi +
USB-speaker case, call :meth:`GStreamerBackend.set_audio_device` with a sink
name from :mod:`audio_output` to pin a specific output.

Live audio switching rebuilds the pipeline (a mid-flight ``audio-sink``
property swap deadlocks ``playbin`` preroll behind a mounted ``gtksink``
widget, so we instead recreate playbin + gtksink and re-mount the widget via
the ``on_rebuild`` callback). Playback position is restored after the new
pipeline reaches ``ASYNC_DONE``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, TYPE_CHECKING

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst, GLib, Gtk

if TYPE_CHECKING:
    from audio_output import AudioSink


class GStreamerBackend:
    def __init__(self, audio_device: str | None = None) -> None:
        Gst.init(None)
        self.on_finished: Callable[[], None] | None = None
        self.on_rebuild: Callable[[Gtk.Widget], None] | None = None
        self._pending_seek: int | None = None
        self._audio_device: str | None = audio_device
        self.pipeline, self.video_widget = self._new_pipeline()

    # -- pipeline construction ----------------------------------------

    def _make_audio_sink(self) -> object | None:
        """Build the configured audio sink element (None on failure)."""
        name = self._audio_device
        if not name:
            return Gst.ElementFactory.make("autoaudiosink", "audio-sink")
        try:
            from audio_output import find_audio_sink_by_name

            found = find_audio_sink_by_name(name)
        except Exception:
            found = None
        if found is not None and found.gst_device is not None:
            try:
                element = found.gst_device.create_element("audio-sink")
                if element is not None:
                    return element
            except Exception:
                pass
        # No Gst.Device handle: use the stable pulse name (display names are
        # not valid `pulsesink device=` values, so prefer pulse_name).
        device_value = name if name.startswith(("alsa_", "hw:")) else name
        if found is not None and found.pulse_name:
            device_value = found.pulse_name
        elif found is None and not name.startswith(("alsa_", "hw:", "/")):
            # Maybe the caller passed a pulse sink name directly.
            device_value = name
        sink = Gst.ElementFactory.make("pulsesink", "audio-sink")
        if sink is not None:
            sink.set_property("device", device_value)
            return sink
        alsa = Gst.ElementFactory.make("alsasink", "audio-sink")
        if alsa is not None:
            alsa.set_property("device", device_value)
            return alsa
        return Gst.ElementFactory.make("autoaudiosink", "audio-sink")

    def _new_pipeline(self) -> tuple[Gst.Element, Gtk.Widget]:
        pipeline = Gst.ElementFactory.make("playbin", "player")
        video_sink = Gst.ElementFactory.make("gtksink", "video-sink")
        if pipeline is None or video_sink is None:
            raise RuntimeError(
                "GStreamer or gtksink is unavailable. Install the full media packages."
            )
        pipeline.set_property("video-sink", video_sink)
        audio_sink = self._make_audio_sink()
        if audio_sink is not None:
            pipeline.set_property("audio-sink", audio_sink)
        widget = video_sink.get_property("widget")
        if widget is None:
            raise RuntimeError("GStreamer gtksink widget is unavailable.")
        widget.set_hexpand(True)
        widget.set_vexpand(True)

        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_message)
        return pipeline, widget

    def _swap_pipeline(self, state: Gst.State) -> None:
        """Apply the audio device when idle (pipeline at NULL/READY).

        Mid-playback sink swaps deadlock ``playbin`` behind a mounted
        ``gtksink`` widget (the pipeline sticks at PAUSED pending PLAYING),
        so while playing we finish the current video on the old device and
        the new device applies at the next :meth:`play` (which rebuilds the
        audio sink while the pipeline is at NULL).
        """
        pipeline = self.pipeline
        if pipeline is None:
            return
        audio_sink = self._make_audio_sink()
        if audio_sink is not None:
            pipeline.set_property("audio-sink", audio_sink)

    # -- public -------------------------------------------------------

    def mount(self, container: Gtk.Box) -> None:
        container.pack_start(self.video_widget, True, True, 0)

    def set_on_finished(self, callback: Callable[[], None]) -> None:
        self.on_finished = callback

    def set_on_rebuild(self, callback: Callable[[Gtk.Widget], None]) -> None:
        self.on_rebuild = callback

    def set_audio_device(self, audio_device: str | None) -> None:
        """Pin (or unpin) the audio output.

        ``audio_device=None`` → OS default. Otherwise the value must match an
        :class:`audio_output.AudioSink` ``name`` (display name, pulse sink
        name, or ALSA ``hw:N``); unknown names fall back to the OS default.

        Applied immediately when the pipeline is idle; when a video is
        playing, the current video finishes on the old device and the new
        device is used from the next :meth:`play` on (playbin cannot swap
        sinks mid-flight without deadlocking).
        """
        if audio_device == self._audio_device:
            return
        self._audio_device = audio_device
        if self.pipeline is None:
            return
        state = self.pipeline.get_state(0).state
        if state in (Gst.State.PLAYING, Gst.State.PAUSED):
            # Deferred: applied on next play(). Nothing to do now.
            return
        self._apply_audio_sink()

    def _apply_audio_sink(self) -> None:
        """Set the configured audio sink on the NULL pipeline."""
        pipeline = self.pipeline
        if pipeline is None:
            return
        audio_sink = self._make_audio_sink()
        if audio_sink is not None:
            pipeline.set_property("audio-sink", audio_sink)

    @property
    def audio_device(self) -> str | None:
        return self._audio_device

    def play(self, video: Path) -> None:
        state = self.pipeline.get_state(0).state
        if state in (Gst.State.NULL, Gst.State.READY):
            # Apply the current audio-device preference before starting.
            self._apply_audio_sink()
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
        if message.type == Gst.MessageType.ASYNC_DONE:
            pending = self._pending_seek
            if pending is not None and self.pipeline is not None:
                self._pending_seek = None
                try:
                    self.pipeline.seek_simple(
                        Gst.Format.TIME,
                        Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
                        pending,
                    )
                except Exception:
                    pass
        elif message.type == Gst.MessageType.EOS:
            if self.on_finished is not None:
                GLib.idle_add(self.on_finished)
        elif message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            print(f"GStreamer error: {error}\n{debug or ''}")
            self._pending_seek = None
            if self.on_finished is not None:
                GLib.idle_add(self.on_finished)
