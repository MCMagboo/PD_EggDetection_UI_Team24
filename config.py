"""Central settings. Edit values here instead of hunting through the code."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    egg_count: int = 10               # eggs per tray (Table 1-1)
    uvc_seconds: int = 30             # UV-C exposure per run (Figure 2-11)
    auto_uvc: bool = True             # start UV-C automatically when cracked eggs are found (operator can toggle)
    detect_threshold: float | None = None   # crack score cut-off; None = the model's validated threshold (0.83)
    model_source: str = "local"       # "local" = the weights file below, on this machine; "api" = the Modal model API
    model_path: str = "model_best_quantized.pth"  # half-precision copy of model_best.pth (in git, same verdicts);
                                                  # relative paths are inside the egg_inspector folder
    model_api_url: str = "https://qeresanjuan-capstone-maskrcnn--capstone-maskrcnn-api-cra-86a8eb.us-east.modal.run"
    model_parallel: int = 4           # views sent to the model API at once (overlaps upload with the spin);
                                      # the local model runs one view at a time while the egg keeps spinning
    frames_per_egg: int = 8           # views captured per 360 deg egg spin (8 = every 45 deg)
    spin_seconds: float = 4.0         # time for the spin motor to turn an egg one full revolution (calibrate; depends on gear ratio)
    settle_seconds: float = 0.15      # pause after each spin step so the photo is not motion-blurred
    tray_move_timeout: float = 5.0    # max seconds to reach the next holder before reporting a fault
    tray_home_timeout: float = 30.0   # max seconds to reverse back to holder 1 before reporting a fault
    screen_w: int = 800               # used only in windowed mode; fullscreen reads the real screen
    screen_h: int = 480
    fullscreen: bool = True
    time_scale: float = 1.0           # <1.0 speeds up the mock backend for demos/tests


# BCM pin numbers. PLACEHOLDERS: edit to match your schematic (Figure 2-3).
PINS = {
    "backlight": 5,            # LED transillumination array (via driver/relay)
    "uvc_relay": 6,            # UV-C module (optocoupler relay)
    "tray_motor_fwd": 13,      # centre-shaft motor, forward input (H-bridge): next holder to the camera
    "tray_motor_rev": 16,      # centre-shaft motor, reverse input: return to holder 1 (unwinds the wiring)
    "tray_home": 20,           # home sensor; pressed = holder 1 at the camera
    "tray_slot": 21,           # slot sensor; pressed = a holder is aligned with the camera / spin gear
    "spin_motor": 12,          # motor by the camera stand: meshes with the holder gear, spins the egg 360 deg
    "buzzer": 19,              # 70-85 dB alert
    "door_switch": 26,         # enclosure interlock; pressed = door closed
    "defect_leds": [2, 3, 4, 14, 15, 17, 18, 22, 23, 24],  # one per tray slot
}

# How the relay/driver boards switch. CHECK BEFORE POWERING THE UV-C LAMP: many cheap relay
# modules are active-LOW (input pulled low = relay ON). With the wrong setting, "off" is "on".
# True = pin HIGH turns the device on; False = pin LOW turns it on.
ACTIVE_HIGH = {
    "backlight": True,
    "uvc_relay": True,
    "spin_motor": True,
}
