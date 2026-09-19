# Rasp UI Media Player

This is the first independent media-player version of the projector interface.
It scans a media directory, displays video thumbnails, and plays videos through
GStreamer. It is designed to run on the Linux development machine first and
then fullscreen on the Raspberry Pi 4.

## Install

### Linux — Omarchy / Arch Linux

```bash
sudo pacman -Syu --needed \
  python \
  python-gobject \
  python-cairo \
  gtk3 \
  gstreamer \
  gst-plugins-base \
  gst-plugins-good \
  gst-plugins-bad \
  gst-plugins-ugly \
  gst-libav \
  gst-plugin-gtk \
  gst-python \
  ffmpeg \
  python-pillow \
  python-numpy \
  python-psutil \
  python-pyudev \
  python-pyusb \
  libusb \
  udisks2
```

The player can play whatever codecs are supported by the installed GStreamer
plugins. MP4, MKV, WebM, MOV, AVI, MPEG, and several other common containers
are recognized.

### macOS — Homebrew (Intel & Apple Silicon)

GStreamer + GTK work via Homebrew, but the package names differ from Arch.
Install the system libraries with `brew` and Python packages with `pip`:

```bash
# 1. Install Homebrew if you don't have it: https://brew.sh
brew update

# Core GUI stack (provides the `gi` / PyGObject module)
brew install python@3.13 gtk+3 pygobject3 gobject-introspection cairo

# GStreamer core + plugins + Python bindings
brew install gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad \
  gst-plugins-ugly gst-libav gst-python ffmpeg

# USB detection (storage hotplug + raw USB enumeration)
brew install libusb
# pyudev is Linux-only — do NOT install it on macOS (pip marker excludes it)

# Optional: verify GTK / gi is importable with the Homebrew Python
/opt/homebrew/bin/python3 -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk; print('gi OK')"
# On Intel Macs the python binary is /usr/local/bin/python3 instead of /opt/homebrew/bin/python3

# Pillow / numpy are best installed via pip inside the project venv (see below)
```

> **Note:** On macOS `gst-libav` may already be pulled in by `gst-plugins-ugly`. If `brew install gst-libav` says "no available formula", skip it. The GUI-only mode (`--gui-only` / `run_gui.py`) does not need any GStreamer packages — use it for visual work without codecs.

### Verify system packages (both platforms)

```bash
# Should print a path inside the system site-packages, not an error
python3 -c "import gi; print(gi.__file__)"
# GTK test
python3 -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk; print('Gtk', Gtk.get_major_version())"
# GStreamer test (skip if you only need GUI-only mode)
python3 -c "import gi; gi.require_version('Gst','1.0'); from gi.repository import Gst; Gst.init(None); print('Gst', Gst.version_string())"
```

### Create the project venv (both platforms)

This project **must** use a venv with `--system-site-packages` so the venv can see the system `gi` (installed via `pacman` on Linux or `brew` on macOS). Do **not** install `PyGObject` with `pip` unless you also need stubs for type checking.

**Linux (Omarchy / Arch):**

```bash
cd ~/Documents/tink_projector   # or wherever you cloned the repo
# IMPORTANT: use the system python, not a conda/miniforge python
/usr/bin/python -m venv --system-site-packages .venv
source .venv/bin/activate
python -c "import gi; print('venv gi OK:', gi.__file__)"
```

**macOS:**

```bash
cd ~/Documents/tink_projector
# Apple Silicon:
 /opt/homebrew/bin/python3 -m venv --system-site-packages .venv
# Intel:
# /usr/local/bin/python3 -m venv --system-site-packages .venv
# Generic (if `python3` already points to Homebrew Python):
# python3 -m venv --system-site-packages .venv

source .venv/bin/activate
python -c "import gi; print('venv gi OK:', gi.__file__)"
```

> **Conda / miniforge users:** If `which python` points to `~/miniforge3/bin/python`, your venv may have been created with the conda interpreter and will **not** see the system `gi`. Fix it by deactivating conda (`conda deactivate`) or using an absolute path as above:
> ```bash
> conda deactivate
> rm -rf .venv
> /usr/bin/python -m venv --system-site-packages .venv   # Linux
> # or /opt/homebrew/bin/python3 -m venv --system-site-packages .venv  # macOS
> ```

### USB detection packages (both platforms)

USB plug/unplug detection lives in `usb_monitor.py` and needs these pip
packages on top of the system libraries above:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

What that pulls in:

- `psutil` — mount listing + polling fallback (Linux/macOS/Pi). System
  alternative on Arch: `sudo pacman -S python-psutil`.
- `pyudev` — event-driven udev monitor, **Linux-only** (Arch/Pi). The
  `sys_platform == "linux"` marker skips it on macOS. System alternative:
  `sudo pacman -S python-pyudev`.
- `pyusb` — optional raw USB enumeration (VID/PID for non-storage devices).
  Needs system `libusb` (`pacman -S libusb` / `brew install libusb`).
  System alternative on Arch: `sudo pacman -S python-pyusb`.

No extra package is needed for the primary event source:
`Gio.VolumeMonitor` comes with `python-gobject` / `pygobject3` and fires
`mount-added` / `mount-removed` straight into the GTK main loop. `pyudev`
and `psutil` polling are automatic fallbacks. If none are installed the
monitor degrades to disabled instead of crashing.

Verify USB detection:

```bash
source .venv/bin/activate
python - <<'PY'
from usb_monitor import list_usb_drives, list_usb_devices_raw, UsbMonitor
print("drives:", [(d.name, str(d.mount_path)) for d in list_usb_drives()])
print("raw USB devices:", len(list_usb_devices_raw()))
m = UsbMonitor()
print("monitor backend:", m.start())
m.stop()
PY
```

Disable it per-run with `python run_gui.py --no-usb-monitor`.

Optional — install stubs for better type checking in your editor (silences `Gdk` / `Gtk` unknown-import noise):

```bash
source .venv/bin/activate
pip install -r requirements-dev.txt
```

## Run locally

Put videos in `./media` and run:

```bash
# activate the venv first if you haven't
source .venv/bin/activate
python media_player.py
```

`media_player.py` is the full launcher and wires everything together:
splash → home (`external disk` / `computer`) → USB library → player.
It starts GStreamer playback (`playbin` + `gtksink`), generates thumbnails,
watches USB mounts (merged into the library automatically), and places the
window on the detected output (PC panel, or the Pi's 40-pin DPI projector).

```bash
python media_player.py                              # full mode, auto output
python media_player.py --media-dir /path/to/videos  # other folder
python media_player.py --media-dir ./media --fullscreen
python media_player.py --gui-only                   # GUI preview, no GStreamer
python media_player.py --no-usb-monitor             # disable USB hotplug
```

If GStreamer or `gtksink` is missing, the player falls back to the GUI
preview (`MockBackend`) instead of crashing, and skips thumbnails if
`ffmpeg` is unavailable.

Flow and controls:

- Splash (2 s, click to skip) → Home: pick `external disk` or `computer`.
- `external disk` → library of videos from `./media` **plus every mounted
  USB drive** (subtitle shows `N titles · media + 1 USB`).
- Click a card (or focus + `Enter`) to play.
- While playing: `Escape`/`q` back to library, `Space` pause/play,
  `←`/`→` seek ±10 s. `F11` toggles fullscreen anywhere.

Thumbnails are generated by `ffmpeg` and cached under
`~/.cache/rasp-ui/thumbnails` (Linux) or `~/Library/Caches/rasp-ui/thumbnails` (macOS if you change the path). If `ffmpeg` is unavailable, the player still
works and displays placeholder video icons.

Verified end-to-end (see git history): pipeline reaches `PLAYING` at
1920×1080 on this PC's `eDP-1`, position advances, EOS returns to the
library, and the same code path drives `DPI-1` fullscreen on the Pi.

## Video output: PC panel vs Pi 40-pin DPI projector

The app renders through GTK/GStreamer like any normal window — it does
**not** bit-bang video over GPIO. The TI setup turns the Pi's 40-pin header
into a DPI/RGB666 display, so the window just needs to be placed on the
right output:

- **Linux PC:** `auto` resolves to the laptop panel (`eDP-1`) or external
  HDMI/DP, windowed unless you pass `--fullscreen`.
- **Pi 4 Model B + DLPDLCR230NPEVM:** `auto` resolves to `DPI-1`
  (1920×1080 @ 58–61 Hz, RGB666), fullscreen on that monitor.

`display_output.py` detects this at runtime with no extra pip packages:

1. Pi check via `/proc/device-tree/model` (`Raspberry Pi 4 Model B`).
2. DRM connectors via `/sys/class/drm/card*-*/status` — the EVM shows up as
   `DPI-1 connected` under KMS (`modetest -M vc4 | grep DPI-1` equivalent).
3. Firmware config check for `enable_dpi_lcd=1` in `/boot/firmware/config.txt`
   or `/boot/config.txt` (TI `sample_config/config.txt`).
4. GDK monitor index for `Gtk.Window.fullscreen_on_monitor()` (falls back to
   plain `fullscreen()` on Wayland/single-monitor).

```bash
# Inspect what the script sees (works on PC and Pi, no display needed):
python display_output.py --list
python run_gui.py --list-displays
python media_player.py --list-displays

# Force a specific output:
python run_gui.py --display dpi          # 40-pin projector (1080p fullscreen)
python run_gui.py --display panel        # laptop panel, windowed
python run_gui.py --display HDMI-A-1     # exact DRM connector
python run_gui.py --display auto --fullscreen  # --fullscreen always wins
```

`ui.run()` / `MediaPlayerWindow` accept the resolved target and call
`display_output.apply_to_window()`: DPI targets get `1920×1080` +
fullscreen-on-monitor, panel targets stay `1280×720` windowed (or
fullscreen with the flag).

### Audio output (USB speaker on the Pi)

**Detection is the OS's job; routing is one line in our app.** When you plug
a USB speaker into the Pi, the kernel (`snd-usb-audio`) claims it instantly
and PipeWire (Pi OS Bookworm default) or PulseAudio exposes it as a playback
sink — no GPIO/I²C involvement, nothing to write. What the app adds is sink
selection: `audio_output.py` enumerates sinks and pins one per playback
start (`gstreamer_backend.py:117` applies it while the pipeline is idle).

- `--audio-device auto` (default) → prefers the USB speaker, so a Pi with a
  single plugged-in speaker never stays silent. Falls back to the OS default.
- `--audio-device default` → always the OS default sink.
- `--audio-device usb` / `hdmi` / any name substring → pins a matching sink.
- `--list-audio` prints what was detected.

```bash
python audio_output.py                # list sinks + what auto would pick
python media_player.py --list-audio
python media_player.py --audio-device usb
```

On the Pi make sure the sound server is running (Bookworm ships PipeWire;
Bullseye: `sudo apt install pipewire pipewire-pulse wireplumber` or
`pulseaudio`). Plug the speaker in before or while the player runs — if it
appears later, the next title you play uses it. The current video keeps its
audio device until it ends (playbin cannot swap sinks mid-flight).

## Raspberry Pi migration (Pi 4B + DLPDLCR230NPEVM over 40-pin)

Hardware: EVM `J2` (40-pin) to Pi GPIO0–21 for 18-bit DPI video, plus
software I2C on GPIO22/23 for control. The DMD is 1080p; the Pi feeds it
`1920×1080` RGB666 (`dpi_output_format=458773`, `WriteInputImageSize(1920,
1080)` in `init_parallel_mode.py`).

1. Apply the TI firmware config (`dlpdlcr230np_python_support_code/.../
   sample_config/config.txt`: `enable_dpi_lcd=1`, `display_default_lcd=1`,
   `dpi_group=2`, `dpi_mode=87`, `hdmi_timings=1920 ... 58 Hz`) to
   `/boot/firmware/config.txt` (Bookworm) or `/boot/config.txt` (Bullseye),
   then reboot. Verify with `python display_output.py --list` — you want
   `card0-DPI-1: connected` (or `card1-DPI-1`).
2. After copying this project and TI's Python support package to the Pi:

```bash
python3 init_parallel_mode.py
python3 media_player.py --media-dir /home/pi/Media
# auto resolves to DPI-1 (1920x1080 fullscreen); explicit equivalents:
python3 media_player.py --media-dir /home/pi/Media --display dpi
python3 media_player.py --media-dir /home/pi/Media --fullscreen
```

3. Smoke tests on the Pi:

```bash
python3 display_output.py --list                      # DPI-1 connected?
python3 media_player.py --media-dir /home/pi/Media --gui-only --display dpi
```

Do not run the TI SPI flash-writing scripts while DPI video is active
because the interfaces share GPIO pins (BCM 8–11).

## Fixing editor LSP errors — `Import "gi" could not be resolved`

If Neovim (pyright / basedpyright / pylance) shows:

```
Diagnostics:
1. Import "gi" could not be resolved [reportMissingImports]
```

it means the language server is using a Python interpreter that cannot see the system `gi`. This project fixes it in two places:

### 1. The venv must be built with system site packages

As described above, recreate it with the **system** Python:

```bash
# Linux
rm -rf .venv
/usr/bin/python -m venv --system-site-packages .venv
source .venv/bin/activate
python -c "import gi; print(gi.__file__)"  # should succeed

# macOS (Apple Silicon example)
rm -rf .venv
/opt/homebrew/bin/python3 -m venv --system-site-packages .venv
source .venv/bin/activate
python -c "import gi; print(gi.__file__)"
```

If you use conda/miniforge, `python` on your `PATH` is the conda Python, which does **not** contain `gi`. Always use the absolute system/Homebrew path when creating the venv.

### 2. Tell the LSP which venv to use (`pyrightconfig.json`)

This repo ships a `pyrightconfig.json` at the project root (`tink_projector/pyrightconfig.json:1`):

```json
{
  "venvPath": ".",
  "venv": ".venv",
  "typeCheckingMode": "basic",
  "extraPaths": ["/usr/lib/python3.14/site-packages"],
  "exclude": ["dlpdlcr230np_python_support_code", ".venv"],
  "ignore": ["dlpdlcr230np_python_support_code"],
  "reportMissingTypeStubs": false,
  "reportUnknownMemberType": false,
  "reportAttributeAccessIssue": "none"
}
```

- `venvPath` / `venv` tells pyright to use `.venv/bin/python` (the one that can import `gi`).
- `extraPaths` is a fallback for the system site-packages. **On macOS you must change this path.** Find it with:
  ```bash
  python3 -c "import gi; import pathlib; print(pathlib.Path(__import__('gi').__file__).parent.parent)"
  # Apple Silicon typical: /opt/homebrew/lib/python3.13/site-packages
  # Intel typical:       /usr/local/lib/python3.13/site-packages
  ```
  Then edit `pyrightconfig.json` and `.vscode/settings.json` to use that path.

### 3. Restart the language server

After fixing the venv or editing `pyrightconfig.json`:

- In Neovim: `:LspRestart` (or `:LspStop` + `:LspStart`), or restart Neovim.
- In VS Code: `Cmd+Shift+P` → `Python: Select Interpreter` → choose `./.venv/bin/python`, then `Developer: Reload Window`.

### 4. Verify from the command line

You should get **zero** errors for the project files:

```bash
source .venv/bin/activate
# If using mason's pyright:
~/.local/share/nvim/mason/packages/pyright/node_modules/.bin/pyright
# Or if pyright is on your PATH:
pyright
```

If you still see `Import "gi" could not be resolved`, check:

```bash
which python
python -c "import sys; print(sys.executable)"
python -c "import gi; print(gi.__file__)"
cat .venv/pyvenv.cfg | grep include-system-site-packages  # should be true
```

`.venv/pyvenv.cfg` must contain `include-system-site-packages = true` and `executable = /usr/bin/python3.14` (Linux) or your Homebrew Python.
