#!/usr/bin/env python3
"""UDP remote-control listener for the projector media player.

On the Raspberry Pi the media player runs in the *local* graphics seat
(Wayland/X on the DPI output), so keyboard input from an SSH session never
reaches it. Instead of a keyboard, commands arrive as UDP datagrams:

    left / right / up / down   move selection (home screen)
    enter / ok / select        activate (play video, choose card)
    back / home / exit         previous screen
    play / pause               toggle playback (player screen)
    prev / next (rew / ff)     seek -10 s / +10 s
    fullscreen                 toggle fullscreen

Aliases map onto the exact same dispatch as physical keys
(``ui.MediaPlayerWindow.dispatch_command``), so remote behavior always
matches the keyboard. One command per datagram, e.g.::

    echo -n "enter" | nc -u -w1 <pi-ip> 5005

Designed for an Arduino on the Pi's USB/WiFi (or a phone app / laptop):
the Arduino reads breadboard buttons and sends a datagram per press. Bind
host defaults to 0.0.0.0 so LAN devices can control the projector — treat
that network as trusted (kiosk). Requires GLib (already part of the app's
runtime); :meth:`ControlServer.start` returns False gracefully when the
port is taken or binding fails.

Keyboard-only alternative with zero app changes: flash an Arduino
Micro/Pro Micro as a USB HID keyboard (Buttons2Key / Keyboard library) —
the Pi mounts it as a keyboard and the player's key handler reacts
natively.
"""

from __future__ import annotations

import socket
from typing import Callable

try:
    import gi

    gi.require_version("GLib", "2.0")
    from gi.repository import GLib

    _HAS_GLIB = True
except (ImportError, ValueError):
    _HAS_GLIB = False

DEFAULT_PORT = 5005
MAX_DATAGRAM = 64


class ControlServer:
    """UDP command listener wired into the GTK main loop.

    Usage::

        server = ControlServer(
            lambda command: window.dispatch_command(command), port=5005
        )
        if not server.start():
            print("control server unavailable")
        ...
        server.stop()

    ``handler`` is invoked on the GTK main thread per command.
    """

    def __init__(
        self,
        handler: Callable[[str], bool],
        port: int = DEFAULT_PORT,
        host: str = "0.0.0.0",
    ) -> None:
        self.handler = handler
        self.port = port
        self.host = host
        self._socket: socket.socket | None = None
        self._watch_id: int | None = None

    @property
    def running(self) -> bool:
        return self._socket is not None

    def start(self) -> bool:
        """Bind the UDP socket and hook it into the GLib main loop."""
        if self.running:
            return True
        if not _HAS_GLIB:
            return False
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            sock.bind((self.host, self.port))
            sock.setblocking(False)
            self._watch_id = GLib.io_add_watch(
                sock,
                GLib.IOCondition.IN,
                self._on_readable,
            )
            self._socket = sock
        except OSError as exc:
            print(f"Control server: bind failed ({exc})")
            self._close()
            return False
        return True

    def stop(self) -> None:
        if not self.running:
            return
        watch_id = self._watch_id
        if watch_id is not None:
            try:
                GLib.source_remove(watch_id)
            except Exception:
                pass
            self._watch_id = None
        self._close()

    def _close(self) -> None:
        sock = self._socket
        self._socket = None
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass

    def _on_readable(self, sock: socket.socket, _condition) -> bool:
        while True:
            try:
                data, _addr = sock.recvfrom(MAX_DATAGRAM)
            except BlockingIOError:
                return True
            except OSError:
                return True
            if not data:
                return True
            command = data.decode("utf-8", errors="replace").strip().lower()
            if not command:
                return True
            try:
                self.handler(command)
            except Exception:
                continue


def send_command(
    command: str, host: str = "127.0.0.1", port: int = DEFAULT_PORT
) -> None:
    """One-shot helper to send a command (used by tests and clients)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.sendto(command.encode("utf-8"), (host, port))
    finally:
        sock.close()


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python control_server.py send <command> [host] [port]``."""
    import argparse

    parser = argparse.ArgumentParser(description="UDP remote control for TINK")
    sub = parser.add_subparsers(dest="mode", required=True)
    send = sub.add_parser("send", help="send one command")
    send.add_argument("command")
    send.add_argument("host", nargs="?", default="127.0.0.1")
    send.add_argument("port", nargs="?", type=int, default=DEFAULT_PORT)
    args = parser.parse_args(argv)
    if args.mode == "send":
        send_command(args.command, args.host, args.port)
        print(f"sent {args.command!r} -> {args.host}:{args.port}")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
