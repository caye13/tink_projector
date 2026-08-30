"""Optional media helpers.

This module is only imported by the full media-player launcher. GUI-only
contributors do not need FFmpeg or this module.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path


class ThumbnailCache:
    def __init__(self) -> None:
        self.directory = Path.home() / ".cache" / "rasp-ui" / "thumbnails"
        self.directory.mkdir(parents=True, exist_ok=True)

    def create(self, video: Path) -> Path | None:
        stat = video.stat()
        key = f"{video.resolve()}:{stat.st_size}:{stat.st_mtime_ns}".encode()
        destination = self.directory / f"{hashlib.sha1(key).hexdigest()}.jpg"
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
