"""Test backends with no Pi hardware: photos from a folder, or a USB webcam, go through the REAL model.

images: each tray slot gets one photo (sorted by file name; the tray shrinks if there are fewer than egg_count).
webcam: each tray slot takes the newest webcam frame; the UI's Live button also checks frames non-stop.
Verdicts, confidence and XGrad-CAM heatmaps all come from the trained Mask R-CNN; nothing is simulated.
Motors, LEDs, buzzer and UV-C are only logged.

    python -m egg_inspector --backend images --images C:\\path\\to\\photos --windowed
    python -m egg_inspector --backend webcam --camera 0 --windowed        (Pi: --camera /dev/videoN)
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from PIL import Image

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


def _where(cfg: Config) -> str:
    return "local model" if cfg.model_source == "local" else "model API"


def open_webcam(camera: str):
    """`camera` is an index ("0") or a device path ("/dev/video8")."""
    import cv2

    source = int(camera) if camera.isdigit() else camera
    cam = cv2.VideoCapture(source, cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY)
    if not cam.isOpened() or not cam.read()[0]:
        raise RuntimeError(f"No webcam at {camera}. Check the USB cable, or try another --camera "
                           "(on the Pi, `v4l2-ctl --list-devices` shows the webcam's /dev/video path).")
    return cam


class ImageFolderBackend:
    def __init__(self, cfg: Config, photos: list[Path], source: str = ""):
        self.cfg, self.photos = cfg, photos[:cfg.egg_count]
        source = source or f"{len(self.photos)} photos from {photos[0].parent.name}"
        self.mode_note = f"Test mode: {source}, {_where(cfg)}"
        self.model = None
        self.pool = ThreadPoolExecutor(1, thread_name_prefix="model")

    def _photo(self, index: int) -> tuple[Image.Image, str]:
        path = self.photos[index]
        return open_photo(path), path.name

    def prepare(self) -> None:
        self.model = load_model(self.cfg)
        log.info(self.mode_note)

    def eggs_loaded(self) -> bool:
        return True

    def enclosure_closed(self) -> bool:
        return True

    def home_tray(self, stop: threading.Event) -> bool:
        return not stop.is_set()

    def inspect_egg(self, index: int, stop: threading.Event, progress: Progress) -> EggResult | None:
        t0 = time.perf_counter()
        photo, name = self._photo(index)
        progress(f"Egg {index + 1}/{self.cfg.egg_count}: checking {name}")
        view = PiBackend._await(self.pool.submit(self.model.predict, photo), stop)
        if view is None:
            return None
        return EggResult(index, view.cracked, view.score if view.cracked else 1 - view.score, photo,
                         view.heatmap, (time.perf_counter() - t0) * 1000, source=name)

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
    """A USB webcam instead of photos. A reader thread keeps only the newest frame, so a check never gets a
    stale one from OpenCV's buffer; live_frame() lets the UI show the feed and run the Live test."""

    def __init__(self, cfg: Config, camera: str):
        super().__init__(cfg, [], f"webcam {camera}")
        self.cam = open_webcam(camera)
        self.frame: Image.Image | None = None
        self._reading = True
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        while self._reading:
            ok, bgr = self.cam.read()
            if ok:
                self.frame = Image.fromarray(bgr[..., ::-1])             # OpenCV gives BGR
            else:
                time.sleep(0.05)

    def live_frame(self) -> Image.Image | None:
        return self.frame

    def _photo(self, index: int) -> tuple[Image.Image, str]:
        if self.frame is None:
            raise RuntimeError("No picture from the webcam")
        return self.frame, "webcam"

    def close(self) -> None:
        self._reading = False
        self._reader.join(1)                     # OpenCV can crash if released mid-read on another thread
        super().close()
        self.cam.release()
