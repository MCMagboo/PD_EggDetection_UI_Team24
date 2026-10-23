"""Plain data containers passed between the worker thread and the UI."""
from __future__ import annotations

from dataclasses import dataclass

from PIL import Image


@dataclass
class EggResult:
    index: int                      # 0-based tray slot
    defective: bool
    confidence: float               # 0..1, confidence in the decision shown (crack or no crack)
    optical: Image.Image            # the view with the highest crack score
    heatmap: Image.Image | None     # XGrad-CAM overlay, only produced for defects
    latency_ms: float
    views: int = 1                  # how many angles were captured during the 360 deg spin
    angle_deg: float = 0.0          # spin angle of the view shown above
    source: str = ""                # photo file name (image test mode only)
    facts: dict | None = None       # heatmap measurements (explain.cam_facts); None without the local model
    explanation: str = ""           # plain-language interpretation shown next to the heatmap
    explained_by: str = ""          # who wrote it: the local SLM, or the fixed wording and why


@dataclass
class UvcOutcome:
    status: str                     # completed | interrupted | blocked | skipped
    note: str = ""


@dataclass
class Summary:
    defective: list[int]
    inspection_s: float
    uvc: UvcOutcome


@dataclass
class Event:
    kind: str                       # status | egg | uvc | done | uvc_done | stopped | error | finished
    data: object = None
