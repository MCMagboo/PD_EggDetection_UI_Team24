"""The contract between the UI/controller and the hardware + model.

MockBackend and PiBackend both implement this, so the UI never changes when
you swap the fake data for the real camera, UV-C relay and the Mask R-CNN model.

Inspection: the camera is fixed and the operator places each egg in front of it; one picture per egg goes to
the model (crack score, XGrad-CAM heatmap). No motors for now.
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
    def inspect_egg(self, index: int, stop: threading.Event, progress: Progress) -> EggResult | None:
        """Take the picture of egg `index` and run the model on it. `progress(text)` reports steps to the UI.
        Return None if `stop` was set."""
    def set_defect_led(self, index: int, on: bool) -> None: ...
    def clear_leds(self) -> None: ...
    def beep(self) -> None: ...
    def uvc(self, on: bool) -> None: ...
    def safe_off(self) -> None:
        """UV-C, buzzer and candling light OFF. Defect LEDs stay on for identification."""
    def close(self) -> None: ...
