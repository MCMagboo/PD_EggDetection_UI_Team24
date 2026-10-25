"""Real Raspberry Pi 5 backend: camera, model, candling light, defect LEDs, buzzer, UV-C relay and door switch.

No motors for now: the operator places each egg in front of the fixed camera. For each tray slot the app waits
until the picture isn't burnt white, takes the sharpest frame of half a second (camera.capture), and runs the
model on it: crack score, XGrad-CAM heatmap and the explanation. The tray and spin motor code is in git history
(commit 9752eeb) for when the mechanism is ready.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor

from PIL import Image

from .backend_base import Progress
from .camera import TOO_BRIGHT, PiCamera, Webcam, capture
from .config import ACTIVE_HIGH, PINS, Config
from .datatypes import EggResult
from .model_client import ViewScore, load_model


def wait_for_swap(cfg: Config, index: int, stop: threading.Event, progress: Progress) -> bool:
    """No motors: give the operator `swap_seconds` to put egg `index` in front of the camera (not before egg 1).
    False if Stop was pressed."""
    if index == 0:
        return True
    for left in range(cfg.swap_seconds, 0, -1):
        progress(f"Place egg {index + 1}/{cfg.egg_count} in front of the camera ({left} s)")
        if stop.wait(1.0):
            return False
    return True


class PiBackend:
    def __init__(self, cfg: Config, camera: str | None = None):
        """`camera`: a USB webcam (index or /dev/videoN) instead of the ribbon camera."""
        from gpiozero import LED, Button, Buzzer, OutputDevice   # imported here so the mock runs anywhere

        def relay(name: str) -> OutputDevice:                      # starts OFF whichever way the board switches
            return OutputDevice(PINS[name], active_high=ACTIVE_HIGH[name], initial_value=False)

        self.cfg = cfg
        self.backlight = relay("backlight")
        self.uvc_relay = relay("uvc_relay")
        self.buzzer = Buzzer(PINS["buzzer"])
        self.door = Button(PINS["door_switch"], pull_up=True)     # pressed = door closed (check your switch)
        self.defect_leds = [LED(p) for p in PINS["defect_leds"]]
        self.model = None
        self.pool = ThreadPoolExecutor(1, thread_name_prefix="model")
        self._init_camera(camera)

    def _init_camera(self, camera: str | None) -> None:
        cfg = self.cfg
        self.camera = Webcam(camera, cfg.camera_fps, cfg.camera_size) if camera else PiCamera(cfg.camera_fps)

    # ---- lifecycle -------------------------------------------------------
    def prepare(self) -> None:
        self.model = self._load_model()

    def close(self) -> None:
        self.safe_off()
        self.camera.close()
        self._close_model()
        self._close_pins()

    def _close_pins(self) -> None:
        for device in (self.backlight, self.uvc_relay, self.buzzer, self.door, *self.defect_leds):
            device.close()

    def _close_model(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)
        if self.model is not None:
            self.model.close()

    # ---- sensors ---------------------------------------------------------
    def eggs_loaded(self) -> bool:
        return True   # TODO: replace with your tray-presence sensor or a camera check

    def enclosure_closed(self) -> bool:
        return self.door.is_pressed

    # ---- inspection ------------------------------------------------------
    def inspect_egg(self, index: int, stop: threading.Event, progress: Progress) -> EggResult | None:
        t0 = time.perf_counter()
        egg = f"Egg {index + 1}/{self.cfg.egg_count}"
        self.backlight.on()
        if not wait_for_swap(self.cfg, index, stop, progress):
            return None
        progress(f"{egg}: taking the picture")
        optical = self._optical_frame(egg, stop, progress)
        if optical is None:
            return None
        progress(f"{egg}: checking for cracks")
        view = self._await(self.pool.submit(self._predict, optical), stop)
        if view is None:
            return None
        return EggResult(index, view.cracked, view.score if view.cracked else 1 - view.score, optical,
                         view.heatmap, (time.perf_counter() - t0) * 1000, facts=view.facts)

    def _optical_frame(self, egg: str, stop: threading.Event, progress: Progress) -> Image.Image | None:
        hint = "tap Auto (Light), or dim / diffuse" if hasattr(self.camera, "auto_light") else "dim or diffuse"
        return capture(self.camera, stop, lambda white: progress(
            f"{TOO_BRIGHT} {egg}: {white:.0%} of the picture is burnt white. Waiting: {hint} the candling light."))

    @staticmethod
    def _await(pending: Future[ViewScore], stop: threading.Event) -> ViewScore | None:
        """Wait for one verdict while watching Stop. Model API errors are raised here."""
        while True:
            try:
                return pending.result(timeout=0.05)
            except TimeoutError:
                if stop.is_set():
                    pending.cancel()              # a request already on the wire finishes in the background
                    return None

    # ---- model (Mask R-CNN + XGrad-CAM: local weights or the Modal API) -----
    def _load_model(self):
        return load_model(self.cfg)

    def _predict(self, optical: Image.Image) -> ViewScore:
        """Crack verdict for one picture; runs on the model thread."""
        return self.model.predict(optical)

    # ---- outputs ---------------------------------------------------------
    def set_defect_led(self, index: int, on: bool) -> None:
        (self.defect_leds[index].on if on else self.defect_leds[index].off)()

    def clear_leds(self) -> None:
        for led in self.defect_leds:
            led.off()

    def beep(self) -> None:
        self.buzzer.beep(on_time=0.15, off_time=0.1, n=2, background=True)

    def uvc(self, on: bool) -> None:
        if on:
            self.backlight.off()                    # the candling light isn't needed for UV-C
        (self.uvc_relay.on if on else self.uvc_relay.off)()

    def light(self, on: bool) -> None:
        """Candling light, e.g. for the UI's Live test while no inspection runs."""
        (self.backlight.on if on else self.backlight.off)()

    def safe_off(self) -> None:
        self.backlight.off()                        # inspection over: candling light off too
        self.uvc_relay.off()
        self.buzzer.off()


SIMULATED_THRESHOLD = 0.5


class _SimulatedModel:
    """Stands in for the model during bring-up: random crack scores (about 1 egg in 4 is rejected) so the reject,
    LED and auto UV-C paths all run. The UI labels results as simulated whenever a backend has `simulated_results`."""

    simulated_results = True

    def __init__(self, cfg: Config, camera: str | None = None):
        import numpy as np

        self._rng = np.random.default_rng()
        self._cracks: dict[int, object] = {}       # id(optical image) -> crack path or None
        super().__init__(cfg, camera)

    def inspect_egg(self, index: int, stop: threading.Event, progress: Progress) -> EggResult | None:
        self._cracks.clear()
        return super().inspect_egg(index, stop, progress)

    def _crack_for(self, optical: Image.Image):
        from .mock_backend import _crack_path

        key = id(optical)
        if key not in self._cracks:
            self._cracks[key] = _crack_path(self._rng) if self._rng.random() < 0.25 else None
        return self._cracks[key]

    def _load_model(self):
        return None

    def _predict(self, optical: Image.Image) -> ViewScore:
        from .mock_backend import H, W, _overlay

        crack = self._crack_for(optical)
        score = float(self._rng.uniform(0.55, 0.95) if crack else self._rng.uniform(0.0, 0.1))
        cracked = score >= (self.cfg.detect_threshold or SIMULATED_THRESHOLD)
        heatmap = _overlay(optical.convert("RGB").resize((W, H)), crack) if cracked else None
        return ViewScore(score, cracked, heatmap)


class NoModelPiBackend(_SimulatedModel, PiBackend):
    """Everything real (camera, light, LEDs, buzzer, UV-C, door) except the model, whose verdicts are simulated
    (no network or API credentials needed)."""

    def prepare(self) -> None:
        pass                                        # the camera is already streaming; no model to load


class FakeVisionPiBackend(_SimulatedModel, PiBackend):
    """Hardware bring-up without a camera: real light, LEDs, buzzer, UV-C and door, synthetic egg images and
    simulated scores."""

    def _init_camera(self, camera: str | None) -> None:
        self.camera = None

    def prepare(self) -> None:
        pass

    def close(self) -> None:
        self.safe_off()
        self._close_model()
        self._close_pins()

    def _optical_frame(self, egg: str, stop: threading.Event, progress: Progress) -> Image.Image | None:
        from .mock_backend import _crack_path, _optical

        crack = _crack_path(self._rng) if self._rng.random() < 0.25 else None
        img = _optical(self._rng, crack)
        self._cracks[id(img)] = crack           # draw the crack the score will "find"
        return img
