"""Is this machine ready to inspect offline? Checks everything runs locally, with timings.

    python -m egg_inspector.selfcheck          (from the folder that contains egg_inspector/)

The installer runs this at the end. Nothing here uses the internet: the crack model is the file in this folder and
the SLM is Ollama at 127.0.0.1 (this machine).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

from .config import Config

SAMPLE = {"score": 0.959, "threshold": 0.83, "box_place": "centre", "box_area": 0.455, "hot_place": "centre",
          "hot_area": 0.035, "hot_spots": 3, "on_crack": 0.895}         # measurements of a real cracked egg


def main() -> None:
    cfg = Config()
    ok, rows = True, []

    def row(passed: bool, name: str, note: str = "", needed: bool = True) -> None:
        nonlocal ok
        ok &= passed or not needed
        rows.append(("OK  " if passed else ("FAIL" if needed else "--  "), name, note))
        print(f"{rows[-1][0]}  {name}  {note}", flush=True)

    weights = Path(cfg.model_path)
    weights = weights if weights.is_absolute() else Path(__file__).resolve().parent / weights
    row(weights.is_file(), "crack model file", f"{weights.name} ({weights.stat().st_size / 2**20:.0f} MB)"
        if weights.is_file() else f"missing: {weights}")
    try:
        import torch
        import torchvision
        row(True, "PyTorch", f"{torch.__version__}, torchvision {torchvision.__version__}")
    except ImportError as exc:
        row(False, "PyTorch", str(exc))
    try:
        import cv2
        row(True, "OpenCV (webcam)", cv2.__version__, needed=False)
    except ImportError:
        row(False, "OpenCV (webcam)", "not installed; only webcam mode needs it", needed=False)

    if ok:
        from PIL import Image

        from .model_client import load_model
        t = time.time()
        model = load_model(cfg)
        loaded = time.time() - t
        t = time.time()
        model.predict(Image.new("RGB", (640, 480), (200, 120, 40)))
        row(True, "crack model runs", f"loads in {loaded:.1f} s, one photo in {time.time() - t:.1f} s")

    from .explain import Explainer
    slm = Explainer(cfg.explain_model, cfg.explain_url)
    installed = slm.installed()
    if installed is None:
        row(False, "Ollama (local SLM server)", f"not running at {cfg.explain_url}; explanations use fixed wording",
            needed=False)
    elif not slm.pick():
        row(False, "SLM", f"none installed ({', '.join(installed) or 'empty'}); run: ollama pull "
            f"{cfg.explain_model}", needed=False)
    else:
        row(True, "Ollama (local SLM server)", f"{cfg.explain_url}, SLM {slm.model}")
        t = time.time()
        slm.warm()
        warm = time.time() - t
        t = time.time()
        text, source = slm.explain(SAMPLE)
        row("local SLM" in source, "SLM writes an explanation", f"loads in {warm:.1f} s, answers in "
            f"{time.time() - t:.1f} s: \"{text[:60]}…\"" if "local SLM" in source else source, needed=False)

    print("\nReady to run offline." if ok else "\nNot ready: fix the FAIL lines above.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
