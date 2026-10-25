"""Cameras with a fixed frame rate, and capture while the egg turns.

Two motions on the rig: the TRAY (centre shaft) carries the holders to the camera, and the SPIN motor there turns
one egg about its own axis. Frames are only used while the tray is still and the egg is turning.

Webcam (USB, OpenCV) and PiCamera (ribbon camera, Picamera2) both run a reader thread at a fixed frame rate and
keep only the newest frame, numbered, with the time it arrived. sweep() then watches one full turn of the egg: the
turn is split into equal time slices (equal angles at a steady spin speed), and in each slice it keeps the sharpest
frame that isn't burnt white. Motion blur hides hairline cracks, so the sharpest frame wins.
"""
from __future__ import annotations

import json
import logging
import math
import subprocess
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image

log = logging.getLogger(__name__)

# Manual webcam exposure steps, darkest first. A candled egg is lit from behind and far brighter than a room, so
# the webcam's automatic exposure turns it into a white blob; these shorter exposures keep the shell detail.
# Linux (V4L2): units of 100 us (0.3 to 80 ms). Windows (DirectShow): log2 seconds (1/8192 to 1/8 s).
# Steps longer than one frame are dropped (see Webcam), or the camera would slow down to fit them.
EXPOSURE_STEPS = list(range(-13, -2)) if sys.platform == "win32" else [3, 5, 10, 20, 40, 80, 150, 300, 500, 800]
# Webcams whose driver refuses manual exposure (many built-in laptop cameras) often still take brightness:
# offsets below the camera's own brightness, darkest first. It darkens the picture, but parts that are already
# burnt white at the sensor stay white, so a dimmer or diffused candling light may still be needed.
BRIGHTNESS_STEPS = [-128, -96, -64, -48, -32, -16, 0]
CLIPPED = 250                 # a pixel at or above this (0-255) in any colour has lost its detail
OVEREXPOSED = 0.02            # more than 2 % of the picture burnt white: too bright to judge
TOO_BRIGHT = "Too bright:"    # progress text that starts with this is shown as a warning
CAMERA_FILE = Path.home() / ".config" / "egg_inspector" / "camera.json"     # remembers the chosen exposure

Frame = tuple[int, float, Image.Image]          # (number, time.monotonic() when it arrived, picture)


def burnt(frame: Image.Image) -> float:
    """Share of the picture that is burnt white (no detail left for the model)."""
    return float((np.asarray(frame).max(axis=2) >= CLIPPED).mean())


def sharpness(frame: Image.Image) -> float:
    """Variance of the Laplacian on a small grey copy: high = crisp edges, low = motion blur or out of focus."""
    small = frame.convert("L")
    small.thumbnail((320, 320))
    g = np.asarray(small, np.float32)
    lap = 4 * g[1:-1, 1:-1] - g[:-2, 1:-1] - g[2:, 1:-1] - g[1:-1, :-2] - g[1:-1, 2:]
    return float(lap.var())


class _Stream:
    """Reader thread: grabs frames as fast as the camera delivers them (its fixed rate), keeps the newest."""

    def __init__(self):
        self.frame: Image.Image | None = None
        self.seq = 0
        self.stamp = 0.0
        self._arrivals: deque[float] = deque(maxlen=31)
        self._new = threading.Condition()
        self._reading = True
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _grab(self) -> Image.Image | None:
        raise NotImplementedError

    def _read(self) -> None:
        while self._reading:
            img = self._grab()
            if img is None:
                time.sleep(0.05)
                continue
            now = time.monotonic()
            with self._new:
                self.frame, self.stamp = img, now
                self.seq += 1
                self._arrivals.append(now)
                self._new.notify_all()

    def live_frame(self) -> Image.Image | None:
        return self.frame

    def next_frame(self, after: int, timeout: float = 1.0) -> Frame | None:
        """The first frame newer than number `after`; None if the camera sent nothing for `timeout` s."""
        with self._new:
            if not self._new.wait_for(lambda: self.seq > after, timeout):
                return None
            return self.seq, self.stamp, self.frame

    @property
    def fps(self) -> float:
        """Frames per second actually arriving (over the last ~30 frames); 0 if the camera has stalled."""
        t = list(self._arrivals)
        if len(t) < 2 or time.monotonic() - t[-1] > 1:
            return 0.0
        return (len(t) - 1) / (t[-1] - t[0])

    def close(self) -> None:
        self._reading = False
        self._reader.join(1)                     # OpenCV can crash if released mid-read on another thread


def open_webcam(camera: str, fps: int, size: tuple[int, int]):
    """`camera` is an index ("0") or a device path ("/dev/video8"). Asks for MJPG at `size` and `fps`: most USB
    webcams only reach 30 fps at 720p in MJPG (raw YUYV drops to 5-10 fps)."""
    import cv2

    source = int(camera) if camera.isdigit() else camera
    cam = cv2.VideoCapture(source, cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY)
    if cam.isOpened():
        cam.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        cam.set(cv2.CAP_PROP_FRAME_WIDTH, size[0])
        cam.set(cv2.CAP_PROP_FRAME_HEIGHT, size[1])
        cam.set(cv2.CAP_PROP_FPS, fps)
        cam.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    if not cam.isOpened() or not cam.read()[0]:
        raise RuntimeError(f"No webcam at {camera}. Check the USB cable, or try another --camera "
                           "(on the Pi, `v4l2-ctl --list-devices` shows the webcam's /dev/video path).")
    if sys.platform.startswith("linux"):
        # UVC webcams lower their frame rate in dim light unless told not to (control name differs by kernel)
        device = f"/dev/video{camera}" if camera.isdigit() else camera
        for ctl in ("exposure_dynamic_framerate=0", "exposure_auto_priority=0"):
            try:
                subprocess.run(["v4l2-ctl", "-d", device, "-c", ctl], capture_output=True, timeout=3)
            except (OSError, subprocess.SubprocessError):
                break                            # no v4l2-ctl: install v4l-utils (install.sh does)
    return cam


class Webcam(_Stream):
    """USB webcam at a fixed frame rate, with the candling light control (exposure, or brightness as fallback)."""

    def __init__(self, camera: str, fps: int = 30, size: tuple[int, int] = (1280, 720)):
        import cv2

        self.name = f"webcam {camera}"
        self.cam = open_webcam(camera, fps, size)
        w, h = int(self.cam.get(cv2.CAP_PROP_FRAME_WIDTH)), int(self.cam.get(cv2.CAP_PROP_FRAME_HEIGHT))
        log.info("Webcam %s: %dx%d, asked for %d fps (the camera reports %.0f)",
                 camera, w, h, fps, self.cam.get(cv2.CAP_PROP_FPS))
        frame_s = 1 / fps                        # an exposure longer than one frame would slow the camera down
        self.exposure_steps = [e for e in EXPOSURE_STEPS
                               if (2.0 ** e if sys.platform == "win32" else e / 10000) <= frame_s]
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
        super().__init__()

    def _grab(self) -> Image.Image | None:
        import cv2

        while self._pending:
            prop, value = self._pending.pop(0)
            done = self.cam.set(getattr(cv2, prop), value)
            if prop == "CAP_PROP_EXPOSURE" and not done:           # driver refuses: use brightness instead
                self.mode = "brightness"
                if self.exposure is not None:
                    self.set_exposure(min(self.exposure, len(BRIGHTNESS_STEPS) - 1), save=False)
            elif prop == "CAP_PROP_BRIGHTNESS" and not done:
                self.mode = "none"
        ok, bgr = self.cam.read()
        return Image.fromarray(bgr[..., ::-1]) if ok else None    # OpenCV gives BGR

    @property
    def steps(self) -> int:
        return len(self.exposure_steps) if self.mode == "exposure" else len(BRIGHTNESS_STEPS)

    def set_exposure(self, step: int | None, save: bool = True) -> None:
        """Light step (0 = darkest), or None for the camera's own automatic setting. Uses manual exposure, or
        brightness on webcams that refuse exposure."""
        win = sys.platform == "win32"
        if step is not None:
            step = max(0, min(self.steps - 1, int(step)))
        if self.mode == "exposure":
            self._pending += ([("CAP_PROP_AUTO_EXPOSURE", 0.75 if win else 3)] if step is None else
                              [("CAP_PROP_AUTO_EXPOSURE", 0.25 if win else 1),             # manual
                               ("CAP_PROP_EXPOSURE", self.exposure_steps[step]),
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

    def close(self) -> None:
        super().close()
        self.cam.release()


def open_camera():
    """Picamera2 for camera 0, with a clear message when none is connected."""
    from picamera2 import Picamera2

    try:
        return Picamera2(0)
    except IndexError as exc:                  # Picamera2 raises this when no camera is detected
        raise RuntimeError("No camera found. Check the ribbon cable (power off first), "
                           "then run: rpicam-hello --list-cameras") from exc


class PiCamera(_Stream):
    """Ribbon camera (Picamera2) streaming at a fixed frame rate: every frame takes exactly 1/fps."""

    def __init__(self, fps: int = 30, size: tuple[int, int] = (1640, 1232)):
        self.name = "Pi camera"
        self.cam = open_camera()
        frame_us = int(1_000_000 / fps)
        self.cam.configure(self.cam.create_video_configuration(
            main={"size": size, "format": "RGB888"}, controls={"FrameDurationLimits": (frame_us, frame_us)}))
        self.cam.start()
        super().__init__()

    def _grab(self) -> Image.Image | None:
        return Image.fromarray(self.cam.capture_array()[..., ::-1])   # Picamera2 "RGB888" is BGR order

    def close(self) -> None:
        super().close()
        self.cam.stop()
        self.cam.close()


EGG_AREA = (0.02, 0.8)        # an egg covers 2-80 % of the picture
EGG_CONTRAST = 30             # and is at least this much brighter (0-255) than its surroundings
EGG_MIN_WIDTH = 0.08          # and at least 8 % of the picture wide along the tray's direction


def egg_position(frame: Image.Image, axis: str = "x") -> float | None:
    """Where the egg is along the tray's direction of travel in the picture (0 = left/top, 1 = right/bottom), or
    None if there's no egg in view. The candled egg is the big bright region: an Otsu threshold on a small grey
    copy, then the widest run of bright columns (axis "x") or rows (axis "y")."""
    small = frame.convert("L")
    small.thumbnail((160, 160))
    g = np.asarray(small)
    p = np.bincount(g.ravel(), minlength=256) / g.size
    omega, mu = np.cumsum(p), np.cumsum(p * np.arange(256))
    with np.errstate(divide="ignore", invalid="ignore"):
        between = (mu[-1] * omega - mu) ** 2 / (omega * (1 - omega))
    mask = g > int(np.nanargmax(between))
    if not EGG_AREA[0] <= mask.mean() <= EGG_AREA[1]:
        return None
    if g[mask].mean() - g[~mask].mean() < EGG_CONTRAST:
        return None                           # an even picture (empty holder): the threshold only split noise
    bright = mask.mean(axis=0 if axis == "x" else 1) > 0.15
    best, start = (0, 0), None
    for i, on in enumerate(np.append(bright, False)):
        if on and start is None:
            start = i
        elif not on and start is not None:
            best = max(best, (start, i), key=lambda run: run[1] - run[0])
            start = None
    if (best[1] - best[0]) / len(bright) < EGG_MIN_WIDTH:
        return None
    return (best[0] + best[1]) / 2 / len(bright)


def wait_usable(cam: _Stream, stop: threading.Event, warn: Callable[[float], None]) -> bool:
    """Wait until the picture isn't burnt white (a white blob hides any crack, so judging it would be a guess).
    `warn(share burnt)` is called while waiting. False if Stop was pressed."""
    while True:
        frame = cam.frame
        if frame is None:
            raise RuntimeError(f"No picture from the {cam.name}")
        white = burnt(frame)
        if white <= OVEREXPOSED:
            return True
        warn(white)
        if stop.wait(0.3):
            return False


def sweep(cam: _Stream, seconds: float, views: int, stop: threading.Event,
          on_view: Callable[[int, Image.Image | None], None]) -> bool:
    """Watch the camera for `seconds` (one full turn of the egg) split into `views` equal slices. At the end of each
    slice, `on_view(k, frame)` gets that slice's sharpest frame that isn't burnt white (None if every frame in it
    was), so the caller can start the model while the egg keeps turning. False if Stop was pressed."""
    after = cam.seq                              # only frames taken from now on
    start = time.monotonic()
    best: tuple[float, Image.Image] | None = None
    k = 0
    while k < views:
        got = cam.next_frame(after, timeout=1.0)
        if stop.is_set():
            return False
        if got is None:
            raise RuntimeError(f"The {cam.name} stopped sending pictures")
        after, stamp, frame = got
        slice_k = min(views, math.floor((stamp - start) / seconds * views))
        while k < slice_k:                       # the frame belongs to a later slice: this one is complete
            on_view(k, best[1] if best else None)
            best, k = None, k + 1
        if k < views and burnt(frame) <= OVEREXPOSED:
            score = sharpness(frame)
            if best is None or score > best[0]:
                best = (score, frame)
    return True
