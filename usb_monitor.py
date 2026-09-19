#!/usr/bin/env python3
"""USB storage detection for the projector media player.

This module is deliberately import-safe for GUI-only contributors:

- No hard dependency on ``psutil``, ``pyudev``, ``pyusb`` or GTK at import
  time. Everything optional is imported lazily inside functions.
- If nothing is installed, :func:`list_usb_drives` returns ``[]`` and
  :class:`UsbMonitor` degrades to a disabled monitor instead of raising.

Detection strategy (first available wins for events):

1. ``Gio.VolumeMonitor`` (via ``gi``) — already required by the GTK UI, works
   on Linux with gvfs/udisks2, no extra pip package. Best for this app because
   callbacks arrive on the GTK main loop.
2. ``pyudev`` (Linux only) — event-driven udev monitor for ``block`` /
   ``partition`` add/remove. Needs ``python-pyudev`` (Arch) or
   ``pip install pyudev``.
3. Polling with ``psutil.disk_partitions()`` — cross-platform fallback that
   works on Linux, macOS and Raspberry Pi OS. Needs ``python-psutil`` (Arch)
   or ``pip install psutil``.

Listing (used by all backends) is based on ``psutil`` mount points with a
removable-media heuristic:

- Linux: ``/run/media/<user>/*``, ``/media/*``, ``/run/mount/*``,
  ``/mnt/usb*``, ``/mnt/media*`` are treated as USB/removable.
- macOS: ``/Volumes/*`` except the system volume.
- Anything else on ``/dev/sd*`` mounted outside system paths is treated as
  likely-USB (covers manually mounted sticks).
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class UsbDrive:
    """A mounted USB/removable storage device."""

    name: str
    mount_path: Path
    device: str = ""
    fstype: str = ""
    label: str | None = None


OnChangeCallback = Callable[[list["UsbDrive"]], None]

_LINUX_USB_PREFIXES = (
    "/run/media/",
    "/media/",
    "/run/mount/",
    "/mnt/usb",
    "/mnt/media",
    "/mnt/udisk",
)

_MACOS_VOLUMES = Path("/Volumes")

_SYSTEM_MOUNTPOINTS = {
    "/",
    "/home",
    "/boot",
    "/boot/efi",
    "/var",
    "/var/log",
    "/var/cache",
    "/usr",
    "/opt",
    "/srv",
    "/etc",
    "/sys",
    "/proc",
    "/dev",
    "/run",
}


def _has_psutil() -> bool:
    try:
        import psutil  # type: ignore[import-not-found]  # noqa: F401

        return True
    except ImportError:
        return False


def is_usb_mount(device: str, mountpoint: str) -> bool:
    """Heuristic: is this mountpoint likely a USB/removable drive?"""
    mp = mountpoint.rstrip("/") or "/"

    # Linux removable-media locations (udisks2/udiskie mount here).
    for prefix in _LINUX_USB_PREFIXES:
        if mountpoint.startswith(prefix.rstrip("/")) and mp not in _SYSTEM_MOUNTPOINTS:
            # "/run/media/cap/PATRIOT" -> True, "/run" itself -> False
            if mountpoint != prefix.rstrip("/") and mountpoint.startswith(prefix):
                return True

    # macOS: every volume under /Volumes except the boot volume.
    if mountpoint.startswith("/Volumes/") and mp != "/Volumes":
        return True

    # Fallback: /dev/sd* mounted outside system paths (manually mounted sticks).
    # Internal NVMe (nvme0n1) and dm-crypt (mapper/root) are excluded by this.
    if device.startswith("/dev/sd") and mp not in _SYSTEM_MOUNTPOINTS:
        if not any(
            mp == sys_mp or mp.startswith(sys_mp.rstrip("/") + "/")
            for sys_mp in ("/boot", "/home", "/var", "/usr", "/opt", "/srv", "/etc")
        ):
            return True

    return False


def list_all_mounts() -> list[UsbDrive]:
    """List all mounted filesystems via psutil (empty list if missing)."""
    try:
        import psutil  # type: ignore[import-not-found]
    except ImportError:
        return []
    drives: list[UsbDrive] = []
    try:
        partitions = psutil.disk_partitions(all=False)
    except Exception:
        return []
    for part in partitions:
        try:
            mount_path = Path(part.mountpoint)
        except Exception:
            continue
        if not mount_path.exists():
            continue
        name = mount_path.name or part.device
        drives.append(
            UsbDrive(
                name=name,
                mount_path=mount_path,
                device=part.device,
                fstype=part.fstype,
                label=mount_path.name or None,
            )
        )
    return drives


def list_usb_drives() -> list[UsbDrive]:
    """List currently mounted USB/removable drives (sorted by name)."""
    result = [
        d
        for d in list_all_mounts()
        if is_usb_mount(d.device, str(d.mount_path))
    ]
    return sorted(result, key=lambda d: d.name.lower())


def list_usb_devices_raw() -> list[dict[str, str]]:
    """List raw USB devices via pyusb (VID/PID). Empty if pyusb missing.

    This is for non-storage USB devices (e.g. capture sticks). Storage
    detection for the library should use :func:`list_usb_drives`.
    """
    try:
        import usb.core  # type: ignore[import-not-found]  # pyright: ignore[reportMissingImports]
    except ImportError:
        return []
    try:
        devices = usb.core.find(find_all=True)
    except Exception:
        return []
    out: list[dict[str, str]] = []
    for dev in devices or []:
        try:
            out.append(
                {
                    "idVendor": f"{dev.idVendor:04x}",
                    "idProduct": f"{dev.idProduct:04x}",
                    "manufacturer": str(getattr(dev, "manufacturer", "") or ""),
                    "product": str(getattr(dev, "product", "") or ""),
                }
            )
        except Exception:
            continue
    return out


def _emit_on_gtk_thread(callback: OnChangeCallback, drives: list[UsbDrive]) -> None:
    """Call ``callback`` on the GTK main loop when possible."""
    try:
        from gi.repository import GLib
    except ImportError:
        callback(drives)
        return
    try:
        GLib.idle_add(callback, drives)
    except Exception:
        callback(drives)


class UsbMonitor:
    """Event-driven USB mount monitor with graceful fallbacks.

    Usage::

        monitor = UsbMonitor(on_change=self._on_usb_changed)
        backend = monitor.start()  # "gio" | "pyudev" | "polling" | "disabled"
        ...
        monitor.stop()

    ``on_change`` receives the current ``list[UsbDrive]`` whenever mounts
    appear or disappear. It is invoked on the GTK main thread when ``gi``
    is available, so it is safe to update widgets directly.
    """

    def __init__(
        self,
        on_change: OnChangeCallback | None = None,
        poll_interval: float = 2.0,
        use_gio: bool = True,
        use_pyudev: bool = True,
    ) -> None:
        self.on_change = on_change
        self.poll_interval = max(0.5, poll_interval)
        self.use_gio = use_gio
        self.use_pyudev = use_pyudev
        self.backend: str = "disabled"
        self._stop_event = threading.Event()
        self._poll_thread: threading.Thread | None = None
        self._last_signature: tuple[tuple[str, str], ...] | None = None
        self._gio_monitor: object | None = None
        self._gio_handlers: list[int] = []
        self._pyudev_observer: object | None = None

    @property
    def current_drives(self) -> list[UsbDrive]:
        return list_usb_drives()

    def set_on_change(self, callback: OnChangeCallback | None) -> None:
        self.on_change = callback

    def _signature(
        self, drives: list[UsbDrive]
    ) -> tuple[tuple[str, str], ...]:
        return tuple(sorted((str(d.mount_path), d.device) for d in drives))

    def _notify(self, drives: list[UsbDrive]) -> None:
        if self.on_change is None:
            return
        _emit_on_gtk_thread(self.on_change, drives)

    def _snapshot_and_notify(self, *, force: bool = False) -> bool:
        """Re-list drives; notify if changed. Returns True if changed."""
        drives = list_usb_drives()
        signature = self._signature(drives)
        if force or signature != self._last_signature:
            self._last_signature = signature
            self._notify(drives)
            return True
        return False

    # -- backends -----------------------------------------------------

    def _try_start_gio(self) -> bool:
        if not self.use_gio:
            return False
        try:
            import gi

            gi.require_version("Gio", "2.0")
            from gi.repository import Gio
        except (ImportError, ValueError):
            return False
        try:
            monitor = Gio.VolumeMonitor.get()
        except Exception:
            return False
        self._gio_monitor = monitor
        try:
            for signal in (
                "mount-added",
                "mount-removed",
                "mount-changed",
                "volume-added",
                "volume-removed",
                "drive-connected",
                "drive-disconnected",
                "drive-changed",
            ):
                handler_id = monitor.connect(
                    signal, lambda *_args: self._snapshot_and_notify(force=True)
                )
                self._gio_handlers.append(handler_id)
        except Exception:
            self._stop_gio()
            return False
        return True

    def _stop_gio(self) -> None:
        monitor = self._gio_monitor
        if monitor is not None and self._gio_handlers:
            try:
                for handler_id in self._gio_handlers:
                    monitor.disconnect(handler_id)
            except Exception:
                pass
        self._gio_handlers.clear()
        self._gio_monitor = None

    def _try_start_pyudev(self) -> bool:
        if not self.use_pyudev:
            return False
        try:
            import pyudev  # type: ignore[import-not-found]  # pyright: ignore[reportMissingImports]
        except ImportError:
            return False
        try:
            context = pyudev.Context()
            monitor = pyudev.Monitor.from_netlink(context)
            monitor.filter_by(subsystem="block", device_type="partition")
            observer = pyudev.MonitorObserver(
                monitor,
                callback=lambda *_args: self._snapshot_and_notify(force=True),
            )
            observer.start()
            self._pyudev_observer = observer
        except Exception:
            self._pyudev_observer = None
            return False
        return True

    def _stop_pyudev(self) -> None:
        observer = self._pyudev_observer
        if observer is not None:
            try:
                observer.stop()
            except Exception:
                pass
        self._pyudev_observer = None

    def _start_polling(self) -> None:
        if not _has_psutil():
            return
        self._stop_event.clear()
        self._last_signature = self._signature(list_usb_drives())

        def _loop() -> None:
            while not self._stop_event.wait(self.poll_interval):
                try:
                    self._snapshot_and_notify()
                except Exception:
                    continue

        thread = threading.Thread(target=_loop, name="usb-monitor", daemon=True)
        self._poll_thread = thread
        thread.start()

    def _stop_polling(self) -> None:
        self._stop_event.set()
        thread = self._poll_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        self._poll_thread = None

    # -- public -------------------------------------------------------

    def start(self) -> str:
        """Start monitoring. Returns backend name. Safe to call twice."""
        if self.backend != "disabled":
            return self.backend
        self._last_signature = self._signature(list_usb_drives())
        if self._try_start_gio():
            # Gio covers events; polling as backup catches mounts Gio misses
            # (e.g. manually mounted sticks outside gvfs).
            if _has_psutil():
                self._start_polling()
            self.backend = "gio+polling" if self._poll_thread else "gio"
            return self.backend
        if self._try_start_pyudev():
            self.backend = "pyudev"
            return self.backend
        if _has_psutil():
            self._start_polling()
            self.backend = "polling"
            return self.backend
        self.backend = "disabled"
        return self.backend

    def stop(self) -> None:
        """Stop all backends. Safe to call when not started."""
        self._stop_gio()
        self._stop_pyudev()
        self._stop_polling()
        self.backend = "disabled"
