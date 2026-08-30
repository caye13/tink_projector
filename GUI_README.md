# GUI-Only Development Guide

This guide is for contributors working only on the visual interface of Rasp UI.
The GUI contributor does not need the Raspberry Pi, TI projector tools,
GStreamer, GPIO, I²C, or SPI packages.

## Scope

The GUI contributor may work on:

- Library layout
- Video cards and thumbnails/placeholders
- Typography, spacing, colors, and CSS
- Navigation and fullscreen behavior
- The mock player screen
- Keyboard and mouse interaction

The GUI contributor should not work on:

- Raspberry Pi DPI configuration
- TI EVM initialization
- GPIO, I²C, or SPI code
- TI firmware or flash-writing scripts
- Raspberry Pi autostart or hardware deployment
- Actual video decoding or playback

## 1. Install only the GUI packages

### Linux — Omarchy / Arch Linux

```bash
sudo pacman -Syu --needed \
  python \
  python-gobject \
  gtk3
```

`python-gobject` provides the Python `gi` module used by GTK. Do not install
PyGObject with `pip` (the system package is required).

### macOS — Homebrew (Intel & Apple Silicon)

```bash
brew update
brew install python@3.13 gtk+3 pygobject3 gobject-introspection cairo

# Verify the Homebrew Python can see GTK:
# Apple Silicon:
 /opt/homebrew/bin/python3 -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk; print('Gtk', Gtk.get_major_version())"
# Intel:
# /usr/local/bin/python3 -c "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk; print('Gtk', Gtk.get_major_version())"
```

Only the GUI packages above are needed. You do **not** need GStreamer, FFmpeg, or `gst-*` plugins for GUI-only work.

## 2. Set up the project environment

```bash
cd ~/Documents/tink_projector
```

Create a project environment that can see the system GTK packages. The venv **must** use `--system-site-packages` and the **system/Homebrew** Python, not a conda/miniforge Python.

**Linux:**

```bash
/usr/bin/python -m venv --system-site-packages .venv
# If you prefer a separate GUI-only venv (optional):
# /usr/bin/python -m venv --system-site-packages .venv-gui
source .venv/bin/activate
```

**macOS:**

```bash
# Apple Silicon:
 /opt/homebrew/bin/python3 -m venv --system-site-packages .venv
# Intel:
# /usr/local/bin/python3 -m venv --system-site-packages .venv
# Generic (if `python3` already points to Homebrew Python):
# python3 -m venv --system-site-packages .venv

source .venv/bin/activate
```

> **Conda / miniforge users:** If `which python` points to `~/miniforge3/bin/python`, deactivate it first or use the absolute path above. A venv created with conda's Python will **not** see the system `gi` and your editor will show `Import "gi" could not be resolved`.

Verify GTK inside the venv:

```bash
python - <<'PY'
import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

print("GTK GUI environment is working")
PY
```

Optional — stubs for nicer type checking in your editor:

```bash
pip install PyGObject-stubs
```

## 3. Run GUI-only mode

Create a few placeholder media entries so the library has cards to display:

```bash
mkdir -p gui-media
touch gui-media/demo-one.mp4
touch gui-media/demo-two.mkv
touch gui-media/demo-three.webm
```

The GUI-only launcher is `run_gui.py` (uses `ui.py` + `MockBackend` and never imports GStreamer):

```bash
source .venv/bin/activate
python run_gui.py --media-dir ./gui-media
```

To test the projector-style fullscreen layout:

```bash
python run_gui.py --media-dir ./gui-media --fullscreen
```

In GUI-only mode, selecting a card opens a mock player screen. The visual
interface can be developed without any real video files or codecs.

> **Legacy note:** Older docs mention `python media_player.py --gui-only`. That flag existed before the UI was split into `ui.py` + backends. The current GUI-only entry point is `run_gui.py`. The full player `media_player.py` requires GStreamer (`gst-plugin-gtk` etc.) and does not have `--gui-only`.

The normal (full) mode remains available for the owner of the project:

```bash
python media_player.py --media-dir ./media
```

## 4. GUI-only mode behavior

`run_gui.py` (and the underlying `ui.MockBackend`) does the following:

- Does not import GStreamer
- Does not create a GStreamer video pipeline
- Does not generate thumbnails with FFmpeg
- Shows placeholder video cards
- Shows a mock player screen when a card is selected

## 5. Files the GUI contributor should edit

The split architecture keeps GUI code free of hardware imports:

```text
ui.py                 Main GUI code (CSS, cards, library, player view, window)
run_gui.py            GUI-only launcher — uses ui.MockBackend
gstreamer_backend.py  GStreamer backend (do not edit for GUI work)
media_backend.py      Thumbnail helper (do not edit for GUI work)
media_player.py       Full player that wires GStreamer + UI together
```

Main areas inside `ui.py` (`tink_projector/ui.py:40`):

```text
CSS                  Visual styling
MediaCard            Individual library card
LibraryView          Video library and grid
PlayerView           Player screen and GUI-only preview
MediaPlayerWindow    Navigation and fullscreen behavior
MockBackend          GUI-only preview backend
```

The hardware-specific work will be added separately during Raspberry Pi
migration. Keep the GUI usable with `run_gui.py` so it remains testable on a
normal desktop.

## 6. Check work before handing it back

Run the syntax check:

```bash
source .venv/bin/activate
python -m py_compile ui.py run_gui.py
```

Run the GUI-only application:

```bash
python run_gui.py --media-dir ./gui-media
# or fullscreen:
python run_gui.py --media-dir ./gui-media --fullscreen
```

Return to the library with `Escape` or `q`. Use `F11` to toggle fullscreen.

Optional — run the type checker (should be clean after the venv fix):

```bash
# mason's pyright (Neovim)
~/.local/share/nvim/mason/packages/pyright/node_modules/.bin/pyright
# or if installed globally
pyright
```

## 7. Fixing editor LSP errors — `Import "gi" could not be resolved`

If Neovim shows:

```
Diagnostics:
1. Import "gi" could not be resolved [reportMissingImports]
1. Import "gi.repository" could not be resolved
```

**Cause:** The language server is using a Python interpreter that cannot see the system `gi`. This happens when:

- The venv was created with conda's Python instead of the system/Homebrew Python.
- The LSP is not pointed at the venv.

**Fix:**

1. Recreate the venv with the correct interpreter (see section 2):

   ```bash
   # Linux
   rm -rf .venv
   /usr/bin/python -m venv --system-site-packages .venv
   source .venv/bin/activate
   python -c "import gi; print(gi.__file__)"

   # macOS Apple Silicon
   rm -rf .venv
   /opt/homebrew/bin/python3 -m venv --system-site-packages .venv
   source .venv/bin/activate
   python -c "import gi; print(gi.__file__)"
   ```

   Verify `.venv/pyvenv.cfg` contains `include-system-site-packages = true`.

2. This repo ships `pyrightconfig.json` (`tink_projector/pyrightconfig.json:1`) that tells pyright to use `.venv`:

   ```json
   {
     "venvPath": ".",
     "venv": ".venv",
     "typeCheckingMode": "basic",
     "extraPaths": ["/usr/lib/python3.14/site-packages"]
   }
   ```

   **On macOS** you must change `extraPaths` to your Homebrew site-packages:

   ```bash
   python3 -c "import gi, pathlib; print(pathlib.Path(gi.__file__).parent.parent)"
   # e.g. /opt/homebrew/lib/python3.13/site-packages  (Apple Silicon)
   # e.g. /usr/local/lib/python3.13/site-packages      (Intel)
   ```

   Update both `pyrightconfig.json` and `.vscode/settings.json` if you use VS Code.

3. Restart the LSP:

   - Neovim: `:LspRestart` or restart Neovim
   - VS Code: `Python: Select Interpreter` → `./.venv/bin/python`, then reload window

4. Verify:

   ```bash
   ~/.local/share/nvim/mason/packages/pyright/node_modules/.bin/pyright
   # should report 0 errors for ui.py / run_gui.py (TI code is excluded)
   ```

## 8. Recommended Git workflow

Create a GUI branch before making changes:

```bash
git switch -c gui/<your-name>
```

Check changed files:

```bash
git status
git diff
```

Commit GUI changes:

```bash
git add ui.py run_gui.py GUI_README.md
git commit -m "Improve media player GUI"
```

If the project has a remote repository, push the branch:

```bash
git push -u origin gui/<your-name>
```

Only merge the GUI branch into the hardware/deployment branch after the visual
changes have been reviewed.
