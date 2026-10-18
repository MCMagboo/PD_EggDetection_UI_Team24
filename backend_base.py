"""The contract between the UI/controller and the hardware + model.

MockBackend and PiBackend both implement this, so the UI never changes when
you swap the fake data for the real camera, motors, UV-C relay and SOLOv2 model.

Inspection mechanics: the camera is fixed. For each egg the tray (centre shaft) brings
its geared holder to the camera station, then the spin motor there meshes with the holder
gear and turns the egg 360 deg upright in `cfg.frames_per_egg` steps, with one optical +
infrared capture per step.

The tray only moves forward during a batch (holder 1 -> 10, 324 deg) and returns to
holder 1 in REVERSE afterwards, so it never turns a full revolution in one direction
and the wiring on the tray cannot wind up.
"""
from __future__ import annotations

import threading
from typing import Callable, Protocol

from .datatypes import EggResult

Progress = Callable[[str], None]


class Backend(Protocol):
    def prepare(self) -> None: ...
    def eggs_loaded(self) -> bool: ...
    def enclosure_closed(self) -> bool: ...
    def home_tray(self, stop: threading.Event) -> bool:
        """Reverse the tray until holder 1 is at the camera. Return False if `stop` was set."""
    def inspect_egg(self, index: int, stop: threading.Event, progress: Progress) -> EggResult | None:
        """Bring egg `index` under the camera, spin it 360 deg capturing every view, run the model
        on each view. `progress(text)` reports steps to the UI. Return None if `stop` was set."""
    def set_defect_led(self, index: int, on: bool) -> None: ...
    def clear_leds(self) -> None: ...
    def beep(self) -> None: ...
    def uvc(self, on: bool) -> None: ...
    def safe_off(self) -> None:
        """Motors, UV-C and buzzer OFF. Defect LEDs stay on for identification."""
    def close(self) -> None: ...
