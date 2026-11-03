"""Explicit target modes and calibration identities for iPad and iPhone screens."""

import io
import json
import os
from pathlib import Path

from PIL import Image

MODES = ("auto", "ipad", "iphone")


class TargetProfiles:
    def __init__(self, settings_file: Path):
        self.file = settings_file
        saved = json.loads(settings_file.read_text()) if settings_file.exists() else {}
        self._last_size = tuple(saved.get("last_size", (0, 0)))
        self.mode = saved.get("mode", os.environ.get("DEVICE_MODE", "auto"))
        if self.mode not in MODES:
            self.mode = "auto"

    def select(self, mode: str) -> None:
        if mode not in MODES:
            raise ValueError("device mode must be auto, ipad, or iphone")
        self.mode = mode
        self.file.write_text(json.dumps({"mode": mode, "last_size": self._last_size}))

    def describe(self, frame: bytes | None) -> dict:
        width, height = self._last_size
        if frame:
            with Image.open(io.BytesIO(frame)) as image:
                width, height = image.size
            if self._last_size != (width, height):
                self._last_size = (width, height)
                self.file.write_text(json.dumps({"mode": self.mode, "last_size": self._last_size}))
        ratio = width / height if height else 0
        short_ratio = min(ratio, 1 / ratio) if ratio else 0
        device = self.mode if self.mode != "auto" else ("ipad" if short_ratio >= 0.6 else "iphone") if ratio else None
        orientation = ("landscape" if width > height else "portrait") if ratio else None
        key = f"{device}-{orientation}-{round(short_ratio * 100)}" if ratio else None
        return {"mode": self.mode, "device": device, "orientation": orientation, "live_frame": bool(frame),
                "width": width, "height": height, "aspect": ratio, "calibration_key": key}
