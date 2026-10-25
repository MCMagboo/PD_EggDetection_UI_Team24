"""Run with:  python -m egg_inspector [--backend mock|pi|images|webcam] [--camera N|/dev/videoN] [--fps 30] [--images DIR] [--no-model | --fake-vision] [--windowed] [--size 800x480] [--fast]"""
import argparse
import dataclasses
import logging
import sys

from .config import Config
from .ui import App


def main() -> None:
    ap = argparse.ArgumentParser(description="Duck egg inspection touchscreen app")
    ap.add_argument("--backend", choices=["mock", "pi", "images", "webcam"], default="mock",
                    help="mock = fake images (no model/hardware); pi = real camera/GPIO + model API; "
                         "images = photos from --images through the real model API (no hardware); "
                         "webcam = USB webcam --camera through the real model, with a Live button (no hardware)")
    ap.add_argument("--model", choices=["local", "api"], default=None,
                    help="crack model: local = model_best_quantized.pth on this machine (default), api = the Modal model API")
    ap.add_argument("--camera", help="USB webcam: index (0 = first) or path, e.g. /dev/video8. With --backend webcam "
                         "(default 0), or with --backend pi to use it instead of the ribbon camera")
    ap.add_argument("--fps", type=int, help=f"camera frame rate (default {Config.camera_fps})")
    ap.add_argument("--images", help="with --backend images: folder of egg photos (.jpg/.png), one per tray slot")
    ap.add_argument("--windowed", action="store_true", help="run in a window instead of fullscreen (for development)")
    ap.add_argument("--size", default="800x480", help="window size when --windowed, e.g. 1024x600")
    sim = ap.add_mutually_exclusive_group()
    sim.add_argument("--no-model", action="store_true",
                     help="with --backend pi: real camera, motors, LEDs and UV-C; simulated crack verdicts")
    sim.add_argument("--fake-vision", action="store_true",
                     help="with --backend pi: real motors/LEDs/UV-C, synthetic images instead of camera + model")
    ap.add_argument("--fast", action="store_true", help="speed up the mock backend")
    ap.add_argument("--seed", type=int, default=None, help="random seed for the mock backend")
    args = ap.parse_args()
    if args.backend != "pi" and (args.no_model or args.fake_vision):
        ap.error("--no-model and --fake-vision need --backend pi")
    if (args.backend == "images") != bool(args.images):
        ap.error("--backend images and --images DIR go together")
    logging.basicConfig(level=logging.INFO)

    w, h = (int(v) for v in args.size.lower().split("x"))
    cfg = Config(screen_w=w, screen_h=h, fullscreen=not args.windowed)
    if args.model:
        cfg = dataclasses.replace(cfg, model_source=args.model)
    if args.fps:
        cfg = dataclasses.replace(cfg, camera_fps=args.fps)
    if args.fast:
        cfg = dataclasses.replace(cfg, time_scale=0.25)

    try:
        if args.backend == "mock":
            from .mock_backend import MockBackend
            backend = MockBackend(cfg, args.seed)
        elif args.backend == "webcam":
            from .image_backend import WebcamBackend
            backend = WebcamBackend(cfg, args.camera or "0")
        elif args.backend == "images":
            from .image_backend import ImageFolderBackend, find_photos
            photos = find_photos(args.images)
            cfg = dataclasses.replace(cfg, egg_count=min(cfg.egg_count, len(photos)))
            backend = ImageFolderBackend(cfg, photos)
        elif args.no_model:
            from .pi_backend import NoModelPiBackend
            backend = NoModelPiBackend(cfg, args.camera)
        elif args.fake_vision:
            from .pi_backend import FakeVisionPiBackend
            backend = FakeVisionPiBackend(cfg)
        else:
            from .pi_backend import PiBackend
            backend = PiBackend(cfg, args.camera)
        backend.prepare()
    except NotImplementedError as exc:
        sys.exit(f"Not written yet: {exc}\nUntil the model is ready, add --no-model to use the real camera "
                 "and hardware with simulated verdicts.")
    except Exception as exc:                          # camera / GPIO / model API problems: say what, not a traceback
        sys.exit(f"Could not start: {type(exc).__name__}: {exc}")

    App(cfg, backend).mainloop()


if __name__ == "__main__":
    main()
