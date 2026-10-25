"""Test backends with no Pi hardware: photos from a folder, or a USB webcam, go through the REAL model.

images: each tray slot gets one photo (sorted by file name; the tray shrinks if there are fewer than egg_count).
webcam: each tray slot watches one full turn of the egg (spin_seconds) at the camera's fixed frame rate and checks
the sharpest frame of every 1/frames_per_egg of the turn, as on the rig; the UI's Live button checks non-stop.
Verdicts, confidence and XGrad-CAM heatmaps all come from the trained Mask R-CNN; nothing is simulated.
Motors, LEDs, buzzer and UV-C are only logged (turn the egg by hand, or run the rig's spin motor).

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
from .camera import Webcam
from .config import Config
from .datatypes import EggResult
from .model_client import ViewScore, load_model, open_photo
from .pi_backend import PiBackend, View, capture_turn

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

    def _views(self, index: int, stop: threading.Event, progress: Progress) -> tuple[list[View], str] | None:
        """The pictures of egg `index`, each already handed to the model, and where they came from."""
        path = self.photos[index]
        photo = open_photo(path)
        progress(f"Egg {index + 1}/{self.cfg.egg_count}: checking {path.name}")
        return [(photo, self.pool.submit(self.model.predict, photo), 0.0)], path.name

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
        got = self._views(index, stop, progress)
        if got is None:                          # stopped while capturing
            return None
        views, name = got
        scores: list[ViewScore] = []
        for _, pending, _ in views:
            score = PiBackend._await(pending, stop)
            if score is None:
                PiBackend._cancel(views)
                return None
            scores.append(score)
        best = max(range(len(views)), key=lambda k: scores[k].score)
        view = scores[best]
        return EggResult(index, view.cracked, view.score if view.cracked else 1 - view.score, views[best][0],
                         view.heatmap, (time.perf_counter() - t0) * 1000, views=len(views),
                         angle_deg=views[best][2], source=name, facts=view.facts)

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
    """A USB webcam instead of photos, at a fixed frame rate. Each egg: one full turn (spin_seconds) split into
    frames_per_egg slices; the sharpest usable frame of each slice is checked (pi_backend.capture_turn).
    The UI's Live button watches the same camera (backend.camera)."""

    def __init__(self, cfg: Config, camera: str):
        super().__init__(cfg, [], f"webcam {camera}, {cfg.camera_fps} fps")
        self.camera = Webcam(camera, cfg.camera_fps, cfg.camera_size)

    def _views(self, index: int, stop: threading.Event, progress: Progress) -> tuple[list[View], str] | None:
        views = capture_turn(self.camera, self.cfg, f"Egg {index + 1}/{self.cfg.egg_count}", stop, progress,
                             lambda frame: self.pool.submit(self.model.predict, frame))
        return None if views is None else (views, "")     # no file name: the result line shows the angle

    def close(self) -> None:
        self.camera.close()
        super().close()
