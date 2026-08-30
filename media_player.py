#!/usr/bin/env python3
"""A small fullscreen-friendly media browser for the projector project.

The application deliberately keeps the media UI independent from the projector
hardware. On the development computer it opens in a normal window. On the Pi,
the same application can be started fullscreen after the TI DPI display has
been configured.
"""

from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
import threading
from pathlib import Path

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
gi.require_version("Gst", "1.0")

from gi.repository import Gdk, GdkPixbuf, GLib, Gst, Gtk, Pango


VIDEO_EXTENSIONS = {
    ".avi",
    ".flv",
    ".m2ts",
    ".m4v",
    ".mkv",
    ".mov",
    ".mp4",
    ".mpeg",
    ".mpg",
    ".ogv",
    ".ts",
    ".webm",
    ".wmv",
}


CSS = """
window {
    background: #111315;
    color: #f1f3f5;
}

headerbar {
    background: #171a1e;
    border-bottom: 1px solid #2a2e34;
}

.library-title {
    font-size: 22px;
    font-weight: 700;
}

.library-subtitle {
    color: #9da5af;
    font-size: 13px;
}

flowbox {
    background: #111315;
}

.media-card {
    background: #1b1f24;
    border: 1px solid #2b3138;
    border-radius: 10px;
    padding: 8px;
}

.media-card:hover,
.media-card:focus {
    background: #252b33;
    border-color: #6f7e8d;
}

.media-title {
    color: #f1f3f5;
    font-size: 14px;
    font-weight: 600;
}

.media-meta {
    color: #98a1ab;
    font-size: 12px;
}

.empty-state {
    color: #9da5af;
    font-size: 18px;
}

.player-bar {
    background: #111315;
    padding: 8px 12px;
}
"""


def load_css() -> None:
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS.encode("utf-8"))
    Gtk.StyleContext.add_provider_for_screen(
        Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )


def format_size(path: Path) -> str:
    size = path.stat().st_size
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


class ThumbnailCache:
    """Generate optional thumbnails without blocking the GTK main loop."""

    def __init__(self) -> None:
        self.directory = Path.home() / ".cache" / "rasp-ui" / "thumbnails"
        self.directory.mkdir(parents=True, exist_ok=True)

    def path_for(self, video: Path) -> Path:
        stat = video.stat()
        key = f"{video.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode()
        digest = hashlib.sha1(key).hexdigest()
        return self.directory / f"{digest}.jpg"

    def create(self, video: Path) -> Path | None:
        destination = self.path_for(video)
        if destination.exists():
            return destination

        temporary = destination.with_suffix(".tmp.jpg")
        try:
            subprocess.run(
                [
                    "ffmpeg",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-ss",
                    "2",
                    "-i",
                    str(video),
                    "-frames:v",
                    "1",
                    "-vf",
                    "scale=480:-2",
                    str(temporary),
                ],
                check=True,
                timeout=30,
            )
            temporary.replace(destination)
            return destination
        except (FileNotFoundError, OSError, subprocess.SubprocessError):
            temporary.unlink(missing_ok=True)
            return None


class MediaCard(Gtk.Button):
    def __init__(self, video: Path, on_open) -> None:
        super().__init__()
        self.video = video
        self.get_style_context().add_class("media-card")
        self.set_relief(Gtk.ReliefStyle.NONE)
        self.set_tooltip_text(video.name)
        self.connect("clicked", lambda *_args: on_open(video))

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=7)
        self.add(content)

        self.image = Gtk.Image.new_from_icon_name(
            "video-x-generic", Gtk.IconSize.DIALOG
        )
        self.image.set_size_request(240, 135)
        self.image.set_pixel_size(72)
        content.pack_start(self.image, False, False, 0)

        title = Gtk.Label(label=video.stem)
        title.set_xalign(0)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_max_width_chars(25)
        title.get_style_context().add_class("media-title")
        content.pack_start(title, False, False, 0)

        metadata = Gtk.Label(label=format_size(video))
        metadata.set_xalign(0)
        metadata.get_style_context().add_class("media-meta")
        content.pack_start(metadata, False, False, 0)

        self.show_all()

    def set_thumbnail(self, thumbnail: Path) -> None:
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                str(thumbnail), 240, 135, False
            )
            self.image.set_from_pixbuf(pixbuf)
            self.image.set_pixel_size(1)
        except GLib.Error:
            pass


class LibraryView(Gtk.Box):
    def __init__(self, media_directory: Path, on_open) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.media_directory = media_directory
        self.on_open = on_open
        self.thumbnail_cache = ThumbnailCache()
        self.cards: list[MediaCard] = []

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_hexpand(True)
        self.scroller.set_vexpand(True)
        self.pack_start(self.scroller, True, True, 0)

        self.flowbox = Gtk.FlowBox()
        self.flowbox.set_selection_mode(Gtk.SelectionMode.NONE)
        self.flowbox.set_homogeneous(False)
        self.flowbox.set_column_spacing(16)
        self.flowbox.set_row_spacing(16)
        self.flowbox.set_margin_top(20)
        self.flowbox.set_margin_bottom(24)
        self.flowbox.set_margin_start(24)
        self.flowbox.set_margin_end(24)
        self.scroller.add(self.flowbox)

        self.empty_label = Gtk.Label(label="No videos found")
        self.empty_label.get_style_context().add_class("empty-state")
        self.empty_label.set_margin_top(80)
        self.empty_label.set_visible(False)
        self.pack_start(self.empty_label, False, False, 0)

    def scan(self) -> None:
        for child in self.flowbox.get_children():
            self.flowbox.remove(child)
        self.cards.clear()

        self.media_directory.mkdir(parents=True, exist_ok=True)
        videos = sorted(
            (
                path
                for path in self.media_directory.rglob("*")
                if path.is_file() and path.suffix.lower() in VIDEO_EXTENSIONS
            ),
            key=lambda path: path.name.lower(),
        )
        self.empty_label.set_visible(not videos)

        for video in videos:
            card = MediaCard(video, self.on_open)
            self.cards.append(card)
            self.flowbox.add(card)

        self.show_all()
        self.empty_label.set_visible(not videos)

        threading.Thread(
            target=self._generate_thumbnails, args=(videos,), daemon=True
        ).start()

    def _generate_thumbnails(self, videos: list[Path]) -> None:
        for video in videos:
            thumbnail = self.thumbnail_cache.create(video)
            if thumbnail is None:
                continue
            GLib.idle_add(self._apply_thumbnail, video, thumbnail)

    def _apply_thumbnail(self, video: Path, thumbnail: Path) -> bool:
        for card in self.cards:
            if card.video == video:
                card.set_thumbnail(thumbnail)
                break
        return False


class PlayerView(Gtk.Box):
    def __init__(self, on_back) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.on_back = on_back
        self.pipeline: Gst.Element | None = None
        self.video_widget: Gtk.Widget | None = None

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        toolbar.get_style_context().add_class("player-bar")
        self.pack_start(toolbar, False, False, 0)

        back = Gtk.Button.new_from_icon_name(
            "go-previous-symbolic", Gtk.IconSize.BUTTON
        )
        back.set_label(" Library")
        back.connect("clicked", lambda *_args: self.stop())
        toolbar.pack_start(back, False, False, 0)

        self.title = Gtk.Label()
        self.title.set_xalign(0)
        self.title.set_ellipsize(Pango.EllipsizeMode.END)
        toolbar.pack_start(self.title, True, True, 0)

        self.video_area = Gtk.Box()
        self.video_area.set_hexpand(True)
        self.video_area.set_vexpand(True)
        self.pack_start(self.video_area, True, True, 0)

        self._create_pipeline()

    def _create_pipeline(self) -> None:
        self.pipeline = Gst.ElementFactory.make("playbin", "player")
        sink = Gst.ElementFactory.make("gtksink", "video-sink")
        if self.pipeline is None or sink is None:
            self.pipeline = None
            message = Gtk.Label(
                label=(
                    "GStreamer video output is unavailable. Install gtksink "
                    "(the gst-plugin-gtk package)."
                )
            )
            message.set_line_wrap(True)
            self.video_area.pack_start(message, True, True, 0)
            return

        self.pipeline.set_property("video-sink", sink)
        widget = sink.get_property("widget")
        if widget is None:
            message = Gtk.Label(label="GStreamer video widget is unavailable.")
            message.set_line_wrap(True)
            self.video_area.pack_start(message, True, True, 0)
            return
        widget.set_hexpand(True)
        widget.set_vexpand(True)
        self.video_area.pack_start(widget, True, True, 0)
        self.video_widget = widget

        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_message)

    def play(self, video: Path) -> None:
        if self.pipeline is None:
            return
        self.title.set_text(video.name)
        self.pipeline.set_property("uri", Gst.filename_to_uri(str(video.resolve())))
        self.pipeline.set_state(Gst.State.PLAYING)

    def stop(self) -> None:
        if self.pipeline is not None:
            self.pipeline.set_state(Gst.State.NULL)
        self.on_back()

    def toggle_pause(self) -> None:
        if self.pipeline is None:
            return
        state = self.pipeline.get_state(0).state
        self.pipeline.set_state(
            Gst.State.PLAYING if state == Gst.State.PAUSED else Gst.State.PAUSED
        )

    def seek(self, seconds: int) -> None:
        if self.pipeline is None:
            return
        self.pipeline.seek_simple(
            Gst.Format.TIME,
            Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT,
            seconds * Gst.SECOND,
        )

    def _on_message(self, _bus, message) -> None:
        if message.type == Gst.MessageType.EOS:
            GLib.idle_add(self.stop)
        elif message.type == Gst.MessageType.ERROR:
            error, debug = message.parse_error()
            print(f"GStreamer error: {error}\n{debug or ''}", file=sys.stderr)
            GLib.idle_add(self.stop)


class MediaPlayerWindow(Gtk.Window):
    def __init__(self, media_directory: Path, fullscreen: bool) -> None:
        super().__init__(title="Rasp UI Media Player")
        self.set_default_size(1280, 720)
        self.connect("delete-event", self._on_delete)
        self.connect("key-press-event", self._on_key_press)

        self.stack = Gtk.Stack()
        self.add(self.stack)

        self.library = LibraryView(media_directory, self.show_player)
        self.player = PlayerView(self.show_library)
        self.stack.add_named(self.library, "library")
        self.stack.add_named(self.player, "player")
        self.stack.set_visible_child_name("library")

        self.show_all()
        self.library.scan()
        if fullscreen:
            self.fullscreen()

    def show_player(self, video: Path) -> None:
        self.stack.set_visible_child_name("player")
        self.player.play(video)

    def show_library(self) -> None:
        self.stack.set_visible_child_name("library")
        self.library.scan()

    def _on_key_press(self, _widget, event) -> bool:
        key = Gdk.keyval_name(event.keyval)
        if self.stack.get_visible_child_name() == "player":
            if key in {"Escape", "q"}:
                self.player.stop()
                return True
            if key == "space":
                self.player.toggle_pause()
                return True
            if key == "Left":
                self.player.seek(-10)
                return True
            if key == "Right":
                self.player.seek(10)
                return True
        elif key == "F11":
            self.set_fullscreen_mode(
                not bool(self.get_window().get_state() & Gdk.WindowState.FULLSCREEN)
            )
            return True
        return False

    def set_fullscreen_mode(self, fullscreen: bool) -> None:
        self.fullscreen() if fullscreen else self.unfullscreen()

    def _on_delete(self, *_args) -> bool:
        self.player.stop() if self.player.pipeline is not None else None
        Gtk.main_quit()
        return False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rasp UI media player")
    parser.add_argument(
        "--media-dir",
        type=Path,
        default=Path("media"),
        help="folder to scan recursively for video files (default: ./media)",
    )
    parser.add_argument(
        "--fullscreen",
        action="store_true",
        help="start fullscreen, intended for the Pi projector",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    Gst.init(None)
    Gtk.init([])
    load_css()

    window = MediaPlayerWindow(args.media_dir.expanduser().resolve(), args.fullscreen)
    window.present()
    Gtk.main()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
