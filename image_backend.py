"""Test backend for a PC: photos from a folder go through the REAL model (local or API), no Pi hardware needed.

Each tray slot gets one photo (sorted by file name; the tray shrinks if there are fewer than egg_count).
Verdicts, confidence and XGrad-CAM heatmaps all come from the trained Mask R-CNN; nothing is simulated.
Motors, LEDs, buzzer and UV-C are only logged.

    python -m egg_inspector --backend images --images C:\\path\\to\\photos --windowed
"""
from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from .backend_base import Progress
from .config import Config
from .datatypes import EggResult
from .model_client import load_model, open_photo
from .pi_backend import PiBackend

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


class ImageFolderBackend:
    def __init__(self, cfg: Config, photos: list[Path]):
        self.cfg, self.photos = cfg, photos[:cfg.egg_count]
        where = "local model" if cfg.model_source == "local" else "model API"
        self.mode_note = f"Test mode: {len(self.photos)} photos from {photos[0].parent.name}, {where}"
        self.model = None
        self.pool = ThreadPoolExecutor(1, thread_name_prefix="model")

    def prepare(self) -> None:
        self.model = load_model(self.cfg)
        log.info("Image test mode: %d photos", len(self.photos))

    def eggs_loaded(self) -> bool:
        return True

    def enclosure_closed(self) -> bool:
        return True

    def home_tray(self, stop: threading.Event) -> bool:
        return not stop.is_set()

    def inspect_egg(self, index: int, stop: threading.Event, progress: Progress) -> EggResult | None:
        t0 = time.perf_counter()
        path = self.photos[index]
        progress(f"Egg {index + 1}/{self.cfg.egg_count}: sending {path.name} to the model")
        photo = open_photo(path)
        view = PiBackend._await(self.pool.submit(self.model.predict, photo), stop)
        if view is None:
            return None
        return EggResult(index, view.cracked, view.score if view.cracked else 1 - view.score, photo,
                         view.heatmap, (time.perf_counter() - t0) * 1000, source=path.name)

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
