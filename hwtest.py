"""Hardware bring-up menu: test one device at a time on the Pi. No camera or model needed.

Run on the Pi from the folder that CONTAINS egg_inspector/:
    python3 -m egg_inspector.hwtest
    python3 -m egg_inspector.hwtest --camera /dev/video0     # test photo from the USB webcam

Ctrl+C during a test switches everything off and returns to the menu; Ctrl+C at the menu quits.
Check PINS and ACTIVE_HIGH in config.py against your wiring first.
"""
from __future__ import annotations

import argparse
import threading
import time

from .config import ACTIVE_HIGH, PINS, Config
from .pi_backend import FakeVisionPiBackend

UVC_MAX_TEST_S = 10                 # keep bring-up exposures short


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
        if camera:                             # USB webcam for the test photo
            from .camera import Webcam

            self.hw.camera = Webcam(camera, cfg.camera_fps, cfg.camera_size)
        self.stop = threading.Event()           # Ctrl+C is handled as KeyboardInterrupt instead

    # ---- inputs ----------------------------------------------------------
    def sensors(self) -> None:
        print("Door switch for 15 s. Open and close the door to see it change.")
        end, last = time.monotonic() + 15, None
        while time.monotonic() < end:
            line = f"door={'CLOSED' if self.hw.door.is_pressed else 'open'}"
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
            ("Door switch (live)", self.sensors),
            ("Backlight on/off", self.backlight),
            ("Defect LEDs 1-10", self.leds),
            ("Buzzer", self.buzzer),
            ("UV-C: short test with door interlock", self.uvc),
            ("Camera: take a test photo (backlight on)", self.camera),
        ]
        while True:
            print("\n=== Egg Inspector hardware test ===")
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
            except RuntimeError as exc:             # missing camera
                print(f"  FAULT: {exc}")
            finally:
                light = self.hw.backlight.is_active
                self.hw.safe_off()                  # UV-C, buzzer off after every test
                if light:
                    self.hw.backlight.on()          # the light stays as the operator set it (camera tests need it)


def main() -> None:
    ap = argparse.ArgumentParser(description="Test the station hardware one device at a time")
    ap.add_argument("--camera", help="USB webcam index or path, e.g. /dev/video0, for the test photo")
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
        test.hw.clear_leds()
        if test.hw.camera is not None:
            test.hw.camera.close()
        print("All outputs off.")


if __name__ == "__main__":
    main()
