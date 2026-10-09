from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

Vector3 = tuple[float, float, float]
ALLOWED_SHAPE_HINTS = {
    "box",
    "generic_large",
    "quadruped_mount",
    "wheeled_artillery",
    "long_vehicle",
    "sports_car",
    "flying_craft",
    "pile",
}


class DirectorActor(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    actor_id: str = Field(alias="id")
    identity_id: str = ""
    name: str = ""
    type: str = "actor_neutral"
    marker_color: str = ""
    position: Vector3 = (0.0, 0.0, 0.0)
    yaw: float = 0.0
    state: str = "standing"
    seat_id: str | None = None
    attached_to: str | None = None
    gaze_target: str | Vector3 | None = None


class DirectorProp(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    prop_uid: str = Field(alias="id")
    prop_id: str = ""
    name: str = ""
    type: str = "prop_hero"
    marker_color: str = ""
    position: Vector3 = (0.0, 1.6, 0.0)
    yaw: float = 0.0
    scale: Vector3 = (1.0, 1.0, 1.0)
    category: str = "prop"
    semantic_label: str = ""
    shape_hint: str = ""
    affordances: list[str] = Field(default_factory=list)
    attachment_points: list[dict[str, Any]] = Field(default_factory=list)
    attached_to: str | None = None
    tracking: str = "ordinary_prop"
    asset_scope: str = ""
    is_global_asset: bool = False
    preserve_marker_color: bool = False

    @model_validator(mode="after")
    def infer_global_asset_fields(self) -> "DirectorProp":
        if self.type != "prop_staging":
            self.shape_hint = ""
        elif self.shape_hint not in ALLOWED_SHAPE_HINTS:
            self.shape_hint = "generic_large" if self.type == "prop_staging" else ""
        if (
            self.is_global_asset
            or self.asset_scope == "global"
            or self.preserve_marker_color
            or self.tracking == "tracked_marker"
        ):
            self.asset_scope = "global"
            self.is_global_asset = True
            self.preserve_marker_color = True
            if not self.tracking or self.tracking == "ordinary_prop":
                self.tracking = "tracked_marker"
        return self
