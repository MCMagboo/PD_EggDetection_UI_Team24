"""Real Raspberry Pi 5 backend. Fill in the NotImplementedError spots once the
model is trained and the wiring is final. Everything else (UI, flow, safety
logic, the per-egg spin + capture sequence) already works through the Backend contract.

Mechanism: eggs stand upright in 10 geared holders on a rotating tray (centre shaft =
tray_motor). A separate spin motor beside the camera stand meshes with the gear of
whichever holder is at the camera station and turns that egg about its vertical axis.

Per egg: tray motor brings the holder to the fixed camera (gears must line up) and the tray settles. Without tray
sensors (cfg.tray_find = "camera") the camera finds the next egg instead: the tray runs forward until an egg comes
near the lined-up position, then moves in short pulses until it sits there -> spin
motor turns the egg one full revolution WITHOUT stopping while the camera streams at a fixed frame rate
(cfg.camera_fps); the turn is split into `frames_per_egg` equal slices and the sharpest frame of each slice
(least motion blur) is kept (camera.sweep) -> the views go to the model (the API during the turn, the local
model right after it) -> the egg is defective if ANY view is cracked (score >= threshold).
cfg.spin_capture = "step" keeps the older stop-settle-photo cycle instead.
The two motors never run together: spinning while the tray moves would grind the gears, and frames taken
while the tray moves are never used.

Tray direction: forward only during a batch (holder 1 -> 10); afterwards it reverses to
holder 1, so it never turns a full revolution one way and the tray wiring cannot wind up.
"""
from __future__ import annotations

import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Callable

from PIL import Image

from .backend_base import Progress
from .camera import TOO_BRIGHT, PiCamera, Webcam, egg_position, sweep, wait_usable
from .config import ACTIVE_HIGH, PINS, Config
from .datatypes import EggResult
from .model_client import ViewScore, load_model

View = tuple[Image.Image, "Future[ViewScore]", float]   # captured frame, its pending model verdict, spin angle


def capture_turn(cam, cfg: Config, egg: str, stop: threading.Event, progress: Progress,
                 submit: Callable[[Image.Image], "Future[ViewScore]"], motor=None) -> list[View] | None:
    """One full turn of the egg without stopping, watched at the camera's fixed frame rate (camera.sweep): the
    sharpest usable frame of each 1/frames_per_egg of the turn becomes a view. `motor` (the spin motor; None when
    the egg is turned some other way) runs only during the turn. Views go to the model API during the turn; the
    local model waits until the turn is over, because it takes every CPU core and the frame rate would drop.
    Waits while the picture is burnt white, and turns again if every view was. None if Stop was pressed."""
    n = cfg.frames_per_egg
    overlap = cfg.model_source == "api"
    hint = "tap Auto (Light), or dim / diffuse" if hasattr(cam, "auto_light") else "dim or diffuse"
    while True:
        if not wait_usable(cam, stop, lambda white: progress(
                f"{TOO_BRIGHT} {egg}: {white:.0%} of the picture is burnt white. Waiting: {hint} the candling light.")):
            return None
        views: list = []

        def take(k: int, frame: Image.Image | None) -> None:
            progress(f"{egg}: turning, view {k + 1}/{n} ({cam.fps:.0f} fps)")
            if frame is not None:
                views.append((frame, submit(frame) if overlap else None, k * 360 / n))

        if motor is not None:
            motor.on()
        try:
            done = (motor is None or not stop.wait(cfg.spin_ramp_seconds)) and \
                sweep(cam, cfg.spin_seconds, n, stop, take)    # the ramp lets the motor reach a steady speed
        finally:
            if motor is not None:
                motor.off()
        if not done:
            PiBackend._cancel(views)
            return None
        if views:
            return [(frame, pending or submit(frame), angle) for frame, pending, angle in views]
        # every view was burnt white during the turn: wait for the light again and turn once more


class PiBackend:
    def __init__(self, cfg: Config, camera: str | None = None):
        """`camera`: a USB webcam (index or /dev/videoN) instead of the ribbon camera."""
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
        self._forward_s = 0.0                    # tray_find camera: forward run time since the start (to unwind)
        self._egg_here = False                   # tray_find camera: an inspected egg is still at the camera
        self._found = 0                          # tray_find camera: eggs found this batch (counted back to egg 1)
        self.buzzer = Buzzer(PINS["buzzer"])
        self.door = Button(PINS["door_switch"], pull_up=True)     # pressed = door closed (check your switch)
        self.defect_leds = [LED(p) for p in PINS["defect_leds"]]
        self.model = None
        self.pool = ThreadPoolExecutor(max(1, self.parallel_views()), thread_name_prefix="model")
        self._init_camera(camera)

    def parallel_views(self) -> int:
        return self.cfg.model_parallel

    def _init_camera(self, camera: str | None) -> None:
        cfg = self.cfg
        self.camera = Webcam(camera, cfg.camera_fps, cfg.camera_size) if camera else PiCamera(cfg.camera_fps)

    # ---- lifecycle -------------------------------------------------------
    def prepare(self) -> None:
        self.model = self._load_model()

    def close(self) -> None:
        self.safe_off()
        self.backlight.off()
        self.camera.close()
        self._close_model()

    def _close_model(self) -> None:
        self.pool.shutdown(wait=False, cancel_futures=True)
        if self.model is not None:
            self.model.close()

    # ---- sensors ---------------------------------------------------------
    def eggs_loaded(self) -> bool:
        return True   # TODO: replace with your tray-presence sensor or a camera check

    def enclosure_closed(self) -> bool:
        return self.door.is_pressed

    # ---- tray ------------------------------------------------------------
    def home_tray(self, stop: threading.Event) -> bool:
        """Reverse until holder 1 is at the camera. Always backwards: the tray only ever got
        here by moving forward from holder 1, so reversing unwinds the wiring."""
        if self.cfg.tray_find == "camera":
            return self._unwind(stop)
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

    # ---- tray without sensors: the camera finds the eggs ---------------------
    def _unwind(self, stop: threading.Event) -> bool:
        """No home sensor: reverse, counting eggs with the camera, until egg 1 of the batch is lined up again (so the
        wiring unwinds and the next batch starts at egg 1). Counting is exact; reversing by time is not, because
        the tray coasts a little after every stop. Only if no egg was found yet does it reverse by time."""
        if self._found:
            steps = self._found - (1 if self._egg_here else 0)    # eggs to go back past to reach egg 1
            for k in range(steps):
                if not self._find_egg(f"Returning to egg 1 ({k + 1}/{steps})", stop, lambda _text: None, -1):
                    return False
        elif self._forward_s > 0:
            t0 = time.monotonic()
            self.tray_motor.backward()
            try:
                stopped = stop.wait(self._forward_s)
            finally:
                self.tray_motor.stop()
                self._forward_s = max(0.0, self._forward_s - (time.monotonic() - t0))
            if stopped:
                return False
        self._forward_s, self._egg_here, self._found, self.slot = 0.0, False, 0, 0
        return True

    def _egg_offset(self) -> float | None:
        """How far a fresh frame shows the egg from the lined-up position (0..1 of the picture); None = no egg."""
        got = self.camera.next_frame(self.camera.seq, timeout=2.0)
        if got is None:
            raise RuntimeError(f"No picture from the {self.camera.name}")
        pos = egg_position(got[2], self.cfg.egg_axis)
        return None if pos is None else abs(pos - self.cfg.egg_center)

    def _find_egg(self, egg: str, stop: threading.Event, progress: Progress, way: int = 1) -> bool:
        """Turn the tray (forward, or back with way=-1) until the camera sees the next egg lined up with the spin
        gear. Runs past the egg just inspected, runs on until the next one comes near, then nudges the tray in short
        pulses (the motor has no speed control) until it sits within egg_tolerance. False on Stop."""
        if self.camera is None:
            raise RuntimeError("tray_find = 'camera' needs a camera (set tray_find = 'sensor' to use tray sensors)")
        cfg = self.cfg
        tol, near = cfg.egg_tolerance, max(3 * cfg.egg_tolerance, 0.15)
        end = time.monotonic() + cfg.tray_move_timeout

        def in_time() -> None:
            if time.monotonic() > end:
                raise RuntimeError(f"No egg lined up at the camera within {cfg.tray_move_timeout:g} s. Is the holder "
                                   "empty, the candling light off, or egg_center / egg_axis in config.py wrong? "
                                   "Check with: python -m egg_inspector.hwtest --camera ...")

        progress(f"{egg}: turning the tray to find the egg")
        off = self._egg_offset()
        leaving = self._egg_here                   # the egg just inspected is still in front of the camera
        if leaving or off is None or off > near:
            t0 = time.monotonic()
            (self.tray_motor.forward if way > 0 else self.tray_motor.backward)()
            try:
                while True:
                    if stop.is_set():
                        return False
                    in_time()
                    off = self._egg_offset()
                    if leaving:
                        leaving = off is not None and off <= near
                    elif off is not None and off <= near:
                        break
            finally:
                self.tray_motor.stop()
                self._forward_s += way * (time.monotonic() - t0)
        progress(f"{egg}: lining up the egg")
        direction, last = way, off
        while True:
            if stop.wait(cfg.settle_seconds):      # let the tray stop before judging the position
                return False
            off = self._egg_offset()
            if off is not None and off <= tol:
                break
            in_time()
            if off is None or (last is not None and off > last):
                direction = -direction             # went past the lined-up position (or lost the egg): back up
            last = off
            (self.tray_motor.forward if direction > 0 else self.tray_motor.backward)()
            try:
                time.sleep(cfg.tray_pulse_seconds)
            finally:
                self.tray_motor.stop()
            self._forward_s += direction * cfg.tray_pulse_seconds
        self._egg_here = True
        if way > 0:
            self._found += 1
        return True

    def _move_to_egg(self, index: int, egg: str, stop: threading.Event, progress: Progress) -> bool:
        if self.cfg.tray_find == "camera":
            return self._find_egg(egg, stop, progress)
        if self.cfg.tray_find != "sensor":
            raise ValueError(f"tray_find must be 'camera' or 'sensor', not {self.cfg.tray_find!r}")
        progress(f"{egg}: moving tray")
        return self._index_tray(index, stop)

    # ---- inspection ------------------------------------------------------
    def inspect_egg(self, index: int, stop: threading.Event, progress: Progress) -> EggResult | None:
        t0 = time.perf_counter()
        egg = f"Egg {index + 1}/{self.cfg.egg_count}"
        self.backlight.on()
        if not self._move_to_egg(index, egg, stop, progress) or stop.wait(self.cfg.settle_seconds):   # come to rest
            return None
        views = self._spin_and_capture(egg, stop, progress)
        if views is None:
            return None
        scores: list[ViewScore] = []
        for k, (_, pending, _) in enumerate(views):
            progress(f"{egg}: analysing view {k + 1}/{len(views)}")
            score = self._await(pending, stop)
            if score is None:
                self._cancel(views)
                return None
            scores.append(score)
        best = max(range(len(views)), key=lambda k: scores[k].score)
        view = scores[best]
        return EggResult(index, view.cracked, view.score if view.cracked else 1 - view.score, views[best][0],
                         view.heatmap, (time.perf_counter() - t0) * 1000, views=len(views),
                         angle_deg=views[best][2], facts=view.facts)

    def _spin_and_capture(self, egg: str, stop: threading.Event, progress: Progress) -> list[View] | None:
        if self.cfg.spin_capture == "step" or self.camera is None:
            return self._step_and_capture(egg, stop, progress)
        if self.cfg.spin_capture != "continuous":
            raise ValueError(f"spin_capture must be 'continuous' or 'step', not {self.cfg.spin_capture!r}")
        return self._turn_and_capture(egg, stop, progress)

    def _turn_and_capture(self, egg: str, stop: threading.Event, progress: Progress) -> list[View] | None:
        if self.tray_motor.is_active:
            raise RuntimeError("Tray motor still running; refusing to spin (gears would grind)")
        return capture_turn(self.camera, self.cfg, egg, stop, progress,
                            lambda frame: self.pool.submit(self._predict, frame), self.spin_motor)

    def _step_and_capture(self, egg: str, stop: threading.Event, progress: Progress) -> list[View] | None:
        """One full turn in equal steps; capture while the egg is still (no motion blur).
        Each view is handed to the model straight away, so inference runs during the next spin step."""
        n = self.cfg.frames_per_egg
        views: list[View] = []
        for k in range(n):
            if k and not self._spin_step(stop):
                self._cancel(views)
                return None
            progress(f"{egg}: capturing view {k + 1}/{n}")
            optical = self._optical_frame()
            views.append((optical, self.pool.submit(self._predict, optical), k * 360 / n))
        return views

    @staticmethod
    def _await(pending: Future[ViewScore], stop: threading.Event) -> ViewScore | None:
        """Wait for one verdict while watching Stop. Model API errors are raised here."""
        while True:
            try:
                return pending.result(timeout=0.05)
            except TimeoutError:
                if stop.is_set():
                    return None

    @staticmethod
    def _cancel(views: list[View]) -> None:
        for _, pending, _ in views:
            if pending is not None:
                pending.cancel()          # requests already on the wire finish in the background

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
        """A frame taken after the egg stopped (not one from the stream's past)."""
        got = self.camera.next_frame(self.camera.seq, timeout=2.0)
        if got is None:
            raise RuntimeError(f"No picture from the {self.camera.name}")
        return got[2]

    # ---- model (Mask R-CNN + XGrad-CAM: local weights or the Modal API) -----
    def _load_model(self):
        return load_model(self.cfg)

    def _predict(self, optical: Image.Image) -> ViewScore:
        """Crack verdict for one view; runs on the model thread pool."""
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
        (self.uvc_relay.on if on else self.uvc_relay.off)()

    def safe_off(self) -> None:
        self.uvc_relay.off()
        self.tray_motor.stop()
        self.spin_motor.off()
        self.buzzer.off()


SIMULATED_THRESHOLD = 0.5


class _SimulatedModel:
    """Stands in for the model during bring-up: random crack scores (about 1 view in 25, so roughly
    1 egg in 4 is rejected) so the reject, LED and auto UV-C paths all run. The UI labels results
    as simulated whenever a backend has `simulated_results`."""

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
            self._cracks[key] = _crack_path(self._rng) if self._rng.random() < 0.04 else None
        return self._cracks[key]

    def parallel_views(self) -> int:
        return 1                                    # instant verdicts; one thread keeps the shared state simple

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
    """Everything real (camera, motors, sensors, LEDs, buzzer, UV-C) except the model, whose
    verdicts are simulated (no network or API credentials needed)."""

    def prepare(self) -> None:
        pass                                        # the camera is already streaming; no model to load


class FakeVisionPiBackend(_SimulatedModel, PiBackend):
    """Hardware bring-up without a camera: real motors, sensors, LEDs, buzzer and UV-C,
    synthetic egg images and simulated scores."""

    def _init_camera(self, camera: str | None) -> None:
        self.camera = None                          # no camera: synthetic views, stop-and-capture spin

    def prepare(self) -> None:
        pass

    def close(self) -> None:
        self.safe_off()
        self.backlight.off()
        self._close_model()

    def _optical_frame(self) -> Image.Image:
        from .mock_backend import _crack_path, _optical

        crack = _crack_path(self._rng) if self._rng.random() < 0.04 else None
        img = _optical(self._rng, crack)
        self._cracks[id(img)] = crack           # draw the crack the score will "find"
        return img
