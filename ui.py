#!/usr/bin/env python3
"""Projector media-player interface.

This module contains GUI code only. It does not import GStreamer, FFmpeg, or
any TI/Raspberry Pi hardware libraries. A launcher supplies a playback backend.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Protocol

import gi

gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")

from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango


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
window { background: #111315; color: #f1f3f5; }
headerbar { background: #171a1e; border-bottom: 1px solid #2a2e34; }
flowbox { background: #111315; }
.media-card {
    background: #1b1f24; border: 1px solid #2b3138;
    border-radius: 10px; padding: 8px;
}
.media-card:hover, .media-card:focus {
    background: #252b33; border-color: #6f7e8d;
}
.media-title { color: #f1f3f5; font-size: 14px; font-weight: 600; }
.media-meta { color: #98a1ab; font-size: 12px; }
.empty-state { color: #9da5af; font-size: 18px; }
.player-bar { background: #111315; padding: 8px 12px; }
.preview-state { color: #9da5af; font-size: 22px; }
"""


class PlaybackBackend(Protocol):
    def mount(self, container: Gtk.Box) -> None: ...
    def play(self, video: Path) -> None: ...
    def stop(self) -> None: ...
    def toggle_pause(self) -> None: ...
    def seek(self, seconds: int) -> None: ...


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


class MediaCard(Gtk.Button):
    def __init__(self, video: Path, on_open: Callable[[Path], None]) -> None:
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
    def __init__(
        self,
        media_directory: Path,
        on_open: Callable[[Path], None],
        thumbnail_provider: Callable[[Path], Path | None] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.media_directory = media_directory
        self.on_open = on_open
        self.thumbnail_provider = thumbnail_provider
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

        for video in videos:
            card = MediaCard(video, self.on_open)
            self.cards.append(card)
            self.flowbox.add(card)

        self.show_all()
        self.empty_label.set_visible(not videos)

        if self.thumbnail_provider is not None:
            threading.Thread(
                target=self._generate_thumbnails, args=(videos,), daemon=True
            ).start()

    def _generate_thumbnails(self, videos: list[Path]) -> None:
        provider = self.thumbnail_provider
        if provider is None:
            return
        for video in videos:
            thumbnail = provider(video)
            if thumbnail is not None:
                GLib.idle_add(self._apply_thumbnail, video, thumbnail)

    def _apply_thumbnail(self, video: Path, thumbnail: Path) -> bool:
        for card in self.cards:
            if card.video == video:
                card.set_thumbnail(thumbnail)
                break
        return False


class MockBackend:
    """GUI-only backend used by contributors without media packages."""

    def __init__(self) -> None:
        self.preview_label: Gtk.Label | None = None

    def mount(self, container: Gtk.Box) -> None:
        label = Gtk.Label(
            label="GUI preview\nVideo playback is disabled in GUI-only mode"
        )
        label.set_justify(Gtk.Justification.CENTER)
        label.get_style_context().add_class("preview-state")
        container.pack_start(label, True, True, 0)
        self.preview_label = label

    def play(self, video: Path) -> None:
        if self.preview_label is not None:
            self.preview_label.set_text(
                f"GUI preview\n\n{video.name}\n\n"
                "Video playback is disabled in GUI-only mode"
            )

    def stop(self) -> None:
        pass

    def toggle_pause(self) -> None:
        pass

    def seek(self, seconds: int) -> None:
        pass


class PlayerView(Gtk.Box):
    def __init__(self, on_back: Callable[[], None], backend: PlaybackBackend) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.on_back = on_back
        self.backend = backend

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
        self.backend.mount(self.video_area)
        set_on_finished = getattr(self.backend, "set_on_finished", None)
        if set_on_finished is not None:
            set_on_finished(self.stop)

    def play(self, video: Path) -> None:
        self.title.set_text(video.name)
        self.backend.play(video)

    def stop(self) -> None:
        self.backend.stop()
        self.on_back()

    def toggle_pause(self) -> None:
        self.backend.toggle_pause()

    def seek(self, seconds: int) -> None:
        self.backend.seek(seconds)


class MediaPlayerWindow(Gtk.Window):
    def __init__(
        self,
        media_directory: Path,
        fullscreen: bool,
        backend: PlaybackBackend,
        thumbnail_provider: Callable[[Path], Path | None] | None = None,
    ) -> None:
        super().__init__(title="Rasp UI Media Player")
        self.set_default_size(1280, 720)
        self.connect("delete-event", self._on_delete)
        self.connect("key-press-event", self._on_key_press)

        self.stack = Gtk.Stack()
        self.add(self.stack)

        self.library = LibraryView(
            media_directory, self.show_player, thumbnail_provider
        )
        self.player = PlayerView(self.show_library, backend)
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
            window = self.get_window()
            if window is not None:
                self.set_fullscreen_mode(
                    not bool(window.get_state() & Gdk.WindowState.FULLSCREEN)
                )
            return True
        return False

    def set_fullscreen_mode(self, fullscreen: bool) -> None:
        self.fullscreen() if fullscreen else self.unfullscreen()

    def _on_delete(self, *_args) -> bool:
        self.player.backend.stop()
        Gtk.main_quit()
        return False


def run(
    media_directory: Path,
    fullscreen: bool,
    backend: PlaybackBackend,
    thumbnail_provider: Callable[[Path], Path | None] | None = None,
) -> int:
    Gtk.init([])
    load_css()
    window = MediaPlayerWindow(
        media_directory.expanduser().resolve(),
        fullscreen,
        backend,
        thumbnail_provider,
    )
    window.present()
    Gtk.main()
    return 0
