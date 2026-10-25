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
    swap_seconds: int = 5             # countdown before each egg after the first, to place it in front of the camera
    camera_fps: int = 30              # fixed camera frame rate
    camera_size: tuple[int, int] = (1280, 720)   # USB webcam resolution; 30 fps at 720p needs MJPG (set automatically)
    screen_w: int = 800               # used only in windowed mode; fullscreen reads the real screen
    screen_h: int = 480
    fullscreen: bool = True
    time_scale: float = 1.0           # <1.0 speeds up the mock backend for demos/tests


# BCM pin numbers. PLACEHOLDERS: edit to match your schematic (Figure 2-3).
PINS = {
    "backlight": 5,            # LED transillumination array (via driver/relay)
    "uvc_relay": 6,            # UV-C module (optocoupler relay)
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
}
