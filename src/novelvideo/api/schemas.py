"""API 请求/响应 Pydantic 模型。"""

from typing import Annotated, Any, Literal, Optional

from fastapi import HTTPException
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from novelvideo.models import SceneRef
from novelvideo.freezone.asset_copy import MAX_SOURCE_URL_LENGTH, MAX_SOURCES_PER_REQUEST
from novelvideo.freezone.slots import PushTarget

ProjectStatus = Literal["active", "archived", "deleted"]
ProjectStatusFilter = Literal["all", "active", "archived", "deleted", "visible"]
FREEZONE_DEFAULT_IMAGE_SELECTION = "newapi_gpt_image2"
FREEZONE_DEFAULT_IMAGE_MODEL = FREEZONE_DEFAULT_IMAGE_SELECTION
CANVAS_MAX_NODES = 50_000
CANVAS_MAX_EDGES = 200_000


# ── 通用响应 ──────────────────────────────────────────────────────────────────


class OkResponse(BaseModel):
    ok: bool = True
    data: Any = None


class TaskResponse(BaseModel):
    ok: bool = True
    task_id: str = ""
    task_type: str = ""
    message: str = ""


class ErrorResponse(BaseModel):
    ok: bool = False
    error: str = ""


# ── 项目 ──────────────────────────────────────────────────────────────────────


class ProjectCreate(BaseModel):
    name: str = Field(max_length=64)


class ProjectSummary(BaseModel):
    id: str = ""
    name: str
    owner_type: str = "user"
    owner_id: str = ""
    owner_username: str = ""
    effective_role: str = ""
    home_node_id: str = ""
    status: ProjectStatus
    archived_at: Optional[str] = None
    deleted_at: Optional[str] = None
    purged_at: Optional[str] = None
    updated_at: Optional[str] = None
    episode_count: Optional[int] = None
    beat_count: Optional[int] = None


class ProjectGrantCreate(BaseModel):
    principal_type: Literal["user", "team"] = "user"
    principal_id: Optional[str] = None
    principal_username: Optional[str] = None
    role: Literal["viewer", "editor", "admin"]


class ProjectGrantUpdate(BaseModel):
    role: Literal["viewer", "editor", "admin"]


class ProjectGrantSummary(BaseModel):
    id: str
    project_id: str
    principal_type: str
    principal_id: str
    principal_username: Optional[str] = None
    role: str
    created_at: Optional[str] = None
    effective: bool = True
    inactive_reason: Literal["principal_access_changed"] | None = None


class ProjectUpdate(BaseModel):
    spine_template: Optional[Literal["drama", "narrated"]] = None
    aspect_ratio: Optional[Literal["2:3", "9:16", "16:9"]] = None
    visual_style: Optional[str] = None
    narration_style: Optional[str] = None
    ethnicity: Optional[str] = None
    rhythm: Optional[str] = None
    tts_provider: Optional[str] = None
    tts_model: Optional[str] = None
    tts_voice: Optional[str] = None
    grid_mode: Optional[str] = None
    grid_model: Optional[str] = None
    video_backend: Optional[str] = None
    use_director_render: Optional[bool] = None
    video_resolution: Optional[str] = None
    add_subtitles: Optional[bool] = None
    sketch_image_selection: Optional[str] = None
    render_image_selection: Optional[str] = None
    sketch_aspect_padding: Optional[bool] = None


class RenderSettingsUpdate(BaseModel):
    render_image_selection: Optional[str] = None
    sketch_aspect_padding: Optional[bool] = None


class SketchSettingsUpdate(BaseModel):
    sketch_image_selection: Optional[str] = None


class BeatBackgroundAnchorUpdate(BaseModel):
    anchor_id: str


# ── 导入 ──────────────────────────────────────────────────────────────────────


class IngestStart(BaseModel):
    filename: str
    rebuild: bool = False
    spine_template: Optional[Literal["drama", "narrated"]] = None


class AutoSceneHeadersStart(BaseModel):
    # 原始上传文件名（uploads/ 下），对其正文跑场景头自动补全。
    filename: str


class IngestAutoStart(BaseModel):
    # 一键自动化只做到资产为止，分集数量是它唯一需要用户决定的参数。
    target_episodes: int = 10


class AutoSceneHeadersApply(BaseModel):
    # 增强场景头文件名（uploads/ 下的 *.scenes.txt）。
    filename: str
    # 人工编辑后的完整文本，覆写回增强文件前先复校导入字数上限。
    text: str


# ── 角色 ──────────────────────────────────────────────────────────────────────


class PortraitGenRequest(BaseModel):
    style: Optional[str] = None
    ethnicity: str = "Chinese"
    model: Optional[str] = None


# ── 分集 ──────────────────────────────────────────────────────────────────────


class EpisodePlanRequest(BaseModel):
    target_episodes: int = 10
    planning_mode: str = "chapters"


# ── 剧本 ──────────────────────────────────────────────────────────────────────


class ContentUpdateRequest(BaseModel):
    content: str


class RewriteGenerateRequest(BaseModel):
    target_beats: int = 18
    beat_chars_min: int = 14
    beat_chars_max: int = 20
    narration_style: Optional[str] = None


class StoryUpdateRequest(BaseModel):
    story: str
    story_style: Optional[str] = None


class StoryOptimizeRequest(BaseModel):
    story: Optional[str] = None
    story_style: Optional[str] = None


class StoryToScriptRequest(BaseModel):
    story: Optional[str] = None
    story_style: Optional[str] = None
    target_beats: int = 18
    beat_chars_min: int = 14
    beat_chars_max: int = 20
    autostart: bool = True


# ── 项目级「故事」[aipdd fork delta] ───────────────────────────────────────────
# L1 故事栏目：导入故事 → AI 优化 → 生成整本多集剧本 → 入剧本库（uploads/）。


class ProjectStoryOptimizeRequest(BaseModel):
    # text 缺省时使用已导入的故事源（project_dir/story/source.txt）。
    text: Optional[str] = None
    story_style: Optional[str] = None


class ProjectStorySourceRequest(BaseModel):
    # JSON 通道写入故事源（MCP/agent 链路）：import 端点的 text 通道是 multipart-only，
    # 这里与它共用同一大小/校验口径，仅接收 JSON 文本。
    text: str


class NovelToScreenplayRequest(BaseModel):
    # 目标集数：按短剧体量把整本故事拆成 N 集剧本。
    episode_count: int = 10
    story_style: Optional[str] = None


class ChapterBodyUpdateRequest(BaseModel):
    # 章节/单集正文（不含标题行）。保存时与原标题行重组后写回源文件，
    # 保证章节结构（标题行）不被编辑破坏。
    body: str


class ScriptGenerateRequest(BaseModel):
    pass


class BeatUpdate(BaseModel):
    narration_segment: Optional[str] = None
    visual_description: Optional[str] = None
    scene_ref: Optional[SceneRef] = None
    time_of_day: Optional[str] = None
    video_prompt: Optional[str] = None
    keyframe_prompt: Optional[str] = None
    video_mode: Optional[str] = None  # "first_frame" | "keyframe"
    seedance2_config_json: Optional[str] = None
    audio_type: Optional[str] = None  # "silence" | "narration" | "dialogue"
    speaker: Optional[str] = None  # 说话人身份ID（dialogue 时必填）
    detected_identities: Optional[list[str]] = None
    detected_props: Optional[list[str]] = None


class Seedance2PromptGenerateRequest(BaseModel):
    manual_prompt_reference: Optional[str] = None
    prompt_guidance: Optional[str] = None
    prompt_guidance_template_keys: Optional[list[str]] = None


class BeatVideoPromptGenerateRequest(BaseModel):
    language: str = "zh"


class Seedance2AssetDeleteRequest(BaseModel):
    media_kind: Literal["images", "audios"]
    path: str


class Seedance2AssetCropRequest(BaseModel):
    asset_key: str
    source_path: str
    target: Literal["reference_image", "first_frame", "last_frame"] = "reference_image"
    x: float = 0
    y: float = 0
    width: float
    height: float


class Seedance2AssetAudioTrimRequest(BaseModel):
    asset_key: str
    source_path: str
    start_seconds: float = 0
    duration_seconds: float = 4


# ── 图片池选择 ────────────────────────────────────────────────────────────────


class PoolSelectRequest(BaseModel):
    pool_id: str
    force: bool = False


class VideoPoolSelectRequest(BaseModel):
    pool_id: str


class GlobalOptimizeRequest(BaseModel):
    language: str = "en"  # "zh" 中文 / "en" SuperPower英文(Gemini)


class VideoGenerateRequest(BaseModel):
    resolution: str = "720x1280"
    video_backend: str = "newapi_seedance-1.0-pro-fast"
    use_director_render: bool = False


class VideoBackendOption(BaseModel):
    value: str
    label: str
    is_default: bool = False
    is_seedance2: bool = False
    is_happyhorse: bool = False
    is_grok_video: bool = False
    dialogue_only: bool = False
    min_duration: Optional[int] = None
    max_duration: Optional[int] = None
    resolution_options: Optional[list[str]] = None
    ratio_options: Optional[list[str]] = None
    supported_modes: Optional[list[str]] = None
    reference_image_max: Optional[int] = None
    reference_video_max: Optional[int] = None
    reference_audio_max: Optional[int] = None


class VideoComposeRequest(BaseModel):
    add_subtitles: bool = True
    add_bgm: bool = False
    resolution: str = "720x1280"


# ── TTS ───────────────────────────────────────────────────────────────────────


class TTSGenerateRequest(BaseModel):
    provider: Optional[str] = None
    voice: Optional[str] = None
    model: Optional[str] = None
    rate: Optional[str] = None
    mode: Optional[str] = None
    beat_numbers: Optional[list[int]] = None


class TTSPreviewRequest(BaseModel):
    text: str
    provider: Optional[str] = None
    voice: Optional[str] = None
    model: Optional[str] = None


# ── 草图 & 首帧 ──────────────────────────────────────────────────────────────


class SketchGenerateRequest(BaseModel):
    style: Optional[str] = None
    model: str = ""  # [aipdd fork delta] 空值=走配置的默认图片模型（原写死 nanobanana）
    grid_index: int = 0
    sketch_scene_grouping: bool = True
    aspect_ratio: Literal["2:3", "16:9"] = "2:3"
    image_generation_selection: Optional[str] = None


# ── 再生 ──────────────────────────────────────────────────────────────────────


class GridRegenerateRequest(BaseModel):
    style: Optional[str] = None
    model: str = ""  # [aipdd fork delta] 空值=走配置的默认图片模型（原写死 nanobanana）
    scene_grouping: bool = False
    character_grouping: bool = False
    image_generation_selection: Optional[str] = None
    sketch_aspect_padding: Optional[bool] = None


class BeatsRegenerateRequest(BaseModel):
    beat_indices: list[int]
    style: Optional[str] = None
    model: str = ""  # [aipdd fork delta] 空值=走配置的默认图片模型（原写死 nanobanana）
    mode_key: str = "1x1_2-3"
    image_generation_selection: Optional[str] = None
    sketch_aspect_padding: Optional[bool] = None


class SketchRegenerateRequest(BaseModel):
    beat_indices: list[int]
    style: Optional[str] = None
    model: str = ""  # [aipdd fork delta] 空值=走配置的默认图片模型（原写死 nanobanana）
    mode_key: str = "1x1_2-3"
    image_generation_selection: Optional[str] = None


class SketchRegenQueueItem(BaseModel):
    id: str
    modeKey: str
    modeLabel: str
    beatNumbers: list[int] = Field(default_factory=list)
    sceneIds: list[str] = Field(default_factory=list)
    createdAt: str
    taskScope: Optional[str] = None


class SketchRegenQueueUpdate(BaseModel):
    items: list[SketchRegenQueueItem] = Field(default_factory=list)


class OperatorPasswordVerifyRequest(BaseModel):
    password: str = ""


class InsertManualShotRequest(BaseModel):
    # None means insert before the first beat. Otherwise insert after this beat_number.
    after_beat_number: Optional[int] = None
    visual_description: str
    duration_seconds: Optional[float] = None
    scene_ref: Optional[SceneRef] = None
    time_of_day: Optional[str] = None
    detected_identities: Optional[list[str]] = None
    detected_props: Optional[list[str]] = None
    audio_type: Literal["silence", "narration", "dialogue"] = "silence"
    speaker: Optional[str] = None
    narration_segment: Optional[str] = None


class SingleVideoRequest(BaseModel):
    resolution: str = "720x1280"
    video_backend: str = "newapi_seedance-1.0-pro-fast"
    use_director_render: bool = False
    seedance2_config_json: Optional[str] = None
    mode: Optional[str] = None
    duration: Optional[int] = None
    ratio: Optional[str] = None
    generate_audio: Optional[bool] = None
    return_last_frame: Optional[bool] = None
    human_review: Optional[bool] = None
    scene_optimize: Optional[str] = None
    final_prompt: Optional[str] = None
    audio_setting: Optional[str] = None
    prompt_guidance: Optional[str] = None
    text_overlay: Optional[dict[str, Any]] = None


# ── 风格 ──────────────────────────────────────────────────────────────────────


class StyleCreateRequest(BaseModel):
    id: str
    name: str
    label: str
    config: dict


# ── 剧集编辑 ─────────────────────────────────────────────────────────────────


class EpisodeUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    title: Optional[str] = None
    content_summary: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("summary", "content_summary"),
        serialization_alias="summary",
    )
    character_names: Optional[list[str]] = None
    key_events: Optional[list[str]] = None
    cliffhanger: Optional[str] = None
    identity_ids: Optional[list[str]] = None
    beat_source_text: Optional[str] = None
    story: Optional[str] = None
    story_style: Optional[str] = None
    identity_default_map: Optional[dict[str, str]] = None


# ── 角色编辑 ─────────────────────────────────────────────────────────────────


class CharacterCreate(BaseModel):
    name: str
    role: str = ""
    is_main: bool = False
    gender: str = ""
    age_group: str = "youth"
    description: str = ""
    face_prompt: str = ""


# ── Freezone ─────────────────────────────────────────────────────────────────


class FreezoneImageCameraConfig(BaseModel):
    """图片节点摄像机参数。"""

    camera_body: str = Field(default="", description="相机机身，例如 Panavision DXL2")
    lens: str = Field(default="", description="镜头型号，例如 Arri Signature Prime")
    focal_length_mm: Optional[int] = Field(
        default=None,
        description="焦距，单位 mm，例如 35",
    )
    aperture: str = Field(default="", description="光圈，例如 f/4")


class FreezoneImageStyleConfig(BaseModel):
    """图片节点风格模板参数。"""

    template_id: str = Field(description="风格模板 id")


class FreezoneGenRequest(BaseModel):
    prompt: str
    aspect_ratio: str = "1:1"
    image_size: str = "2K"
    reference_urls: list[str] = Field(default_factory=list)
    canvas_id: str = Field(
        default="",
        description="可选来源画布 id。用于后端按节点记录生成历史；为空时不记录节点历史。",
    )
    node_id: str = Field(
        default="",
        description="可选来源节点 id。用于后端按节点记录生成历史；为空时不记录节点历史。",
    )
    camera: Optional[FreezoneImageCameraConfig] = Field(
        default=None,
        description="可选摄像机参数，用于把机身 / 镜头 / 焦距 / 光圈注入图片提示词",
    )
    style: Optional[FreezoneImageStyleConfig] = Field(
        default=None,
        description="可选风格模板参数，用于把内置风格模板注入图片提示词",
    )
    provider: Optional[str] = None
    model: Optional[str] = None
    quality: Optional[str] = Field(default="medium", description="图片画质档位，默认 medium")
    model_id: Optional[str] = Field(
        default=None, description="可选：注册表模型 id，用于还原节点时回填 model"
    )
    gen_mode: Optional[str] = Field(default=None, description="可选：生成模式，用于还原节点时回填 genMode")
    model_params: dict[str, Any] = Field(default_factory=dict)


class FreezoneEditRequest(BaseModel):
    prompt: str
    base_url: str
    extra_reference_urls: list[str] = Field(default_factory=list)
    aspect_ratio: str = "2:3"
    image_size: str = "2K"
    canvas_id: str = Field(
        default="",
        description="可选来源画布 id。用于后端按节点记录生成历史；为空时不记录节点历史。",
    )
    node_id: str = Field(
        default="",
        description="可选来源节点 id。用于后端按节点记录生成历史；为空时不记录节点历史。",
    )
    camera: Optional[FreezoneImageCameraConfig] = Field(
        default=None,
        description="可选摄像机参数，用于把机身 / 镜头 / 焦距 / 光圈注入图片提示词",
    )
    style: Optional[FreezoneImageStyleConfig] = Field(
        default=None,
        description="可选风格模板参数，用于把内置风格模板注入图片提示词",
    )
    provider: Optional[str] = None
    model: Optional[str] = None
    quality: Optional[str] = Field(default="medium", description="图片画质档位，默认 medium")
    model_id: Optional[str] = Field(
        default=None, description="可选：注册表模型 id，用于还原节点时回填 model"
    )
    gen_mode: Optional[str] = Field(default=None, description="可选：生成模式，用于还原节点时回填 genMode")
    model_params: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "媒体模型目录声明的动态参数取值。与 /freezone/gen 同一口径 —— "
            "带参考图的图编辑走这条路由，少了这个字段目录参数在图编辑侧等于没生效"
        ),
    )
    archive_to_library: bool = Field(
        default=False,
        description=(
            "[aipdd fork delta] 工具页批量任务用：为 true 时任务产出后会把结果自动"
            "登记进资产库「待分类资产」。画布等其它入口保持默认关闭。"
        ),
    )
    source_tool: str = Field(
        default="",
        description=(
            "[aipdd fork delta] 工具页任务归属标识：提交来自哪个功能（取工具 "
            "kind，如 lip-sync）；写入任务 metadata.source_tool，任务列表据此"
            "把任务只显示在产生它的功能页。画布等其它入口保持为空。"
        ),
    )


class FreezoneSketchFromContextRequest(BaseModel):
    episode: int
    beat: int
    aspect_ratio: Literal["2:3", "16:9"] = "2:3"
    source_kind: Literal[
        "beat",
        "selected_background",
        "director_combined",
        "background_candidate",
    ] = "beat"
    source_url: Optional[str] = None
    canvas_id: str = Field(default="")
    node_id: str = Field(default="")
    provider: Optional[str] = None
    model: Optional[str] = None
    quality: Optional[str] = Field(default="medium", description="图片画质档位，默认 medium")


class FreezoneFrameFromContextRequest(BaseModel):
    episode: int
    beat: int
    aspect_ratio: Literal["2:3", "16:9"] = "2:3"
    sketch_url: str
    background_url: Optional[str] = None
    identity_urls: list[str] = Field(default_factory=list)
    prop_urls: list[str] = Field(default_factory=list)
    canvas_id: str = Field(default="")
    node_id: str = Field(default="")
    provider: Optional[str] = None
    model: Optional[str] = None
    quality: Literal["low", "medium", "high"] = Field(
        default="medium",
        description="图片画质档位，默认 medium",
    )


class FreezoneScene360Request(BaseModel):
    """场景 360 全景生成请求。

    约定只接收一张场景源图 `master.png` 作为参考输入。
    """

    reference_url: str = Field(
        description="场景源图静态地址，通常指向 assets/scenes/<scene_id>/master.png"
    )
    reverse_reference_url: Optional[str] = Field(
        default=None,
        description=("可选反向场景源图静态地址，通常指向 assets/scenes/<scene_id>/reverse.png"),
    )
    canvas_id: str = Field(default="")
    node_id: str = Field(default="")
    image_size: str = Field(default="2K", description="输出分辨率档位，默认 2K")
    mode: Literal["candidate", "commit"] = Field(
        default="candidate",
        description="candidate 只生成画布候选；commit 明确写回主线 360 slot",
    )
    model: str = Field(
        default=FREEZONE_DEFAULT_IMAGE_MODEL,
        description=f"图片模型名，默认 {FREEZONE_DEFAULT_IMAGE_MODEL}",
    )
    catalog_id: str = Field(
        default="",
        description="媒体模型目录条目 id。前端按目录条目报价时必须一并下发，否则计费口径与报价不一致",
    )
    quality: Optional[str] = Field(default="medium", description="图片画质档位，默认 medium")


class FreezoneImageTo3GSRequest(BaseModel):
    """从 Freezone 图片节点启动 SHARP，生成压缩 3GS SOG。"""

    source_url: str = Field(description="源图静态地址，通常来自 Freezone 图片节点")
    source_kind: Literal["master", "reverse", "pano"] = Field(
        default="master",
        description=("3GS 来源类型；master/reverse 生成单面 SOG，pano 使用 360 全景生成 pano SOG"),
    )
    canvas_id: str = Field(default="", description="可选：来源画布 id，用于记录节点生成历史")
    node_id: str = Field(default="", description="可选：来源节点 id，用于记录节点生成历史")


class FreezoneThreeDViewerScreenshotRequest(BaseModel):
    """保存 Freezone 内置 3D viewer 的普通截图。"""

    data_url: str = Field(description="canvas.toDataURL('image/png') 得到的 data URL")
    node_id: Optional[str] = Field(default=None, description="来源 3D 世界节点 id")
    label: Optional[str] = Field(default=None, description="可选显示名")


class FreezoneAssetCopyRequest(BaseModel):
    """跨项目粘贴：把别的项目里的素材拷进当前项目。"""

    sources: list[Annotated[str, Field(max_length=MAX_SOURCE_URL_LENGTH)]] = Field(
        min_length=1,
        max_length=MAX_SOURCES_PER_REQUEST,
        description=(
            "源素材的同源 URL 列表（`/static/projects/<pid>/...` 或 "
            "`/api/v1/projects/<pid>/media/...`）；带查询串也行，映射按原字符串返回"
        ),
    )


# [aipdd fork delta] Blender MCP 白模视频联合（B+C）：从本机 Blender 拉取 / 手动入库，
# 再把白模视频写进指定画布「白模视频」节点（可选再打资产槽指针）。
class FreezoneBlenderPullRequest(BaseModel):
    """驱动本机 Blender 渲染导出白模视频，入库后写入指定画布节点。"""

    canvas_id: str = Field(description="目标画布 id")
    node_id: str = Field(description="目标 whiteModelVideoNode 的节点 id")
    commit_slot: bool = Field(default=False, description="是否同时在节点上打资产槽指针")
    host: Optional[str] = Field(default=None, description="覆盖 Blender socket 主机（默认 ST_BLENDER_HOST）")
    port: Optional[int] = Field(default=None, description="覆盖 Blender socket 端口（默认 ST_BLENDER_PORT）")


class FreezoneBlenderIngestRequest(BaseModel):
    """把一个白模视频来源（可访问外链或本项目 /static 直链）入库并写入指定画布节点。"""

    canvas_id: str = Field(description="目标画布 id")
    node_id: str = Field(description="目标 whiteModelVideoNode 的节点 id")
    url: str = Field(description="白模视频来源：可访问外链或本项目 /static 直链")
    commit_slot: bool = Field(default=False, description="是否同时在节点上打资产槽指针")


class ViewerBeatContextManifest(BaseModel):
    episode: int
    beat: int
    visual_description: Optional[str] = None
    detected_identities: list[str] = Field(default_factory=list)
    detected_props: list[str] = Field(default_factory=list)


class PanoViewerSource(BaseModel):
    slot_kind: Literal["scene_director_pano_360", "scene_360_candidate"] = "scene_director_pano_360"
    url: str
    fs: Optional[str] = None


class PanoSphereCorrection(BaseModel):
    roll: float = 0.0
    pitch: float = 0.0
    yaw: float = 0.0


class PanoViewerCorrection(BaseModel):
    front_yaw_deg: float = 0.0
    sphere_correction_deg: PanoSphereCorrection = Field(default_factory=PanoSphereCorrection)


class PanoViewerManifest(BaseModel):
    viewer_kind: Literal["pano360"] = "pano360"
    mode: Literal["scene", "beat"]
    project: str
    scene_id: str
    display_name: str
    source: PanoViewerSource
    correction: PanoViewerCorrection = Field(default_factory=PanoViewerCorrection)
    beat_context: Optional[ViewerBeatContextManifest] = None
    allowed_destinations: list[
        Literal["view", "download", "canvas_screenshot_node", "beat_selected_background"]
    ] = Field(default_factory=list)


class DirectorStageSource(BaseModel):
    source_type: Literal["sog", "pano360"] = "sog"
    ply_url: str
    splat_url: str
    splat_format: Literal["ply", "sog", "splat", "ksplat", "unknown"] = "unknown"
    pano_url: Optional[str] = None
    slot_kind: Optional[Literal["scene_director_pano_360", "scene_360_candidate"]] = None
    collision_glb_url: Optional[str] = None
    source_kind: Literal["master", "reverse", "pano", "uploaded", "custom"] = "custom"


class DirectorStageSourceOption(BaseModel):
    kind: Literal["active", "master", "reverse", "pano", "uploaded", "custom"]
    label: str
    source_type: Literal["sog", "pano360"] = "sog"
    ply_url: Optional[str] = None
    splat_url: Optional[str] = None
    splat_format: Literal["ply", "sog", "splat", "ksplat", "unknown"] = "unknown"
    pano_url: Optional[str] = None
    slot_kind: Optional[Literal["scene_director_pano_360", "scene_360_candidate"]] = None
    fs: Optional[str] = None
    current: bool = False


class DirectorPaletteActor(BaseModel):
    identity_id: str
    label: str
    color: str


class DirectorPaletteProp(BaseModel):
    prop_id: str
    label: str
    color: str


class DirectorStagePalette(BaseModel):
    actors: list[DirectorPaletteActor] = Field(default_factory=list)
    props: list[DirectorPaletteProp] = Field(default_factory=list)
    anonymous_colors: list[str] = Field(default_factory=list)
    anonymous_prop_colors: list[str] = Field(default_factory=list)


class DirectorStageManifest(BaseModel):
    viewer_kind: Literal["three_d_director"] = "three_d_director"
    mode: Literal["scene", "beat"]
    project: str
    scene_id: str
    display_name: str
    active_source_id: Optional[str] = None
    scene: Optional[dict[str, Any]] = None
    scenes_by_source_id: dict[str, dict[str, Any]] = Field(default_factory=dict)
    source: DirectorStageSource
    source_options: list[DirectorStageSourceOption] = Field(default_factory=list)
    source_orientation_mode: Literal["supersplat_auto", "identity", "lcc_legacy", "flip_z"] = (
        "supersplat_auto"
    )
    blockings_dir_fs: Optional[str] = None
    control_frames_dir_fs: Optional[str] = None
    slate_beat: Optional[int] = None
    beat_context: Optional[ViewerBeatContextManifest] = None
    palette: DirectorStagePalette = Field(default_factory=DirectorStagePalette)
    allowed_destinations: list[
        Literal[
            "view",
            "download",
            "canvas_screenshot_node",
            "beat_director_combined",
            "beat_director_env_only",
            "beat_selected_background",
        ]
    ] = Field(default_factory=list)


class FreezoneCharacterMultiViewRequest(BaseModel):
    """多角度编辑器请求。

    基于一张源图做机位重定位或视角重构，输出单张结果图。
    """

    source_url: str = Field(description="源图静态地址，作为图生图的 base 图")
    preset: Literal[
        "custom",
        "fisheye",
        "oblique",
        "front",
        "front_up",
        "full_body",
        "back",
    ] = Field(default="custom", description="视角预设。custom 表示完全按 yaw/pitch 自定义")
    yaw_degrees: float = Field(
        default=0.0, description="水平旋转角度，单位为度；正负方向由前端约定"
    )
    pitch_degrees: float = Field(
        default=0.0, description="垂直俯仰角度，单位为度；正负方向由前端约定"
    )
    shot_size: Literal[
        "extreme_close_up",
        "close_up",
        "medium_close",
        "medium",
        "full_body",
        "wide",
        "extreme_wide",
    ] = Field(
        default="medium",
        description="景别档位：大特写 / 特写 / 近景 / 中景 / 全身 / 远景 / 大远景",
    )
    prompt: str = Field(default="", description="用户补充提示词，可为空")
    image_size: str = Field(default="2K", description="输出分辨率档位，默认 2K")
    camera: Optional[FreezoneImageCameraConfig] = Field(
        default=None,
        description="可选摄像机参数，用于补充镜头语言和摄影机规格",
    )
    style: Optional[FreezoneImageStyleConfig] = Field(
        default=None,
        description="可选风格模板参数，用于把内置风格模板注入图片提示词",
    )
    model: str = Field(
        default=FREEZONE_DEFAULT_IMAGE_MODEL,
        description=f"图片模型名，默认 {FREEZONE_DEFAULT_IMAGE_MODEL}",
    )
    quality: Optional[str] = Field(default="medium", description="图片画质档位，默认 medium")


class FreezoneTemplateEditRequest(BaseModel):
    """九宫格下拉能力统一请求。

    本质上都是基于一张源图，叠加不同的提示词模板后走同一条图编辑链路。
    """

    source_url: str = Field(description="源图静态地址，作为图生图的 base 图")
    mode: Literal[
        "multi_camera_nine_grid",
        "story_pitch_four_grid",
        "character_face_three_view",
        "product_three_view",
        "storyboard_25_grid",
        "cinematic_light_correction",
        "character_three_view_generation",
        "image_projection_after_3s",
        "image_projection_before_5s",
    ] = Field(
        description=(
            "模板模式。分别对应：多机位九宫格 / 剧情推演四宫格 / 角色脸部三视图 / "
            "产品三视图 / 25宫格连贯分镜 / 电影级光影校正 / 角色三视图生成 / "
            "画面推演-3秒后 / 画面推演-5秒前"
        )
    )
    prompt: str = Field(default="", description="用户补充提示词，可为空")
    image_size: str = Field(default="2K", description="输出分辨率档位，默认 2K")
    camera: Optional[FreezoneImageCameraConfig] = Field(
        default=None,
        description="可选摄像机参数，用于补充镜头语言和摄影机规格",
    )
    style: Optional[FreezoneImageStyleConfig] = Field(
        default=None,
        description="可选风格模板参数，用于把内置风格模板注入图片提示词",
    )
    model: str = Field(
        default=FREEZONE_DEFAULT_IMAGE_MODEL,
        description=f"图片模型名，默认 {FREEZONE_DEFAULT_IMAGE_MODEL}",
    )
    catalog_id: str = Field(
        default="",
        description="媒体模型目录条目 id。前端按目录条目报价时必须一并下发，否则计费口径与报价不一致",
    )
    quality: Optional[str] = Field(default="medium", description="图片画质档位，默认 medium")


class FreezoneExtractFramesRequest(BaseModel):
    video_url: str
    max_frames: int = 20
    scene_threshold: float = 0.3


class FreezoneAnalyzeShotsRequest(BaseModel):
    frame_urls: list[str]
    provider: Optional[str] = None
    model: Optional[str] = None
    analysis_mode: Literal["shots", "video_story"] = "shots"
    duration_sec: Optional[float] = None


class FreezoneAnalyzeVideoStoryRequest(BaseModel):
    video_url: str = Field(
        description=("视频静态地址。必须是当前项目下真实存在的 /static/... 视频 URL"),
        examples=["/static/admin/58/freezone/_uploads/example.mp4"],
    )
    max_frames: int = Field(
        default=20,
        ge=3,
        le=50,
        description=("最多抽取多少张关键帧。建议 12-20；" "越多分析越细，但耗时和 token 成本更高"),
    )
    scene_threshold: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description=(
            "ffmpeg 场景切换阈值，范围 0-1。越低越容易抽到帧；"
            "长镜头/剧情片建议 0.2-0.3，快剪视频建议 0.4-0.5"
        ),
    )
    duration_sec: Optional[float] = Field(
        default=None,
        gt=0,
        description=(
            "视频总时长，单位秒。可选；" "传入后视频故事表的 start_time/end_time 会更准确"
        ),
        examples=[15],
    )


class FreezoneUpscaleRequest(BaseModel):
    """高清放大请求。

    使用图片模型 + 提示词方式做高清放大与修复。
    """

    source_url: str = Field(description="待高清放大的源图静态地址")
    model: str = Field(
        default=FREEZONE_DEFAULT_IMAGE_MODEL,
        description=f"图片模型名，默认 {FREEZONE_DEFAULT_IMAGE_MODEL}",
    )
    quality: Optional[str] = Field(default="medium", description="图片画质档位，默认 medium")
    camera: Optional[FreezoneImageCameraConfig] = Field(
        default=None,
        description="可选摄像机参数，用于补充镜头语言和摄影机规格",
    )
    style: Optional[FreezoneImageStyleConfig] = Field(
        default=None,
        description="可选风格模板参数，用于把内置风格模板注入图片提示词",
    )
    scale_factor: Literal[2, 4, 6] = Field(
        default=2,
        description="放大倍数，可选 2 / 4 / 6",
    )
    image_size: str = Field(default="2K", description="输出分辨率档位，默认 2K")


class FreezoneOutpaintRequest(BaseModel):
    """扩图请求。

    基于一张源图向外补画，保留中心主体和原始构图。
    """

    source_url: str = Field(description="待扩图的源图静态地址，作为图生图的 base 图")
    target_aspect_ratio: Literal["original", "1:1", "4:3", "3:4", "16:9", "9:16"] = Field(
        default="original",
        description="目标比例。original 表示保持原图比例，其余值表示扩展到指定比例",
    )
    num_images: int = Field(
        default=1,
        ge=1,
        le=4,
        description="目标生成图片数量。当前后端单次任务只支持 1 张，预留该字段用于前端协议对齐",
    )
    camera: Optional[FreezoneImageCameraConfig] = Field(
        default=None,
        description="可选摄像机参数，用于补充镜头语言和摄影机规格",
    )
    style: Optional[FreezoneImageStyleConfig] = Field(
        default=None,
        description="可选风格模板参数，用于把内置风格模板注入图片提示词",
    )
    image_size: str = Field(default="2K", description="输出分辨率档位，默认 2K")
    model: str = Field(
        default=FREEZONE_DEFAULT_IMAGE_MODEL,
        description=f"图片模型名，默认 {FREEZONE_DEFAULT_IMAGE_MODEL}",
    )
    quality: Optional[str] = Field(default="medium", description="图片画质档位，默认 medium")


class FreezoneRedrawRequest(BaseModel):
    """重绘请求。

    统一承接整体重绘和局部擦除：
    - 不传 mask_url：整体/局部自由重绘
    - 传 mask_url：仅在标注的编辑区内按 prompt 执行局部编辑。mask 约定为
      「源图 + 涂抹区半透明红色高亮」（红色仅作区域标注，不进入结果）
    """

    source_url: str = Field(description="待重绘的源图静态地址，作为图生图的 base 图")
    mask_url: Optional[str] = Field(
        default=None,
        description=(
            "可选的遮罩图静态地址。传入后走局部擦除/局部重绘模式；约定为"
            "「源图 + 涂抹区半透明红色高亮」，仅编辑红色区域"
        ),
    )
    aspect_ratio: Literal["original", "1:1", "4:3", "3:4", "16:9", "9:16"] = Field(
        default="original",
        description="目标比例。original 表示保持原图比例，其余值表示按指定比例重绘",
    )
    num_images: int = Field(
        default=1,
        ge=1,
        le=4,
        description="目标生成图片数量。当前后端单次任务只支持 1 张，预留该字段用于前端协议对齐",
    )
    prompt: str = Field(default="", description="重绘要求或补充提示词")
    camera: Optional[FreezoneImageCameraConfig] = Field(
        default=None,
        description="可选摄像机参数，用于补充镜头语言和摄影机规格",
    )
    style: Optional[FreezoneImageStyleConfig] = Field(
        default=None,
        description="可选风格模板参数，用于把内置风格模板注入图片提示词",
    )
    image_size: str = Field(default="2K", description="输出分辨率档位，默认 2K")
    model: str = Field(
        default=FREEZONE_DEFAULT_IMAGE_MODEL,
        description=f"图片模型名，默认 {FREEZONE_DEFAULT_IMAGE_MODEL}",
    )
    quality: Optional[str] = Field(default="medium", description="图片画质档位，默认 medium")


class FreezoneRelightRequest(BaseModel):
    """打光参考图编辑请求。

    基于一张源图和一张打光参考图，重塑当前画面的光照氛围。
    """

    source_url: str = Field(description="待打光的源图静态地址，作为图生图的 base 图")
    lighting_reference_url: Optional[str] = Field(
        default=None,
        description="打光参考图静态地址，用于提供光照方向、强弱和氛围参考",
    )
    scope: Literal["global", "local"] = Field(
        default="global",
        description="打光作用范围：global 表示整体打光，local 表示局部打光",
    )
    smart_mode: bool = Field(default=True, description="是否启用智能模式")
    brightness: int = Field(default=50, ge=0, le=100, description="亮度强度，0-100")
    color_hex: str = Field(
        default="#ffffff",
        description="用于控制主光源颜色或整体画面色调的十六进制色值，例如 #ffffff",
    )
    color_temperature_kelvin: Optional[int] = Field(
        default=None,
        ge=1500,
        le=12000,
        description="主光源色温 Kelvin 值，适用于可拖动色温轴",
    )
    key_light_direction: Literal["left", "top", "right", "front", "bottom", "back"] = Field(
        default="front",
        description="主光源方向",
    )
    rim_light: bool = Field(default=False, description="是否添加轮廓光")
    prompt: str = Field(default="", description="用户补充提示词，可为空")
    image_size: str = Field(default="2K", description="输出分辨率档位，默认 2K")
    model: str = Field(
        default=FREEZONE_DEFAULT_IMAGE_MODEL,
        description=f"图片模型名，默认 {FREEZONE_DEFAULT_IMAGE_MODEL}",
    )
    quality: Optional[str] = Field(default="medium", description="图片画质档位，默认 medium")


class FreezoneVideoCharacterLibraryItemRequest(BaseModel):
    """视频节点资产库录入请求。

    素材先通过通用 upload 上传，再把静态地址登记到资产库。图片走 image_urls，
    视频/音频走对应的单地址字段。
    """

    name: str = Field(description="资产名称，用于前端资产库展示")
    media: Literal["image", "video", "audio"] = Field(
        default="image",
        description="素材类型：图片 / 视频 / 音频",
    )
    image_urls: list[str] = Field(
        default_factory=list,
        description="图片参考图静态地址列表（media=image 时至少一张）",
    )
    video_url: str | None = Field(
        default=None,
        description="视频静态地址（media=video 时必填）",
    )
    audio_url: str | None = Field(
        default=None,
        description="音频静态地址（media=audio 时必填）",
    )
    category: Literal["other", "character", "scene", "prop", "sketch", "style", "audio", "topdown", "storyVersion"] | None = Field(
        default=None,
        description=(
            "用途类目（其它/人物/场景/物品/草图/风格/音效/俯视图/故事版）；不传时后端按 source/media "
            "兜底推导。剪映草稿（draft）为 VectCut 产物专用类目，不走本接口"
        ),
    )
    folder: str | None = Field(
        default=None,
        description=(
            "保存位置：系统文件夹 key（其它/人物/场景/物品/草图/风格/音效同名，"
            "对应 other/character/scene/prop/sketch/style/audio）或用户自建文件夹 id；"
            "不传时按类目落到同名系统文件夹。剪映草稿（draft）只收 VectCut 草稿产物，"
            "与主线一样不接受上传"
        ),
    )
    # [aipdd fork delta] 俯视图草稿（category=topdown）随条目内联一份可编辑场景
    # （TopDownScene JSON）；其它类目不传。与 draft_id 同为透传字段，原样落库。
    scene: dict | None = Field(
        default=None,
        description="俯视图草稿的可编辑场景 JSON（仅 category=topdown 时使用）",
    )


class FreezoneAssetLibraryItemPatchRequest(BaseModel):
    """改资产库条目。目前只支持改名——URL / 类目 / 保存位置都不在这条路由的职责里。"""

    name: str = Field(description="资产的新名称，最长 60 字")


class FreezoneAssetLibraryFolderRequest(BaseModel):
    """新建资产库文件夹请求。文件夹只管保存位置，和素材的类目标签互不影响。"""

    name: str = Field(description="文件夹名称，最长 20 字，不能与系统文件夹重名")


class FreezoneAssetLibraryFolderPatchRequest(BaseModel):
    """改文件夹。两个字段都可选，只改传上来的那个。"""

    name: str | None = Field(default=None, description="新名称；不传表示不改名")
    cover: str | None = Field(
        default=None,
        description="封面图 URL，取自该文件夹内的素材；传空串表示清掉封面",
    )


class FreezoneVideoGenRequest(BaseModel):
    """文生视频请求。

    运镜通过模板库和补充提示词控制；角色库通过 `character_ids` 引用已上传的人物参考图。
    """

    prompt: str = Field(description="用户输入的视频内容描述")
    camera_template_id: Optional[str] = Field(
        default=None,
        description="运镜模板 id，例如 locked_off / follow_tracking / orbit_up",
    )
    character_ids: list[str] = Field(
        default_factory=list,
        description="视频角色库条目 id 列表，用于追加角色参考图",
    )
    marks: list["FreezoneVideoMark"] = Field(
        default_factory=list,
        description="局部元素标记列表。来自前端点击图片选中的主体/物体局部区域，不是普通 tags",
    )
    aspect_ratio: str = Field(
        default="16:9",
        description="视频比例；auto 当前回退为 16:9",
    )
    resolution: str = Field(
        default="720p",
        description="输出清晰度档位",
    )
    duration_seconds: int = Field(
        default=5,
        ge=1,
        description="视频时长，至少 1 秒；不同模型支持的时长范围可能不同",
    )
    generate_audio: bool = Field(default=False, description="是否生成原生音频")
    human_review: bool = Field(
        default=False,
        description="是否开启 HuiMeng 真人素材审核/加白流程，用于可能包含真人人脸的素材",
    )
    scene_optimize: Optional[Literal["anime", "realistic"]] = Field(
        default=None,
        description="Seedance 2.0 Value 系列的场景风格优化参数",
    )
    model: str = Field(
        default="newapi_seedance-2.0-fast",
        description="视频模型名称。请传 `/api/v1/projects/{project}/freezone/video/models` 返回值之一。",
    )
    canvas_id: str = Field(default="", description="可选：来源画布 id，用于记录节点生成历史")
    node_id: str = Field(default="", description="可选：来源节点 id，用于记录节点生成历史")
    gen_mode: Literal["textToVideo"] = Field(
        default="textToVideo",
        description="文生视频入口的固定业务模式",
    )
    model_params: dict[str, Any] = Field(default_factory=dict)


class FreezoneImageToVideoRequest(BaseModel):
    """图片参考视频请求。

    统一承接图生视频和图片参考视频：
    - 图生视频：1 张图片作为整体画面参考，不锁定第一帧
    - 2-9 张图片：多图图片参考视频
    """

    image_urls: list[str] = Field(
        default_factory=list,
        description="图片参考静态地址列表。图生视频只允许 1 张，图片参考模式按模型上限接收多张",
    )
    prompt: str = Field(default="", description="用户补充视频描述，可为空")
    camera_template_id: Optional[str] = Field(
        default=None,
        description="运镜模板 id，例如 locked_off / follow_tracking / pedestal_up",
    )
    marks: list["FreezoneVideoMark"] = Field(
        default_factory=list,
        description="局部元素标记列表。来自前端点击图片选中的主体/物体局部区域，不是普通 tags",
    )
    aspect_ratio: str = Field(
        default="16:9",
        description="视频比例；auto 当前回退为 16:9",
    )
    resolution: str = Field(
        default="720p",
        description="输出清晰度档位",
    )
    duration_seconds: int = Field(
        default=5,
        ge=1,
        description="视频时长，至少 1 秒；不同模型支持的时长范围可能不同",
    )
    generate_audio: bool = Field(default=False, description="是否生成原生音频")
    human_review: bool = Field(
        default=False,
        description="是否开启 HuiMeng 真人素材审核/加白流程，用于可能包含真人人脸的素材",
    )
    scene_optimize: Optional[Literal["anime", "realistic"]] = Field(
        default=None,
        description="Seedance 2.0 Value 系列的场景风格优化参数",
    )
    model: str = Field(
        default="newapi_seedance-2.0-fast",
        description="视频模型或模型选项 id。请传 /freezone/video/models 返回值之一",
    )
    canvas_id: str = Field(default="", description="可选：来源画布 id，用于记录节点生成历史")
    node_id: str = Field(default="", description="可选：来源节点 id，用于记录节点生成历史")
    gen_mode: Literal["imageToVideo", "imageReference"] = Field(
        description=(
            "必填：imageToVideo 表示单图整体参考；imageReference 表示按目录上限接收图片参考。"
            "两者执行时都使用 image_reference 协议，不代表首帧。"
        )
    )
    model_params: dict[str, Any] = Field(default_factory=dict)


class FreezoneKeyframeVideoRequest(BaseModel):
    """关键帧视频请求。

    接受仅首帧、首帧+尾帧或仅尾帧，至少需要提供一个槽位。
    """

    first_frame_url: Optional[str] = Field(
        default=None,
        description="首帧参考图静态地址，可为空；与尾帧至少提供一个",
    )
    last_frame_url: Optional[str] = Field(
        default=None,
        description="尾帧参考图静态地址，可为空；与首帧至少提供一个",
    )
    prompt: str = Field(default="", description="用户补充视频描述，可为空")
    camera_template_id: Optional[str] = Field(
        default=None,
        description="运镜模板 id，例如 locked_off / follow_tracking / pedestal_up",
    )
    marks: list["FreezoneVideoMark"] = Field(
        default_factory=list,
        description="局部元素标记列表。来自前端点击图片选中的主体/物体局部区域，不是普通 tags",
    )
    aspect_ratio: str = Field(
        default="auto",
        description="关键帧模式固定为 auto，输出比例跟随首帧或尾帧",
    )
    resolution: str = Field(
        default="720p",
        description="输出清晰度档位",
    )
    duration_seconds: int = Field(
        default=5,
        ge=1,
        description="视频时长，至少 1 秒；不同模型支持的时长范围可能不同",
    )
    generate_audio: bool = Field(default=False, description="是否生成原生音频")
    human_review: bool = Field(
        default=False,
        description="是否开启 HuiMeng 真人素材审核/加白流程，用于可能包含真人人脸的素材",
    )
    scene_optimize: Optional[Literal["anime", "realistic"]] = Field(
        default=None,
        description="Seedance 2.0 Value 系列的场景风格优化参数",
    )
    model: str = Field(
        default="newapi_seedance-2.0-fast",
        description="视频模型或模型选项 id。请传 /freezone/video/models 返回值之一",
    )
    canvas_id: str = Field(default="", description="可选：来源画布 id，用于记录节点生成历史")
    node_id: str = Field(default="", description="可选：来源节点 id，用于记录节点生成历史")
    gen_mode: Literal["firstFrame", "firstLastFrame"] = Field(
        description="必填：首帧或首尾帧业务模式；不能根据实际上传的帧数推断",
    )
    model_params: dict[str, Any] = Field(default_factory=dict)


class FreezoneVideoEditRequest(BaseModel):
    """视频编辑请求。

    输入 1 个源视频，并按媒体模型目录配置接收参考图片和独立参考音频。
    """

    video_url: str = Field(description="源视频静态地址，必填")
    image_urls: list[str] = Field(
        default_factory=list,
        description="参考图静态地址列表；数量上限由媒体模型目录决定",
    )
    audio_urls: list[str] = Field(
        default_factory=list,
        description="独立参考音频静态地址列表；数量和时长上限由媒体模型目录决定",
    )
    prompt: str = Field(default="", description="用户编辑指令/视频描述，可为空")
    camera_template_id: Optional[str] = Field(
        default=None,
        description="运镜模板 id，例如 locked_off / follow_tracking / pedestal_up",
    )
    marks: list["FreezoneVideoMark"] = Field(
        default_factory=list,
        description="局部元素标记列表",
    )
    resolution: str = Field(
        default="720p",
        description="输出清晰度档位",
    )
    audio_setting: Literal["auto", "origin"] = Field(
        default="auto",
        description="视频编辑音频策略：auto 自动 / origin 保留原声",
    )
    generate_audio: bool = Field(default=False, description="是否生成原生音频")
    human_review: bool = Field(
        default=False,
        description="是否开启真人素材审核/加白流程",
    )
    model: str = Field(
        default="",
        description="视频模型或模型选项 id（需支持 video_edit 能力）。请传 /freezone/video/models 返回值之一",
    )
    canvas_id: str = Field(default="", description="可选：来源画布 id，用于记录节点生成历史")
    node_id: str = Field(default="", description="可选：来源节点 id，用于记录节点生成历史")
    gen_mode: Literal["videoEdit"] = Field(
        default="videoEdit",
        description="视频编辑入口的固定业务模式",
    )
    model_params: dict[str, Any] = Field(default_factory=dict)
    archive_to_library: bool = Field(
        default=False,
        description=(
            "[aipdd fork delta] 工具页批量任务用：为 true 时任务产出后会把结果自动"
            "登记进资产库「待分类资产」。画布等其它入口保持默认关闭。"
        ),
    )
    source_tool: str = Field(
        default="",
        description=(
            "[aipdd fork delta] 工具页任务归属标识：提交来自哪个功能（取工具 "
            "kind，如 lip-sync）；写入任务 metadata.source_tool，任务列表据此"
            "把任务只显示在产生它的功能页。画布等其它入口保持为空。"
        ),
    )


class FreezoneVideoExtendRequest(BaseModel):
    """视频延长请求。输入一个源视频，并从其结尾继续生成指定时长。"""

    video_url: str = Field(description="待延长的源视频静态地址，必填")
    prompt: str = Field(description="源视频结束后要继续生成的内容")
    camera_template_id: Optional[str] = Field(
        default=None,
        description="运镜模板 id，例如 locked_off / follow_tracking / pedestal_up",
    )
    resolution: str = Field(default="720p", description="输出清晰度档位")
    duration_seconds: int = Field(
        default=5,
        ge=1,
        description="新生成延长片段的时长；不同模型支持的范围可能不同",
    )
    generate_audio: bool = Field(default=False, description="是否生成原生音频")
    human_review: bool = Field(
        default=False,
        description="是否开启真人素材审核/加白流程",
    )
    model: str = Field(
        default="newapi_seedance-2.5",
        description="视频模型或模型选项 id。请传 /freezone/video/models 返回值之一",
    )
    canvas_id: str = Field(default="", description="可选：来源画布 id，用于记录节点生成历史")
    node_id: str = Field(default="", description="可选：来源节点 id，用于记录节点生成历史")
    gen_mode: Literal["videoExtend"] = Field(
        default="videoExtend",
        description="视频延长入口的固定业务模式",
    )
    model_params: dict[str, Any] = Field(default_factory=dict)


class FreezoneVideoReferenceItem(BaseModel):
    """全能参考单条素材。"""

    type: Literal["image", "video", "audio", "file", "link"] = Field(
        description="素材类型"
    )
    url: str = Field(description="素材静态地址")
    role: str = Field(default="", description="素材角色，例如 角色参考 / 场景参考 / 配乐参考")
    label: str = Field(default="", description="前端展示标签，可为空")


class FreezoneVideoMark(BaseModel):
    """视频节点局部元素标记。"""

    label: str = Field(description="标记出的元素名称，例如 老人 / 氧气管 / 病床")
    source_url: str = Field(default="", description="标记来源图片静态地址，可为空")
    point_x: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="点击点的归一化横坐标，范围 0-1",
    )
    point_y: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="点击点的归一化纵坐标，范围 0-1",
    )
    box_x: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="局部框左上角归一化横坐标，范围 0-1",
    )
    box_y: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="局部框左上角归一化纵坐标，范围 0-1",
    )
    box_width: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="局部框归一化宽度，范围 0-1",
    )
    box_height: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="局部框归一化高度，范围 0-1",
    )
    note: str = Field(default="", description="前端补充说明，可为空")


class FreezoneMarkDetectRequest(BaseModel):
    """局部元素标记识别请求。"""

    source_url: str = Field(description="待识别图片静态地址")
    point_x: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="点击点归一化横坐标，范围 0-1"
    )
    point_y: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="点击点归一化纵坐标，范围 0-1"
    )
    box_x: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="框选左上角归一化横坐标，范围 0-1"
    )
    box_y: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="框选左上角归一化纵坐标，范围 0-1"
    )
    box_width: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="框选归一化宽度，范围 0-1"
    )
    box_height: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="框选归一化高度，范围 0-1"
    )


class FreezoneMarkDetectData(BaseModel):
    """局部元素标记识别结果。"""

    mark: FreezoneVideoMark
    provider: str
    model: str


class FreezoneMarkDetectResponse(BaseModel):
    ok: bool
    data: FreezoneMarkDetectData


class FreezoneImageReversePromptRequest(BaseModel):
    """图反推提示词请求。"""

    source_url: str = Field(description="待分析图片静态地址")
    instruction: str = Field(default="", description="可选：反推提示词的补充要求")
    canvas_id: str = Field(default="", description="可选：来源画布 id，用于记录节点生成历史")
    node_id: str = Field(default="", description="可选：来源节点 id，用于记录节点生成历史")


class FreezoneImageReversePromptData(BaseModel):
    """图反推提示词结果。"""

    prompt: str


class FreezoneImageReversePromptResponse(BaseModel):
    ok: bool
    data: FreezoneImageReversePromptData


class FreezoneVideoOmniGenRequest(BaseModel):
    """全能参考视频请求。

    支持文本、图像、视频、音频、文件和公开网页链接混合输入。
    """

    prompt: str = Field(description="用户输入的视频内容描述")
    theme: str = Field(default="", description="主题参数，用于额外补充镜头主题、风格或叙事方向")
    camera_template_id: Optional[str] = Field(
        default=None,
        description="运镜模板 id，例如 locked_off / follow_tracking / orbit_up",
    )
    references: list[FreezoneVideoReferenceItem] = Field(
        default_factory=list,
        description=(
            "混合参考素材列表。图像、视频、音频上限由模型目录控制；"
            "文件和网页链接各最多 1 个且互斥"
        ),
    )
    marks: list[FreezoneVideoMark] = Field(
        default_factory=list,
        description="局部元素标记列表。来自前端点击图片选中的主体/物体局部区域，不是普通 tags",
    )
    aspect_ratio: str = Field(
        default="auto",
        description=(
            "视频比例。``auto``（默认）由后端按源视频画面探测、落到模型目录声明的档位；"
            "没有参考视频或探测不成时回退 16:9。"
        ),
    )
    resolution: str = Field(
        default="720p",
        description="输出清晰度档位",
    )
    duration_seconds: int = Field(
        default=5,
        ge=1,
        description="视频时长，至少 1 秒；不同模型支持的时长范围可能不同",
    )
    generate_audio: bool = Field(default=False, description="是否生成原生音频")
    human_review: bool = Field(
        default=False,
        description="是否开启 HuiMeng 真人素材审核/加白流程，用于可能包含真人人脸的素材",
    )
    scene_optimize: Optional[Literal["anime", "realistic"]] = Field(
        default=None,
        description="Seedance 2.0 Value 系列的场景风格优化参数",
    )
    model: str = Field(
        default="newapi_seedance-2.0-fast",
        description="视频模型或模型选项 id。请传 /freezone/video/models 返回值之一",
    )
    canvas_id: str = Field(default="", description="可选：来源画布 id，用于记录节点生成历史")
    node_id: str = Field(default="", description="可选：来源节点 id，用于记录节点生成历史")
    gen_mode: Literal["allReference"] = Field(
        default="allReference",
        description="全能参考入口的固定业务模式",
    )
    model_params: dict[str, Any] = Field(default_factory=dict)
    archive_to_library: bool = Field(
        default=False,
        description=(
            "[aipdd fork delta] 工具页批量任务用：为 true 时任务产出后会把结果自动"
            "登记进资产库「待分类资产」。画布等其它入口保持默认关闭。"
        ),
    )
    source_tool: str = Field(
        default="",
        description=(
            "[aipdd fork delta] 工具页任务归属标识：提交来自哪个功能（取工具 "
            "kind，如 lip-sync）；写入任务 metadata.source_tool，任务列表据此"
            "把任务只显示在产生它的功能页。画布等其它入口保持为空。"
        ),
    )


class FreezoneVideoCloneRequest(BaseModel):
    """视频复刻请求。

    参考视频 → 抽帧解读 → 逐镜复刻规划 → 逐镜生成 → 自动拼接成片。
    全自动成片：一次提交跑完整链路，任务产出为一条完整视频。
    """

    video_url: str = Field(
        description=("参考视频静态地址。必须是当前项目下真实存在的 /static/... 视频 URL"),
        examples=["/static/admin/58/freezone/_uploads/reference.mp4"],
    )
    prompt: str = Field(
        default="",
        description="可选的复刻附加要求（风格、主体、画面细节等）；留空则完全按参考视频复刻",
    )
    reference_urls: list[str] = Field(
        default_factory=list,
        max_length=4,
        description=(
            "[aipdd fork delta] 可选「参考内容」图片（最多 4 张）：角色形象/场景/风格参照，"
            "与提示词一起作用于对原视频的复刻处理；需模型支持全能参考模式"
        ),
    )
    model_text: str = Field(
        default="",
        description=(
            "[aipdd fork delta] 文本（LLM）模型或模型选项 id，用于抽帧解读后的"
            "逐镜复刻规划；留空走系统默认文本模型链；请传 /model-gateway/text-models 返回值之一"
        ),
    )
    model: str = Field(
        default="newapi_seedance-2.0-fast",
        description="视频模型或模型选项 id。请传 /freezone/video/models 返回值之一",
    )
    reference_mode: Literal["frames", "text"] = Field(
        default="frames",
        description=(
            "参考策略：frames 用原视频关键帧作为逐镜参考图（默认）；"
            "text 纯文本重制，不携带参考图"
        ),
    )
    reference_priority: Literal["subject", "composition"] = Field(
        default="subject",
        description=(
            "[aipdd fork delta] 带 reference_urls 时的复刻优先级："
            "subject 主体替换优先（默认，只提交参考内容、不提交原视频关键帧，"
            "画面主体以参考内容为准）；composition 忠实构图优先（关键帧与参考内容"
            "一并提交，保留原视频构图，但实测部分模型会整体还原关键帧里的原主体）。"
            "未提供参考内容时两种取值行为相同。"
        ),
    )
    audio_mode: Literal["generate", "mute", "source"] = Field(
        default="generate",
        description=(
            "音频策略：generate 由模型生成音（默认）；mute 静音成片；"
            "source 保留参考视频原声"
        ),
    )
    aspect_ratio: str = Field(
        default="16:9",
        description="成片比例；auto 当前回退为 16:9",
    )
    resolution: str = Field(
        default="720p",
        description="逐镜生成清晰度档位",
    )
    max_frames: int = Field(
        default=12,
        ge=3,
        le=50,
        description=("最多抽取多少张关键帧用于解读与逐镜参考。建议 12-20；" "越多复刻越细，耗时和 token 成本更高"),
    )
    scene_threshold: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description=(
            "ffmpeg 场景切换阈值，范围 0-1。越低越容易抽到帧；"
            "长镜头/剧情片建议 0.2-0.3，快剪视频建议 0.4-0.5"
        ),
    )
    human_review: bool = Field(
        default=False,
        description="是否开启 HuiMeng 真人素材审核/加白流程，用于可能包含真人人脸的素材",
    )
    scene_optimize: Optional[Literal["anime", "realistic"]] = Field(
        default=None,
        description="Seedance 2.0 Value 系列的场景风格优化参数",
    )
    model_params: dict[str, Any] = Field(default_factory=dict)
    archive_to_library: bool = Field(
        default=False,
        description=(
            "[aipdd fork delta] 工具页批量任务用：为 true 时任务产出后会把结果自动"
            "登记进资产库「待分类资产」。画布等其它入口保持默认关闭。"
        ),
    )
    source_tool: str = Field(
        default="",
        description=(
            "[aipdd fork delta] 工具页任务归属标识：提交来自哪个功能（取工具 "
            "kind，如 lip-sync）；写入任务 metadata.source_tool，任务列表据此"
            "把任务只显示在产生它的功能页。画布等其它入口保持为空。"
        ),
    )




class FreezoneVideoEraseRequest(BaseModel):
    """视频擦除请求。

    统一承接：
    - smart_subtitle: 智能去字幕（自动估计底部字幕框）
    - box: 框选擦除（前端传固定框）
    """

    source_url: str = Field(description="待处理视频的静态地址")
    mode: Literal["smart_subtitle", "box"] = Field(
        default="smart_subtitle",
        description="擦除模式：smart_subtitle 为智能去字幕，box 为框选擦除",
    )
    engine: Literal["auto", "local", "mediakit"] = Field(
        default="auto",
        description=(
            "[aipdd fork delta] 工具页引擎三选一：local 强制本机 ffmpeg 擦除（不出网）；"
            "mediakit 为用户显式选择火山 AI MediaKit 云端擦除；auto（默认，画布等既有"
            "入口）为隐式路由。mediakit / auto 未配置 Key、组织身份或失败时回退本地。"
        ),
    )
    box_x: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="框选左上角 x，归一化 0-1"
    )
    box_y: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="框选左上角 y，归一化 0-1"
    )
    box_width: Optional[float] = Field(
        default=None,
        gt=0.0,
        le=1.0,
        description="框选宽度，归一化 0-1",
    )
    box_height: Optional[float] = Field(
        default=None,
        gt=0.0,
        le=1.0,
        description="框选高度，归一化 0-1",
    )
    archive_to_library: bool = Field(
        default=False,
        description=(
            "[aipdd fork delta] 工具页批量任务用：为 true 时任务产出后会把结果自动"
            "登记进资产库「待分类资产」。画布等其它入口保持默认关闭。"
        ),
    )
    source_tool: str = Field(
        default="",
        description=(
            "[aipdd fork delta] 工具页任务归属标识：提交来自哪个功能（取工具 "
            "kind，如 lip-sync）；写入任务 metadata.source_tool，任务列表据此"
            "把任务只显示在产生它的功能页。画布等其它入口保持为空。"
        ),
    )


class FreezoneVideoUpscaleRequest(BaseModel):
    """视频高清请求。

    [aipdd fork delta] 优先走火山 AI MediaKit 生成式超分（需在设置页/环境变量
    配置 MEDIAKIT_API_KEY）；未配置、组织身份或失败时回退本地 ffmpeg
    传统缩放+降噪+锐化。denoise_strength / frame_interpolation 仅用于 ffmpeg 兜底。
    """

    source_url: str = Field(description="待高清处理视频的静态地址")
    resolution: Literal["1080p", "2k", "4k"] = Field(
        default="1080p",
        description="目标清晰度档位。按长边缩放：1080p=1920，2k=2560，4k=3840",
    )
    frame_interpolation: Literal["none"] = Field(
        default="none",
        description="补帧模式。基础版仅支持 none，不改变原视频帧率",
    )
    denoise_strength: Literal["none", "1x", "2x"] = Field(
        default="1x",
        description="降噪强度。none 不降噪；1x 轻度降噪；2x 中等降噪",
    )


class FreezoneAudioSeparateRequest(BaseModel):
    """音视频分离请求。

    当前仅实现轻量版：
    - 提取纯音频
    - 导出无声视频
    """

    source_url: str = Field(description="待处理视频的静态地址")
    target_episode: Optional[int] = Field(
        default=None,
        ge=1,
        description="可选：目标主线集数。提供后，任务结果会返回 beat_audio 推送目标",
    )
    target_beat: Optional[int] = Field(
        default=None,
        ge=1,
        description="可选：目标主线 beat。提供后，任务结果会返回 beat_audio 推送目标",
    )


class FreezoneAudioVoiceRef(BaseModel):
    """Freezone 音频节点声线引用。

    推荐先调用 `GET /freezone/audio/references` 获取可选声线，再把其中
    `available[]` 项目的 scope / voice_id / character_name / identity_id / slot 传回来。
    后端只信任这些标识，会重新从账号或项目数据解析真实音频文件，不使用前端传入的 path/url。
    """

    scope: Literal[
        "project_narrator",
        "user_custom",
        "character_default",
        "character_age_group",
        "identity",
        "identity_resolved",
    ] = Field(
        description=(
            "声线类型：project_narrator=项目解说人；user_custom=账号级我的音色；"
            "character_default=角色默认声线；"
            "character_age_group=角色年龄段声线；identity=身份自己的声线；"
            "identity_resolved=按身份声线→年龄段声线→角色默认声线兜底后的实际声线"
        ),
        examples=["identity_resolved"],
    )
    character_name: str = Field(
        default="",
        description="角色名。scope 为 character_* 或 identity* 时必填，需匹配项目角色名。",
        examples=["林小满"],
    )
    identity_id: str = Field(
        default="",
        description="身份 ID。scope 为 identity 或 identity_resolved 时必填。",
        examples=["林小满_青年"],
    )
    slot: str = Field(
        default="",
        description="年龄段声线槽位。scope=character_age_group 时必填，可选 child/youth/middle/elder。",
        examples=["youth"],
    )
    voice_id: str = Field(
        default="",
        description=(
            "账号级我的音色 ID。scope=user_custom 时必填，来自 "
            "GET /freezone/audio/references 的 user_voices[]/available[]，"
            "或 POST /freezone/audio/voices 的返回值。"
        ),
        examples=["fv_abc123"],
    )


class FreezoneAudioSpeechRequest(BaseModel):
    """Freezone 音频节点：文本生成语音请求。"""

    text: str = Field(
        description=("要合成的台词/旁白文本。"),
        examples=["她低声说：终于等到这一天了。"],
    )
    emotion_prompt: str = Field(
        default="",
        description=(
            "可选情绪提示词，会传给 IndexTTS2 的 emotion_prompt。为空时使用项目解说风格。"
            "示例：紧张、压低声音、带一点恐惧感。"
        ),
        examples=["紧张、压低声音、带一点恐惧感"],
    )
    voice_ref: Optional[FreezoneAudioVoiceRef] = Field(
        default=None,
        description=(
            "可选声线引用。为空时使用项目默认解说/解说主角声线；传入时后端会按 scope 和角色/身份标识"
            "重新解析账号级或项目内参考音频。"
        ),
    )
    # [aipdd fork delta] 画布音频节点按类型选模型：传入后台「默认生成模型」
    # 面板里 mediaType=audio 的目录 id；为空保持原 IndexTTS2 声线复刻链路。
    model: str = Field(
        default="",
        description=(
            "可选音频模型目录 id（后台媒体模型目录 mediaType=audio 的条目）。"
            "为空时使用默认语音引擎。"
        ),
    )
    target_episode: Optional[int] = Field(
        default=None,
        ge=1,
        description="可选：目标主线集数。提供后，任务结果会返回 beat_audio 推送目标",
    )
    target_beat: Optional[int] = Field(
        default=None,
        ge=1,
        description="可选：目标主线 beat。提供后，任务结果会返回 beat_audio 推送目标",
    )
    archive_to_library: bool = Field(
        default=False,
        description=(
            "[aipdd fork delta] 工具页批量任务用：为 true 时任务产出后会把结果自动"
            "登记进资产库「待分类资产」。画布等其它入口保持默认关闭。"
        ),
    )
    source_tool: str = Field(
        default="",
        description=(
            "[aipdd fork delta] 工具页任务归属标识：提交来自哪个功能（取工具 "
            "kind，如 lip-sync）；写入任务 metadata.source_tool，任务列表据此"
            "把任务只显示在产生它的功能页。画布等其它入口保持为空。"
        ),
    )


class FreezoneVoiceReplaceRequest(BaseModel):
    """[aipdd fork delta] 工具页「人声复刻 → 替换人声」请求。

    上传原始音频/视频 + 参考音频（目标音色）：后端抽取原始台词，把参考音频
    注册成账号级声线后走 IndexTTS2 零样本克隆，输出「目标音色的新声」音频。
    两个上传地址都必须是本会话上传得到的项目内 /static/... 地址。
    """

    source_url: str = Field(
        description="原始音频/视频的静态地址（从中抽取待替换的台词）"
    )
    reference_audio_url: str = Field(
        description=(
            "参考音频（目标音色）的静态地址；后端会先把它注册成账号级 user_custom "
            "声线再用于零样本合成"
        )
    )
    reference_name: str = Field(
        default="",
        description="可选：注册音色时的展示名；为空则用上传文件名。",
    )
    model: str = Field(
        default="",
        description=(
            "可选音频模型目录 id（后台媒体模型目录 mediaType=audio 的条目）。"
            "为空时使用默认 IndexTTS2 零样本声线复刻链路。"
        ),
    )
    asr_model: str = Field(
        default="",
        description=(
            "台词识别（ASR）候选 id（取 /freezone/douyin-copywrite/models 返回值之一）。"
            "为空时由执行时自动选择能处理本地音频的候选（非组织任务：豆包语音→网关；"
            "组织任务：只走网关），指定的候选不可用时同样退回自动选择。"
        ),
    )
    target_episode: Optional[int] = Field(
        default=None,
        ge=1,
        description="可选：目标主线集数。提供后，任务结果会返回 beat_audio 推送目标",
    )
    target_beat: Optional[int] = Field(
        default=None,
        ge=1,
        description="可选：目标主线 beat。提供后，任务结果会返回 beat_audio 推送目标",
    )
    archive_to_library: bool = Field(
        default=False,
        description=(
            "[aipdd fork delta] 工具页批量任务用：为 true 时任务产出后会把结果自动"
            "登记进资产库「待分类资产」。其它入口保持默认关闭。"
        ),
    )
    source_tool: str = Field(
        default="",
        description=(
            "[aipdd fork delta] 工具页任务归属标识（如 voice-clone）；写入任务 "
            "metadata.source_tool，任务列表据此把任务只显示在产生它的功能页。"
        ),
    )


class FreezoneVideoDubbingRequest(BaseModel):
    """[aipdd fork delta] 工具页「视频配音」请求。

    上传原始视频 + 参考音频（目标音色）：后端抽取原始台词（不翻译），把参考音频
    注册成账号级声线后走零样本克隆在原语言重说，最后用新声**替换原视频整条音轨**，
    输出配好音的 mp4 成片。两个上传地址都必须是本会话上传得到的项目内 /static/... 地址。
    与 ``FreezoneVoiceReplaceRequest`` 同族，多一个 ``language`` 发音提示（不触发翻译）。
    """

    source_url: str = Field(
        description="原始视频的静态地址（从中抽取待配音的台词，并作为回填视频画面输入）"
    )
    reference_audio_url: str = Field(
        description=(
            "参考音频（目标音色）的静态地址；后端会先把它注册成账号级 user_custom "
            "声线再用于零样本合成"
        )
    )
    reference_name: str = Field(
        default="",
        description="可选：注册音色时的展示名；为空则用上传文件名。",
    )
    language: str = Field(
        default="",
        description=(
            "[aipdd fork delta] 语种/发音提示，透传合成上游 metadata（支持的渠道认、"
            "不支持的自然忽略）；台词本身不翻译，为空则不带该提示。"
        ),
    )
    model: str = Field(
        default="",
        description=(
            "可选音频模型目录 id（后台媒体模型目录 mediaType=audio 的条目）。"
            "为空时使用默认 IndexTTS2 零样本声线复刻链路。"
        ),
    )
    asr_model: str = Field(
        default="",
        description=(
            "台词识别（ASR）候选 id（取 /freezone/douyin-copywrite/models 返回值之一）。"
            "为空时由执行时自动选择能处理本地音频的候选（非组织任务：豆包语音→网关；"
            "组织任务：只走网关），指定的候选不可用时同样退回自动选择。"
        ),
    )
    target_episode: Optional[int] = Field(
        default=None,
        ge=1,
        description="可选：目标主线集数。提供后，任务结果会返回 beat_audio 推送目标",
    )
    target_beat: Optional[int] = Field(
        default=None,
        ge=1,
        description="可选：目标主线 beat。提供后，任务结果会返回 beat_audio 推送目标",
    )
    archive_to_library: bool = Field(
        default=False,
        description=(
            "[aipdd fork delta] 工具页批量任务用：为 true 时任务产出后会把结果自动"
            "登记进资产库「待分类资产」。其它入口保持默认关闭。"
        ),
    )
    source_tool: str = Field(
        default="",
        description=(
            "[aipdd fork delta] 工具页任务归属标识（如 video-dubbing）；写入任务 "
            "metadata.source_tool，任务列表据此把任务只显示在产生它的功能页。"
        ),
    )


class FreezoneVideoMixRequest(BaseModel):
    """混剪短视频请求。

    资产库图片/视频素材池 → 主题或手写文案 → 配音 → 字幕 → 顺序循环混剪成片。
    素材按选择顺序切段、循环铺满配音时长（MoneyPrinterTurbo local 模式口径）。
    """

    material_urls: list[str] = Field(
        description=(
            "素材池静态地址列表（图片/视频混合），必须是当前项目下真实存在的 "
            "/static/... URL；按顺序循环铺满成片时长"
        ),
        examples=[["/static/admin/58/freezone/_uploads/a.jpg"]],
    )
    subject: str = Field(
        default="",
        description="视频主题（如「春日露营的三个理由」）；与 script 至少填一项",
    )
    script: str = Field(
        default="",
        description="手写口播文案；留空则由主题用 AI 生成",
    )
    model_text: str = Field(
        default="",
        description=(
            "[aipdd fork delta] 文本（LLM）模型或模型选项 id，用于由主题自动生成"
            "口播文案（手写 script 时不用）；留空走系统默认文本模型链；"
            "请传 /model-gateway/text-models 返回值之一"
        ),
    )
    audio_mode: Literal["generate", "mute"] = Field(
        default="generate",
        description=(
            "音频策略：generate 用模型合成配音（默认，时长驱动剪辑）；"
            "mute 不配音，按素材总长（或 max_duration_seconds）成片"
        ),
    )
    subtitle_enabled: bool = Field(
        default=False,
        description="是否烧录字幕（仅配音模式生效；默认不烧录，由用户选择）",
    )
    subtitle_font_size: str = Field(
        default="medium",
        description=(
            "字幕字号档位 small/medium/large（仅烧录字幕时生效；"
            "默认 medium=随画布自动换算）"
        ),
    )
    bgm_url: str = Field(
        default="",
        description="可选背景音乐静态地址（资产库音频）；留空不加 BGM",
    )
    bgm_volume: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description=(
            "背景音乐音量 0-1；留空自动：与配音混音为 0.2，静音模式作为唯一音轨为 1.0"
        ),
    )
    aspect_ratio: str = Field(
        default="9:16",
        description="成片比例：9:16（竖屏默认）/ 16:9 / 1:1",
    )
    resolution: str = Field(
        default="720p",
        description="成片清晰度档位：720p（默认）/ 1080p",
    )
    clip_duration: float = Field(
        default=3.0,
        ge=1.0,
        le=10.0,
        description="每个素材片段的时长（秒），1-10，默认 3",
    )
    transition: Literal["none", "fade"] = Field(
        default="fade",
        description="转场：fade 每段淡入（末段补淡出，默认）；none 硬切",
    )
    fit_mode: Literal["cover", "contain"] = Field(
        default="cover",
        description="画面适配：cover 裁切铺满（默认）；contain 缩放留黑边",
    )
    max_duration_seconds: Optional[float] = Field(
        default=None,
        ge=5.0,
        le=600.0,
        description="静音模式可选：成片目标时长（秒），超过一份素材时循环铺满",
    )
    emotion_prompt: str = Field(
        default="",
        description="可选配音情绪提示词，如「轻松、亲切」；留空用项目解说风格",
    )
    voice_ref: Optional[FreezoneAudioVoiceRef] = Field(
        default=None,
        description="可选声线引用；为空时使用项目默认解说声线",
    )
    model: str = Field(
        default="",
        description=(
            "可选音频模型目录 id（后台媒体模型目录 mediaType=audio 的条目）；"
            "为空时使用默认语音引擎"
        ),
    )
    archive_to_library: bool = Field(
        default=False,
        description=(
            "[aipdd fork delta] 工具页任务用：为 true 时任务产出后会把结果自动"
            "登记进资产库「待分类资产」。"
        ),
    )
    source_tool: str = Field(
        default="",
        description=(
            "[aipdd fork delta] 工具页任务归属标识：提交来自哪个功能（取工具 "
            "kind）；写入任务 metadata.source_tool。画布等其它入口保持为空。"
        ),
    )


class FreezoneWhiteboardAnimationRequest(BaseModel):
    """[aipdd fork delta] 白板动画视频请求。

    粘贴口播文案 → 分镜规划 → 逐幕暖米黄线稿出图 → 本地叠加重点文字 →
    逐笔揭示白板动画 → 拼接 + 配音（+ 可选字幕/BGM）成片。图像/配音/文本三类
    模型均可自选，留空走系统默认链。
    """

    script: str = Field(
        description="口播文案（白板动画旁白全文）；必填，1-10000 字",
    )
    model_text: str = Field(
        default="",
        description=(
            "文本（LLM）分镜规划模型或模型选项 id；留空走系统默认文本模型链；"
            "请传 /model-gateway/text-models 返回值之一"
        ),
    )
    model_image: str = Field(
        default="",
        description=(
            "线稿出图模型目录 id（后台媒体模型目录 mediaType=image 的条目）；"
            "留空使用默认图像模型"
        ),
    )
    image_model_params: Optional[dict] = Field(
        default=None,
        description="可选图像模型参数（与 model_image 配套的 request 参数覆盖）",
    )
    model: str = Field(
        default="",
        description=(
            "配音音频模型目录 id（后台媒体模型目录 mediaType=audio 的条目）；"
            "留空使用默认语音引擎"
        ),
    )
    aspect_ratio: str = Field(
        default="16:9",
        description="成片比例：16:9（横屏默认）/ 9:16 / 1:1",
    )
    resolution: str = Field(
        default="720p",
        description="成片清晰度档位：720p（默认）/ 1080p",
    )
    max_scenes: int = Field(
        default=8,
        ge=1,
        le=12,
        description="分镜幕数上限 1-12，默认 8；实际幕数随文案长度自适应",
    )
    pen_cursor: bool = Field(
        default=True,
        description="是否在逐笔揭示前沿叠加笔尖光标（可关闭）",
    )
    subtitle_enabled: bool = Field(
        default=False,
        description="是否烧录字幕（按配音时长切分；默认不烧录）",
    )
    subtitle_font_size: str = Field(
        default="medium",
        description="字幕字号档位 small/medium/large（仅烧录字幕时生效）",
    )
    emotion_prompt: str = Field(
        default="",
        description="可选配音情绪提示词，如「轻松、亲切」；留空用项目解说风格",
    )
    voice_ref: Optional[FreezoneAudioVoiceRef] = Field(
        default=None,
        description="可选声线引用；为空时使用项目默认解说声线",
    )
    bgm_url: str = Field(
        default="",
        description="可选背景音乐静态地址（资产库音频）；留空不加 BGM",
    )
    bgm_volume: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="背景音乐音量 0-1；留空自动：与配音混音为 0.2",
    )
    archive_to_library: bool = Field(
        default=False,
        description=(
            "[aipdd fork delta] 工具页任务用：为 true 时任务产出后会把结果自动"
            "登记进资产库「待分类资产」。"
        ),
    )
    source_tool: str = Field(
        default="",
        description=(
            "[aipdd fork delta] 工具页任务归属标识：提交来自哪个功能（取工具 "
            "kind）；写入任务 metadata.source_tool。"
        ),
    )


# [aipdd fork delta] 科普讲解视频（MG explainer）：用户交参考资料 → 网关文本模型
# 改写分章解说词 → 网关文本模型出受限分镜 JSON → 网关音频模型配音 → 本地逐帧代码
# 绘制 MG 画面并编码成片。画面不依赖浏览器/前端渲染框架，模型侧只用文本 + 音频。
class FreezoneExplainerVideoRequest(BaseModel):
    """科普讲解视频请求。

    参考内容必填（一段主题说明、一篇文章或一份文档），其余留空走系统默认链；
    成片固定横屏 16:9，时长 90-330 秒。
    """

    reference_content: str = Field(
        description=(
            "参考资料（主题说明 / 文章 / 文档正文）；必填，1-12000 字。"
            "解说词由文本模型在这份资料范围内改写，不会补资料里没有的事实"
        ),
    )
    title: str = Field(
        default="",
        description="可选片名提示（如「RAG 检索增强生成」）；留空由模型自拟",
    )
    duration_s: int = Field(
        default=180,
        description="目标成片时长（秒），自动夹到 90-330；默认 180",
    )
    model_text: str = Field(
        default="",
        description=(
            "文本（LLM）模型或模型选项 id（解说词与分镜共用）；留空走系统默认文本模型链；"
            "请传 /model-gateway/text-models 返回值之一"
        ),
    )
    model: str = Field(
        default="",
        description=(
            "配音音频模型目录 id（后台媒体模型目录 mediaType=audio 的条目）；"
            "留空使用默认语音引擎"
        ),
    )
    voice_ref: Optional[FreezoneAudioVoiceRef] = Field(
        default=None,
        description="可选声线引用；为空时使用项目默认解说声线",
    )
    emotion_prompt: str = Field(
        default="",
        description="可选配音情绪提示词，如「沉稳、克制」；留空用项目解说风格",
    )
    background: str = Field(
        default="dots",
        description="幕底样式：dots 点阵波（默认）/ stars 星点",
    )
    resolution: str = Field(
        default="720p",
        description="成片清晰度档位：720p（默认）/ 1080p（横屏 16:9 固定）",
    )
    bgm_url: str = Field(
        default="",
        description="可选背景音乐静态地址（资产库音频）；留空不加 BGM",
    )
    bgm_volume: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="背景音乐音量 0-1；留空自动：与配音混音为 0.2",
    )
    archive_to_library: bool = Field(
        default=False,
        description=(
            "[aipdd fork delta] 工具页任务用：为 true 时任务产出后会把结果自动"
            "登记进资产库「待分类资产」。"
        ),
    )
    source_tool: str = Field(
        default="",
        description=(
            "[aipdd fork delta] 工具页任务归属标识：提交来自哪个功能（取工具 "
            "kind）；写入任务 metadata.source_tool。"
        ),
    )


# [aipdd fork delta] 一句话生成短视频（ViMax idea2video 口径）：一句话创意 →
# 故事扩写 → 角色提取与三视图 → 分场景脚本 → 分镜/机位树/帧 → 逐镜视频 →
# 拼接成片。帧/肖像走图片模型目录，逐镜视频走视频模型目录。
class FreezoneVideoIdeaRequest(BaseModel):
    """一句话生成短视频请求。"""

    idea: str = Field(
        description="一句话创意（必填）。如「一个程序员发现自己的影子有了意识」",
        examples=["一只流浪猫在雨夜救下迷路的小女孩"],
    )
    style: str = Field(
        default="Cinematic, realistic, film-like.",
        description="视觉风格，贯穿角色肖像与逐镜画面的生成",
    )
    user_requirement: str = Field(
        default="",
        description="可选附加要求（受众、类型、节奏、结局等）",
    )
    max_scenes: int = Field(
        default=3,
        ge=1,
        le=6,
        description=(
            "场景数上限 1-6，默认 3（故事自然分场少于上限也可）；"
            "[aipdd fork delta] 设置 total_duration 时由总时长推导，本值不生效"
        ),
    )
    max_shots_per_scene: int = Field(
        default=5,
        ge=1,
        le=10,
        description=(
            "每个场景的镜头数上限 1-10，默认 5；"
            "[aipdd fork delta] 设置 total_duration 时由 AI 自定（上限 10）"
        ),
    )
    shot_duration: float = Field(
        default=5.0,
        ge=3.0,
        le=10.0,
        description=(
            "单镜生成时长（秒）3-10，默认 5；"
            "[aipdd fork delta] 设置 total_duration 时作为缺省基线（AI 逐镜规划）"
        ),
    )
    total_duration: Optional[float] = Field(
        default=None,
        ge=5,
        le=300,
        description=(
            "[aipdd fork delta] 成片目标总时长（秒）5-300，工具页默认 60：设置后"
            "由 AI 依总时长自主分场与分镜（场景数、每场景镜头数、单镜时长不再是"
            "用户输入）；留空沿用 max_scenes / max_shots_per_scene / shot_duration"
        ),
    )
    portrait_mode: Literal["three", "front"] = Field(
        default="three",
        description=(
            "角色肖像策略：three 生成正/侧/背三视图（默认，画面一致性更好、"
            "耗时更长）；front 只生成正面视图"
        ),
    )
    aspect_ratio: str = Field(
        default="16:9",
        description="成片比例：16:9（默认）/ 9:16 / 1:1 / 4:3 / 3:4 / 21:9",
    )
    resolution: str = Field(
        default="720p",
        description="逐镜生成清晰度档位",
    )
    generate_audio: bool = Field(
        default=True,
        description="逐镜视频是否由模型生成音频（对白/音效）；模型不支持时静默关闭",
    )
    model_text: str = Field(
        default="",
        description=(
            "[aipdd fork delta] 文本（LLM）模型或模型选项 id，用于故事扩写、"
            "角色提取、分场景脚本、分镜、镜头分解、机位树等文本步骤；"
            "留空走系统默认文本模型链；请传 /model-gateway/text-models 返回值之一"
        ),
    )
    model_image: str = Field(
        default="",
        description=(
            "图片模型或模型选项 id（角色肖像与逐镜帧生成）；"
            "请传 /freezone/image/models 返回值之一"
        ),
    )
    image_model_params: dict[str, Any] = Field(
        default_factory=dict,
        description="图片模型高级参数（随图片目录选型提交）",
    )
    model_video: str = Field(
        default="newapi_seedance-2.0-fast",
        description=(
            "视频模型或模型选项 id（逐镜生成）；"
            "请传 /freezone/video/models 返回值之一"
        ),
    )
    model_params: dict[str, Any] = Field(
        default_factory=dict,
        description="视频模型高级参数",
    )
    human_review: bool = Field(
        default=False,
        description="是否开启 HuiMeng 真人素材审核/加白流程，用于可能包含真人人脸的素材",
    )
    scene_optimize: Optional[Literal["anime", "realistic"]] = Field(
        default=None,
        description="Seedance 2.0 Value 系列的场景风格优化参数",
    )
    archive_to_library: bool = Field(
        default=False,
        description=(
            "[aipdd fork delta] 工具页任务用：为 true 时任务产出后会把结果自动"
            "登记进资产库「待分类资产」。"
        ),
    )
    source_tool: str = Field(
        default="",
        description=(
            "[aipdd fork delta] 工具页任务归属标识：提交来自哪个功能（取工具 "
            "kind）；写入任务 metadata.source_tool。画布等其它入口保持为空。"
        ),
    )


class FreezoneDouyinCopywriteRequest(BaseModel):
    """[aipdd fork delta] 工具页「抖音文案提取」请求。

    视频链接（完整链接 / v.douyin.com 短链 / 纯数字 ID，可整段粘贴 App 分享
    文案）→ 拦截详情 → 用户指定 ASR 模型转写；文案以任务结果文本与 txt
    文件返回。
    """

    video: str = Field(
        description="抖音视频链接或纯数字视频 ID；支持直接粘贴 App 分享文案",
        examples=["https://v.douyin.com/xxxxxxx/", "7659806220995317046"],
    )
    model: str = Field(
        default="",
        description=(
            "ASR 模型候选 id（取 /freezone/douyin-copywrite/models 返回值之一，"
            "如 gateway:volcengine:doubao-seed-asr 或 "
            "funasr:aipdd:aipdd_funasr_transcribe）；工具页要求显式选择"
            "（不设默认模型），留空仅作程序化调用兜底、回落内置 FunASR"
        ),
    )
    source_tool: str = Field(
        default="",
        description=(
            "[aipdd fork delta] 工具页任务归属标识：提交时带上功能 kind，"
            "经 payload 浮现到 task metadata.source_tool，供前端按功能过滤任务。"
        ),
    )


class FreezoneMediaCopywriteRequest(BaseModel):
    """[aipdd fork delta] req21 工具页「音视频提取文案」请求。

    两大来源类：链接下载（``source_type="link"``，抖音 / B站 / 快手 / 小红书 /
    TikTok / YouTube / 通用，统一走 yt-dlp、抖音双通路回退）、上传视频
    （``"upload_video"``）与上传音频（``"upload_audio"``）——后两者的 ``url``
    为前端先传 ``/freezone/upload`` 返回的同源静态地址。
    """

    source_type: str = Field(
        description='来源类型："link" | "upload_video" | "upload_audio"',
        examples=["link", "upload_video", "upload_audio"],
    )
    url: str = Field(
        description=(
            "link：视频链接 / 分享文案 / 纯 ID；upload_video、upload_audio："
            "上传后返回的同源静态地址"
        ),
        examples=["https://b23.tv/xxxx/", "/static/projects/p1/freezone/_uploads/a.mp4"],
    )
    platform: str = Field(
        default="",
        description=(
            "链接来源的平台提示（douyin / bilibili / kuaishou / xiaohongshu / "
            "tiktok / youtube / other）；douyin 启用双通路，其余交 yt-dlp 自识别"
        ),
    )
    model: str = Field(
        default="",
        description=(
            "ASR 模型候选 id（取 /freezone/media-copywrite/models 返回值之一）；"
            "工具页要求显式选择，留空仅作程序化调用兜底、回落内置 FunASR"
        ),
    )
    source_tool: str = Field(
        default="",
        description=(
            "[aipdd fork delta] 工具页任务归属标识：提交时带上功能 kind，"
            "经 payload 浮现到 task metadata.source_tool，供前端按功能过滤任务。"
        ),
    )


class FreezoneAudioMusicRequest(BaseModel):
    """Freezone 音频节点：文本生成音乐请求。"""

    input: str = Field(
        description="音乐描述 prompt。",
        examples=["cinematic rain-soaked suspense music"],
    )
    model: str = Field(
        default="",
        description="音乐模型；留空 = 用「设置 › 模型」里配好的默认音频模型。",
    )
    response_format: Literal["mp3", "opus", "pcm", "ulaw", "alaw"] = Field(
        default="mp3",
        description="音频返回格式。mp3 会自动映射为 mp3_44100_128。",
    )
    music_length_ms: int = Field(
        default=30_000,
        ge=3000,
        le=600000,
        description="生成长度，毫秒，范围 3000 到 600000。",
    )
    force_instrumental: bool = Field(
        default=True,
        description="是否强制生成纯音乐。",
    )
    respect_sections_durations: bool = Field(
        default=True,
        description="是否严格遵循分段时长。对 prompt 生成通常由模型忽略。",
    )
    output_format: str = Field(
        default="mp3_44100_128",
        description="上游原生音频格式，例如 mp3_44100_128、opus_48000_128。",
    )


class FreezoneMusicCreateRequest(BaseModel):
    """[aipdd fork delta req46] 工具页「音乐创作」：MiniMax Music 3.0 五种模式。

    与上面的画布音乐节点不同链路：那条走网关通用音频协议 + 默认音频位，本条直连
    厂商音乐接口（模式→字段在 ``freezone/minimax_music.py`` 收口）。模型名与站点
    都来自设置：``model`` 留空 = 用设置页「音乐默认模型」，两者皆空提交期拦住。
    """

    mode: Literal["one_sentence", "lyrics", "instrumental", "cover", "humming"] = Field(
        description=(
            "创作模式：one_sentence 一句话生成、lyrics 用歌词生成、"
            "instrumental 纯音乐、cover 翻唱、humming 哼唱（走两步翻唱）"
        ),
        examples=["one_sentence"],
    )
    prompt: str = Field(
        default="",
        description=(
            "音乐描述：一句话/纯音乐必填（1-2000 字）；歌词模式选填；"
            "翻唱/哼唱为目标风格描述（10-300 字，必填）"
        ),
        examples=["轻快的流行摇滚，副歌开阔"],
    )
    lyrics: str = Field(
        default="",
        description=(
            "歌词：歌词模式必填（1-3500 字，支持 [Verse] / [Chorus] 等结构标签）；"
            "翻唱选填；带 cover_feature_id 的两步翻唱必填（10-1000 字）"
        ),
    )
    model: str = Field(
        default="",
        description=(
            "音乐模型目录 id（取 /freezone/music/models）；留空用设置页的"
            "「音乐默认模型」。翻唱/哼唱需要翻唱模型，其余需要文本生成音乐模型"
        ),
    )
    provider: str = Field(
        default="",
        description=(
            "站点渠道（minimax 海外站 / minimax_cn 国内站）；留空时取模型条目在"
            "设置页所属渠道，两边 Key 不通用"
        ),
    )
    reference_audio: str = Field(
        default="",
        description=(
            "参考音频：同源静态地址 / 项目内相对路径 / 公网直链。"
            "上限 50MB，时长 6 秒-6 分钟，仅 mp3 / wav / flac"
        ),
        examples=["/static/projects/p1/freezone/_uploads/hum.wav"],
    )
    cover_feature_id: str = Field(
        default="",
        description=(
            "两步翻唱的特征 id（取 /freezone/audio/music-cover-preprocess 返回值，"
            "24 小时有效）：哼唱模式改词后回填；传了就不要再带 reference_audio"
        ),
    )
    sample_rate: int = Field(
        default=44100,
        description="采样率，仅允许 16000 / 24000 / 32000 / 44100。",
    )
    bitrate: int = Field(
        default=256000,
        description="码率，仅允许 32000 / 64000 / 128000 / 256000。",
    )
    audio_format: Literal["mp3", "wav", "pcm"] = Field(
        default="mp3",
        description="音频格式（产物后缀与之一致）。",
    )
    source_name: str = Field(
        default="",
        description="[aipdd fork delta] 归档条目名前缀（来源素材名），可空",
    )
    archive_to_library: bool = Field(
        default=False,
        description=(
            "[aipdd fork delta] 工具页任务用：为 true 时任务产出后会把结果自动"
            "登记进资产库「待分类资产」。"
        ),
    )
    source_tool: str = Field(
        default="",
        description=(
            "[aipdd fork delta] 工具页任务归属标识：提交时带上功能 kind，"
            "经 payload 浮现到 task metadata.source_tool，供前端按功能过滤任务。"
        ),
    )


class FreezoneMusicCoverPreprocessRequest(BaseModel):
    """[aipdd fork delta req46] 两步翻唱第一步：参考音频 → 特征 id + ASR 歌词。

    同步返回（不建任务）：产物只是两个字段，用户要在界面上看到/修改抽取的歌词后
    再提交生成，拆成异步任务反而多一次往返。
    """

    model: str = Field(
        default="",
        description="翻唱音乐模型目录 id；留空用设置页的「音乐默认模型」。",
    )
    provider: str = Field(
        default="",
        description="站点渠道（minimax / minimax_cn）；留空取模型条目所属渠道。",
    )
    reference_audio: str = Field(
        description="参考音频：同源静态地址 / 项目内相对路径 / 公网直链。",
        examples=["/static/projects/p1/freezone/_uploads/hum.wav"],
    )


class FreezoneVideoComposeItem(BaseModel):
    item_id: str = Field(description="前端片段唯一标识")
    source_url: str = Field(description="源媒体静态地址")
    timeline_start: float = Field(default=0.0, ge=0.0, description="片段在时间线上的开始秒数")
    source_start: float = Field(default=0.0, ge=0.0, description="源媒体裁剪起始秒")
    source_end: float = Field(gt=0.0, description="源媒体裁剪结束秒，必须大于 source_start")
    volume: float = Field(default=1.0, ge=0.0, le=2.0, description="音量倍率")
    muted: bool = Field(default=False, description="是否静音")


class FreezoneVideoComposeTrack(BaseModel):
    track_id: str = Field(description="前端轨道唯一标识")
    kind: Literal["video", "audio"] = Field(description="轨道类型")
    items: list[FreezoneVideoComposeItem] = Field(default_factory=list, description="轨道片段列表")


class FreezoneVideoComposeRequest(BaseModel):
    title: str = Field(default="", description="合成任务标题，可为空")
    canvas_id: str = Field(default="", description="来源画布 id，可为空")
    resolution: Literal["720p", "1080p"] = Field(default="1080p", description="目标输出分辨率")
    fps: int = Field(default=30, ge=1, le=60, description="输出帧率")
    background_color: str = Field(default="#000000", description="补边或空隙使用的背景色")
    keep_original_audio: bool = Field(default=True, description="是否保留视频片段自带音频")
    tracks: list[FreezoneVideoComposeTrack] = Field(
        default_factory=list, description="时间线轨道列表"
    )


FreezoneVideoGenRequest.model_rebuild()
FreezoneImageToVideoRequest.model_rebuild()
FreezoneKeyframeVideoRequest.model_rebuild()
FreezoneVideoOmniGenRequest.model_rebuild()
FreezoneVideoCloneRequest.model_rebuild()


class FreezoneTextTranslateRequest(BaseModel):
    """Freezone 文本工具：中英文互译请求。"""

    text: str = Field(description="待翻译的原始文本或提示词")
    node_type: Literal["generic", "image", "video", "audio", "text"] = Field(
        default="generic",
        description="使用场景。用于帮助翻译器按节点类型保留合适的提示词语气",
    )
    canvas_id: str = Field(default="", description="可选：来源画布 id，用于记录节点生成历史")
    node_id: str = Field(default="", description="可选：来源节点 id，用于记录节点生成历史")


class FreezoneTextGenerateRequest(BaseModel):
    """Freezone 文本节点：根据创作要求生成自由文本。"""

    prompt: str = Field(min_length=1, max_length=20000, description="文本创作要求")
    canvas_id: str = Field(default="", description="可选：来源画布 id，用于记录节点生成历史")
    node_id: str = Field(default="", description="可选：来源节点 id，用于记录节点生成历史")


class FreezoneTextTranslateData(BaseModel):
    translated_text: str
    source_language: Literal["zh", "en"]
    target_language: Literal["zh", "en"]
    node_type: Literal["generic", "image", "video", "audio", "text"]


class FreezoneTextTranslateResponse(BaseModel):
    ok: Literal[True] = True
    data: FreezoneTextTranslateData


class FreezoneStoryScriptCharacterRef(BaseModel):
    """角色参考输入项：画布上连进脚本节点的角色图节点。"""

    name: str = Field(default="", description="角色名/角色标签，用于和生成行里的角色名对齐")
    description: str = Field(default="", description="可选：已有角色描述")
    image_url: str = Field(default="", description="角色参考图静态 URL（/static/...）")
    role: str = Field(default="", description="可选：角色定位或叙事功能，如「女主」")


class FreezoneStoryScriptGenerateRequest(BaseModel):
    """Freezone 文本节点：故事脚本生成请求。

    四种输入至少给一个：``source_text`` / ``source_url``（剧本文本）、
    ``video_url``（视频参考，走抽帧 + 视觉模型）、``character_refs``（角色图参考）。
    早期版本只声明了文本字段，前端发来的 ``video_url`` / ``character_refs`` 会被
    Pydantic 的 ``extra="ignore"`` 静默丢弃，导致「视频参考生成分镜脚本」实际上
    从未把视频交给模型（issue #207）。
    """

    source_text: str = Field(
        default="",
        description="已上传剧本的文本内容。与 source_url / video_url / character_refs 至少提供一个",
    )
    source_url: Optional[str] = Field(
        default=None,
        description="已上传剧本文本文件的静态 URL",
    )
    video_url: Optional[str] = Field(
        default=None,
        description="视频参考的静态 URL；给了就先抽关键帧再交给视觉模型拆分镜",
    )
    duration_sec: Optional[float] = Field(
        default=None,
        description="视频总时长（秒），用于让分镜时长分配更贴近原视频",
    )
    character_refs: list[FreezoneStoryScriptCharacterRef] = Field(
        default_factory=list,
        description="角色参考图列表；生成后按角色名回填 character_image_1/2",
    )
    max_frames: int = Field(
        default=20,
        ge=3,
        le=50,
        description="视频参考时的最大抽帧数",
    )
    scene_threshold: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="视频参考时 ffmpeg 场景切换检测阈值",
    )
    prompt: str = Field(
        default="根据我上传的剧本生成一个完整的故事脚本",
        description="用户补充要求，会和源剧本内容一起交给模型",
    )
    model: str = Field(
        default="newapi_gemini_flash",
        description="文本模型选项 id 或展示名",
    )
    canvas_id: str = Field(default="", description="可选：来源画布 id，用于记录节点生成历史")
    node_id: str = Field(default="", description="可选：来源节点 id，用于记录节点生成历史")


class FreezoneVideoScriptReverseRequest(BaseModel):
    """[aipdd fork delta] 工具页「视频反推剧本」提交请求。

    上传视频后由视觉模型抽帧反推纯文本剧本；产物写
    ``outputs/freezone_video_script_reverse/{job}.txt``，不覆盖故事脚本 JSON。
    ``model`` 为用户选定的视觉模型（候选见 ``/model-gateway/vision-models``），
    留空回退系统默认视觉链。
    """

    video_url: str = Field(
        ...,
        min_length=1,
        description="已上传视频的静态 URL（/freezone/upload 落盘后回传）",
    )
    prompt: str = Field(
        default="",
        description="用户补充要求（如风格/侧重），随任务书交给视觉模型",
    )
    model: str = Field(
        default="",
        description="选定的视觉模型调用名；留空走系统默认视觉模型",
    )
    duration_sec: Optional[float] = Field(
        default=None,
        description="视频总时长（秒），用于让分镜时长分配更贴近原视频",
    )
    max_frames: int = Field(
        default=20,
        ge=3,
        le=50,
        description="最大抽帧数",
    )
    scene_threshold: float = Field(
        default=0.3,
        ge=0.0,
        le=1.0,
        description="ffmpeg 场景切换检测阈值",
    )


class FreezoneStoryScriptRow(BaseModel):
    shot_no: int = Field(description="镜号")
    duration: int = Field(description="时长，单位秒")
    visual_description: str = Field(description="画面描述")
    character_1: str = Field(default="", description="角色1")
    character_description_1: str = Field(default="", description="角色描述1")
    character_image_1: str = Field(
        default="",
        description="角色图1 URL；模型留空，由后端按角色名匹配 character_refs 回填",
    )
    character_2: str = Field(default="", description="角色2")
    character_description_2: str = Field(default="", description="角色描述2")
    character_image_2: str = Field(
        default="",
        description="角色图2 URL；模型留空，由后端按角色名匹配 character_refs 回填",
    )
    reference: str = Field(
        default="",
        description="参考图 URL；模型留空，由后端按 keyframe_index 回填对应关键帧",
    )
    keyframe_index: int = Field(
        default=0,
        description="视频参考模式下，这一镜对应的输入关键帧序号（1-based，0 表示无）",
    )
    shot: str = Field(default="", description="景别")
    character_action: str = Field(default="", description="角色动作")
    emotion: str = Field(default="", description="情绪")
    scene_tags: str = Field(default="", description="场景标签")
    lighting_mood: str = Field(default="", description="光影氛围")
    sound: str = Field(default="", description="音效")
    dialogue: str = Field(default="", description="对白")
    shot_prompt: str = Field(default="", description="分镜提示词")
    video_motion_prompt: str = Field(default="", description="视频运动提示词")


class FreezoneStoryScriptGenerateData(BaseModel):
    title: str = Field(default="", description="故事脚本标题")
    rows: list[FreezoneStoryScriptRow] = Field(default_factory=list, description="结构化故事脚本行")


class FreezoneStoryScriptGenerateResponse(BaseModel):
    ok: Literal[True] = True
    data: FreezoneStoryScriptGenerateData


class FreezoneJobAcceptedData(BaseModel):
    task_type: str
    job_id: str
    task_key: str


class FreezoneJobAcceptedResponse(BaseModel):
    ok: Literal[True] = True
    data: FreezoneJobAcceptedData


class FreezoneStageAssetAcceptedData(BaseModel):
    task_type: str
    job_id: str
    task_key: str
    scope: str
    scene_id: str
    step: str


class FreezoneStageAssetAcceptedResponse(BaseModel):
    ok: Literal[True] = True
    data: FreezoneStageAssetAcceptedData


class CanvasPayload(BaseModel):
    schema_version: Optional[Literal[2]] = None
    canvas_id: Optional[str] = None
    project_id: Optional[str] = None
    canvas_scope: Optional[Literal["default", "episode", "beat", "asset"]] = None
    owner_principal_type: Optional[Literal["user", "team"]] = None
    owner_principal_id: Optional[str] = None
    access_model: Optional[Literal["project_role"]] = None
    min_project_role: Optional[Literal["viewer", "editor", "admin"]] = None
    episode: Optional[int] = None
    beat: Optional[int] = None
    asset_target: Optional[dict] = None
    revision: Optional[int] = None
    base_revision: Optional[int] = None
    created_by: Optional[str] = None
    updated_by: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    client_save_id: Optional[str] = None
    save_source: Literal[
        "autosave",
        "manual_save",
        "manual_clear",
        "restore",
        "from_preset",
        "projection_remove",
        "import",
    ] = "autosave"
    allow_empty_overwrite: bool = False
    nodes: list[dict] = Field(default_factory=list)
    edges: list[dict] = Field(default_factory=list)
    viewport: Optional[dict] = None
    metadata: Optional[dict] = None

    @model_validator(mode="after")
    def _check_payload_size(self) -> "CanvasPayload":
        # Raise HTTPException directly so the response carries a stable,
        # machine-readable code instead of Pydantic's default
        # "List should have at most N items" message (which echoes the
        # entire offending list and is unparseable client-side).
        if len(self.nodes) > CANVAS_MAX_NODES:
            raise HTTPException(
                422,
                {
                    "code": "canvas_payload_too_large",
                    "field": "nodes",
                    "limit": CANVAS_MAX_NODES,
                    "got": len(self.nodes),
                },
            )
        if len(self.edges) > CANVAS_MAX_EDGES:
            raise HTTPException(
                422,
                {
                    "code": "canvas_payload_too_large",
                    "field": "edges",
                    "limit": CANVAS_MAX_EDGES,
                    "got": len(self.edges),
                },
            )
        return self


class PresetCanvasRequest(BaseModel):
    """Stateless factory input for a project-scoped preset canvas."""

    scope: Literal["episode", "beat", "asset", "blank"] = "beat"
    episode: Optional[int] = None
    beat: Optional[int] = None
    primary_slot: str = "render"
    asset_kind: Optional[str] = None
    character: Optional[str] = None
    identity_id: Optional[str] = None
    asset_id: Optional[str] = None
    canvas_id: Optional[str] = None
    overwrite_existing: bool = False
    base_revision: Optional[int] = None


class ProjectionPresetCanvasRequest(BaseModel):
    """Project one preset subgraph into an existing user canvas."""

    scope: Literal["episode", "beat", "asset", "blank"] = "beat"
    projection_key: str = Field(min_length=1, max_length=160)
    episode: Optional[int] = None
    beat: Optional[int] = None
    primary_slot: str = "render"
    asset_kind: Optional[str] = None
    character: Optional[str] = None
    identity_id: Optional[str] = None
    asset_id: Optional[str] = None
    base_revision: int
    force_refresh: bool = False


class ProjectionStatusRequest(BaseModel):
    """Check whether projected preset subgraphs are stale."""

    projection_keys: Optional[list[str]] = None


class ProjectionRemoveRequest(BaseModel):
    """Remove one projected preset subgraph from an existing user canvas."""

    projection_key: str = Field(min_length=1, max_length=160)
    base_revision: int


class PushRequest(BaseModel):
    source_url: str
    target: PushTarget = Field(discriminator="kind")
    mark_stale: bool = False


class ImpactRequest(BaseModel):
    target: PushTarget = Field(discriminator="kind")


class CreateIdentityAssetRequest(BaseModel):
    source_url: str
    character: str
    identity_name: str
    appearance_details: str = ""
    face_prompt: str = ""
    age_group: str = ""


class CharacterUpdate(BaseModel):
    name: Optional[str] = None
    face_prompt: Optional[str] = None
    description: Optional[str] = None
    gender: Optional[str] = None
    age_group: Optional[str] = None
    is_main: Optional[bool] = None
    role: Optional[str] = None  # "主角" / "配角" / "反派"
    body_type: Optional[str] = None  # "纤细高挑" / "健壮魁梧" 等
    fish_voice_id: Optional[str] = None  # Fish Audio S2 声线 ID
    aliases: Optional[list[str]] = None


# ── 场景资产 ─────────────────────────────────────────────────────────────────


class SceneCreate(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    scene_type: str = "interior"
    base_scene_id: str = ""
    variant_id: str = ""
    time_of_day: str = ""
    environment_prompt: str = ""
    variant_prompt: str = ""
    description: str = ""
    spatial_layout_image: str = ""
    notes: str = ""


class SceneUpdate(BaseModel):
    name: Optional[str] = None
    aliases: Optional[list[str]] = None
    scene_type: Optional[str] = None
    base_scene_id: Optional[str] = None
    variant_id: Optional[str] = None
    time_of_day: Optional[str] = None
    environment_prompt: Optional[str] = None
    variant_prompt: Optional[str] = None
    description: Optional[str] = None
    spatial_layout_image: Optional[str] = None
    notes: Optional[str] = None


class ScenePanoGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source: Literal["master", "text"] = "master"


class SceneReferenceGenerateRequest(BaseModel):
    model: Optional[str] = None


# ── 道具资产 ─────────────────────────────────────────────────────────────────


class PropCreate(BaseModel):
    name: str
    aliases: list[str] = Field(default_factory=list)
    prop_type: str = "object"
    visual_prompt: str = ""
    description: str = ""
    owner: str = ""
    notes: str = ""


class PropUpdate(BaseModel):
    name: Optional[str] = None
    aliases: Optional[list[str]] = None
    prop_type: Optional[str] = None
    visual_prompt: Optional[str] = None
    description: Optional[str] = None
    owner: Optional[str] = None
    notes: Optional[str] = None


class PropReferenceGenerateRequest(BaseModel):
    style: Optional[str] = None
    model: Optional[str] = None


# ── 身份 CRUD ────────────────────────────────────────────────────────────────


class IdentityCreate(BaseModel):
    identity_name: str
    age_group: str = ""
    appearance_details: str = ""


class IdentityImageGenRequest(BaseModel):
    style: Optional[str] = None
    model: Optional[str] = None


CharacterAssetKind = Literal["portrait", "identity", "identity_costume", "identity_portrait"]


class CharacterAssetRestoreRequest(BaseModel):
    kind: CharacterAssetKind
    history_id: str
    identity_id: Optional[str] = None


class CharacterImageSelectionRequest(BaseModel):
    character_image_selection: str


class AssetImageSourceSelectionRequest(BaseModel):
    image_source_selection: str


class CharacterVoiceRecordRequest(BaseModel):
    data_url: str


class NarratorVoiceCopyRequest(BaseModel):
    source_path: str


class NarratorVoiceTrimRequest(BaseModel):
    start_seconds: float = 0.0
    duration_seconds: float = 4.0


class CharacterVoiceTrimRequest(BaseModel):
    source_path: str
    start_seconds: float = 0.0
    duration_seconds: float = 4.0


class IdentityUpdate(BaseModel):
    identity_name: Optional[str] = None
    appearance_details: Optional[str] = None
    face_prompt: Optional[str] = None
    age_group: Optional[str] = None
    body_type: Optional[str] = None
    fish_voice_id: Optional[str] = None


# ── 脚本保存 ─────────────────────────────────────────────────────────────────


class ScriptSaveRequest(BaseModel):
    beats: list[dict]


# ── 草图切割 ─────────────────────────────────────────────────────────────────


class GridCutRequest(BaseModel):
    grid_type: Literal["render", "sketch"] = "sketch"
    mode_key: str | None = None
    rows: int
    cols: int
    beat_start: int
    beat_end: int
    beat_numbers: list[int] | None = None


class GridSketchPreviewRequest(BaseModel):
    rows: int = Field(..., ge=1)
    cols: int = Field(..., ge=1)
    beat_numbers: list[int] = Field(..., min_length=1)


# ── 渲染计划 ─────────────────────────────────────────────────────────────────


class RenderPlanRequest(BaseModel):
    beat_indices: list[int] = Field(..., min_length=1)
    strategy: Literal["location", "naive"] = "naive"
    force_one_by_one: bool = False
    aspect_mode: str = Field(..., description="e.g. '9:16', '1:1', '16:9'")
    image_generation_selection: Optional[str] = None
    sketch_aspect_padding: Optional[bool] = None


class PlanEntryOut(BaseModel):
    mode_key: str
    rows: int
    cols: int
    beat_numbers: list[int]
    location: str = ""
    padding_count: int = 0
    reasons: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class RenderPlanResponse(BaseModel):
    plan: list[PlanEntryOut]
    plan_hash: str
    input_fingerprint: str
    strategy: str
    total_beats: int
    total_grids: int


class RenderPlanExecuteRequest(BaseModel):
    plan: list[PlanEntryOut]
    plan_hash: str
    input_fingerprint: str
    strategy: Literal["location", "naive"]
    aspect_mode: str
    force_one_by_one: bool = False
    custom_plan: bool = False
    beat_indices: list[int] = Field(..., min_length=1)
    image_generation_selection: Optional[str] = None
    sketch_aspect_padding: Optional[bool] = None


class RenderPlanExecuteResponse(BaseModel):
    task_type: str
    message: str
    scope: str
    resolved_grids: list[PlanEntryOut]


# ── 风格预览 ─────────────────────────────────────────────────────────────────


class StylePreviewRequest(BaseModel):
    project: Optional[str] = None
    prompt: str = "A beautiful woman standing in a garden"
    model: str = ""  # [aipdd fork delta] 空值=走配置的默认图片模型（原写死 nanobanana）
