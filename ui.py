#!/usr/bin/env python3
"""Projector media-player interface.

This module contains GUI code only. It does not import GStreamer, FFmpeg, or
any TI/Raspberry Pi hardware libraries. A launcher supplies a playback backend.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Protocol

if TYPE_CHECKING:
    from display_output import DisplayTarget
    from usb_monitor import UsbDrive, UsbMonitor

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

# macOS/Windows filesystem junk that is never playable video.
# - "._name.ext"   AppleDouble resource-fork forks copied from macOS
# - ".Trashes", "$RECYCLE.BIN", "System Volume Information", ".Spotlight-V100",
#   ".fseventsd", ".TemporaryItems" — hidden system folders on removable drives
# - ".DS_Store" and other hidden files that only look like video by extension
JUNK_DIRS = {
    ".trashes",
    ".spotlight-v100",
    ".fseventsd",
    ".temporaryitems",
    "$recycle.bin",
    "system volume information",
    "lost+found",
}


def is_junk_media(path: Path) -> bool:
    """True for AppleDouble files, hidden/system folders, and dotfiles."""
    name = path.name
    if name.startswith("._"):  # macOS AppleDouble resource fork
        return True
    if name.startswith("."):  # generic dotfiles (e.g. .DS_Store.mp4 copies)
        return True
    for part in path.parts[:-1]:  # every ancestor directory
        part_lower = part.lower()
        if part_lower in JUNK_DIRS or (
            part_lower.startswith(".") and part_lower not in {".."}
        ):
            return True
    return False

CARD_WIDTH = 320
CARD_HEIGHT = 180
SPLASH_MS = 2000
ASSETS_DIR = Path(__file__).resolve().parent / "assets"
LOGO_PATH = ASSETS_DIR / "tink-logo.png"


CSS = """
window {
    background: #000000;
    color: #f2ede4;
}

flowbox {
    background: #0b0d10;
}

flowboxchild {
    padding: 4px;
    border-radius: 16px;
    outline: none;
}

flowboxchild:selected .media-card,
.media-card:hover {
    background: #16120c;
    border-color: #e5c07b;
}

.splash {
    background: #000000;
}

.home-stage, .computer-stage {
    background: #000000;
}

.home-mark {
    color: #f2ede4;
    font-size: 28px;
    font-weight: 500;
    letter-spacing: 14px;
}

.option-card {
    background-image: linear-gradient(
        160deg,
        rgba(229, 192, 123, 0.22),
        rgba(152, 193, 192, 0.10)
    );
    background-color: rgba(242, 237, 228, 0.05);
    border: 1px solid rgba(242, 237, 228, 0.20);
    border-radius: 28px;
    padding: 8px 28px;
    box-shadow: 0 18px 40px rgba(0, 0, 0, 0.45),
                inset 0 1px 0 rgba(242, 237, 228, 0.28);
}

.option-card.gold.selected {
    background-image: linear-gradient(
        160deg,
        rgba(229, 192, 123, 0.38),
        rgba(229, 192, 123, 0.08)
    );
    border-color: rgba(229, 192, 123, 0.70);
    box-shadow: 0 22px 50px rgba(0, 0, 0, 0.5),
                inset 0 1px 0 rgba(242, 237, 228, 0.42);
}

.option-card.teal.selected {
    background-image: linear-gradient(
        160deg,
        rgba(152, 193, 192, 0.38),
        rgba(152, 193, 192, 0.08)
    );
    border-color: rgba(152, 193, 192, 0.70);
    box-shadow: 0 22px 50px rgba(0, 0, 0, 0.5),
                inset 0 1px 0 rgba(242, 237, 228, 0.42);
}

.option-label {
    color: #98c1c0;
    font-size: 13px;
}

.option-title {
    color: #f2ede4;
    font-size: 22px;
    font-weight: 600;
}

button.glass {
    background-image: linear-gradient(
        160deg,
        rgba(229, 192, 123, 0.22),
        rgba(152, 193, 192, 0.10)
    );
    background-color: rgba(242, 237, 228, 0.05);
    border: 1px solid rgba(242, 237, 228, 0.22);
    border-radius: 20px;
    padding: 8px 16px;
    color: #f2ede4;
    box-shadow: inset 0 1px 0 rgba(242, 237, 228, 0.28);
}

button.glass:hover,
button.glass:active,
button.glass:checked {
    background-image: linear-gradient(
        160deg,
        rgba(229, 192, 123, 0.32),
        rgba(152, 193, 192, 0.12)
    );
    border-color: rgba(229, 192, 123, 0.55);
}

button.glass label {
    color: #f2ede4;
}

.library-header {
    background: #12151a;
    padding: 16px 28px;
    border-bottom: 1px solid #2a2e34;
}

.library-title {
    color: #f1f3f5;
    font-size: 28px;
    font-weight: 700;
}

.library-subtitle {
    color: #9da5af;
    font-size: 15px;
}

.hint-bar {
    background: #12151a;
    padding: 10px 28px;
    border-top: 1px solid #2a2e34;
    color: #8b949e;
    font-size: 13px;
}

.media-card {
    background: #1b1f24;
    border: 1px solid #2b3138;
    border-radius: 14px;
    padding: 10px;
}

.poster {
    background: #15191e;
    border-radius: 8px;
}

.media-title {
    color: #f1f3f5;
    font-size: 16px;
    font-weight: 600;
}

.media-meta {
    color: #98a1ab;
    font-size: 13px;
}

.empty-state {
    color: #c5ccd4;
    font-size: 26px;
    font-weight: 600;
}

.empty-hint {
    color: #8b949e;
    font-size: 16px;
}

.player-bar {
    background: #12151a;
    padding: 12px 16px;
    border-bottom: 1px solid #2a2e34;
}

.player-title {
    font-size: 18px;
    font-weight: 600;
}

.preview-stage {
    background: #050607;
}

.preview-state {
    color: #d7dde4;
    font-size: 28px;
    font-weight: 600;
}

.preview-hint {
    color: #8b949e;
    font-size: 16px;
}

.computer-title {
    color: #f1f3f5;
    font-size: 32px;
    font-weight: 700;
}

.computer-copy {
    color: #9da5af;
    font-size: 16px;
}

.pairing-code {
    color: #f1f3f5;
    font-size: 28px;
    font-weight: 700;
    letter-spacing: 4px;
}
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


def _header_button(label: str) -> Gtk.Button:
    button = Gtk.Button.new_with_label(label)
    button.get_style_context().add_class("glass")
    button.set_image(
        Gtk.Image.new_from_icon_name("pan-start-symbolic", Gtk.IconSize.BUTTON)
    )
    button.set_always_show_image(True)
    return button


def format_size(path: Path) -> str:
    size = path.stat().st_size
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return ""


def format_meta(path: Path) -> str:
    ext = path.suffix.lstrip(".").upper() or "VIDEO"
    size = path.stat().st_size
    if size == 0:
        return ext
    return f"{ext}  ·  {format_size(path)}"


class SplashView(Gtk.EventBox):
    def __init__(self, on_finished: Callable[[], None]) -> None:
        super().__init__()
        self.on_finished = on_finished
        self._done = False
        self.get_style_context().add_class("splash")
        self.connect("button-press-event", lambda *_args: self.finish())

        stage = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        stage.set_halign(Gtk.Align.CENTER)
        stage.set_valign(Gtk.Align.CENTER)
        self.add(stage)

        if LOGO_PATH.exists():
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                str(LOGO_PATH), 280, 280, True
            )
            image = Gtk.Image.new_from_pixbuf(pixbuf)
            stage.pack_start(image, False, False, 0)
        else:
            mark = Gtk.Label(label="TINK")
            mark.get_style_context().add_class("home-mark")
            stage.pack_start(mark, False, False, 0)

        hint = Gtk.Label(label=" ")
        hint.get_style_context().add_class("preview-hint")
        stage.pack_start(hint, False, False, 0)
        self.show_all()

    def finish(self) -> bool:
        if self._done:
            return False
        self._done = True
        self.on_finished()
        return False


class OptionCard(Gtk.EventBox):
    def __init__(self, title: str, tone: str, on_choose: Callable[[], None]) -> None:
        super().__init__()
        self.on_choose = on_choose
        self.set_visible_window(False)
        self.connect("button-press-event", self._on_press)

        self.frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.frame.set_size_request(280, 112)
        self.frame.get_style_context().add_class("option-card")
        self.frame.get_style_context().add_class(tone)
        self.add(self.frame)

        title_label = Gtk.Label(label=title)
        title_label.set_halign(Gtk.Align.CENTER)
        title_label.set_valign(Gtk.Align.CENTER)
        title_label.get_style_context().add_class("option-title")
        self.frame.pack_start(title_label, True, True, 0)
        self.show_all()

    def set_selected(self, selected: bool) -> None:
        ctx = self.frame.get_style_context()
        if selected:
            ctx.add_class("selected")
        else:
            ctx.remove_class("selected")

    def _on_press(self, *_args) -> bool:
        self.on_choose()
        return True


class HomeView(Gtk.Box):
    def __init__(
        self,
        on_usb: Callable[[], None],
        on_computer: Callable[[], None],
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.on_usb = on_usb
        self.on_computer = on_computer
        self.selected = 0
        self.get_style_context().add_class("home-stage")

        stage = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=56)
        stage.set_halign(Gtk.Align.CENTER)
        stage.set_valign(Gtk.Align.CENTER)
        self.pack_start(stage, True, True, 0)

        mark = Gtk.Label(label="TINK")
        mark.get_style_context().add_class("home-mark")
        stage.pack_start(mark, False, False, 0)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=28)
        row.set_halign(Gtk.Align.CENTER)
        stage.pack_start(row, False, False, 0)

        self.usb_card = OptionCard("external disk", "gold", self._choose_usb)
        self.computer_card = OptionCard("computer", "teal", self._choose_computer)
        row.pack_start(self.usb_card, False, False, 0)
        row.pack_start(self.computer_card, False, False, 0)

        self.usb_status = Gtk.Label(label="plug in a USB drive")
        self.usb_status.get_style_context().add_class("preview-hint")
        stage.pack_start(self.usb_status, False, False, 0)
        self._refresh()

    def move_selection(self, delta: int) -> None:
        self.selected = (self.selected + delta) % 2
        self._refresh()

    def activate_selected(self) -> None:
        if self.selected == 0:
            self.on_usb()
        else:
            self.on_computer()

    def _choose_usb(self) -> None:
        self.selected = 0
        self._refresh()
        self.on_usb()

    def _choose_computer(self) -> None:
        self.selected = 1
        self._refresh()
        self.on_computer()

    def set_usb_drives(self, drives: list[Any]) -> None:
        """Update the USB status line. Accepts list[UsbDrive], duck-typed."""
        try:
            count = len(drives)
        except TypeError:
            count = 0
        if count == 0:
            self.usb_status.set_text("plug in a USB drive")
        elif count == 1:
            name = getattr(drives[0], "name", "USB")
            self.usb_status.set_text(f"1 USB drive · {name}")
        else:
            self.usb_status.set_text(f"{count} USB drives")

    def _refresh(self) -> None:
        self.usb_card.set_selected(self.selected == 0)
        self.computer_card.set_selected(self.selected == 1)


class ComputerView(Gtk.Box):
    def __init__(self, on_home: Callable[[], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.get_style_context().add_class("computer-stage")

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        header.get_style_context().add_class("library-header")
        self.pack_start(header, False, False, 0)
        home = _header_button("home")
        home.connect("clicked", lambda *_args: on_home())
        header.pack_start(home, False, False, 0)
        title = Gtk.Label(label="computer")
        title.set_xalign(0)
        title.get_style_context().add_class("library-title")
        header.pack_start(title, True, True, 0)

        stage = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        stage.set_halign(Gtk.Align.CENTER)
        stage.set_valign(Gtk.Align.CENTER)
        self.pack_start(stage, True, True, 0)

        waiting = Gtk.Label(label="waiting for a computer")
        waiting.get_style_context().add_class("computer-title")
        copy = Gtk.Label(
            label="a laptop or desktop will send video here.\n"
            "Wi‑Fi, cable, or screen-share can be plugged in later."
        )
        copy.set_justify(Gtk.Justification.CENTER)
        copy.get_style_context().add_class("computer-copy")
        code_label = Gtk.Label(label="this projector")
        code_label.get_style_context().add_class("option-label")
        code = Gtk.Label(label="TINK-230")
        code.get_style_context().add_class("pairing-code")
        note = Gtk.Label(label="placeholder name until pairing is implemented")
        note.get_style_context().add_class("preview-hint")
        stage.pack_start(waiting, False, False, 0)
        stage.pack_start(copy, False, False, 0)
        stage.pack_start(code_label, False, False, 0)
        stage.pack_start(code, False, False, 0)
        stage.pack_start(note, False, False, 0)

        hint = Gtk.Label(label="esc returns to Home")
        hint.set_xalign(0)
        hint.get_style_context().add_class("hint-bar")
        self.pack_start(hint, False, False, 0)


class MediaCard(Gtk.EventBox):
    def __init__(self, video: Path) -> None:
        super().__init__()
        self.video = video
        self.set_can_focus(False)
        self.set_visible_window(False)

        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        frame.get_style_context().add_class("media-card")
        self.add(frame)

        self.image = Gtk.Image.new_from_icon_name(
            "video-x-generic", Gtk.IconSize.DIALOG
        )
        self.image.set_size_request(CARD_WIDTH, CARD_HEIGHT)
        self.image.set_pixel_size(84)
        self.image.get_style_context().add_class("poster")
        frame.pack_start(self.image, False, False, 0)

        title = Gtk.Label(label=video.stem)
        title.set_xalign(0)
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_max_width_chars(28)
        title.get_style_context().add_class("media-title")
        frame.pack_start(title, False, False, 0)

        metadata = Gtk.Label(label=format_meta(video))
        metadata.set_xalign(0)
        metadata.get_style_context().add_class("media-meta")
        frame.pack_start(metadata, False, False, 0)
        self.show_all()

    def set_thumbnail(self, thumbnail: Path) -> None:
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                str(thumbnail), CARD_WIDTH, CARD_HEIGHT, False
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
        on_home: Callable[[], None],
        thumbnail_provider: Callable[[Path], Path | None] | None = None,
        extra_media_dirs: list[Path] | None = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.media_directory = media_directory
        self.on_open = on_open
        self.thumbnail_provider = thumbnail_provider
        self.extra_media_dirs: list[Path] = list(extra_media_dirs or [])
        self.cards: list[MediaCard] = []

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        header.get_style_context().add_class("library-header")
        self.pack_start(header, False, False, 0)

        home = _header_button("home")
        home.connect("clicked", lambda *_args: on_home())
        header.pack_start(home, False, False, 0)

        titles = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        header.pack_start(titles, True, True, 0)

        title = Gtk.Label(label="usb library")
        title.set_xalign(0)
        title.get_style_context().add_class("library-title")
        titles.pack_start(title, False, False, 0)

        self.subtitle = Gtk.Label()
        self.subtitle.set_xalign(0)
        self.subtitle.get_style_context().add_class("library-subtitle")
        titles.pack_start(self.subtitle, False, False, 0)

        self.body = Gtk.Stack()
        self.body.set_hexpand(True)
        self.body.set_vexpand(True)
        self.pack_start(self.body, True, True, 0)

        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_hexpand(True)
        self.scroller.set_vexpand(True)
        self.body.add_named(self.scroller, "grid")

        self.flowbox = Gtk.FlowBox()
        self.flowbox.set_valign(Gtk.Align.START)
        self.flowbox.set_max_children_per_line(4)
        self.flowbox.set_min_children_per_line(1)
        self.flowbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.flowbox.set_activate_on_single_click(True)
        self.flowbox.set_homogeneous(False)
        self.flowbox.set_column_spacing(20)
        self.flowbox.set_row_spacing(20)
        self.flowbox.set_margin_top(24)
        self.flowbox.set_margin_bottom(24)
        self.flowbox.set_margin_start(28)
        self.flowbox.set_margin_end(28)
        self.flowbox.connect("child-activated", self._on_child_activated)
        self.scroller.add(self.flowbox)

        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        empty.set_valign(Gtk.Align.CENTER)
        empty.set_halign(Gtk.Align.CENTER)
        empty_label = Gtk.Label(label="no videos on this drive")
        empty_label.get_style_context().add_class("empty-state")
        empty_hint = Gtk.Label(
            label="plug in a USB stick or external disk with MP4, MKV, MOV, or similar files"
        )
        empty_hint.get_style_context().add_class("empty-hint")
        empty.pack_start(empty_label, False, False, 0)
        empty.pack_start(empty_hint, False, False, 0)
        self.body.add_named(empty, "empty")

        hint = Gtk.Label(
            label="Click a title to open it    ·    Enter plays    ·    Esc Home    ·    F11 fullscreen"
        )
        hint.set_xalign(0)
        hint.get_style_context().add_class("hint-bar")
        self.pack_start(hint, False, False, 0)

    def set_extra_media_dirs(self, dirs: list[Path]) -> None:
        """Set additional scan roots (e.g. USB mount points)."""
        seen: list[Path] = []
        for root in dirs:
            try:
                resolved = root.expanduser().resolve()
            except OSError:
                continue
            if resolved not in seen:
                seen.append(resolved)
        self.extra_media_dirs = seen

    def _scan_roots(self) -> list[Path]:
        roots = [self.media_directory]
        roots.extend(
            root for root in self.extra_media_dirs if root not in roots
        )
        return roots

    def scan(self) -> None:
        for child in self.flowbox.get_children():
            self.flowbox.remove(child)
        self.cards.clear()

        self.media_directory.mkdir(parents=True, exist_ok=True)
        videos: list[Path] = []
        for root in self._scan_roots():
            if not root.exists():
                continue
            try:
                found = [
                    path
                    for path in root.rglob("*")
                    if path.is_file()
                    and path.suffix.lower() in VIDEO_EXTENSIONS
                    and not is_junk_media(path)
                    and path.stat().st_size > 0
                ]
            except OSError:
                continue
            videos.extend(found)
        videos = sorted(videos, key=lambda path: path.name.lower())

        count = len(videos)
        folder = self.media_directory.name
        usb_count = len(self.extra_media_dirs)
        if usb_count:
            suffix = f"{folder} + {usb_count} USB"
        else:
            suffix = folder
        if count == 1:
            self.subtitle.set_text(f"1 title  ·  {suffix}")
        else:
            self.subtitle.set_text(f"{count} titles  ·  {suffix}")

        for video in videos:
            card = MediaCard(video)
            self.cards.append(card)
            self.flowbox.add(card)

        self.show_all()
        self.body.set_visible_child_name("empty" if not videos else "grid")

        children = self.flowbox.get_children()
        if children:
            self.flowbox.select_child(children[0])
            self.flowbox.grab_focus()

        if self.thumbnail_provider is not None:
            threading.Thread(
                target=self._generate_thumbnails, args=(videos,), daemon=True
            ).start()

    def activate_selected(self) -> None:
        selected = self.flowbox.get_selected_children()
        if selected:
            self._on_child_activated(self.flowbox, selected[0])

    def _on_child_activated(self, _flowbox: Gtk.FlowBox, child: Gtk.FlowBoxChild) -> None:
        card = child.get_child()
        if isinstance(card, MediaCard):
            self.on_open(card.video)

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
        self.title_label: Gtk.Label | None = None

    def mount(self, container: Gtk.Box) -> None:
        stage = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        stage.set_valign(Gtk.Align.CENTER)
        stage.set_halign(Gtk.Align.CENTER)
        container.get_style_context().add_class("preview-stage")

        title = Gtk.Label(label="Select a title from the library")
        title.set_justify(Gtk.Justification.CENTER)
        title.get_style_context().add_class("preview-state")
        hint = Gtk.Label(label="Playback is a preview in GUI-only mode")
        hint.set_justify(Gtk.Justification.CENTER)
        hint.get_style_context().add_class("preview-hint")
        stage.pack_start(title, False, False, 0)
        stage.pack_start(hint, False, False, 0)
        container.pack_start(stage, True, True, 0)
        self.title_label = title

    def play(self, video: Path) -> None:
        if self.title_label is not None:
            self.title_label.set_text(video.stem)

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
        self.theater_mode = False

        toolbar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        toolbar.get_style_context().add_class("player-bar")
        # Theater mode hides this bar; set_no_show_all keeps it hidden
        # even when show_all() runs on the stack switch.
        toolbar.set_no_show_all(True)
        self.toolbar = toolbar
        self.pack_start(toolbar, False, False, 0)

        back = _header_button("Library")
        back.connect("clicked", lambda *_args: self.stop())
        toolbar.pack_start(back, False, False, 0)

        self.title = Gtk.Label()
        self.title.set_xalign(0)
        self.title.set_ellipsize(Pango.EllipsizeMode.END)
        self.title.get_style_context().add_class("player-title")
        toolbar.pack_start(self.title, True, True, 0)

        self.video_area = Gtk.Box()
        self.video_area.set_hexpand(True)
        self.video_area.set_vexpand(True)
        click = Gtk.EventBox()
        click.add(self.video_area)
        click.connect("button-press-event", self._on_video_click)
        self.pack_start(click, True, True, 0)
        self.backend.mount(self.video_area)
        set_on_finished = getattr(self.backend, "set_on_finished", None)
        if set_on_finished is not None:
            set_on_finished(self.stop)
        set_on_rebuild = getattr(self.backend, "set_on_rebuild", None)
        if set_on_rebuild is not None:
            set_on_rebuild(self._remount_video_widget)

    def _remount_video_widget(self, widget: Gtk.Widget) -> None:
        """Swap in a rebuilt video widget (live audio-output switch)."""
        for child in self.video_area.get_children():
            self.video_area.remove(child)
        widget.set_hexpand(True)
        widget.set_vexpand(True)
        self.video_area.pack_start(widget, True, True, 0)
        widget.show_all()

    def set_theater_mode(self, enabled: bool) -> None:
        """Hide/show the top bar (video fills the window while playing)."""
        if enabled == self.theater_mode:
            return
        self.theater_mode = enabled
        self.toolbar.set_visible(not enabled)

    def _on_video_click(self, *_args) -> bool:
        # Click the picture to toggle the bar (theater on/off).
        self.set_theater_mode(not self.theater_mode)
        return True

    def play(self, video: Path) -> None:
        self.title.set_text(video.name)
        self.set_theater_mode(True)
        self.backend.play(video)

    def stop(self) -> None:
        self.set_theater_mode(False)
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
        usb_monitor: UsbMonitor | None = None,
        display_target: DisplayTarget | None = None,
    ) -> None:
        super().__init__(title="TINK Projector")
        self.connect("delete-event", self._on_delete)
        self.connect("key-press-event", self._on_key_press)
        self.usb_monitor = usb_monitor
        self.display_target = display_target
        if display_target is not None:
            self.set_default_size(display_target.width, display_target.height)
        else:
            self.set_default_size(1280, 720)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(280)
        self.add(self.stack)

        self.splash = SplashView(self.show_home)
        self.home = HomeView(self.show_library, self.show_computer)
        self.library = LibraryView(
            media_directory, self.show_player, self.show_home, thumbnail_provider
        )
        self.computer = ComputerView(self.show_home)
        self.player = PlayerView(self.show_library, backend)

        self.stack.add_named(self.splash, "splash")
        self.stack.add_named(self.home, "home")
        self.stack.add_named(self.library, "library")
        self.stack.add_named(self.computer, "computer")
        self.stack.add_named(self.player, "player")
        self.stack.set_visible_child_name("splash")

        self.show_all()
        GLib.timeout_add(SPLASH_MS, self.splash.finish)
        self._apply_display(fullscreen)
        self._start_usb_monitor()

    def show_home(self) -> None:
        self.stack.set_visible_child_name("home")

    def show_library(self) -> None:
        self.stack.set_visible_child_name("library")
        self.library.scan()

    def show_computer(self) -> None:
        self.stack.set_visible_child_name("computer")

    def show_player(self, video: Path) -> None:
        self.stack.set_visible_child_name("player")
        self.player.play(video)

    def _on_key_press(self, _widget, event) -> bool:
        key = Gdk.keyval_name(event.keyval)
        screen = self.stack.get_visible_child_name()

        if key == "F11":
            window = self.get_window()
            if window is not None:
                self.set_fullscreen_mode(
                    not bool(window.get_state() & Gdk.WindowState.FULLSCREEN)
                )
            return True

        if screen == "splash":
            self.splash.finish()
            return True

        if screen == "home":
            if key in {"Left", "Right"}:
                self.home.move_selection(-1 if key == "Left" else 1)
                return True
            if key in {"Return", "KP_Enter"}:
                self.home.activate_selected()
                return True
            return False

        if screen == "computer":
            if key in {"Escape", "q"}:
                self.show_home()
                return True
            return False

        if screen == "library":
            if key in {"Escape", "q"}:
                self.show_home()
                return True
            if key in {"Return", "KP_Enter"}:
                self.library.activate_selected()
                return True
            return False

        if screen == "player":
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
        return False

    def set_fullscreen_mode(self, fullscreen: bool) -> None:
        self.fullscreen() if fullscreen else self.unfullscreen()

    def _apply_display(self, fullscreen_flag: bool) -> None:
        """Place the window on the PC panel or the Pi DPI projector output."""
        target = self.display_target
        if target is None:
            if fullscreen_flag:
                self.fullscreen()
            return
        if fullscreen_flag and not target.fullscreen:
            # Explicit --fullscreen from the CLI always wins on PC panel.
            target.fullscreen = True
        try:
            from display_output import apply_to_window, describe

            result = apply_to_window(self, target)
            print(f"Display: {describe(target)} -> {result}")
        except Exception as exc:
            print(f"Display setup failed ({exc}); falling back")
            try:
                if fullscreen_flag or target.fullscreen:
                    self.fullscreen()
            except Exception:
                pass

    def _start_usb_monitor(self) -> None:
        monitor = self.usb_monitor
        if monitor is None:
            return
        try:
            set_callback = getattr(monitor, "set_on_change", None)
            if callable(set_callback):
                set_callback(self._on_usb_changed)
            else:
                monitor.on_change = self._on_usb_changed  # type: ignore[attr-defined]
            backend_name = monitor.start()
            print(f"USB monitor started ({backend_name})")
        except Exception as exc:
            print(f"USB monitor unavailable: {exc}")
            return
        try:
            initial = list(monitor.current_drives)
        except Exception:
            initial = []
        self._apply_usb_drives(initial)

    def _on_usb_changed(self, drives: list[UsbDrive]) -> None:
        self._apply_usb_drives(list(drives))

    def _apply_usb_drives(self, drives: list[UsbDrive]) -> None:
        try:
            self.home.set_usb_drives(drives)
        except Exception:
            pass
        try:
            self.library.set_extra_media_dirs(
                [drive.mount_path for drive in drives]
            )
        except Exception:
            pass
        try:
            visible = self.stack.get_visible_child_name()
        except Exception:
            visible = None
        if visible == "library":
            try:
                self.library.scan()
            except Exception:
                pass

    def _on_delete(self, *_args) -> bool:
        monitor = self.usb_monitor
        if monitor is not None:
            try:
                monitor.stop()
            except Exception:
                pass
        self.player.backend.stop()
        Gtk.main_quit()
        return False


def run(
    media_directory: Path,
    fullscreen: bool,
    backend: PlaybackBackend,
    thumbnail_provider: Callable[[Path], Path | None] | None = None,
    usb_monitor: UsbMonitor | None = None,
    display_target: DisplayTarget | None = None,
    display_preference: str = "auto",
) -> int:
    Gtk.init([])
    load_css()
    if display_target is None:
        try:
            from display_output import resolve_target

            display_target = resolve_target(display_preference)
        except Exception as exc:
            print(f"Display detection failed ({exc}); using panel default")
            display_target = None
    window = MediaPlayerWindow(
        media_directory.expanduser().resolve(),
        fullscreen,
        backend,
        thumbnail_provider,
        usb_monitor,
        display_target,
    )
    window.present()
    Gtk.main()
    return 0
