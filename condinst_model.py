"""Run the CondInst crack model (condinst_model.pth) on this machine, in plain PyTorch (no detectron2/AdelaiDet).

Same interface as local_model.LocalCrackModel: predict(image) -> ViewScore, close().

The checkpoint is AdelaiDet's CondInst with a detectron2 ResNet-50 + FPN (P3-P7), 1 class (crack), saved inside
{"model": state_dict, "epoch", "validation_ap", "config_sha256"}. The modules below carry the same names as the
checkpoint, so it loads with strict=True (weights_only=True: tensors only, no code runs).

Detection is FCOS: per FPN location a crack logit, a centre-ness logit and box distances; score =
sqrt(sigmoid(crack) * sigmoid(centre-ness)). The verdict uses the best detection's score. CondInst's mask branch and
dynamic mask head only draw the crack's outline, which the app doesn't show, so they aren't run.

XGrad-CAM (only for views with a crack): gradients of the top crack score w.r.t. the last ResNet block (res5),
channel weights sum(A * dY/dA) / sum(A), ReLU, upsampled and overlaid on the photo, as for the Mask R-CNN.
"""
from __future__ import annotations

import math
import threading
from pathlib import Path

import numpy as np
from PIL import Image

from .explain import cam_facts
from .local_model import _jet
from .model_client import ViewScore

THRESHOLD = 0.45                 # from the client dataset (19 cracked, 5 intact): 13 cracks caught, 1 false reject.
                                 # PLACEHOLDER until the model team gives the validated threshold for this model
MIN_SIZE, MAX_SIZE = 800, 1333   # detectron2 test-time resize (shorter side, longer-side cap)
STRIDES = (8, 16, 32, 64, 128)   # P3-P7
CANDIDATE_TH = 0.05              # FCOS: locations below this crack probability are ignored
NMS_TH = 0.6


def is_condinst(state: dict) -> bool:
    return any(k.startswith("proposal_generator.fcos_head.") for k in state)


def _build(torch, nn, F):
    """The CondInst network (inference parts), with module names matching the checkpoint."""

    class FrozenBN(nn.Module):
        def __init__(self, n):
            super().__init__()
            for name, value in (("weight", 1.0), ("bias", 0.0), ("running_mean", 0.0), ("running_var", 1.0)):
                self.register_buffer(name, torch.full((n,), value))

        def forward(self, x):
            scale = self.weight * (self.running_var + 1e-5).rsqrt()
            return x * scale[None, :, None, None] + (self.bias - self.running_mean * scale)[None, :, None, None]

    class Conv(nn.Conv2d):                      # detectron2 Conv2d: conv followed by its own .norm
        def __init__(self, cin, cout, k, stride=1):
            super().__init__(cin, cout, k, stride, k // 2, bias=False)
            self.norm = FrozenBN(cout)

        def forward(self, x):
            return self.norm(super().forward(x))

    class Bottleneck(nn.Module):
        def __init__(self, cin, mid, cout, stride, stride_in_1x1):
            super().__init__()
            s1, s3 = (stride, 1) if stride_in_1x1 else (1, stride)
            self.shortcut = Conv(cin, cout, 1, stride) if cin != cout else None
            self.conv1, self.conv2, self.conv3 = Conv(cin, mid, 1, s1), Conv(mid, mid, 3, s3), Conv(mid, cout, 1)

        def forward(self, x):
            y = F.relu(self.conv1(x))
            y = F.relu(self.conv2(y))
            y = self.conv3(y)
            return F.relu(y + (self.shortcut(x) if self.shortcut is not None else x))

    class Stem(nn.Module):
        def __init__(self):
            super().__init__()
            self.conv1 = Conv(3, 64, 7, 2)

        def forward(self, x):
            return F.max_pool2d(F.relu(self.conv1(x)), 3, 2, 1)

    class ResNet(nn.Module):
        def __init__(self, stride_in_1x1):
            super().__init__()
            self.stem = Stem()
            cin = 64
            for name, blocks, mid, stride in (("res2", 3, 64, 1), ("res3", 4, 128, 2), ("res4", 6, 256, 2),
                                              ("res5", 3, 512, 2)):
                layers = []
                for i in range(blocks):
                    layers.append(Bottleneck(cin, mid, mid * 4, stride if i == 0 else 1, stride_in_1x1))
                    cin = mid * 4
                setattr(self, name, nn.Sequential(*layers))

        def forward(self, x):
            x = self.stem(x)
            out = {}
            for name in ("res2", "res3", "res4", "res5"):
                x = getattr(self, name)(x)
                out[name] = x
            return out

    class TopBlock(nn.Module):                  # P6, P7 from P5
        def __init__(self):
            super().__init__()
            self.p6, self.p7 = nn.Conv2d(256, 256, 3, 2, 1), nn.Conv2d(256, 256, 3, 2, 1)

    class Backbone(nn.Module):                  # detectron2 FPN over res3-res5, plus P6/P7
        def __init__(self, stride_in_1x1):
            super().__init__()
            self.bottom_up = ResNet(stride_in_1x1)
            for level, cin in ((3, 512), (4, 1024), (5, 2048)):
                setattr(self, f"fpn_lateral{level}", nn.Conv2d(cin, 256, 1))
                setattr(self, f"fpn_output{level}", nn.Conv2d(256, 256, 3, 1, 1))
            self.top_block = TopBlock()

        def forward(self, x):
            c = self.bottom_up(x)
            prev = self.fpn_lateral5(c["res5"])
            p = {5: self.fpn_output5(prev)}
            for level in (4, 3):
                prev = getattr(self, f"fpn_lateral{level}")(c[f"res{level}"]) + \
                    F.interpolate(prev, scale_factor=2.0, mode="nearest")
                p[level] = getattr(self, f"fpn_output{level}")(prev)
            p6 = self.top_block.p6(p[5])
            p7 = self.top_block.p7(F.relu(p6))
            return [p[3], p[4], p[5], p6, p7]

    def tower():
        layers = []
        for _ in range(4):
            layers += [nn.Conv2d(256, 256, 3, 1, 1), nn.GroupNorm(32, 256), nn.ReLU()]
        return nn.Sequential(*layers)

    class Scale(nn.Module):
        def __init__(self):
            super().__init__()
            self.scale = nn.Parameter(torch.ones(1))

    class FCOSHead(nn.Module):
        def __init__(self):
            super().__init__()
            self.cls_tower, self.bbox_tower = tower(), tower()
            self.cls_logits = nn.Conv2d(256, 1, 3, 1, 1)
            self.bbox_pred = nn.Conv2d(256, 4, 3, 1, 1)
            self.ctrness = nn.Conv2d(256, 1, 3, 1, 1)
            self.scales = nn.ModuleList(Scale() for _ in STRIDES)

    class ProposalGenerator(nn.Module):
        def __init__(self):
            super().__init__()
            self.fcos_head = FCOSHead()

    def block(cin, cout, k):
        return nn.Sequential(nn.Conv2d(cin, cout, k, 1, k // 2, bias=False), nn.BatchNorm2d(cout), nn.ReLU())

    class MaskBranch(nn.Module):                # loaded so the checkpoint matches; not run (see the docstring)
        def __init__(self):
            super().__init__()
            self.refine = nn.ModuleList(block(256, 128, 3) for _ in range(3))
            self.tower = nn.Sequential(*[block(128, 128, 3) for _ in range(4)], nn.Conv2d(128, 8, 1))

    class MaskHead(nn.Module):
        def __init__(self):
            super().__init__()
            self.register_buffer("sizes_of_interest", torch.tensor([64, 128, 256, 512, 1024]))
            self.register_buffer("_iter", torch.zeros(1))

    class CondInst(nn.Module):
        def __init__(self, stride_in_1x1):
            super().__init__()
            self.backbone = Backbone(stride_in_1x1)
            self.proposal_generator = ProposalGenerator()
            self.mask_branch = MaskBranch()
            self.mask_head = MaskHead()
            self.controller = nn.Conv2d(256, 169, 3, 1, 1)

        def forward(self, x):
            """Per FPN level: crack logits (N,1,H,W), centre-ness logits, box distances in pixels (N,4,H,W)."""
            head = self.proposal_generator.fcos_head
            out = []
            for level, feature in enumerate(self.backbone(x)):
                cls_t, box_t = head.cls_tower(feature), head.bbox_tower(feature)
                reg = F.relu(head.scales[level].scale * head.bbox_pred(box_t)) * STRIDES[level]
                out.append((head.cls_logits(cls_t), head.ctrness(box_t), reg))
            return out

    return CondInst


class CondInstCrackModel:
    def __init__(self, path: str | Path, threshold: float | None = None, threads: int | None = None,
                 stride_in_1x1: bool = True, bgr: bool = True):
        try:
            import torch
            import torch.nn.functional as F
            from torch import nn
        except ImportError:
            raise RuntimeError("The local model needs PyTorch: pip install torch torchvision "
                               "--index-url https://download.pytorch.org/whl/cpu") from None
        path = Path(path)
        if not path.is_file():
            raise RuntimeError(f"Model weights not found: {path}")
        self.torch, self.F = torch, F
        if threads:
            torch.set_num_threads(threads)
        self.threshold = THRESHOLD if threshold is None else threshold
        ckpt = torch.load(path, map_location="cpu", weights_only=True)
        state = ckpt.get("model", ckpt)
        self.info = {k: v for k, v in ckpt.items() if k != "model"} if "model" in ckpt else {}
        self.net = _build(torch, nn, F)(stride_in_1x1)
        self.net.load_state_dict({k: v.float() if v.is_floating_point() else v for k, v in state.items()},
                                 strict=True)
        self.net.eval()
        for p in self.net.parameters():          # XGrad-CAM only needs activation gradients, never weight ones
            p.requires_grad_(False)
        # detectron2's default input: BGR, ImageNet mean subtracted, no std scaling (MSRA ResNet weights)
        self.bgr = bgr
        self.mean = torch.tensor([103.530, 116.280, 123.675] if bgr else [123.675, 116.280, 103.530]).view(3, 1, 1)
        self.std = torch.ones(3).view(3, 1, 1) if bgr else torch.tensor([58.395, 57.120, 57.375]).view(3, 1, 1)
        self._lock = threading.Lock()

    def _input(self, image: Image.Image):
        """Resized (shorter side MIN_SIZE, longer side at most MAX_SIZE), normalised, zero-padded to a multiple of
        128 (the P7 stride). Returns the batch tensor and the resized size (w, h)."""
        torch = self.torch
        w, h = image.size
        scale = min(MIN_SIZE / min(w, h), MAX_SIZE / max(w, h))
        nw, nh = round(w * scale), round(h * scale)
        img = image.resize((nw, nh), Image.BILINEAR)
        x = torch.from_numpy(np.asarray(img, np.float32).copy()).permute(2, 0, 1)
        if self.bgr:
            x = x.flip(0)
        x = (x - self.mean) / self.std
        ph, pw = math.ceil(nh / 128) * 128, math.ceil(nw / 128) * 128
        x = self.F.pad(x, (0, pw - nw, 0, ph - nh))
        return x[None], (nw, nh)

    def predict(self, image: Image.Image) -> ViewScore:
        torch = self.torch
        image = image.convert("RGB")
        x, (nw, nh) = self._input(image)
        saved = {}

        def leaf(_module, _inputs, output):
            saved["act"] = output.detach().requires_grad_()
            return saved["act"]

        with self._lock:
            hook = self.net.backbone.bottom_up.res5.register_forward_hook(leaf)
            try:
                with torch.enable_grad():
                    levels = self.net(x)
                    best = self._best(levels, (nw, nh))
                    if best is None:
                        return ViewScore(0.0, False, None)
                    score_t, box = best
                    score = float(score_t.detach())
                    cracked = score >= self.threshold
                    if cracked:
                        score_t.backward()
            finally:
                hook.remove()
            if not cracked:
                return ViewScore(score, False, None)
            heatmap, cam = self._overlay(saved["act"], x.shape[-2:], (nw, nh), image)
            box = [box[0] / nw, box[1] / nh, box[2] / nw, box[3] / nh]           # 0..1 of the photo
            facts = cam_facts(cam, tuple(min(1.0, max(0.0, v)) for v in box), score, self.threshold)
            return ViewScore(score, True, heatmap, facts)

    def _best(self, levels, size):
        """The highest-scoring crack detection inside the image: (score tensor with its graph, box in resized
        pixels), or None if no location reaches CANDIDATE_TH. Only the top detection decides the verdict, and NMS
        never removes the top one, so no NMS is needed here."""
        torch = self.torch
        nw, nh = size
        best = None
        for level, (cls, ctr, reg) in enumerate(levels):
            stride = STRIDES[level]
            prob = cls[0, 0].sigmoid()
            score = (prob * ctr[0, 0].sigmoid()).sqrt()
            h, w = prob.shape
            ys = (torch.arange(h) * stride + stride // 2).view(h, 1).expand(h, w)
            xs = (torch.arange(w) * stride + stride // 2).view(1, w).expand(h, w)
            ok = (prob.detach() > CANDIDATE_TH) & (xs < nw) & (ys < nh)
            if not ok.any():
                continue
            masked = torch.where(ok, score.detach(), torch.full_like(score.detach(), -1.0))
            i = int(masked.argmax())
            r, c = divmod(i, w)
            if best is None or float(masked[r, c]) > float(best[0].detach()):
                left, top, right, bottom = (float(v) for v in reg[0, :, r, c].detach())
                cx, cy = float(xs[r, c]), float(ys[r, c])
                best = (score[r, c], (max(0.0, cx - left), max(0.0, cy - top), min(nw, cx + right),
                                      min(nh, cy + bottom)))
        return best

    def _overlay(self, act_leaf, padded_hw, size, image: Image.Image) -> tuple[Image.Image, np.ndarray]:
        """The heatmap over the photo, and the raw heatmap (0..1, feature-map size) for measuring."""
        torch = self.torch
        act, grad = act_leaf[0].detach(), act_leaf.grad[0]
        weights = (act * grad).sum((1, 2)) / (act.sum((1, 2)) + 1e-7)
        cam = torch.relu((weights[:, None, None] * act).sum(0)).numpy()
        (ph, pw), (nw, nh) = padded_hw, size        # the feature map covers the padded input; keep the photo part
        cam = cam[: max(1, round(cam.shape[0] * nh / ph)), : max(1, round(cam.shape[1] * nw / pw))]
        cam = cam / cam.max() if cam.max() > 0 else cam
        small = cam
        cam = np.asarray(Image.fromarray((cam * 255).astype(np.uint8)).resize(image.size, Image.BILINEAR)) / 255.0
        alpha = (0.55 * cam)[..., None]
        blend = np.asarray(image, np.float32) * (1 - alpha) + _jet(cam).astype(np.float32) * alpha
        return Image.fromarray(blend.astype(np.uint8)), small

    def close(self) -> None:
        pass
