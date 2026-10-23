"""Test backends with no Pi hardware: photos from a folder, or a USB webcam, go through the REAL model.

images: each tray slot gets one photo (sorted by file name; the tray shrinks if there are fewer than egg_count).
webcam: each tray slot takes the newest webcam frame; the UI's Live button also checks frames non-stop.
Verdicts, confidence and XGrad-CAM heatmaps all come from the trained Mask R-CNN; nothing is simulated.
Motors, LEDs, buzzer and UV-C are only logged.

    python -m egg_inspector --backend images --images C:\\path\\to\\photos --windowed
    python -m egg_inspector --backend webcam --camera 0 --windowed        (Pi: --camera /dev/videoN)
"""
from __future__ import annotations

import json
import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
from PIL import Image

from .backend_base import Progress
from .config import Config
from .datatypes import EggResult
from .model_client import load_model, open_photo
from .pi_backend import PiBackend

log = logging.getLogger(__name__)

PHOTO_TYPES = {".jpg", ".jpeg", ".png", ".heic", ".heif"}

# Manual webcam exposure steps, darkest first. A candled egg is lit from behind and far brighter than a room, so
# the webcam's automatic exposure turns it into a white blob; these shorter exposures keep the shell detail.
# Linux (V4L2): units of 100 us (0.3 to 80 ms). Windows (DirectShow): log2 seconds (1/8192 to 1/8 s).
EXPOSURE_STEPS = list(range(-13, -2)) if sys.platform == "win32" else [3, 5, 10, 20, 40, 80, 150, 300, 500, 800]
# Webcams whose driver refuses manual exposure (many built-in laptop cameras) often still take brightness:
# offsets below the camera's own brightness, darkest first. It darkens the picture, but parts that are already
# burnt white at the sensor stay white, so a dimmer or diffused candling light may still be needed.
BRIGHTNESS_STEPS = [-128, -96, -64, -48, -32, -16, 0]
CLIPPED = 250                 # a pixel at or above this (0-255) in any colour has lost its detail
OVEREXPOSED = 0.02            # more than 2 % of the picture burnt white: too bright to judge
TOO_BRIGHT = "Too bright:"    # progress text that starts with this is shown as a warning
CAMERA_FILE = Path.home() / ".config" / "egg_inspector" / "camera.json"     # remembers the chosen exposure


def find_photos(folder: str | Path) -> list[Path]:
    folder = Path(folder)
    if not folder.is_dir():
        raise RuntimeError(f"Photo folder not found: {folder}")
    photos = sorted(p for p in folder.iterdir() if p.suffix.lower() in PHOTO_TYPES)
    if not photos:
        raise RuntimeError(f"No .jpg/.png/.heic photos in {folder}")
    return photos


def burnt(frame: Image.Image) -> float:
    """Share of the picture that is burnt white (no detail left for the model)."""
    return float((np.asarray(frame).max(axis=2) >= CLIPPED).mean())


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

    def _photo(self, index: int, stop: threading.Event, progress: Progress) -> tuple[Image.Image, str] | None:
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
    """A USB webcam instead of photos. A reader thread keeps only the newest frame, so a check never gets a
    stale one from OpenCV's buffer; live_frame() lets the UI show the feed and run the Live test."""

    def __init__(self, cfg: Config, camera: str):
        super().__init__(cfg, [], f"webcam {camera}")
        import cv2

        self.cam = open_webcam(camera)
        self.frame: Image.Image | None = None
        self.exposure: int | None = None         # light step (0 = darkest); None = the camera's own automatic
        self.mode = "exposure"                   # "brightness" if the driver refuses exposure; "none" if both
        self._brightness0 = self.cam.get(cv2.CAP_PROP_BRIGHTNESS)
        self._pending: list = []                 # settings for the reader thread: OpenCV isn't thread-safe
        try:
            saved = json.loads(CAMERA_FILE.read_text())
            self.mode = saved.get("mode", "exposure")
            self.set_exposure(saved["exposure"])
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            pass                                 # nothing saved yet: keep the camera's auto exposure
        self._reading = True
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        import cv2

        while self._reading:
            while self._pending:
                prop, value = self._pending.pop(0)
                done = self.cam.set(getattr(cv2, prop), value)
                if prop == "CAP_PROP_EXPOSURE" and not done:       # driver refuses: use brightness instead
                    self.mode = "brightness"
                    if self.exposure is not None:
                        self.set_exposure(min(self.exposure, len(BRIGHTNESS_STEPS) - 1), save=False)
                elif prop == "CAP_PROP_BRIGHTNESS" and not done:
                    self.mode = "none"
            ok, bgr = self.cam.read()
            if ok:
                self.frame = Image.fromarray(bgr[..., ::-1])             # OpenCV gives BGR
            else:
                time.sleep(0.05)

    def live_frame(self) -> Image.Image | None:
        return self.frame

    @property
    def steps(self) -> int:
        return len(EXPOSURE_STEPS) if self.mode == "exposure" else len(BRIGHTNESS_STEPS)

    def set_exposure(self, step: int | None, save: bool = True) -> None:
        """Light step (0 = darkest), or None for the camera's own automatic setting. Uses manual exposure, or
        brightness on webcams that refuse exposure."""
        win = sys.platform == "win32"
        if step is not None:
            step = max(0, min(self.steps - 1, int(step)))
        if self.mode == "exposure":
            self._pending += ([("CAP_PROP_AUTO_EXPOSURE", 0.75 if win else 3)] if step is None else
                              [("CAP_PROP_AUTO_EXPOSURE", 0.25 if win else 1),             # manual
                               ("CAP_PROP_EXPOSURE", EXPOSURE_STEPS[step]),
                               ("CAP_PROP_GAIN", 0)])                                      # strong backlight
        elif self.mode == "brightness":
            offset = 0 if step is None else BRIGHTNESS_STEPS[step]
            self._pending.append(("CAP_PROP_BRIGHTNESS", self._brightness0 + offset))
        self.exposure = step
        if save:
            try:
                CAMERA_FILE.parent.mkdir(parents=True, exist_ok=True)
                CAMERA_FILE.write_text(json.dumps({"exposure": step, "mode": self.mode}))
            except OSError:
                log.warning("Could not save the camera setting to %s", CAMERA_FILE)

    def auto_light(self, settle: float = 0.6) -> int | None:
        """Pick the brightest light step that doesn't burn out the candled egg: step up from the darkest until
        more than 1 % of the picture is clipped white, then keep the step before. Takes a few seconds.
        Returns None, and goes back to the camera's own setting, if the webcam takes neither exposure nor
        brightness."""
        self.set_exposure(0, save=False)         # also finds out whether the driver takes exposure
        time.sleep(settle)
        best = 0
        for step in range(self.steps):
            if self.mode == "none":
                self.set_exposure(None)
                return None
            self.set_exposure(step, save=False)
            time.sleep(settle)                   # let a few frames through at the new setting
            if self.frame is None or burnt(self.frame) > 0.01:
                break
            best = step
        self.set_exposure(best)
        return best

    def _photo(self, index: int, stop: threading.Event, progress: Progress) -> tuple[Image.Image, str] | None:
        """The newest frame, but only once it's usable: a burnt-white candled egg hides any crack, so judging it
        would be a guess. Waits (Stop still works) while the operator taps Auto or dims the light."""
        while True:
            frame = self.frame
            if frame is None:
                raise RuntimeError("No picture from the webcam")
            white = burnt(frame)
            if white <= OVEREXPOSED:
                return frame, "webcam"
            progress(f"{TOO_BRIGHT} Egg {index + 1}/{self.cfg.egg_count}: {white:.0%} of the picture is burnt "
                     "white. Waiting: tap Auto (Light), or dim / diffuse the candling light.")
            if stop.wait(0.3):
                return None

    def close(self) -> None:
        self._reading = False
        self._reader.join(1)                     # OpenCV can crash if released mid-read on another thread
        super().close()
        self.cam.release()
