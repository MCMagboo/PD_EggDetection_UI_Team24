"""Hardware bring-up menu: test one device at a time on the Pi. No camera or model needed.

Run on the Pi from the folder that CONTAINS egg_inspector/:
    python3 -m egg_inspector.hwtest
    python3 -m egg_inspector.hwtest --camera /dev/video0     # with the USB webcam: find eggs with the camera

Ctrl+C during a test switches everything off and returns to the menu; Ctrl+C at the menu quits.
Check PINS and ACTIVE_HIGH in config.py against your wiring first.
"""
from __future__ import annotations

import argparse
import threading
import time

from .camera import egg_position
from .config import ACTIVE_HIGH, PINS, Config
from .pi_backend import FakeVisionPiBackend

UVC_MAX_TEST_S = 10                 # keep bring-up exposures short
JOG_MAX_S = 3.0                     # sensorless tray jog limit per command


def ask(prompt: str, default: str = "") -> str:
    return input(f"{prompt} [{default}]: ").strip() or default


def ask_float(prompt: str, default: float, lo: float, hi: float) -> float | None:
    try:
        value = float(ask(prompt, str(default)))
    except ValueError:
        print("  Not a number.")
        return None
    if not lo <= value <= hi:
        print(f"  Must be between {lo:g} and {hi:g}.")
        return None
    return value


def print_pins() -> None:
    print("Pin map from config.py (BCM numbering):")
    for name, pin in PINS.items():
        polarity = ""
        if name in ACTIVE_HIGH:
            polarity = "  (on = pin HIGH)" if ACTIVE_HIGH[name] else "  (on = pin LOW, active-low board)"
        print(f"  {name:<15} {pin}{polarity}")


class HwTest:
    def __init__(self, cfg: Config, camera: str | None = None):
        self.cfg = cfg
        self.hw = FakeVisionPiBackend(cfg)     # real GPIO devices, no model
        if camera:                             # USB webcam: lets the tray find eggs with the camera
            from .camera import Webcam

            self.hw.camera = Webcam(camera, cfg.camera_fps, cfg.camera_size)
        self.stop = threading.Event()           # Ctrl+C is handled as KeyboardInterrupt instead

    # ---- inputs ----------------------------------------------------------
    def sensors(self) -> None:
        print("Live sensors for 15 s. Open/close the door and turn the tray by hand to see changes.")
        end, last = time.monotonic() + 15, None
        while time.monotonic() < end:
            line = (f"door={'CLOSED' if self.hw.door.is_pressed else 'open  '}  "
                    f"home={'YES' if self.hw.tray_home.is_pressed else 'no '}  "
                    f"slot={'ALIGNED' if self.hw.tray_slot.is_pressed else '-'}")
            if line != last:
                print("  " + line)
                last = line
            time.sleep(0.05)

    # ---- lights / sound --------------------------------------------------
    def backlight(self) -> None:
        self.hw.backlight.toggle()
        print(f"  Backlight {'ON' if self.hw.backlight.is_active else 'OFF'}")

    def leds(self) -> None:
        for i, led in enumerate(self.hw.defect_leds):
            print(f"  Defect LED {i + 1} (GPIO {PINS['defect_leds'][i]})")
            led.on()
            time.sleep(0.4)
            led.off()
        print("  All LEDs on for 1 s")
        for led in self.hw.defect_leds:
            led.on()
        time.sleep(1)
        self.hw.clear_leds()

    def buzzer(self) -> None:
        print("  Beep beep")
        self.hw.buzzer.beep(on_time=0.15, off_time=0.1, n=2, background=False)

    # ---- tray ------------------------------------------------------------
    def tray_jog(self) -> None:
        print("  Sensorless jog, for setup before the sensors work. Mind the wiring: jog back the other way.")
        direction = ask("  Direction f=forward / r=reverse", "f").lower()
        if direction not in ("f", "r"):
            print("  Enter f or r.")
            return
        secs = ask_float("  Seconds", 0.5, 0.05, JOG_MAX_S)
        if secs is None:
            return
        (self.hw.tray_motor.forward if direction == "f" else self.hw.tray_motor.backward)()
        try:
            time.sleep(secs)
        finally:
            self.hw.tray_motor.stop()
        print("  Done")

    def tray_home(self) -> None:
        if self.cfg.tray_find == "camera":
            print(f"  Reversing back to egg 1, counting eggs with the camera ({self.hw._found} found so far)...")
        else:
            print("  Reversing to holder 1 (home sensor)...")
        t0 = time.monotonic()
        self.hw.home_tray(self.stop)
        print(f"  Home in {time.monotonic() - t0:.1f} s")

    def tray_next(self) -> None:
        if self.cfg.tray_find == "camera":
            if self.hw.camera is None:
                print("  tray_find is 'camera': start this test with --camera /dev/videoN")
                return
            t0 = time.monotonic()
            self.hw._find_egg("Next egg", self.stop, lambda text: print("  " + text))
            print(f"  Egg lined up in {time.monotonic() - t0:.2f} s. Check the holder gear meshes with the spin "
                  "motor gear. If it stops off the gear, set egg_center in config.py (see 'Camera: where is the egg').")
            return
        if self.hw.slot is None:
            print("  Position unknown: run 'Tray: home' first.")
            return
        if self.hw.slot >= self.cfg.egg_count - 1:
            print("  Already at holder 10. Home the tray (reverse) instead of going further forward.")
            return
        t0 = time.monotonic()
        self.hw._index_tray(self.hw.slot + 1, self.stop)
        print(f"  At holder {self.hw.slot + 1} in {time.monotonic() - t0:.2f} s. "
              "Check the holder gear meshes with the spin motor gear.")

    # ---- spin ------------------------------------------------------------
    def spin_full(self) -> None:
        if self.hw.tray_motor.is_active:
            print("  Tray motor is running; not spinning.")
            return
        print(f"  Continuous spin for spin_seconds = {self.cfg.spin_seconds:g} s.")
        print("  Mark the egg holder: it should turn EXACTLY once. Adjust spin_seconds in config.py if not.")
        self.hw.spin_motor.on()
        try:
            time.sleep(self.cfg.spin_seconds)
        finally:
            self.hw.spin_motor.off()
        print("  Done")

    def spin_steps(self) -> None:
        n = self.cfg.frames_per_egg
        print(f"  {n} steps with {self.cfg.settle_seconds:g} s settle, exactly as during inspection.")
        for k in range(n):
            print(f"  View {k + 1}/{n} ({k * 360 / n:.0f} deg)")
            if k:
                self.hw._spin_step(self.stop)
            time.sleep(0.3)                     # stands in for the capture
        print("  Done. The egg should be one step short of a full turn (the next step returns it to 0 deg).")

    # ---- camera ----------------------------------------------------------
    def camera(self) -> None:
        import os

        from .camera import open_camera

        path = os.path.expanduser("~/egg_camera_test.jpg")
        if self.hw.camera is not None:             # the USB webcam
            self.hw.backlight.on()
            try:
                time.sleep(1.5)
                frame = self.hw.camera.live_frame()
            finally:
                self.hw.backlight.off()
            if frame is None:
                print("  No picture from the webcam.")
                return
            frame.save(path)
            print(f"  Saved {path} ({frame.size[0]}x{frame.size[1]}). Open it to check focus, framing and exposure.")
            return
        print("  Backlight on, capturing at 1640x1232 (the inspection resolution)...")
        cam = open_camera()
        self.hw.backlight.on()
        try:
            cam.configure(cam.create_still_configuration(main={"size": (1640, 1232)}))
            cam.start()
            time.sleep(1.5)                     # let auto-exposure settle on the backlit egg
            cam.capture_file(path)
        finally:
            self.hw.backlight.off()
            cam.close()
        print(f"  Saved {path}. Open it to check focus, framing and exposure of the egg at the camera.")

    def egg_live(self) -> None:
        if self.hw.camera is None:
            print("  Start this test with --camera /dev/videoN")
            return
        axis, centre = self.cfg.egg_axis, self.cfg.egg_center
        print(f"  Egg position for 20 s along {axis} (0 = {'left' if axis == 'x' else 'top'}, "
              f"1 = {'right' if axis == 'x' else 'bottom'}); lined up = {centre:g} +/- {self.cfg.egg_tolerance:g}.")
        print("  Jog the tray (or turn it by hand) until a holder's gear meshes with the spin gear, and note the")
        print("  number: put it in config.py as egg_center. 'no egg' with an egg in view: turn the candling light on.")
        end, last = time.monotonic() + 20, None
        while time.monotonic() < end:
            frame = self.hw.camera.live_frame()
            pos = None if frame is None else egg_position(frame, axis)
            line = "no egg" if pos is None else (f"egg at {pos:.2f}" + (
                "   LINED UP" if abs(pos - centre) <= self.cfg.egg_tolerance else ""))
            if line != last:
                print("  " + line)
                last = line
            time.sleep(0.1)

    # ---- UV-C ------------------------------------------------------------
    def uvc(self) -> None:
        print("  WARNING: UV-C damages eyes and skin. Do not look at the lamp; keep the chamber closed.")
        if not self.hw.enclosure_closed():
            print("  Door is OPEN (or the door switch is miswired). UV-C not started.")
            return
        secs = ask_float("  Seconds", 3, 1, UVC_MAX_TEST_S)
        if secs is None or ask("  Type UVC to switch the lamp on", "").upper() != "UVC":
            print("  Cancelled.")
            return
        self.hw.uvc(True)
        try:
            end = time.monotonic() + secs
            while time.monotonic() < end:
                if not self.hw.enclosure_closed():
                    print("  Door opened: UV-C switched off. (Software interlock works.)")
                    return
                time.sleep(0.05)
            print("  UV-C test complete.")
        finally:
            self.hw.uvc(False)
        print("  Now open the door and run this again: it must refuse to start.")

    # ---- menu ------------------------------------------------------------
    def loop(self) -> None:
        items = [
            ("Sensors: door, tray home, tray slot (live)", self.sensors),
            ("Backlight on/off", self.backlight),
            ("Defect LEDs 1-10", self.leds),
            ("Buzzer", self.buzzer),
            ("Tray: jog (no sensors, max 3 s)", self.tray_jog),
            ("Tray: home (reverse to holder 1)", self.tray_home),
            ("Tray: next holder (forward)" if self.cfg.tray_find == "sensor" else
             "Tray: find the next egg with the camera (forward)", self.tray_next),
            ("Camera: where is the egg (live, set egg_center)", self.egg_live),
            ("Spin: one full turn (calibrate spin_seconds)", self.spin_full),
            ("Spin: step sequence as in inspection", self.spin_steps),
            ("UV-C: short test with door interlock", self.uvc),
            ("Camera: take a test photo (backlight on)", self.camera),
        ]
        while True:
            if self.cfg.tray_find == "camera":
                where = f"egg {self.hw._found} of this run" if self.hw._found else "at the start"
            else:
                where = "unknown (not homed)" if self.hw.slot is None else f"holder {self.hw.slot + 1}"
            print(f"\n=== Egg Inspector hardware test ===   tray: {where}")
            for i, (label, _) in enumerate(items, 1):
                print(f" {i:>2}  {label}")
            print("  0  Quit (everything off)")
            try:
                choice = input("> ").strip()
            except (KeyboardInterrupt, EOFError):
                print()
                return
            if choice == "0":
                return
            if not choice.isdigit() or not 1 <= int(choice) <= len(items):
                continue
            try:
                items[int(choice) - 1][1]()
            except KeyboardInterrupt:
                print("\n  Interrupted.")
            except RuntimeError as exc:             # tray/spin faults, missing camera
                print(f"  FAULT: {exc}")
            finally:
                self.hw.safe_off()                  # motors, UV-C, buzzer off after every test


def main() -> None:
    ap = argparse.ArgumentParser(description="Test the station hardware one device at a time")
    ap.add_argument("--camera", help="USB webcam index or path, e.g. /dev/video0 (finds eggs with the camera)")
    args = ap.parse_args()
    print(__doc__)
    print_pins()
    if ask("\nDo these pins and relay settings match your wiring? y/n", "n").lower() != "y":
        print("Edit PINS and ACTIVE_HIGH in config.py first.")
        return
    from gpiozero.exc import BadPinFactory

    try:
        test = HwTest(Config(), args.camera)
    except BadPinFactory:
        print("\nNo GPIO pins found: this test must run ON the Raspberry Pi.\n"
              "To rehearse the menu on a PC (nothing physical happens), set simulated pins first:\n"
              "  PowerShell:  $env:GPIOZERO_PIN_FACTORY = \"mock\"\n"
              "  bash:        export GPIOZERO_PIN_FACTORY=mock")
        return
    try:
        test.loop()
    finally:
        test.hw.safe_off()
        test.hw.backlight.off()
        test.hw.clear_leds()
        if test.hw.camera is not None:
            test.hw.camera.close()
        print("All outputs off.")


if __name__ == "__main__":
    main()
