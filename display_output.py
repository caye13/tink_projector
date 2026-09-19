#!/usr/bin/env python3
"""Video-output detection and selection for PC panel vs Pi DPI projector.

Target hardware: Raspberry Pi 4 Model B driving a TI DLPDLCR230NPEVM over the
40-pin header as an 18-bit DPI (RGB666) display::

    J2 (EVM, 40-pin) <-> Pi GPIO0-21 (DPI) + I2C on GPIO22/23
    1920x1080 @ 58-61 Hz, dpi_output_format=458773
    enable_dpi_lcd=1, display_default_lcd=1 in /boot/config.txt or
    /boot/firmware/config.txt (see TI sample_config/config.txt and
    dlpu103b user's guide, section 9).

On a Linux dev PC the same script must default to the normal screen panel
(eDP/HDMI). On the Pi it must output to the 40-pin DPI connector.

This module has no third-party pip dependencies (stdlib only, ``gi`` lazily
and optionally for GDK monitor names). Importing it never touches hardware.

Detection order:

1. :func:`is_raspberry_pi` — ``/proc/device-tree/model`` or
   ``/sys/firmware/devicetree/base/model`` containing ``Raspberry Pi``.
   Falls back to ``/proc/cpuinfo`` STM32-style ``Revision`` check (best
   effort, not authoritative).
2. :func:`list_drm_connectors` — ``/sys/class/drm/card*-*/status`` plus
   ``modes``. On the Pi with KMS the projector shows up as ``DPI-1``
   ``connected`` (``modetest -M vc4 | grep DPI-1`` equivalent). On this dev
   PC it shows ``eDP-1``/``HDMI-A-1``/``DP-x``.
3. :func:`is_dpi_enabled_in_config` — greps both Pi firmware paths for
   ``enable_dpi_lcd=1`` (TI sample config). Useful when the DPI cable is
   unplugged but the Pi is still configured for the EVM.
4. :func:`list_gdk_monitors` — optional ``gi``/GDK names for window
   placement. Returns ``[]`` headless or when ``gi`` is missing.

:func:`resolve_target` picks ``dpi`` on Pi+DPI, ``hdmi`` on Pi without DPI
but with HDMI, otherwise ``panel``. :func:`apply_to_window` moves a
``Gtk.Window`` to the right monitor and sizes it for 1080p on DPI.
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

# TI EVM native input (see init_parallel_mode.py: WriteInputImageSize(1920, 1080)
# and sample_config/config.txt hdmi_timings 1920x1080 @ 58 Hz).
DPI_WIDTH = 1920
DPI_HEIGHT = 1080

# Where Raspberry Pi OS keeps the firmware config (Bookworm moved it).
CONFIG_PATHS = (Path("/boot/firmware/config.txt"), Path("/boot/config.txt"))

# Model-file locations for Pi detection.
_MODEL_PATHS = (
    Path("/proc/device-tree/model"),
    Path("/sys/firmware/devicetree/base/model"),
)


@dataclass
class DrmConnector:
    """One DRM connector, e.g. ``card1-DPI-1``."""

    card: str  # e.g. "card1"
    name: str  # e.g. "DPI-1"
    status: str = "unknown"  # "connected" | "disconnected" | "unknown"
    modes: list[str] = field(default_factory=list)

    @property
    def kind(self) -> str:
        upper = self.name.upper()
        if upper.startswith("DPI"):
            return "dpi"
        if upper.startswith("HDMI"):
            return "hdmi"
        if upper.startswith("DP"):
            return "dp"
        if upper.startswith("EDP") or upper.startswith("LVDS"):
            return "panel"
        if upper.startswith("COMPOSITE") or upper.startswith("TV"):
            return "composite"
        if upper.startswith("DSI"):
            return "dsi"
        return "other"

    @property
    def connected(self) -> bool:
        return self.status.strip().lower() == "connected"


@dataclass
class DisplayTarget:
    """Where the UI should render."""

    kind: str  # "panel" | "dpi" | "hdmi" | "dp" | "dsi" | "composite" | "unknown"
    connector: str | None = None  # e.g. "DPI-1"
    width: int = 1280
    height: int = 720
    fullscreen: bool = False
    is_pi: bool = False
    pi_model: str | None = None
    reason: str = ""
    monitor_index: int | None = None


def read_text(path: Path) -> str | None:
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return data.decode("utf-8", errors="replace").strip("\x00").strip()


def is_raspberry_pi() -> tuple[bool, str | None]:
    """Return (is_pi, model string or None). Never raises."""
    for path in _MODEL_PATHS:
        text = read_text(path)
        if text and "raspberry pi" in text.lower():
            return True, text
    # Best-effort fallback: Pi 4 Model B cpuinfo has no "Raspberry" string on
    # some kernels, but the device-tree path above is authoritative when it
    # exists. Do not guess True from cpuinfo alone.
    return False, None


def is_dpi_enabled_in_config(
    paths: tuple[Path, ...] = CONFIG_PATHS,
) -> tuple[bool, Path | None]:
    """Check firmware config for ``enable_dpi_lcd=1`` (TI EVM setup)."""
    pattern = re.compile(r"^\s*enable_dpi_lcd\s*=\s*1", re.MULTILINE)
    for path in paths:
        text = read_text(path)
        if text is None:
            continue
        # Strip comments: config.txt uses "#" comments.
        stripped = "\n".join(
            line.split("#", 1)[0] for line in text.splitlines()
        )
        if pattern.search(stripped):
            return True, path
    return False, None


def list_drm_connectors(
    drm_root: Path = Path("/sys/class/drm"),
) -> list[DrmConnector]:
    """Parse ``/sys/class/drm/card*-*`` status/modes. Empty list if absent."""
    connectors: list[DrmConnector] = []
    try:
        entries = sorted(drm_root.iterdir())
    except OSError:
        return []
    for entry in entries:
        name = entry.name
        if name in {"version", "renderD128", "renderD129"} or not name.startswith(
            "card"
        ):
            continue
        # Split "card1-DPI-1" -> card="card1", connector="DPI-1".
        match = re.match(r"^(card\d+)-(.+)$", name)
        if not match:
            continue
        card, connector_name = match.groups()
        status = (read_text(entry / "status") or "unknown").strip()
        modes: list[str] = []
        modes_text = read_text(entry / "modes")
        if modes_text:
            modes = [line.strip() for line in modes_text.splitlines() if line.strip()]
        connectors.append(
            DrmConnector(card=card, name=connector_name, status=status, modes=modes)
        )
    return connectors


def list_gdk_monitors() -> list[dict[str, str]]:
    """List GDK monitors (model/manufacturer/geometry). [] when unavailable."""
    try:
        import gi

        gi.require_version("Gdk", "3.0")
        from gi.repository import Gdk
    except (ImportError, ValueError):
        return []
    try:
        display = Gdk.Display.get_default()
        if display is None:
            return []
        count = display.get_n_monitors()
    except Exception:
        return []
    monitors: list[dict[str, str]] = []
    for index in range(count):
        try:
            monitor = display.get_monitor(index)
            geometry = monitor.get_geometry()
            monitors.append(
                {
                    "index": str(index),
                    "model": str(monitor.get_model() or ""),
                    "manufacturer": str(monitor.get_manufacturer() or ""),
                    "geometry": f"{geometry.width}x{geometry.height}"
                    f"+{geometry.x}+{geometry.y}",
                }
            )
        except Exception:
            continue
    return monitors


def _connected_of_kind(
    connectors: list[DrmConnector], *kinds: str
) -> list[DrmConnector]:
    return [c for c in connectors if c.connected and c.kind in kinds]


def resolve_target(
    preference: str = "auto",
    connectors: list[DrmConnector] | None = None,
    is_pi: bool | None = None,
    pi_model: str | None = None,
    drm_root: Path = Path("/sys/class/drm"),
) -> DisplayTarget:
    """Pick the video output. ``preference`` overrides auto-detection.

    Accepted preferences: ``auto``, ``panel``, ``dpi``, ``hdmi``, ``dp``,
    ``dsi``, ``composite``, or an exact connector name such as ``DPI-1`` or
    ``HDMI-A-1`` (case-insensitive). Unknown names fall back to auto with a
    reason noting the miss.
    """
    if connectors is None:
        connectors = list_drm_connectors(drm_root)
    if is_pi is None or pi_model is None:
        detected_pi, detected_model = is_raspberry_pi()
        if is_pi is None:
            is_pi = detected_pi
        if pi_model is None:
            pi_model = detected_model

    pref = (preference or "auto").strip().lower()

    def _target_for(
        connector: DrmConnector | None, kind: str, reason: str
    ) -> DisplayTarget:
        if connector is not None and connector.modes:
            first = connector.modes[0]
            match = re.match(r"(\d+)x(\d+)", first)
            if match:
                width, height = int(match.group(1)), int(match.group(2))
            else:
                width, height = (DPI_WIDTH, DPI_HEIGHT) if kind == "dpi" else (1280, 720)
        else:
            width, height = (
                (DPI_WIDTH, DPI_HEIGHT) if kind == "dpi" else (1280, 720)
            )
        # The projector is a fixed 1080p panel: always render 1080p fullscreen
        # on DPI even if the EDID/modes file reports something else.
        if kind == "dpi":
            width, height = DPI_WIDTH, DPI_HEIGHT
        return DisplayTarget(
            kind=kind,
            connector=connector.name if connector else None,
            width=width,
            height=height,
            fullscreen=kind in {"dpi", "hdmi", "dsi"} and bool(is_pi),
            is_pi=bool(is_pi),
            pi_model=pi_model,
            reason=reason,
        )

    known_kinds = {"panel", "dpi", "hdmi", "dp", "dsi", "composite"}
    is_kind = pref in known_kinds
    if not is_kind and pref != "auto":
        # Treat as explicit connector name.
        wanted = pref.upper()
        for connector in connectors:
            if connector.name.upper() == wanted and connector.connected:
                kind = connector.kind if connector.kind != "other" else "panel"
                return _target_for(
                    connector, kind, f"explicit connector {connector.name}"
                )
        # Fall through to auto, but remember the miss.
        miss_note = f"requested {preference!r} not connected; "
    else:
        miss_note = ""

    if is_kind:
        matches = _connected_of_kind(connectors, pref)
        if matches:
            return _target_for(matches[0], pref, f"explicit preference {pref}")
        # Explicit kind requested but nothing connected: still honor the kind
        # so the Pi boots to DPI timings even with the cable unplugged.
        dpi_configured, _ = (
            is_dpi_enabled_in_config() if pref == "dpi" else (False, None)
        )
        return DisplayTarget(
            kind=pref,
            connector=None,
            width=DPI_WIDTH if pref == "dpi" else 1280,
            height=DPI_HEIGHT if pref == "dpi" else 720,
            fullscreen=pref in {"dpi", "hdmi", "dsi"} and bool(is_pi),
            is_pi=bool(is_pi),
            pi_model=pi_model,
            reason=f"{miss_note}explicit {pref} requested but not connected"
            + ("; DPI enabled in firmware config" if dpi_configured else ""),
        )

    # -- auto ---------------------------------------------------------
    dpi = _connected_of_kind(connectors, "dpi")
    if dpi:
        return _target_for(dpi[0], "dpi", f"Pi DPI projector on {dpi[0].name}")

    if is_pi:
        hdmi = _connected_of_kind(connectors, "hdmi")
        if hdmi:
            return _target_for(
                hdmi[0], "hdmi", f"Pi without DPI; HDMI on {hdmi[0].name}"
            )
        dsi = _connected_of_kind(connectors, "dsi")
        if dsi:
            return _target_for(dsi[0], "dsi", f"Pi DSI panel on {dsi[0].name}")
        dpi_configured, cfg_path = is_dpi_enabled_in_config()
        if dpi_configured:
            target = _target_for(
                None, "dpi", f"Pi configured for DPI ({cfg_path}), cable unplugged?"
            )
            target.fullscreen = True
            return target
        return DisplayTarget(
            kind="unknown",
            connector=None,
            width=1280,
            height=720,
            fullscreen=True,
            is_pi=True,
            pi_model=pi_model,
            reason="Pi detected but no connected DRM output",
        )

    # Dev PC: prefer internal panel, then HDMI/DP.
    panel = _connected_of_kind(connectors, "panel")
    if panel:
        return _target_for(
            panel[0], "panel", f"{miss_note}PC panel on {panel[0].name}"
        )
    hdmi = _connected_of_kind(connectors, "hdmi", "dp")
    if hdmi:
        return _target_for(
            hdmi[0], hdmi[0].kind, f"{miss_note}PC external on {hdmi[0].name}"
        )
    if connectors and all(not c.connected for c in connectors):
        return DisplayTarget(
            kind="panel",
            connector=None,
            width=1280,
            height=720,
            fullscreen=False,
            is_pi=False,
            pi_model=None,
            reason=f"{miss_note}no connected outputs; defaulting to panel",
        )
    return DisplayTarget(
        kind="panel",
        connector=None,
        width=1280,
        height=720,
        fullscreen=False,
        is_pi=False,
        pi_model=None,
        reason=f"{miss_note}default panel (no DRM info)",
    )


def find_monitor_index(target: DisplayTarget) -> int | None:
    """Best-effort GDK monitor index for ``target``. None = leave default."""
    monitors = list_gdk_monitors()
    if not monitors:
        return None
    if len(monitors) == 1:
        return 0
    if target.connector:
        want = target.connector.upper()
        for monitor in monitors:
            blob = f"{monitor.get('model', '')} {monitor.get('manufacturer', '')}".upper()
            # DRM names rarely appear in EDID strings, so match loosely.
            if want.split("-")[0] in blob:
                try:
                    return int(monitor["index"])
                except (KeyError, ValueError):
                    continue
    return None


def apply_to_window(window: object, target: DisplayTarget) -> str:
    """Move/resize a ``Gtk.Window`` for ``target``. Returns what was done.

    - Sets the default size to the target resolution.
    - On DPI/HDMI-on-Pi: fullscreen (on the matching monitor when found).
    - On PC panel: leaves windowed; caller may still pass ``--fullscreen``.
    Never raises: returns a ``"skipped: ...`` string on failure.
    """
    try:
        set_size = getattr(window, "set_default_size", None)
        if callable(set_size):
            set_size(target.width, target.height)
    except Exception as exc:
        return f"skipped: resize failed ({exc})"

    if not target.fullscreen:
        return f"windowed {target.width}x{target.height} ({target.reason})"

    try:
        index = target.monitor_index
        if index is None:
            index = find_monitor_index(target)
        if index is not None:
            fullscreen_on_monitor = getattr(window, "fullscreen_on_monitor", None)
            if callable(fullscreen_on_monitor):
                try:
                    from gi.repository import Gdk  # type: ignore[import-not-found]  # pyright: ignore[reportMissingImports]

                    screen = Gdk.Screen.get_default()
                    if screen is not None:
                        fullscreen_on_monitor(screen, index)
                        return (
                            f"fullscreen on monitor {index} "
                            f"({target.connector or target.kind})"
                        )
                except Exception:
                    pass
        fullscreen = getattr(window, "fullscreen", None)
        if callable(fullscreen):
            fullscreen()
            return f"fullscreen ({target.connector or target.kind})"
    except Exception as exc:
        return f"skipped: fullscreen failed ({exc})"
    return "skipped: no fullscreen method"


def describe(target: DisplayTarget) -> str:
    where = target.connector or target.kind
    host = f"Pi ({target.pi_model})" if target.is_pi else "PC"
    mode = "fullscreen" if target.fullscreen else "windowed"
    return (
        f"{host} -> {where} {target.width}x{target.height} {mode} [{target.reason}]"
    )


def _xrandr_connected() -> list[str]:
    """Fallback connector list via xrandr. [] when unavailable."""
    try:
        proc = subprocess.run(
            ["xrandr", "--query"], capture_output=True, text=True, timeout=5
        )
    except (FileNotFoundError, OSError, subprocess.SubprocessError):
        return []
    if proc.returncode != 0:
        return []
    connected: list[str] = []
    for line in proc.stdout.splitlines():
        match = re.match(r"^(\S+)\s+connected\b", line)
        if match:
            connected.append(match.group(1))
    return connected


def main(argv: list[str] | None = None) -> int:
    """CLI: ``python display_output.py [--display auto|dpi|...] [--list]``."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Detect PC panel vs Pi DPI projector output"
    )
    parser.add_argument(
        "--display",
        default="auto",
        help="auto (default), panel, dpi, hdmi, dp, dsi, or connector e.g. DPI-1",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="list DRM connectors, GDK monitors, Pi/config status and exit",
    )
    args = parser.parse_args(argv)

    is_pi, model = is_raspberry_pi()
    connectors = list_drm_connectors()
    dpi_cfg, cfg_path = is_dpi_enabled_in_config()

    if args.list:
        print(f"raspberry_pi: {is_pi} model={model}")
        print(f"dpi_in_config: {dpi_cfg} path={cfg_path}")
        print("drm_connectors:")
        for connector in connectors:
            modes = ",".join(connector.modes[:3])
            print(
                f"  {connector.card}-{connector.name}: {connector.status}"
                f" kind={connector.kind} modes=[{modes}]"
            )
        if not connectors:
            print("  (none — no /sys/class/drm or no permission)")
            xr = _xrandr_connected()
            if xr:
                print(f"xrandr connected: {', '.join(xr)}")
        print("gdk_monitors:")
        for monitor in list_gdk_monitors():
            print(f"  {monitor}")
        if not list_gdk_monitors():
            print("  (none — headless or gi missing)")
        target = resolve_target(args.display, connectors, is_pi, model)
        print(f"target: {describe(target)}")
        return 0

    target = resolve_target(args.display, connectors, is_pi, model)
    print(describe(target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
