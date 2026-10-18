"""Worker thread that runs an inspection cycle or a manual UV-C run and reports to the UI via a queue.

Nothing here touches Tkinter, so it can be unit-tested without a display.
UV-C policy:
  * after inspection, UV-C runs automatically only if cracked eggs were found and `auto_uvc` is on
  * the operator can also start a UV-C run on demand with `start_uvc()`
Safety rules enforced in software (the hardware interlock must ALSO cut UV-C power on its own):
  * UV-C never starts with the chamber door open
  * UV-C stops within ~50 ms of Stop or a door opening
  * UV-C is always switched off in `finally`
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from typing import Callable

from .backend_base import Backend
from .config import Config
from .datatypes import Event, Summary, UvcOutcome

log = logging.getLogger(__name__)


class InspectionController:
    def __init__(self, backend: Backend, cfg: Config, events: queue.Queue):
        self.backend, self.cfg, self.events = backend, cfg, events
        self.auto_uvc = cfg.auto_uvc               # set from the UI thread; read once inspection ends
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """Inspect the whole tray, then UV-C if cracks were found (and auto UV-C is on)."""
        self._launch(self._cycle)

    def start_uvc(self) -> None:
        """Operator-requested UV-C run, independent of any inspection result."""
        self._launch(self._manual_uvc)

    def stop(self) -> None:
        self._stop.set()

    def join(self, timeout: float = 2.0) -> None:
        if self._thread:
            self._thread.join(timeout)

    def _launch(self, job: Callable[[], None]) -> None:
        if self.running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(job,), daemon=True)
        self._thread.start()

    def _emit(self, kind: str, data: object = None) -> None:
        self.events.put(Event(kind, data))

    def _run(self, job: Callable[[], None]) -> None:
        try:
            job()
        except Exception as exc:                      # report any failure to the operator
            log.exception("Worker job failed")
            self._emit("error", f"{type(exc).__name__}: {exc}")
        finally:
            self.backend.safe_off()
            self._emit("finished")

    def _cycle(self) -> None:
        self.backend.clear_leds()
        self._emit("status", "Checking tray")
        if not self.backend.eggs_loaded():
            self._emit("error", "No eggs detected. Load the tray and press Start.")
            return
        self._emit("status", "Homing tray")
        if not self.backend.home_tray(self._stop):     # left mid-way by an earlier Stop or fault
            self._emit("stopped")
            return
        t0 = time.perf_counter()
        flagged = self._inspect_all()
        if flagged is None:
            self._emit("stopped")
            return
        inspection_s = time.perf_counter() - t0
        self._emit("status", "Returning tray to holder 1")
        if not self.backend.home_tray(self._stop):     # reverse, so the tray wiring unwinds
            self._emit("stopped")
            return
        if not flagged:
            outcome = UvcOutcome("skipped", "No cracks found, so UV-C was not needed.")
        elif not self.auto_uvc:
            outcome = UvcOutcome("skipped", "Auto UV-C is off. Tap Run UV-C to disinfect.")
        else:
            self._emit("status", "Cracks found: starting UV-C")
            outcome = self._run_uvc()
        self._emit("done", Summary(flagged, inspection_s, outcome))

    def _manual_uvc(self) -> None:
        self._emit("status", "Manual UV-C")
        self._emit("uvc_done", self._run_uvc())

    def _inspect_all(self) -> list[int] | None:
        flagged: list[int] = []
        total = self.cfg.egg_count
        for i in range(total):
            self._emit("status", f"Inspecting egg {i + 1}/{total}")
            result = self.backend.inspect_egg(i, self._stop, lambda text: self._emit("status", text))
            if result is None:
                return None
            self._emit("egg", result)
            if result.defective:
                flagged.append(i)
                self.backend.set_defect_led(i, True)
                self.backend.beep()
        return flagged

    def _run_uvc(self) -> UvcOutcome:
        if not self.backend.enclosure_closed():
            return UvcOutcome("blocked", "Close the chamber door. UV-C was not started.")
        total = self.cfg.uvc_seconds
        self.backend.uvc(True)
        try:
            for elapsed in range(total):
                self._emit("uvc", (elapsed, total))
                reason = self._hold(1.0 * self.cfg.time_scale)
                if reason:
                    return UvcOutcome("interrupted", reason)
            self._emit("uvc", (total, total))
            return UvcOutcome("completed")
        finally:
            self.backend.uvc(False)

    def _hold(self, seconds: float) -> str:
        """Wait while watching Stop and the door. Returns a reason string if UV-C must end."""
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if self._stop.wait(0.05):
                return "UV-C stopped by operator."
            if not self.backend.enclosure_closed():
                return "Door opened. UV-C shut off."
        return ""
