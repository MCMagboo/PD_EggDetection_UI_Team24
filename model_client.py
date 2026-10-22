"""Client for the deployed Mask R-CNN + XGrad-CAM model (Modal web API).

The model runs on a GPU server, not on the Pi: each view is sent as a JPEG to `POST {url}/predict`
and the reply (checked against api_contract.py) gives the crack decision, the max crack score
and, for cracked views, the XGrad-CAM overlay.

Credentials come from the environment, never from the repo:
    MASKRCNN_API_KEY, MASKRCNN_API_SECRET   Modal proxy token pair (required)
    MASKRCNN_API_URL                        optional; defaults to Config.model_api_url
deploy/run.sh loads them from ~/.config/egg_inspector/model.env.

Quick check from a terminal (PC or Pi):  python -m egg_inspector.model_client photo.jpg
"""
from __future__ import annotations

import base64
import io
import os
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from PIL import Image

from .api_contract import PredictionResponse

MAX_UPLOAD = 10 * 1024 * 1024               # the API rejects larger files
MAX_SIDE = 2048                             # ...and larger images (HTTP 413); phone photos are 4032 px
RETRIES = 3                                 # extra tries after a dropped connection or 502/503/504
RETRY_STATUS = {502, 503, 504}
RETRY_WAIT = 2.0                            # seconds, growing each try


@dataclass
class ViewScore:
    score: float                            # max crack score for this view, 0..1
    cracked: bool                           # score >= threshold
    heatmap: Image.Image | None             # XGrad-CAM overlay; None when not cracked (or CAM failed)


def _base_url(value: str) -> str:
    parts = urlsplit(value)
    if parts.scheme != "https" or not parts.netloc or parts.username or parts.query or parts.fragment:
        raise ValueError("Model API URL must be a plain https:// base URL")
    if parts.path.rstrip("/").endswith("/predict"):
        raise ValueError("Set the model API URL without /predict")
    return value.rstrip("/")


def _png(value: str, size: tuple[int, int]) -> Image.Image:
    img = Image.open(io.BytesIO(base64.b64decode(value, validate=True)))
    if img.format != "PNG" or img.size != size:
        raise ValueError("Model API returned a visualization of the wrong format or size")
    return img.convert("RGB")


def open_photo(path) -> Image.Image:
    """Open a photo upright (phones store rotation in EXIF). Reads iPhone .HEIC if pillow-heif is installed."""
    from PIL import ImageOps

    if str(path).lower().endswith((".heic", ".heif")):
        try:
            from pillow_heif import register_heif_opener
        except ImportError:
            raise RuntimeError("To read .HEIC photos: pip install pillow-heif") from None
        register_heif_opener()
    with Image.open(path) as img:
        return ImageOps.exif_transpose(img).convert("RGB")


class CrackModel:
    def __init__(self, url: str, key: str, secret: str, threshold: float | None = None, timeout: float = 180):
        import httpx                                    # imported here so the mock backend runs without it

        if threshold is not None and not 0.01 <= threshold <= 0.99:
            raise ValueError("detect_threshold must be between 0.01 and 0.99 (or None for the model's own)")
        self.url = _base_url(url) + "/predict"
        self.params = {"threshold": threshold} if threshold is not None else None
        # timeout is long because the first request after an idle spell waits for the GPU to cold-start
        self.http = httpx.Client(headers={"Modal-Key": key, "Modal-Secret": secret},
                                 timeout=httpx.Timeout(timeout, connect=20), follow_redirects=False)

    @classmethod
    def from_env(cls, default_url: str, threshold: float | None = None) -> CrackModel:
        key, secret = os.environ.get("MASKRCNN_API_KEY"), os.environ.get("MASKRCNN_API_SECRET")
        if not key or not secret:
            raise RuntimeError("Model API credentials missing: set MASKRCNN_API_KEY and MASKRCNN_API_SECRET "
                               "(on the Pi, put them in ~/.config/egg_inspector/model.env)")
        return cls(os.environ.get("MASKRCNN_API_URL") or default_url, key, secret, threshold)

    def predict(self, image: Image.Image) -> ViewScore:
        """Score one view. Thread-safe, so several views can be in flight at once."""
        image = image.convert("RGB")
        if max(image.size) > MAX_SIDE:
            image.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)    # heatmap comes back at this size
        buf = io.BytesIO()
        image.save(buf, "JPEG", quality=95)
        if buf.tell() > MAX_UPLOAD:
            raise ValueError("Camera frame is over the model API's 10 MiB limit")
        reply = self._post(buf.getvalue())
        if not reply.is_success:              # never echo headers: they carry the credentials
            hint = " (check MASKRCNN_API_KEY / MASKRCNN_API_SECRET)" if reply.status_code in (401, 403) else ""
            raise RuntimeError(f"Model API returned HTTP {reply.status_code}{hint}")
        result = PredictionResponse.model_validate(reply.json())
        heatmap = None
        if result.xgradcam.status == "generated":
            heatmap = _png(result.xgradcam.overlay_png_base64, (result.image.width, result.image.height))
        return ViewScore(result.crack_confidence, result.crack_detected, heatmap)

    def _post(self, jpeg: bytes):
        """POST one JPEG, retrying the hiccups a cold-starting server produces (dropped connection,
        502/503/504). Other errors, e.g. bad credentials, are returned or raised at once."""
        import httpx

        for attempt in range(RETRIES + 1):
            last = attempt == RETRIES
            try:
                reply = self.http.post(self.url, params=self.params, files={"file": ("view.jpg", jpeg, "image/jpeg")})
            except httpx.TransportError as exc:     # connect failure or dropped connection
                if isinstance(exc, httpx.TimeoutException) and not isinstance(exc, httpx.ConnectTimeout):
                    raise RuntimeError("Model API took too long to answer (over 3 minutes)") from exc
                if last:
                    raise RuntimeError(f"Model API unreachable after {RETRIES + 1} tries "
                                       f"({type(exc).__name__}). Check the internet connection.") from exc
            else:
                if reply.status_code not in RETRY_STATUS or last:
                    return reply
            time.sleep(RETRY_WAIT * (attempt + 1))

    def close(self) -> None:
        self.http.close()


def load_model(cfg):
    """The crack model chosen by cfg.model_source: local weights file or the model API."""
    if cfg.model_source == "api":
        return CrackModel.from_env(cfg.model_api_url, cfg.detect_threshold)
    if cfg.model_source != "local":
        raise ValueError(f"model_source must be 'local' or 'api', not {cfg.model_source!r}")
    from pathlib import Path

    from .local_model import LocalCrackModel

    path = Path(cfg.model_path)
    if not path.is_absolute():
        path = Path(__file__).resolve().parent / path
    return LocalCrackModel(path, cfg.detect_threshold)


def main() -> None:
    import argparse

    from .config import Config

    ap = argparse.ArgumentParser(description="Score one photo with the crack model and print the verdict")
    ap.add_argument("image")
    ap.add_argument("--model", choices=["local", "api"], default=Config.model_source,
                    help="local = model_best.pth on this machine (default); api = the Modal model API")
    ap.add_argument("--heatmap", help="save the XGrad-CAM overlay here (only produced for cracks)")
    args = ap.parse_args()
    if not os.path.isfile(args.image):
        ap.error(f"photo not found: {args.image}")
    cfg = Config(model_source=args.model)
    model = load_model(cfg)
    try:
        t0 = time.perf_counter()
        view = model.predict(open_photo(args.image))
        print(f"{'CRACK' if view.cracked else 'no crack'}  score {view.score:.3f}  "
              f"({time.perf_counter() - t0:.1f} s, {args.model} model)")
        if view.heatmap is not None and args.heatmap:
            view.heatmap.save(args.heatmap)
            print(f"Saved heatmap to {args.heatmap}")
    finally:
        model.close()


if __name__ == "__main__":
    main()
