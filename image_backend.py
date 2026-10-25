"""Test backends with no Pi hardware: photos from a folder, or a USB webcam, go through the REAL model.

images: each tray slot gets one photo (sorted by file name; the tray shrinks if there are fewer than egg_count).
webcam: each tray slot takes the sharpest usable frame of half a second (camera.capture); the UI's Live button
checks non-stop. Place the next egg in front of the camera between slots.
Verdicts, confidence and XGrad-CAM heatmaps all come from the trained Mask R-CNN; nothing is simulated.
LEDs, buzzer and UV-C are only logged.

    python -m egg_inspector --backend images --images C:\\path\\to\\photos --windowed
    python -m egg_inspector --backend webcam --camera 0 --windowed        (Pi: --camera /dev/videoN)
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .backend_base import Progress
from PIL import Image

from .camera import TOO_BRIGHT, Webcam, capture
from .config import Config
from .datatypes import EggResult
from .model_client import load_model, open_photo
from .pi_backend import PiBackend, wait_for_swap

log = logging.getLogger(__name__)

PHOTO_TYPES = {".jpg", ".jpeg", ".png", ".heic", ".heif"}


def find_photos(folder: str | Path) -> list[Path]:
    folder = Path(folder)
    if not folder.is_dir():
        raise RuntimeError(f"Photo folder not found: {folder}")
    photos = sorted(p for p in folder.iterdir() if p.suffix.lower() in PHOTO_TYPES)
    if not photos:
        raise RuntimeError(f"No .jpg/.png/.heic photos in {folder}")
    return photos


def _where(cfg: Config) -> str:
    return "local model" if cfg.model_source == "local" else "model API"


class ImageFolderBackend:
    camera = None

    def __init__(self, cfg: Config, photos: list[Path], source: str = ""):
        self.cfg, self.photos = cfg, photos[:cfg.egg_count]
        source = source or f"{len(self.photos)} photos from {photos[0].parent.name}"
        self.mode_note = f"Test mode: {source}, {_where(cfg)}"
        self.model = None
        self.pool = ThreadPoolExecutor(1, thread_name_prefix="model")

    def _photo(self, index: int, stop: threading.Event, progress: Progress) -> tuple[Image.Image, str] | None:
        """The picture of egg `index` and where it came from; None if Stop was pressed while waiting for it."""
        path = self.photos[index]
        return open_photo(path), path.name

    def prepare(self) -> None:
        self.model = load_model(self.cfg)
        log.info(self.mode_note)

    def eggs_loaded(self) -> bool:
        return True

    def enclosure_closed(self) -> bool:
        return True

    def inspect_egg(self, index: int, stop: threading.Event, progress: Progress) -> EggResult | None:
        t0 = time.perf_counter()
        got = self._photo(index, stop, progress)
        if got is None:                          # stopped while waiting for a usable picture
            return None
        photo, name = got
        progress(f"Egg {index + 1}/{self.cfg.egg_count}: checking {name}")
        view = PiBackend._await(self.pool.submit(self.model.predict, photo), stop)
        if view is None:
            return None
        return EggResult(index, view.cracked, view.score if view.cracked else 1 - view.score, photo,
                         view.heatmap, (time.perf_counter() - t0) * 1000, source=name, facts=view.facts)

    def set_defect_led(self, index: int, on: bool) -> None:
        log.info("Defect LED %d %s", index + 1, "on" if on else "off")

    def clear_leds(self) -> None:
        pass

    def beep(self) -> None:
        log.info("beep")

    def uvc(self, on: bool) -> None:
        log.info("UV-C %s (not connected in test mode)", "ON" if on else "OFF")

    def safe_off(self) -> None:
        pass

    def close(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)
        if self.model is not None:
            self.model.close()


class WebcamBackend(ImageFolderBackend):
    """A USB webcam instead of photos, at a fixed frame rate. Each slot takes the sharpest usable frame of half a
    second, after waiting while the picture is burnt white. The UI's Live button watches the same camera."""

    def __init__(self, cfg: Config, camera: str):
        super().__init__(cfg, [], f"webcam {camera}, {cfg.camera_fps} fps")
        self.camera = Webcam(camera, cfg.camera_fps, cfg.camera_size)

    def _photo(self, index: int, stop: threading.Event, progress: Progress) -> tuple[Image.Image, str] | None:
        egg = f"Egg {index + 1}/{self.cfg.egg_count}"
        if not wait_for_swap(self.cfg, index, stop, progress):
            return None
        frame = capture(self.camera, stop, lambda white: progress(
            f"{TOO_BRIGHT} {egg}: {white:.0%} of the picture is burnt white. Waiting: tap Auto (Light), or dim / "
            "diffuse the candling light."))
        return None if frame is None else (frame, "webcam")

    def close(self) -> None:
        self.camera.close()
        super().close()
