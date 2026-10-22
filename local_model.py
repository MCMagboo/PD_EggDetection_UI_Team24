"""Run the trained Mask R-CNN (model_best.pth) on this machine instead of calling the model API.

Same interface as model_client.CrackModel: predict(image) -> ViewScore, close().

The checkpoint is torchvision's maskrcnn_resnet50_fpn with 2 classes (background, crack), saved inside
{"model": state_dict, "epoch", "validation_ap", "config_sha256"}. Its mask head layers are named
deconv / mask_logits instead of torchvision's conv5_mask / mask_fcn_logits; the shapes are identical,
so they are renamed on load. The file is opened with weights_only=True: tensors only, no code runs.

XGrad-CAM (only for views with a crack): gradients of the top crack score w.r.t. the last ResNet block
(backbone.body.layer4[-1], the layer the API uses), channel weights sum(A * dY/dA) / sum(A), ReLU,
upsampled and overlaid on the photo.

Needs PyTorch + torchvision (CPU build is fine):
    pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
"""
from __future__ import annotations

import threading
from pathlib import Path

import numpy as np
from PIL import Image

from .model_client import MAX_SIDE, ViewScore

VALIDATED_THRESHOLD = 0.83          # the threshold the model team validated (same default as the API)
CRACK_LABEL = 1
RENAMED = {"roi_heads.mask_predictor.deconv.": "roi_heads.mask_predictor.conv5_mask.",
           "roi_heads.mask_predictor.mask_logits.": "roi_heads.mask_predictor.mask_fcn_logits."}


def _jet(v: np.ndarray) -> np.ndarray:
    """Float 0..1 array -> RGB uint8 jet colormap."""
    r = np.clip(1.5 - np.abs(4 * v - 3), 0, 1)
    g = np.clip(1.5 - np.abs(4 * v - 2), 0, 1)
    b = np.clip(1.5 - np.abs(4 * v - 1), 0, 1)
    return (np.stack([r, g, b], -1) * 255).astype(np.uint8)


class LocalCrackModel:
    def __init__(self, path: str | Path, threshold: float | None = None, threads: int | None = None):
        try:
            import torch
            import torchvision
        except ImportError:
            raise RuntimeError("The local model needs PyTorch: pip install torch torchvision "
                               "--index-url https://download.pytorch.org/whl/cpu") from None
        path = Path(path)
        if not path.is_file():
            raise RuntimeError(f"Model weights not found: {path}")
        self.torch = torch
        if threads:
            torch.set_num_threads(threads)
        self.threshold = VALIDATED_THRESHOLD if threshold is None else threshold
        ckpt = torch.load(path, map_location="cpu", weights_only=True)
        state = ckpt.get("model", ckpt)
        self.info = {k: v for k, v in ckpt.items() if k != "model"} if "model" in ckpt else {}
        for old, new in RENAMED.items():
            state = {(new + k[len(old):] if k.startswith(old) else k): v for k, v in state.items()}
        classes = state["roi_heads.box_predictor.cls_score.weight"].shape[0]
        self.net = torchvision.models.detection.maskrcnn_resnet50_fpn(
            weights=None, weights_backbone=None, num_classes=classes)
        self.net.load_state_dict(state, strict=True)
        self.net.eval()
        for p in self.net.parameters():          # XGrad-CAM only needs activation gradients, never weight ones
            p.requires_grad_(False)
        self.to_tensor = torchvision.transforms.functional.to_tensor
        self._lock = threading.Lock()          # one inference at a time; torch already uses every core

    def predict(self, image: Image.Image) -> ViewScore:
        """One forward pass. The graph starts at layer4's output (a fresh leaf tensor), so the backbone
        runs as fast as plain inference and only a cracked view pays for a short backward pass."""
        torch = self.torch
        image = image.convert("RGB")
        if max(image.size) > MAX_SIDE:                  # same input size as the API gets; much faster heatmap
            image = image.copy()
            image.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
        x = self.to_tensor(image)
        saved = {}

        def leaf(_module, _inputs, output):
            saved["act"] = output.detach().requires_grad_()
            return saved["act"]

        with self._lock:
            hook = self.net.backbone.body.layer4[-1].register_forward_hook(leaf)
            try:
                with torch.enable_grad():
                    out = self.net([x])[0]
                    crack = (out["labels"] == CRACK_LABEL).nonzero().flatten()
                    best = crack[out["scores"][crack].argmax()] if len(crack) else None
                    score = float(out["scores"][best].detach()) if best is not None else 0.0
                    cracked = score >= self.threshold
                    if cracked:
                        out["scores"][best].backward()
            finally:
                hook.remove()
            if not cracked:
                return ViewScore(score, False, None)
            images, _ = self.net.transform([x])           # resized + padded size, as the detector saw it
            return ViewScore(score, True, self._overlay(saved["act"], images, image))

    def _overlay(self, act_leaf, images, image: Image.Image) -> Image.Image:
        torch = self.torch
        act, grad = act_leaf[0].detach(), act_leaf.grad[0]
        weights = (act * grad).sum((1, 2)) / (act.sum((1, 2)) + 1e-7)
        cam = torch.relu((weights[:, None, None] * act).sum(0)).numpy()
        # the feature map covers the padded batch; keep the part that is the photo, then scale to the photo
        (h, w), (ph, pw) = images.image_sizes[0], images.tensors.shape[-2:]
        cam = cam[: max(1, round(cam.shape[0] * h / ph)), : max(1, round(cam.shape[1] * w / pw))]
        cam = cam / cam.max() if cam.max() > 0 else cam
        cam = np.asarray(Image.fromarray((cam * 255).astype(np.uint8)).resize(image.size, Image.BILINEAR)) / 255.0
        alpha = (0.55 * cam)[..., None]
        blend = np.asarray(image, np.float32) * (1 - alpha) + _jet(cam).astype(np.float32) * alpha
        return Image.fromarray(blend.astype(np.uint8))

    def close(self) -> None:
        pass
