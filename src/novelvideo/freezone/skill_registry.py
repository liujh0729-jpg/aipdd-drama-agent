"""Freezone skill registry and run contract schemas."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

SkillProvider = Literal["freezone_mainline", "agent", "tool", "workflow"]
SkillCardinality = Literal["single", "multi"]
SkillMediaType = Literal["image", "text", "json", "node_patch", "graph_patch"]
SKILL_SCHEMA_VERSION = "skill.v1"


class SkillCapabilities(BaseModel):
    can_read_canvas: bool = False
    can_read_project_state: bool = False
    can_access_network: bool = False
    can_propose_canvas_patch: bool = False
    can_apply_canvas_patch: bool = False


class SkillInputAcceptSpec(BaseModel):
    node_types: list[str] = Field(default_factory=list)
    canonical_slot_kinds: list[str] = Field(default_factory=list)
    candidate_origin_skill_ids: list[str] = Field(default_factory=list)
    media_kinds: list[str] = Field(default_factory=list)
    has_field: list[str] = Field(default_factory=list)


class SkillInputSpec(BaseModel):
    schema_version: str = SKILL_SCHEMA_VERSION
    role: str
    label: str
    accepts: SkillInputAcceptSpec = Field(default_factory=SkillInputAcceptSpec)
    required: bool
    cardinality: SkillCardinality


class SkillOutputSpec(BaseModel):
    schema_version: str = SKILL_SCHEMA_VERSION
    role: str
    label: str
    media_type: SkillMediaType
    node_type: str
    pushable: bool
    requires_apply: bool = False


class SkillDefinition(BaseModel):
    schema_version: str = SKILL_SCHEMA_VERSION
    id: str
    provider: SkillProvider
    capabilities: SkillCapabilities = Field(default_factory=SkillCapabilities)
    display_name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    inputs: list[SkillInputSpec]
    outputs: list[SkillOutputSpec]


class ResolvedSkillInput(BaseModel):
    model_config = ConfigDict(extra="allow")

    role: str
    node_id: str = ""
    node_type: str = ""
    beat_context: dict[str, Any] | None = None
    image_url: str | None = None
    text: str | None = None
    slot_target: dict[str, Any] | None = None
    reference_target: dict[str, Any] | None = None
    candidate_origin: dict[str, Any] | None = None
    media_kind: str | None = None


class SkillRunRequest(BaseModel):
    schema_version: str = SKILL_SCHEMA_VERSION
    skill_node_id: str = ""
    canvas_id: str = ""
    idempotency_key: str | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    resolved_inputs: list[ResolvedSkillInput] = Field(default_factory=list)


class SkillRunResponse(BaseModel):
    schema_version: str = SKILL_SCHEMA_VERSION
    run_id: str
    status: str
    task_key: str | None = None
    task_type: str | None = None
    job_id: str | None = None
    error: "SkillErrorEnvelope | None" = None


class SkillErrorEnvelope(BaseModel):
    code: str
    category: str
    message: str
    retryable: bool = False
    user_action_hint: str | None = None


class CanvasGraphPatchOperation(BaseModel):
    model_config = ConfigDict(extra="allow")

    op: Literal[
        "add_node",
        "update_node",
        "delete_node",
        "add_edge",
        "update_edge",
        "delete_edge",
    ]
    node: dict[str, Any] | None = None
    edge: dict[str, Any] | None = None
    node_id: str | None = None
    edge_id: str | None = None
    data: dict[str, Any] | None = None


class CanvasGraphPatch(BaseModel):
    schema_version: str = "graph_patch.v1"
    operations: list[CanvasGraphPatchOperation] = Field(default_factory=list)
    requires_apply: bool = True
    summary: str | None = None


class SkillRunOutput(BaseModel):
    model_config = ConfigDict(extra="allow")

    schema_version: str = SKILL_SCHEMA_VERSION
    role: str
    media_type: SkillMediaType
    node_type: str
    pushable: bool
    image_url: str | None = None
    text: str | None = None
    json_value: Any | None = None
    graph_patch: CanvasGraphPatch | None = None
    slot_target: dict[str, Any] | None = None


class SkillRunResult(BaseModel):
    schema_version: str = SKILL_SCHEMA_VERSION
    run_id: str
    status: str
    outputs: list[SkillRunOutput] = Field(default_factory=list)
    task_key: str | None = None
    task_type: str | None = None
    job_id: str | None = None
    error: SkillErrorEnvelope | None = None


IMAGE_NODE_TYPES = [
    "imageGenNode",
    "imageNode",
    "exportImageNode",
    "uploadNode",
    "uploadImageNode",
    "freezoneImageNode",
    "assetImageNode",
    "sceneNode",
    "identityNode",
    "propNode",
]


def _input(
    role: str,
    label: str,
    *,
    required: bool,
    cardinality: SkillCardinality,
    accepts: SkillInputAcceptSpec,
) -> SkillInputSpec:
    return SkillInputSpec(
        role=role,
        label=label,
        accepts=accepts,
        required=required,
        cardinality=cardinality,
    )


def _image_accepts(
    *,
    canonical_slot_kinds: list[str] | None = None,
    candidate_origin_skill_ids: list[str] | None = None,
    allow_plain_image: bool = False,
) -> SkillInputAcceptSpec:
    return SkillInputAcceptSpec(
        node_types=IMAGE_NODE_TYPES,
        canonical_slot_kinds=canonical_slot_kinds or [],
        candidate_origin_skill_ids=candidate_origin_skill_ids or [],
        media_kinds=["image"] if allow_plain_image else [],
        has_field=["image_url"],
    )


def _text_accepts(*, node_types: list[str] | None = None) -> SkillInputAcceptSpec:
    return SkillInputAcceptSpec(
        node_types=node_types or ["textAnnotationNode"],
        media_kinds=["text"],
    )


_BEAT_CONTEXT_ACCEPTS = SkillInputAcceptSpec(
    node_types=["beatContextNode"],
    has_field=["beat_context"],
)

_FREEZONE_MAINLINE_CAPABILITIES = SkillCapabilities(can_read_project_state=True)
_AGENT_PROOF_CAPABILITIES = SkillCapabilities()
_WORKFLOW_PATCH_CAPABILITIES = SkillCapabilities(
    can_read_canvas=True,
    can_read_project_state=True,
    can_propose_canvas_patch=True,
    can_apply_canvas_patch=False,
)

_REGISTRY: dict[str, SkillDefinition] = {
    "freezone.sketch_from_context": SkillDefinition(
        id="freezone.sketch_from_context",
        provider="freezone_mainline",
        capabilities=_FREEZONE_MAINLINE_CAPABILITIES,
        display_name="Sketch From Selected Background",
        description=(
            "Generate a mainline sketch candidate from beat context and selected background."
        ),
        parameters={
            "aspect_ratio": {
                "type": "enum",
                "label": "比例",
                "default": "2:3",
                "options": ["2:3", "16:9"],
            },
        },
        inputs=[
            _input(
                "beat_context",
                "Beat context",
                required=True,
                cardinality="single",
                accepts=_BEAT_CONTEXT_ACCEPTS,
            ),
            _input(
                "background",
                "Background",
                required=True,
                cardinality="single",
                accepts=_image_accepts(
                    canonical_slot_kinds=[
                        "selected_background",
                        "background",
                        "background_candidate",
                    ],
                    candidate_origin_skill_ids=["freezone.scene_360"],
                    allow_plain_image=True,
                ),
            ),
        ],
        outputs=[
            SkillOutputSpec(
                role="current_sketch_candidate",
                label="Current sketch candidate",
                media_type="image",
                node_type="imageGenNode",
                pushable=True,
            )
        ],
    ),
    "freezone.sketch_from_director_combined": SkillDefinition(
        id="freezone.sketch_from_director_combined",
        provider="freezone_mainline",
        capabilities=_FREEZONE_MAINLINE_CAPABILITIES,
        display_name="从导演合成图生成草图",
        description="从 Beat 上下文和导演合成图生成主线草图候选。",
        parameters={
            "aspect_ratio": {
                "type": "enum",
                "label": "比例",
                "default": "2:3",
                "options": ["2:3", "16:9"],
            },
        },
        inputs=[
            _input(
                "beat_context",
                "Beat context",
                required=True,
                cardinality="single",
                accepts=_BEAT_CONTEXT_ACCEPTS,
            ),
            _input(
                "director_combined",
                "导演合成图",
                required=True,
                cardinality="single",
                accepts=_image_accepts(
                    canonical_slot_kinds=["director_combined"],
                    candidate_origin_skill_ids=["freezone.director_combined"],
                    allow_plain_image=True,
                ),
            ),
        ],
        outputs=[
            SkillOutputSpec(
                role="current_sketch_candidate",
                label="Current sketch candidate",
                media_type="image",
                node_type="imageGenNode",
                pushable=True,
            )
        ],
    ),
    "freezone.frame_from_context": SkillDefinition(
        id="freezone.frame_from_context",
        provider="freezone_mainline",
        capabilities=_FREEZONE_MAINLINE_CAPABILITIES,
        display_name="Frame From Context",
        description="Render a mainline frame candidate from beat context, sketch, and references.",
        parameters={
            "quality": {
                "type": "enum",
                "label": "质量",
                "default": "medium",
                "options": ["low", "medium", "high"],
            },
            "background_reference_mode": {
                "type": "enum",
                "label": "背景参考模式",
                "default": "material_only",
                "options": ["material_only", "scene_anchor"],
            },
        },
        inputs=[
            _input(
                "beat_context",
                "Beat context",
                required=True,
                cardinality="single",
                accepts=_BEAT_CONTEXT_ACCEPTS,
            ),
            _input(
                "sketch",
                "Sketch",
                required=True,
                cardinality="single",
                accepts=_image_accepts(
                    canonical_slot_kinds=["sketch"],
                    candidate_origin_skill_ids=[
                        "freezone.sketch_from_context",
                        "freezone.sketch_from_director_combined",
                    ],
                    allow_plain_image=True,
                ),
            ),
            _input(
                "background",
                "Background",
                required=False,
                cardinality="single",
                accepts=_image_accepts(
                    canonical_slot_kinds=["selected_background", "background"],
                    allow_plain_image=True,
                ),
            ),
            _input(
                "identity",
                "Identity",
                required=False,
                cardinality="multi",
                accepts=_image_accepts(
                    canonical_slot_kinds=["identity", "portrait"],
                    allow_plain_image=True,
                ),
            ),
            _input(
                "prop",
                "Prop",
                required=False,
                cardinality="multi",
                accepts=_image_accepts(canonical_slot_kinds=["prop"], allow_plain_image=True),
            ),
        ],
        outputs=[
            SkillOutputSpec(
                role="current_frame_candidate",
                label="Current frame candidate",
                media_type="image",
                node_type="imageGenNode",
                pushable=True,
            )
        ],
    ),
    "freezone.set_selected_background": SkillDefinition(
        id="freezone.set_selected_background",
        provider="freezone_mainline",
        capabilities=_FREEZONE_MAINLINE_CAPABILITIES,
        display_name="设为当前背景",
        description=(
            "Set a Beat-owned selected background from an explicit image source. "
            "This is deterministic and does not call an image model."
        ),
        inputs=[
            _input(
                "beat_context",
                "Beat context",
                required=True,
                cardinality="single",
                accepts=_BEAT_CONTEXT_ACCEPTS,
            ),
            _input(
                "source_image",
                "背景来源图",
                required=True,
                cardinality="single",
                accepts=_image_accepts(
                    canonical_slot_kinds=[
                        "selected_background",
                        "background",
                        "background_candidate",
                        "scene_master",
                        "scene_reverse_master",
                        "director_env_only",
                        "director_env",
                    ],
                    candidate_origin_skill_ids=[
                        "freezone.scene_360",
                        "freezone.set_selected_background",
                    ],
                    allow_plain_image=True,
                ),
            ),
        ],
        outputs=[
            SkillOutputSpec(
                role="selected_background",
                label="当前背景",
                media_type="image",
                node_type="imageGenNode",
                pushable=False,
            )
        ],
    ),
    "freezone.set_director_combined": SkillDefinition(
        id="freezone.set_director_combined",
        provider="freezone_mainline",
        capabilities=_FREEZONE_MAINLINE_CAPABILITIES,
        display_name="设为导演合成图",
        description=(
            "Set a Beat-owned 3GS director combined image from an explicit image source. "
            "This is deterministic and does not call an image model."
        ),
        inputs=[
            _input(
                "beat_context",
                "Beat context",
                required=True,
                cardinality="single",
                accepts=_BEAT_CONTEXT_ACCEPTS,
            ),
            _input(
                "source_image",
                "导演合成图来源",
                required=True,
                cardinality="single",
                accepts=_image_accepts(
                    canonical_slot_kinds=[
                        "director_combined",
                        "director_render",
                    ],
                    candidate_origin_skill_ids=[
                        "freezone.set_director_combined",
                    ],
                    allow_plain_image=True,
                ),
            ),
        ],
        outputs=[
            SkillOutputSpec(
                role="director_combined",
                label="导演合成图",
                media_type="image",
                node_type="imageGenNode",
                pushable=True,
            )
        ],
    ),
    "freezone.scene_360": SkillDefinition(
        id="freezone.scene_360",
        provider="freezone_mainline",
        capabilities=_FREEZONE_MAINLINE_CAPABILITIES,
        display_name="Scene 360",
        description="Generate a 2:1 scene panorama candidate from a scene master.",
        inputs=[
            _input(
                "scene",
                "Scene prompt",
                required=False,
                cardinality="single",
                accepts=_text_accepts(),
            ),
            _input(
                "scene_master",
                "Scene master",
                required=True,
                cardinality="single",
                accepts=_image_accepts(
                    canonical_slot_kinds=["scene_master"],
                    allow_plain_image=True,
                ),
            ),
            _input(
                "scene_reverse_master",
                "Scene reverse master",
                required=False,
                cardinality="single",
                accepts=_image_accepts(
                    canonical_slot_kinds=["scene_reverse_master"],
                    allow_plain_image=True,
                ),
            ),
        ],
        outputs=[
            SkillOutputSpec(
                role="scene_360_candidate",
                label="Scene 360 candidate",
                media_type="image",
                node_type="imageGenNode",
                pushable=True,
            )
        ],
    ),
    "agent.review_frame": SkillDefinition(
        id="agent.review_frame",
        provider="agent",
        capabilities=_AGENT_PROOF_CAPABILITIES,
        display_name="Review Frame",
        description="Review a frame candidate against beat context.",
        inputs=[
            _input(
                "beat_context",
                "Beat context",
                required=True,
                cardinality="single",
                accepts=_BEAT_CONTEXT_ACCEPTS,
            ),
            _input(
                "frame",
                "Frame",
                required=True,
                cardinality="single",
                accepts=_image_accepts(
                    canonical_slot_kinds=["frame"],
                    candidate_origin_skill_ids=["freezone.frame_from_context"],
                ),
            ),
        ],
        outputs=[
            SkillOutputSpec(
                role="review_report",
                label="Review report",
                media_type="text",
                node_type="textAnnotationNode",
                pushable=False,
            )
        ],
    ),
    "workflow.plan_beat_graph": SkillDefinition(
        id="workflow.plan_beat_graph",
        provider="workflow",
        capabilities=_WORKFLOW_PATCH_CAPABILITIES,
        display_name="Plan Beat Graph",
        description=(
            "Propose a graph patch of SkillNodes and role edges for a beat. "
            "The patch is not applied automatically."
        ),
        inputs=[
            _input(
                "beat_context",
                "Beat context",
                required=True,
                cardinality="single",
                accepts=_BEAT_CONTEXT_ACCEPTS,
            ),
        ],
        outputs=[
            SkillOutputSpec(
                role="planned_canvas_patch",
                label="Planned canvas patch",
                media_type="graph_patch",
                node_type="graphPatchNode",
                pushable=False,
                requires_apply=True,
            )
        ],
    ),
}


def list_skills() -> list[SkillDefinition]:
    return list(_REGISTRY.values())


def get_skill(skill_id: str) -> SkillDefinition:
    return _REGISTRY[skill_id]


def find_skill(skill_id: str) -> SkillDefinition | None:
    return _REGISTRY.get(skill_id)
