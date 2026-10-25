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
    explain_model: str = "qwen2.5:1.5b"   # small language model (Ollama) that words the heatmap explanation
    explain_url: str = "http://127.0.0.1:11434"   # Ollama on this machine
    model_api_url: str = "https://qeresanjuan-capstone-maskrcnn--capstone-maskrcnn-api-cra-86a8eb.us-east.modal.run"
    model_parallel: int = 4           # views sent to the model API at once (overlaps upload with the spin);
                                      # the local model runs one view at a time while the egg keeps spinning
    frames_per_egg: int = 8           # views checked per 360 deg egg spin (8 = every 45 deg)
    spin_seconds: float = 4.0         # time for the spin motor to turn an egg one full revolution (calibrate; depends on gear ratio)
    spin_capture: str = "continuous"  # "continuous" = the egg turns without stopping and the sharpest frame of each
                                      # 1/frames_per_egg of the turn is checked; "step" = stop, settle, photo, repeat
    spin_ramp_seconds: float = 0.2    # continuous: wait after switching the spin motor on, until it turns steadily
    settle_seconds: float = 0.15      # pause after the tray stops (and, in "step" mode, after each spin step)
    camera_fps: int = 30              # fixed camera frame rate (30 fps at 4 s per turn = a frame every 3 deg)
    camera_size: tuple[int, int] = (1280, 720)   # USB webcam resolution; 30 fps at 720p needs MJPG (set automatically)
    tray_find: str = "camera"         # "camera" = no tray sensors: turn the tray until the camera sees the next egg
                                      # lined up; "sensor" = home + slot sensors (PINS tray_home / tray_slot)
    egg_axis: str = "x"               # camera: how eggs cross the picture as the tray turns, "x" = left-right, "y" = up-down
    egg_center: float = 0.5           # camera: where along that axis an egg sits when its gear meshes with the spin
                                      # motor (0 = left/top, 1 = right/bottom; hwtest "Camera: where is the egg" shows it)
    egg_tolerance: float = 0.05       # camera: how far from egg_center still counts as lined up
    tray_pulse_seconds: float = 0.05  # camera: short tray nudges for the final line-up (the motor has no speed control)
    tray_move_timeout: float = 8.0    # max seconds to reach the next holder / egg before reporting a fault
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
