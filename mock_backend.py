"""Fake backend: synthetic candled-egg images so the whole UI works with no model or hardware."""
from __future__ import annotations

import logging
import threading
import time

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from .backend_base import Progress
from .config import Config
from .datatypes import EggResult

log = logging.getLogger(__name__)

W, H = 320, 240
_YY, _XX = np.mgrid[0:H, 0:W]
_R2 = ((_XX - W / 2) / (W * 0.35)) ** 2 + ((_YY - H / 2) / (H * 0.42)) ** 2
_EGG = _R2 <= 1.0


def _jet(v: np.ndarray) -> np.ndarray:
    """Float 0..1 array -> RGB uint8 jet colormap (no OpenCV needed)."""
    r = np.clip(1.5 - np.abs(4 * v - 3), 0, 1)
    g = np.clip(1.5 - np.abs(4 * v - 2), 0, 1)
    b = np.clip(1.5 - np.abs(4 * v - 1), 0, 1)
    return (np.stack([r, g, b], -1) * 255).astype(np.uint8)


def _crack_path(rng: np.random.Generator) -> list[tuple[float, float]]:
    x = rng.uniform(W * 0.35, W * 0.65)
    y = rng.uniform(H * 0.25, H * 0.4)
    pts = [(x, y)]
    for _ in range(int(rng.integers(5, 9))):
        x += rng.uniform(-14, 14)
        y += rng.uniform(6, 12)
        pts.append((x, y))
    return pts


def _crack_field(crack, blur: float) -> np.ndarray:
    """Blurred crack mask scaled to 0..1 (zeros when there is no crack)."""
    img = Image.new("L", (W, H), 0)
    if crack:
        ImageDraw.Draw(img).line(crack, fill=255, width=3)
    arr = np.asarray(img.filter(ImageFilter.GaussianBlur(blur)), dtype=np.float32)
    return arr / max(float(arr.max()), 1e-6)


def _optical(rng: np.random.Generator, crack) -> Image.Image:
    glow = np.clip(1.0 - 0.45 * _R2, 0, 1) * _EGG
    g = np.clip(glow + rng.normal(0, 0.06, glow.shape) * _EGG, 0, 1)
    rgb = np.stack([230 * g, 110 * g, 30 * g], -1).astype(np.uint8)
    img = Image.fromarray(rgb)
    if crack:
        ImageDraw.Draw(img).line(crack, fill=(255, 230, 170), width=2)
    return img.filter(ImageFilter.GaussianBlur(0.8))


def _thermal(crack) -> Image.Image:
    base = 0.35 * _EGG + 0.2 * np.clip(1.0 - _R2, 0, 1)
    return Image.fromarray(_jet(np.clip(base + 0.4 * _crack_field(crack, 2), 0, 1)))


def _overlay(optical: Image.Image, crack) -> Image.Image:
    field = _crack_field(crack, 10)
    heat = _jet(field).astype(np.float32)
    alpha = (0.7 * np.clip(field * 1.4, 0, 1))[..., None]
    base = np.asarray(optical, dtype=np.float32)
    return Image.fromarray((base * (1 - alpha) + heat * alpha).astype(np.uint8))


class MockBackend:
    def __init__(self, cfg: Config, seed: int | None = None):
        self.cfg = cfg
        self.rng = np.random.default_rng(seed)
        self.door_closed = True
        self.leds: set[int] = set()
        self.slot = 0                                    # holder at the camera
        self.max_slot = 0                                # furthest holder reached (tests check it stays < egg_count)

    def prepare(self) -> None:
        log.info("Mock backend ready")

    def eggs_loaded(self) -> bool:
        return True

    def enclosure_closed(self) -> bool:
        return self.door_closed

    def home_tray(self, stop: threading.Event) -> bool:
        if stop.wait(0.05 * self.slot * self.cfg.time_scale):      # reverse, slot by slot
            return False
        self.slot = 0
        return True

    def inspect_egg(self, index: int, stop: threading.Event, progress: Progress) -> EggResult | None:
        t0 = time.perf_counter()
        egg = f"Egg {index + 1}/{self.cfg.egg_count}"
        n = self.cfg.frames_per_egg
        progress(f"{egg}: moving tray")
        if stop.wait(0.2 * self.cfg.time_scale):
            return None
        self.slot = index
        self.max_slot = max(self.max_slot, index)
        for k in range(n):                               # stands in for spin step + capture + inference
            progress(f"{egg}: capturing view {k + 1}/{n}")
            if stop.wait(0.7 / n * self.cfg.time_scale):
                return None
        defective = bool(self.rng.random() < 0.3)
        crack = _crack_path(self.rng) if defective else None
        optical = _optical(self.rng, crack)
        conf = self.rng.uniform(0.60, 0.97) if defective else self.rng.uniform(0.85, 0.99)
        return EggResult(
            index=index,
            defective=defective,
            confidence=float(conf),
            optical=optical,
            thermal=_thermal(crack),
            heatmap=_overlay(optical, crack) if defective else None,
            latency_ms=(time.perf_counter() - t0) * 1000,
            views=n,
            angle_deg=float(self.rng.integers(n)) * 360 / n,   # view where the crack faced the camera
        )

    def set_defect_led(self, index: int, on: bool) -> None:
        (self.leds.add if on else self.leds.discard)(index)

    def clear_leds(self) -> None:
        self.leds.clear()

    def beep(self) -> None:
        log.debug("beep")

    def uvc(self, on: bool) -> None:
        log.info("UV-C %s", "ON" if on else "OFF")

    def safe_off(self) -> None:
        self.uvc(False)

    def close(self) -> None:
        self.safe_off()
