# Copied unchanged from the model team's MaskRCNN_Modal_API client (Drive folder "model").
# Keep it in sync with the server: it validates every response from the model API.
"""Shared JSON contract; independent of Torch and Modal."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ImageInfo(Contract):
    width: int = Field(gt=0)
    height: int = Field(gt=0)


class ModelInfo(Contract):
    name: Literal["MaskRCNN_XGradCAM"]
    run_id: str
    checkpoint_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    best_epoch: int


class Instance(Contract):
    index: int = Field(ge=0)
    label: Literal["crack"]
    score: float = Field(ge=0, le=1)
    box_xyxy: tuple[float, float, float, float]
    mask_png_base64: str = Field(min_length=1)


class Explanation(Contract):
    status: Literal["generated", "not_triggered", "failed"]
    target_layer: Literal["backbone.body.layer4[-1]"]
    selected_instance_index: int | None = Field(default=None, ge=0)
    target_score: float | None = Field(default=None, ge=0, le=1)
    gradient_l1: float | None = Field(default=None, gt=0)
    heatmap_png_base64: str | None = None
    overlay_png_base64: str | None = None
    error_code: Literal["cam_generation_failed"] | None = None

    @model_validator(mode="after")
    def consistent(self):
        values = (self.selected_instance_index, self.target_score, self.gradient_l1,
                  self.heatmap_png_base64, self.overlay_png_base64)
        if self.status == "generated":
            if any(v is None for v in values) or self.error_code is not None:
                raise ValueError("Generated CAM requires genuine visualization and gradient metadata")
        elif any(v is not None for v in values):
            raise ValueError("Unavailable CAM cannot contain generated data")
        if (self.status == "failed") != (self.error_code is not None):
            raise ValueError("CAM error code must match failure status")
        return self


class Timings(Contract):
    inference_ms: float = Field(ge=0)
    xgradcam_ms: float = Field(ge=0)
    pipeline_ms: float = Field(ge=0)


class PredictionResponse(Contract):
    schema_version: Literal["1.0"]
    request_id: str
    status: Literal["ok", "partial_success"]
    model: ModelInfo
    image: ImageInfo
    threshold: float = Field(gt=0, lt=1)
    threshold_source: Literal["validation", "request_override"] = "validation"
    crack_detected: bool
    crack_confidence: float = Field(ge=0, le=1)
    instance_count: int = Field(ge=0, le=100)
    instances: list[Instance]
    segmentation_overlay_png_base64: str = Field(min_length=1)
    xgradcam: Explanation
    timings: Timings | None = None

    @model_validator(mode="after")
    def consistent(self):
        if self.instance_count != len(self.instances):
            raise ValueError("Instance count mismatch")
        if self.crack_detected != (self.instance_count > 0):
            raise ValueError("Crack decision disagrees with accepted instances")
        if self.crack_detected != (self.crack_confidence >= self.threshold):
            raise ValueError("Crack decision disagrees with confidence")
        if [i.index for i in self.instances] != list(range(self.instance_count)):
            raise ValueError("Instance indexes must be consecutive")
        if any(i.score < self.threshold for i in self.instances):
            raise ValueError("Accepted instance below threshold")
        for item in self.instances:
            x1, y1, x2, y2 = item.box_xyxy
            if not (0 <= x1 <= x2 <= self.image.width and 0 <= y1 <= y2 <= self.image.height):
                raise ValueError("Box outside original image geometry")
        if self.instances and abs(max(i.score for i in self.instances) - self.crack_confidence) > 1e-6:
            raise ValueError("Maximum instance score mismatch")
        if self.crack_detected == (self.xgradcam.status == "not_triggered"):
            raise ValueError("CAM trigger disagrees with crack decision")
        if (self.status == "partial_success") != (self.xgradcam.status == "failed"):
            raise ValueError("Partial success must describe CAM failure")
        if self.xgradcam.status == "generated":
            index = self.xgradcam.selected_instance_index
            if index is None or index >= self.instance_count:
                raise ValueError("CAM target instance missing")
            if abs(self.instances[index].score - self.xgradcam.target_score) > 1e-6:
                raise ValueError("CAM target score differs from selected prediction")
        return self
