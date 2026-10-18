"""Real Raspberry Pi 5 backend. Fill in the NotImplementedError spots once the
model is trained and the wiring is final. Everything else (UI, flow, safety
logic, the per-egg spin + capture sequence) already works through the Backend contract.

Mechanism: eggs stand upright in 10 geared holders on a rotating tray (centre shaft =
tray_motor). A separate spin motor beside the camera stand meshes with the gear of
whichever holder is at the camera station and turns that egg about its vertical axis.

Per egg: tray motor brings the holder to the fixed camera (gears must line up) -> spin
motor turns the egg 360 deg in `frames_per_egg` steps (stop, settle, capture) -> model
scores every view -> the egg is defective if ANY view scores >= detect_threshold.
The two motors never run together: spinning while the tray moves would grind the gears.

Tray direction: forward only during a batch (holder 1 -> 10); afterwards it reverses to
holder 1, so it never turns a full revolution one way and the tray wiring cannot wind up.
"""
from __future__ import annotations

import threading
import time

from PIL import Image

from .backend_base import Progress
from .config import ACTIVE_HIGH, PINS, Config
from .datatypes import EggResult

View = tuple[Image.Image, Image.Image]      # (optical, thermal) captured at one spin angle


def open_camera():
    """Picamera2 for camera 0, with a clear message when none is connected."""
    from picamera2 import Picamera2

    try:
        return Picamera2(0)
    except IndexError as exc:                  # Picamera2 raises this when no camera is detected
        raise RuntimeError("No camera found. Check the ribbon cable (power off first), "
                           "then run: rpicam-hello --list-cameras") from exc


class PiBackend:
    def __init__(self, cfg: Config):
        from gpiozero import LED, Button, Buzzer, Motor, OutputDevice   # imported here so the mock runs anywhere

        def relay(name: str) -> OutputDevice:                      # starts OFF whichever way the board switches
            return OutputDevice(PINS[name], active_high=ACTIVE_HIGH[name], initial_value=False)

        self.cfg = cfg
        self.backlight = relay("backlight")
        self.uvc_relay = relay("uvc_relay")
        self.spin_motor = relay("spin_motor")
        self.tray_motor = Motor(forward=PINS["tray_motor_fwd"], backward=PINS["tray_motor_rev"], pwm=False)
        self.tray_home = Button(PINS["tray_home"], pull_up=True)   # pressed = holder 1 at the camera
        self.tray_slot = Button(PINS["tray_slot"], pull_up=True)   # pressed = a holder aligned at the camera
        self.slot: int | None = None                               # holder at the camera; None until homed
        self.buzzer = Buzzer(PINS["buzzer"])
        self.door = Button(PINS["door_switch"], pull_up=True)     # pressed = door closed (check your switch)
        self.defect_leds = [LED(p) for p in PINS["defect_leds"]]
        self.model = None
        self._init_camera()

    def _init_camera(self) -> None:
        self.cam = open_camera()
        self.cam.configure(self.cam.create_still_configuration(main={"size": (1640, 1232), "format": "RGB888"}))

    # ---- lifecycle -------------------------------------------------------
    def prepare(self) -> None:
        self.cam.start()
        self.model = self._load_model()

    def close(self) -> None:
        self.safe_off()
        self.backlight.off()
        self.cam.stop()

    # ---- sensors ---------------------------------------------------------
    def eggs_loaded(self) -> bool:
        return True   # TODO: replace with your tray-presence sensor or a camera check

    def enclosure_closed(self) -> bool:
        return self.door.is_pressed

    # ---- tray ------------------------------------------------------------
    def home_tray(self, stop: threading.Event) -> bool:
        """Reverse until holder 1 is at the camera. Always backwards: the tray only ever got
        here by moving forward from holder 1, so reversing unwinds the wiring."""
        if not self.tray_home.is_pressed and not self._run_tray(
                self.tray_motor.backward, [lambda: self.tray_home.is_pressed], stop,
                self.cfg.tray_home_timeout, "home sensor"):
            return False
        self.slot = 0
        return True

    def _index_tray(self, index: int, stop: threading.Event) -> bool:
        """Move forward, one holder at a time, until holder `index` is aligned with the camera."""
        if self.slot is None:
            raise RuntimeError("Tray position unknown; home the tray first")
        if index < self.slot:
            raise RuntimeError(f"Tray only moves forward during a batch (at holder {self.slot + 1}, "
                               f"asked for {index + 1}); home it first")
        while self.slot < index:
            # one motor run per holder: leave the current holder's mark, then stop on the next one
            if not self._run_tray(self.tray_motor.forward,
                                  [lambda: not self.tray_slot.is_pressed, lambda: self.tray_slot.is_pressed],
                                  stop, self.cfg.tray_move_timeout, "next holder"):
                return False
            self.slot += 1
        return True

    def _run_tray(self, direction, until, stop: threading.Event, timeout: float, what: str) -> bool:
        """Run the tray motor until each condition in `until` has become true in turn.
        Returns False on Stop (position then unknown, so the next Start homes first)."""
        end = time.monotonic() + timeout
        direction()
        try:
            for done in until:
                while not done():
                    if stop.wait(0.005):
                        self.slot = None
                        return False
                    if time.monotonic() > end:
                        self.slot = None
                        raise RuntimeError(f"Tray did not reach the {what} within {timeout:g} s. "
                                           "Check the tray motor and sensor.")
        finally:
            self.tray_motor.stop()
        return True

    # ---- inspection ------------------------------------------------------
    def inspect_egg(self, index: int, stop: threading.Event, progress: Progress) -> EggResult | None:
        t0 = time.perf_counter()
        egg = f"Egg {index + 1}/{self.cfg.egg_count}"
        self.backlight.on()
        progress(f"{egg}: moving tray")
        if not self._index_tray(index, stop):
            return None
        views = self._spin_and_capture(egg, stop, progress)
        if views is None:
            return None
        scores = []
        for k, (optical, _) in enumerate(views):
            if stop.is_set():
                return None
            progress(f"{egg}: analysing view {k + 1}/{len(views)}")
            scores.append(self._predict(optical))
        best = max(range(len(views)), key=scores.__getitem__)
        score = scores[best]
        defective = score >= self.cfg.detect_threshold
        optical, thermal = views[best]
        heatmap = self._explain(optical) if defective else None
        return EggResult(index, defective, score if defective else 1 - score, optical, thermal, heatmap,
                         (time.perf_counter() - t0) * 1000, views=len(views),
                         angle_deg=best * 360 / len(views))

    def _spin_and_capture(self, egg: str, stop: threading.Event, progress: Progress) -> list[View] | None:
        """One full turn in equal steps; capture while the egg is still (no motion blur)."""
        n = self.cfg.frames_per_egg
        views: list[View] = []
        for k in range(n):
            if k and not self._spin_step(stop):
                return None
            progress(f"{egg}: capturing view {k + 1}/{n}")
            views.append((self._optical_frame(), self._thermal_frame()))
        return views

    def _spin_step(self, stop: threading.Event) -> bool:
        """Turn the egg 1/frames_per_egg of a revolution, then let it settle.
        Time-based for a DC motor on a relay; with a stepper, drive a fixed step count here instead."""
        if self.tray_motor.is_active:
            raise RuntimeError("Tray motor still running; refusing to spin (gears would grind)")
        self.spin_motor.on()
        try:
            if stop.wait(self.cfg.spin_seconds / self.cfg.frames_per_egg):
                return False
        finally:
            self.spin_motor.off()
        return not stop.wait(self.cfg.settle_seconds)

    def _optical_frame(self) -> Image.Image:
        return Image.fromarray(self.cam.capture_array()[..., ::-1])   # Picamera2 "RGB888" is BGR order

    # ---- the pieces you still have to write ------------------------------
    def _load_model(self):
        raise NotImplementedError("Load the trained SOLOv2 weights (PyTorch) here")

    def _thermal_frame(self) -> Image.Image:
        raise NotImplementedError("Grab a frame from the infrared/thermal camera")

    def _predict(self, optical: Image.Image) -> float:
        raise NotImplementedError("Run SOLOv2 on one view; return its max crack score (0..1)")

    def _explain(self, optical: Image.Image) -> Image.Image:
        raise NotImplementedError("XGrad-CAM on ResNet-50 Layer 3 (1024x64x64), upsample, overlay on `optical`")

    # ---- outputs ---------------------------------------------------------
    def set_defect_led(self, index: int, on: bool) -> None:
        (self.defect_leds[index].on if on else self.defect_leds[index].off)()

    def clear_leds(self) -> None:
        for led in self.defect_leds:
            led.off()

    def beep(self) -> None:
        self.buzzer.beep(on_time=0.15, off_time=0.1, n=2, background=True)

    def uvc(self, on: bool) -> None:
        (self.uvc_relay.on if on else self.uvc_relay.off)()

    def safe_off(self) -> None:
        self.uvc_relay.off()
        self.tray_motor.stop()
        self.spin_motor.off()
        self.buzzer.off()


class _SimulatedModel:
    """Stands in for the model during bring-up: random crack scores (about 1 view in 25, so roughly
    1 egg in 4 is rejected) so the reject, LED and auto UV-C paths all run. The UI labels results
    as simulated whenever a backend has `simulated_results`."""

    simulated_results = True

    def __init__(self, cfg: Config):
        import numpy as np

        self._rng = np.random.default_rng()
        self._cracks: dict[int, object] = {}       # id(optical image) -> crack path or None
        super().__init__(cfg)

    def inspect_egg(self, index: int, stop: threading.Event, progress: Progress) -> EggResult | None:
        self._cracks.clear()
        return super().inspect_egg(index, stop, progress)

    def _crack_for(self, optical: Image.Image):
        from .mock_backend import _crack_path

        key = id(optical)
        if key not in self._cracks:
            self._cracks[key] = _crack_path(self._rng) if self._rng.random() < 0.04 else None
        return self._cracks[key]

    def _load_model(self):
        return None

    def _predict(self, optical: Image.Image) -> float:
        cracked = self._crack_for(optical) is not None
        return float(self._rng.uniform(0.3, 0.9) if cracked else self._rng.uniform(0.0, 0.1))

    def _explain(self, optical: Image.Image) -> Image.Image:
        from .mock_backend import H, W, _overlay

        return _overlay(optical.convert("RGB").resize((W, H)), self._crack_for(optical))


class NoModelPiBackend(_SimulatedModel, PiBackend):
    """Everything real (camera, motors, sensors, LEDs, buzzer, UV-C) except the model, whose
    verdicts are simulated. The infrared panel shows a placeholder until _thermal_frame is written."""

    def prepare(self) -> None:
        self.cam.start()

    def _thermal_frame(self) -> Image.Image | None:
        try:
            return super()._thermal_frame()
        except NotImplementedError:
            return None                         # UI shows "Infrared camera not connected"


class FakeVisionPiBackend(_SimulatedModel, PiBackend):
    """Hardware bring-up without a camera: real motors, sensors, LEDs, buzzer and UV-C,
    synthetic egg images and simulated scores."""

    def _init_camera(self) -> None:
        self.cam = None

    def prepare(self) -> None:
        pass

    def close(self) -> None:
        self.safe_off()
        self.backlight.off()

    def _optical_frame(self) -> Image.Image:
        from .mock_backend import _crack_path, _optical

        crack = _crack_path(self._rng) if self._rng.random() < 0.04 else None
        img = _optical(self._rng, crack)
        self._cracks[id(img)] = crack           # draw the crack the score will "find"
        return img

    def _thermal_frame(self) -> Image.Image:
        from .mock_backend import _thermal

        return _thermal(None)
