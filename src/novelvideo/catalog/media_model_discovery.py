"""媒体模型发现层：网关能力声明 + 模型名分类口径（单一真源）。

[aipdd fork delta] 画布媒体目录的契约是「配置驱动」（见 freezone
``_custom_gateway_media_catalog``）：目录只回放用户在设置页显式配置且启用的模型，
网关实拉清单不再参与目录构建 —— 因此旧版「按网关清单收敛内置目录 / 自动补条目」
的发现逻辑已整体删除。本模块只保留两块仍被生产消费的职责：

1. **网关能力声明**（vres「真·跟随网关声明」）：从 OpenAI 兼容渠道的
   ``/v1/models`` 逐模型拉 ``aipdd.capabilities.media_metadata``（分辨率 / 比例 /
   时长 / 参考输入上限），归一成目录档位；拉不到时静默退回通用默认。优先级：
   用户显式配置 > 网关声明 > 通用默认。
2. **模型名分类口径**：``guess_model_kind``（八分类，与设置页「默认生成模型」
   七个 TAB 的 ``guessMediaType`` 完全同口径，含「视觉理解」分支）、
   ``guess_media_type``（媒体目录逻辑用的关键词归类）、``is_vision_capable``
   （视觉候选资格，含 doubao-seed 多模态系列），以及 ``is_vision_candidate_name``
   （**「视觉理解」下拉的候选资格** = ``is_vision_capable`` 再额外排除向量
   embedding 模型，见该函数注释）。

分类关键词是后端与前端各持一份的启发式：修改时必须同步钉住两侧的同一组用例
（后端 tests/test_media_model_discovery_catalog.py、前端
model-classification.test.ts），任一侧漂移都会让用例失败。
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

logger = logging.getLogger("novelvideo.media_model_discovery")

# [aipdd fork delta] vres「真·跟随网关声明」：网关（如 AIPDD）在 ``/v1/models``
# 的每个模型条目上带 ``aipdd.capabilities.media_metadata`` —— 这是分辨率 / 比例 /
# 时长的权威声明。能力元数据基本不变，缓存给到 5 分钟；单次探测套超时、失败静默
# 回退到通用默认，绝不拖住画布开目录。超时下界取 8s：AIPDD /v1/models 冷启动 /
# 偶发慢响应在 3s 内常被判超时→回落成错误的 720p，放宽后成功率明显提升。
GATEWAY_CAPS_TTL_SECONDS = 300.0
GATEWAY_CAPS_TIMEOUT_SECONDS = 8.0
# 拉失败 / 拿到空结果时用短 TTL，避免一次超时的空声明把通用默认锁死 5 分钟（画布
# 会连续几次开在错误的 720p 上）；到点就重试，网络恢复后自动跟上网关真实声明。
GATEWAY_CAPS_FAILURE_TTL_SECONDS = 15.0

# 与设置页 MEDIA_TYPE_PATTERNS 同步：顺序即优先级（embedding → music → audio →
# video → image → text），改一边必须改另一边。
MEDIA_TYPE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "embedding",
        re.compile(
            r"(embedding|embed|bge[-_]|m3e|gte[-_]?embedding|jina[-_]?embed|voyage"
            r"|text[-_]embedding|oe[-_]embedding)",
            re.IGNORECASE,
        ),
    ),
    (
        # [aipdd fork delta req46] 「音乐」独立成类：Music 3.0 的 music-3.0 /
        # music-cover 与 suno 系列过去被 audio 的 ``suno|\bmusic\b`` 收进语音族，
        # 设置页与候选目录里和 TTS 混在一个 TAB —— 而音乐走的是完全不同的协议
        # （直连 music_generation，不经网关 audio/speech），混类会让「音频默认模型」
        # 指向一个根本没法配音的模型。必须排在 audio **之前**：audio 的 ``\baudio\b``
        # 会抢走带 audio 字样的音乐名。
        # [aipdd fork delta req48] 补 lyria：Gemini 清单里的 Lyria / lyria-realtime-*
        # 是真正的音乐模型，过去只被 audio 的 ``\bvoice\b``/text 抢走或落 other。
        # 归类只管展示与候选分组；能不能调另看渠道能力位与调用形态（网关里没有
        # music 路由，所以 gemini 不挂 music 能力位，这里不把它当「能调」的依据）。
        "music",
        re.compile(
            r"(\bmusic|\bsuno\b|\blyrics?\b|\blyria\b|audiocraft)",
            re.IGNORECASE,
        ),
    ),
    (
        # [aipdd fork delta req48] 补 whisper / transcri：OpenAI 官方清单里的
        # whisper-1 / whisper-1-transcribe 是转写（语音→文本），过去一个都不命中
        # audio（名字里没有 tts/speech/voice/audio），只能落 other——而目录里还有
        # audio/transcriptions 端点声明，候选面却看不到它。
        "audio",
        re.compile(
            r"(tts|indextts|cosyvoice|fireredasr|fish[-_]?(speech)?"
            r"|\baudio\b|\bspeech\b|\bvoice\b|elevenlabs|\bbark\b|doubao[-_]audio"
            r"|whisper|transcri)",
            re.IGNORECASE,
        ),
    ),
    (
        "video",
        re.compile(
            r"(seedance|sora|kling|vidu|pixverse|hailuo|cogvideo|animereels|mochi"
            r"|runway|\bpika\b|hunyuan[-_]?video|\bveo\d?\b|\bltx\b|\bh3\b"
            r"|wan[-_]?(i2v|t2v|2)|flf2v|image[-_]to[-_]video|text[-_]to[-_]video"
            r"|\bi2v\b|\bt2v\b|\br2v\b|\bvideo\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "image",
        re.compile(
            r"(image|seedream|jimeng|dall[-_]?e|\bimagen\b|flux|sdxl|\bsd[-_]?3?\b"
            r"|stable[-_]diffusion|midjourney|nano[-_]?banana|firefly|recraft"
            r"|qwen[-_]?(image|vl)|grok[-_]?image|text[-_]to[-_]image|\bt2i\b|\bi2i\b)",
            re.IGNORECASE,
        ),
    ),
    (
        "text",
        re.compile(
            r"(gpt|chatgpt|claude|gemini|qwen|deepseek|llama|hermes|\bllm\b|mistral"
            r"|\bglm\b|minimax[-_ ]?m\d|moonshot|kimi|\bdoubao\b|baichuan|internlm"
            r"|ernie|wenxin|text[-_]generation|\bchat\b|completion|\bphi[-_\d]"
            r"|command[-_]?r|senova|\byi[-_]?\d)",
            re.IGNORECASE,
        ),
    ),
)

# 自动纳入的图片模型预置档位。注意：这只是**默认值**，设置页「媒体模型」可以逐条
# 改；运行时的画质 / 分辨率 / 比例是否下发只看目录里的声明，不再看模型名。
DEFAULT_IMAGE_RESOLUTION_OPTIONS: tuple[str, ...] = ("1K", "2K", "4K")
DEFAULT_IMAGE_RATIO_OPTIONS: tuple[str, ...] = (
    "auto",
    "1:1",
    "16:9",
    "9:16",
    "4:3",
    "3:4",
    "3:2",
    "2:3",
    "21:9",
)
DEFAULT_IMAGE_QUALITY_OPTIONS: tuple[str, ...] = ("low", "medium", "high")
# 只有名字落在 gpt-image 这一族里才预置画质档位：它是上游确实接受 quality
# 参数的家族。给不接受 quality 的模型预置画质，等于让网关报错，所以宁可不给
# —— 用户想要就在设置页勾上。发行方私有别名不进这条启发式：曾经带过一族私有
# 名，绑着只会撞回网关的 503。
_QUALITY_CAPABLE_IMAGE = re.compile(r"(gpt[-_]?image)", re.IGNORECASE)

# 自动纳入的视频模型预置档位。同样只是**默认值**，设置页可逐条改；运行时是否下发
# 只看目录声明。分辨率用与后端写死表一致的三档，比例覆盖常见画幅，时长取通用区间。
DEFAULT_VIDEO_RESOLUTION_OPTIONS: tuple[str, ...] = ("480p", "720p", "1080p")
DEFAULT_VIDEO_RATIO_OPTIONS: tuple[str, ...] = (
    "16:9",
    "9:16",
    "1:1",
    "4:3",
    "3:4",
    "21:9",
)
DEFAULT_VIDEO_MIN_DURATION = 5
DEFAULT_VIDEO_MAX_DURATION = 15

# [aipdd fork delta] 带 LLM 标记的名字一定是语言模型：DC-seedance2-prompt-composer-LLM /
# DC-video-identity-detector-LLM / DC-image-caption-LLM 会因为名字里含 seedance/video/image
# 被下面的 video/image 关键词抢走，出现在画布视频 / 图片下拉里 —— 选中必失败。
_TEXT_ONLY_NAME = re.compile(r"(llm|language[-_]model)", re.IGNORECASE)

# [aipdd fork delta req48] 非生成类工具模型（OpenAI 官方清单里的 omni-moderation-latest
# 等内容审核接口）：名字里的 omni 会命中下面的视觉资格正则而进了「视觉理解」候选，
# 而它根本不接图。这类名字一律落 other，不进任何 TAB（与设置页
# model-classification.UTILITY_ONLY_RE 同步）。
_UTILITY_ONLY_NAME = re.compile(r"moderation", re.IGNORECASE)

# [aipdd fork delta] 「视觉理解」口径（单一真源）：与设置页「默认生成模型」面板的
# guessMediaType 视觉分支完全一致 —— vision / vlm / qvq / video-understanding /
# multimodal / omni / *-vl 等名字先归「视觉理解」，再轮到 LLM 标记与媒体关键词。
# doubao-seed 系列是多模态输入（文本输出）模型：主分类仍按 text（文本 TAB 合法），
# 视觉候选资格由 is_vision_capable 的附加规则给出，供「视觉理解」TAB 与别名能力位
# 校验（newapi_provisioner.model_supports_vision）使用。
_VISION_MODEL_NAME = re.compile(
    r"(vision|\bvlm\b|\bqvq\b|video[-_]?understanding|multi[-_]?modal|\bomni\b"
    r"|[-_]vl[-_\d]|[-_]vl\b)",
    re.IGNORECASE,
)
_VISION_CAPABLE_NAME = re.compile(
    r"vision|\bvlm\b|\bqvq\b|video[-_]?understanding|multi[-_]?modal|\bomni\b"
    r"|[-_]vl[-_\d]|[-_]vl\b|doubao[-_.]?seed(?![a-zA-Z])",
    re.IGNORECASE,
)

_gateway_caps_cache: tuple[float, dict[str, dict]] | None = None
_gateway_caps_lock: asyncio.Lock | None = None
# [aipdd fork delta] stale-while-revalidate 的下次重试时刻（monotonic）：
# 一旦拉到过非空能力就长期可复用旧值，但重试按此时间节流，不每请求猛打网关。
_gateway_caps_next_attempt: float = 0.0


def guess_media_type(name: str) -> str:
    """按模型名关键词归类（媒体目录逻辑用）；认不出返回 ``other``。"""

    text = str(name or "")
    if _UTILITY_ONLY_NAME.search(text):
        return "other"
    if _TEXT_ONLY_NAME.search(text):
        return "text"
    for media_type, pattern in MEDIA_TYPE_PATTERNS:
        if pattern.search(text):
            return media_type
    return "other"


def is_vision_capable(name: str) -> bool:
    """视觉候选资格（= 设置页 ``VISION_CAPABLE_RE``）：含 doubao-seed 多模态系列。

    [aipdd fork delta req48] 先过工具模型护栏：omni-moderation-* 这类内容审核接口
    名字里带 omni，不算视觉资格。
    """

    text = str(name or "")
    if _UTILITY_ONLY_NAME.search(text):
        return False
    return bool(_VISION_CAPABLE_NAME.search(text))


def guess_model_kind(name: str) -> str:
    """八分类口径（与设置页 ``guessMediaType`` 完全同口径，单一真源）。

    顺序即优先级：视觉理解 → LLM 标记（text）→ 媒体关键词 → other。
    设置页七个 TAB 的展示分类与模型广场的能力位都依赖本函数；修改关键词表时
    必须同步钉住两侧的同一组用例（后端
    tests/test_media_model_discovery_catalog.py、前端 model-classification.test.ts）。
    """

    text = str(name or "")
    if _UTILITY_ONLY_NAME.search(text):
        return "other"
    if _VISION_MODEL_NAME.search(text):
        return "vision"
    if _TEXT_ONLY_NAME.search(text):
        return "text"
    for media_type, pattern in MEDIA_TYPE_PATTERNS:
        if pattern.search(text):
            return media_type
    return "other"


def is_vision_candidate_name(name: str, capability: str = "") -> bool:
    """「视觉理解」候选资格（单一真源）：视觉能力位命中，且**不是向量模型**。

    两个口径上的修正（都是容器实测钉死的）：

    1. 不能直接用 ``guess_model_kind(name) == "vision"``：那边的顺序是
       「视觉 → LLM → 媒体关键词」，而方舟清单里的 ``doubao-embedding-vision-*`` 是
       **多模态 embedding**（供检索用，不是 chat 视觉理解模型），名字里的 ``vision``
       让它先命中视觉分支、绕过了 embedding 判定。把它当「视觉理解」候选摆进下拉，
       点下去必然报错 —— 实测：视觉下拉收敛后剩下的 2 项全是它俩。
       embedding 判定只用既有的 ``guess_media_type``（那边 embedding 排第 1 优先），
       不另造正则，避免第二套口径。
    2. 资格面取 ``is_vision_capable``（= 设置页「视觉理解」TAB 的口径）而不是
       ``guess_model_kind``：前者是后者的**严格超集**，多出的主要是 doubao-seed
       多模态系列。只按「名字里带 vision/vl」筛，方舟 135 个名字只剩 11 个，而它们
       全部未开通 —— 排掉 embedding 后下拉直接空掉（实测：12 → 0）；而 seed 系列是
       真的能接图输入且已开通（文本实测 ok）。资格面只负责「不拒绝真视觉模型」，
       「这个名字到底能不能读图」交给带图实测（text_model_probe kind=vision）判定。

    ``capability`` 是渠道/别名上的显式声明，优先于按名字猜；显式声明为 ``vision`` 时
    同样要求不是 embedding —— 声明也不会把向量模型变成理解模型。

    改动本函数必须同步 tests/test_media_model_discovery_catalog.py 的用例。
    """

    clean = str(name or "").strip()
    if not clean:
        return False
    if guess_media_type(clean) == "embedding":
        return False
    declared = str(capability or "").strip().lower()
    if declared:
        return declared == "vision"
    return is_vision_capable(clean)


def default_image_capabilities(name: str) -> dict[str, Any]:
    """自动纳入的图片模型的默认档位（可在设置页覆盖）。"""

    capabilities: dict[str, Any] = {
        "resolutionOptions": list(DEFAULT_IMAGE_RESOLUTION_OPTIONS),
        "ratioOptions": list(DEFAULT_IMAGE_RATIO_OPTIONS),
        "request": {"endpoint": "images/generations", "parameters": []},
    }
    if _QUALITY_CAPABLE_IMAGE.search(str(name or "")):
        capabilities["qualityOptions"] = list(DEFAULT_IMAGE_QUALITY_OPTIONS)
    return capabilities


def default_video_capabilities(
    name: str,
    gateway_caps: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """自动纳入的视频模型的默认档位（可在设置页覆盖）。

    不预置 supportedModes：模式能力靠名字猜不出来，猜错会让上游直接拒单；
    宁可由用户在设置页显式声明。

    [aipdd fork delta] ``gateway_caps`` 是发现层从网关 ``/v1/models`` 拉到的该模型
    权威能力声明（resolutionOptions / ratioOptions / minDuration / maxDuration）。
    传进来时逐字段跟随网关声明，只有网关没声明的字段才退回通用默认 —— 这就是
    「真·跟随网关声明」：比如 ap-minimax-h3 只有 768p 没有 720p，就不会再被套上
    错误的 720p。用户已显式配置的条目走不到这里（调用方只对空档位调用本函数），
    因此优先级仍是「用户配置 > 网关声明 > 通用默认」。
    """

    caps = gateway_caps or {}
    resolutions = [
        str(item or "").strip()
        for item in (caps.get("resolutionOptions") or [])
        if str(item or "").strip()
    ] or list(DEFAULT_VIDEO_RESOLUTION_OPTIONS)
    ratios = [
        str(item or "").strip()
        for item in (caps.get("ratioOptions") or [])
        if str(item or "").strip()
    ] or list(DEFAULT_VIDEO_RATIO_OPTIONS)
    min_duration = _positive_int(caps.get("minDuration")) or DEFAULT_VIDEO_MIN_DURATION
    max_duration = _positive_int(caps.get("maxDuration")) or DEFAULT_VIDEO_MAX_DURATION

    capabilities: dict[str, Any] = {
        "resolutionOptions": resolutions,
        "ratioOptions": ratios,
        "minDuration": min_duration,
        "maxDuration": max_duration,
        "request": {"endpoint": "video/generations", "parameters": []},
    }
    # [aipdd fork delta] 网关权威声明的参考输入上限（多参数 / 全能参考的容量）：
    # 图 / 音频只透传 >0 的声明，没声明就不写这个键（留给 omni 端点按各模式默认值兜底）；
    # 视频连明说的 0 一起透下去（见 :func:`_declared_video_ref_cap`）。
    # 仍刻意不预置 supportedModes —— 引用上限是数量、不是模式猜测，两者不冲突。
    for key in ("referenceImageMax", "referenceAudioMax"):
        value = _positive_int(caps.get(key))
        if value is not None:
            capabilities[key] = value
    video_value = _declared_video_ref_cap(caps.get("referenceVideoMax"))
    if video_value is not None:
        capabilities["referenceVideoMax"] = video_value
    return capabilities


def _fill_missing_option(entry: dict[str, Any], key: str, defaults: dict[str, Any]) -> None:
    """只在没声明（缺失 / 空列表 / 全空字符串）时补默认档位，不覆盖已声明的。"""

    declared = entry.get(key)
    if isinstance(declared, list) and any(
        str(item or "").strip() for item in declared
    ):
        return
    if key in defaults:
        entry[key] = list(defaults[key])


def _positive_int(value: Any) -> int | None:
    return value if type(value) is int and value > 0 else None


def _declared_video_ref_cap(value: Any) -> int | None:
    """参考视频上限：**非负数都算声明**，包含渠道明说的 0。

    [aipdd fork delta req46] 与图 / 音频不同，视频这一类要区分「没写」与「明说 0」：
    2026-10-08 实拉 AIPDD ``ap-minimax-h3`` 写的是 ``maxVideoInputs: 0`` +
    ``supportsReferenceVideo: false``，带外探针递 ``video_urls`` 收到的是「当前 MiniMax H3
    渠道不支持视频参考；本次请求未扣费」。把 0 归成「没说」就等于把一个已确定的不支持
    推成「未申报」：前端照常让人挂参考视频，提交时才撞上拒单。图 / 音频仍只收 >0：
    它们的顶层 0 在多渠道里会按档位变化（H3 的 ``text`` 档位图槽就是 0，整模却支持 9 图），
    拿它当硬边界会误关图生视频。
    """

    return value if type(value) is int and value >= 0 else None


def _entry_gateway_names(entry: dict[str, Any]) -> set[str]:
    """一个目录条目可能对应到网关名的几种键位（含 ``newapi_`` 前缀 / 上游名）。"""

    names: set[str] = set()
    for key in (
        "gatewayModel",
        "gateway_model",
        "catalogId",
        "catalog_id",
        "id",
        "apiModel",
        "api_model",
        "upstreamModel",
        "upstream_model",
    ):
        raw = str(entry.get(key) or "").strip()
        if not raw:
            continue
        names.add(raw)
        if raw.startswith("newapi_"):
            names.add(raw[len("newapi_") :])
    return {n for n in names if n}


def _pick_gateway_caps(
    entry: dict[str, Any],
    caps_map: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """按条目名命中网关声明的能力（先精确、再忽略大小写）。命中不到返回 None。"""

    if not caps_map:
        return None
    names = _entry_gateway_names(entry)
    for name in names:
        hit = caps_map.get(name)
        if hit:
            return hit
    folded = {str(k).strip().casefold(): v for k, v in caps_map.items()}
    for name in names:
        hit = folded.get(name.casefold())
        if hit:
            return hit
    return None


def _official_video_supported_modes_by_name() -> dict[str, list[str]]:
    """官方目录逐视频模型声明的 supportedModes（模型名 -> 声明清单）。

    [aipdd fork delta] custom 模式目录只装配用户真源，真源条目常常没写
    supportedModes（例如推荐配置写入的 seedance 系列），前端按声明做的能力
    过滤（视频去水印的 Seedance 组、画布节点页签）会把它们整体剔掉。官方目录
    是本产品的权威能力声明（2.0 系列的编辑能力对齐上游网关 task_type
    VideoEditing 声明），这里取出来供 apply_default_video_capabilities 做
    「只补空」兜底。
    """

    from novelvideo.model_gateway_settings import get_official_media_model_mappings

    declared: dict[str, list[str]] = {}
    try:
        mappings = get_official_media_model_mappings()
    except Exception:  # noqa: BLE001 - 声明缺失不阻碍目录装配
        logger.warning("failed to read official media model mappings", exc_info=True)
        return declared
    for name, item in mappings.items():
        if not isinstance(item, dict) or item.get("mediaType") != "video":
            continue
        config = item.get("config") if isinstance(item.get("config"), dict) else {}
        modes = config.get("supportedModes")
        if not isinstance(modes, list):
            continue
        cleaned = [str(mode).strip() for mode in modes if str(mode or "").strip()]
        if cleaned:
            declared[str(name).strip()] = cleaned
    return declared


def apply_default_video_capabilities(
    entries: list[dict[str, Any]],
    gateway_capabilities: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """给没声明档位的视频条目补上默认档位（只补空，不覆盖已声明的）。

    [aipdd fork delta] vres「真·跟随网关声明」：用户在设置页建了映射、却没填
    分辨率档位的条目（如 ap-minimax-h3）过去拿不到网关声明，最终落到通用默认
    480p/720p/1080p → 出现错误的 720p。这里把网关声明作为「补空」阶段的优先
    来源：空档位先按网关声明填，网关也没声明才退通用默认。优先级仍是
    「用户显式声明（非空）> 网关声明 > 通用默认」（用户声明靠
    ``_fill_missing_option`` 只补空不覆盖保住）。

    [aipdd fork delta] 同一入口也给未声明 supportedModes 的视频条目补空：按条目名
    从官方目录的权威声明回填（只补空、不覆盖用户声明、匹配不到的不猜）——
    前端去水印的 Seedance 组等按声明做的过滤依赖这份声明。
    """

    caps_map = gateway_capabilities or {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        defaults = default_video_capabilities(
            str(entry.get("apiModel") or entry.get("api_model") or entry.get("catalogId") or ""),
            gateway_caps=_pick_gateway_caps(entry, caps_map),
        )
        for key in ("resolutionOptions", "ratioOptions"):
            _fill_missing_option(entry, key, defaults)
        for key in ("minDuration", "maxDuration"):
            if _positive_int(entry.get(key)) is None:
                entry[key] = defaults[key]
        # [aipdd fork delta] 参考输入上限同样「只补空不覆盖」：用户在设置页显式配过的
        # 优先，没配且网关声明了的才按网关声明填进条目（如 ap-minimax-h3 的
        # referenceImageMax=9 / referenceAudioMax=3），让画布与 omni 端点都能识别全能参考容量。
        for key in ("referenceImageMax", "referenceAudioMax", "referenceVideoMax"):
            reader = _declared_video_ref_cap if key == "referenceVideoMax" else _positive_int
            if reader(entry.get(key)) is None and reader(defaults.get(key)) is not None:
                entry[key] = defaults[key]
        if not isinstance(entry.get("request"), dict):
            entry["request"] = {"endpoint": "video/generations", "parameters": []}
    # [aipdd fork delta] supportedModes 同步「只补空」：真源条目（用户配置的映射）
    # 常没声明能力模式，前端按声明做的过滤（视频去水印 Seedance 组、画布节点页签、
    # omni 过滤）会把模型整体剔出候选。这里按条目名从官方目录的权威声明补一份——
    # 已声明的（含用户手填）一律不覆盖，匹配不到的不猜（前端对无声明条目仍走按名
    # 启发式，后端门禁对空声明交网关裁决）。
    declared_modes = _official_video_supported_modes_by_name()
    folded_modes = {name.casefold(): modes for name, modes in declared_modes.items()}
    for entry in entries:
        if not isinstance(entry, dict) or entry.get("supportedModes"):
            continue
        for name in _entry_gateway_names(entry):
            modes = declared_modes.get(name) or folded_modes.get(name.casefold())
            if modes:
                entry["supportedModes"] = list(modes)
                break
    return entries


def apply_video_binding_mode_evidence(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把渠道在线绑定的模式证据以**咨询字段** ``bindingModes`` 挂到视频条目上。

    [aipdd fork delta req44] 为什么不直接写进 ``supportedModes``：那是把双刃声明 ——
    后端 ``_require_catalog_video_mode`` 一见到非空就严校验，前端多处分组过滤也按声明
    覆盖启发式。把「本次读到的在线绑定并集」写进去，等于拿渠道接线的完备程度当用户的
    能力边界（H3 的绑定入参里没声明参考视频，写进去反而会关掉它的图片参考 / 视频编辑
    页签）。``bindingModes`` 只做一件事：顶掉前端那句 ``isSeedance2VideoModel`` 的按名
    猜测，把渠道确实备了对口绑定的入口**放开**；它永远不参与收紧。

    关联不上就不写字段（渠道不暴露能力接口 / 快照还没落 / 模型由第三方代理）：前端保持
    原有判定，后端门禁也不受影响。读不到事实就是「不表态」，与 req43 同一口径。
    """

    from novelvideo.model_gateway_settings import (
        get_provider_capability_bindings,
        model_owner_providers,
        split_provider_spot_model,
    )
    from novelvideo.catalog.upstream_capabilities import video_binding_modes_for_model

    bindings_by_provider: dict[str, list[dict[str, Any]]] = {}

    def _bindings(provider_key: str) -> list[dict[str, Any]]:
        if provider_key not in bindings_by_provider:
            try:
                bindings_by_provider[provider_key] = get_provider_capability_bindings(
                    provider_key
                )
            except Exception:  # noqa: BLE001 - 事实读不到只是没证据，不拖住开目录
                logger.warning(
                    "[aipdd fork delta req44] capability bindings unreadable for %s",
                    provider_key,
                    exc_info=True,
                )
                bindings_by_provider[provider_key] = []
        return bindings_by_provider[provider_key]

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        modes: set[str] = set()
        for name in _entry_gateway_names(entry):
            provider, bare_model = split_provider_spot_model(str(name or ""))
            bare_model = str(bare_model or "").strip()
            if not bare_model:
                continue
            providers = [provider] if provider else list(model_owner_providers(bare_model))
            for item in providers:
                provider_key = str(item or "").strip().lower()
                if not provider_key:
                    continue
                modes.update(video_binding_modes_for_model(_bindings(provider_key), bare_model))
        if modes:
            entry["bindingModes"] = sorted(modes)
    return entries


def apply_default_image_capabilities(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """给没声明档位能力的图片条目补上默认档位（只补空，不覆盖已声明的）。

    [aipdd fork delta] 画布上的分辨率 / 比例 / 画质下拉现在严格跟随模型自己的
    声明（前端不再套通用兜底档位），所以目录这一侧必须保证：未声明的模型拿到一份
    可用默认值，而不是在画布上变成一个没有参数可调的模型。用户在设置页显式填过的
    档位优先，这里只补空。
    """

    for entry in entries:
        if not isinstance(entry, dict):
            continue
        defaults = default_image_capabilities(
            str(entry.get("apiModel") or entry.get("api_model") or entry.get("catalogId") or "")
        )
        for key in ("resolutionOptions", "ratioOptions", "qualityOptions"):
            _fill_missing_option(entry, key, defaults)
        if not isinstance(entry.get("request"), dict):
            entry["request"] = {"endpoint": "images/generations", "parameters": []}
    return entries


# [aipdd fork delta] 旧版 ``filter_entries_to_gateway_models`` / ``build_missing_entries``
# （把内置目录按网关实拉名单收敛 + 把网关有而目录没有的模型自动补成条目）已随
# 「画布目录 = 配置驱动」契约（见 freezone ``_custom_gateway_media_catalog``）整体
# 删除：未配置的模型绝不进入画布候选项，渠道新增模型由设置页拉取后由用户显式配置。


# [aipdd fork delta] 旧版 ``gateway_models_by_name`` / ``_collect_gateway_models_sync``
# （带 TTL 与墙钟预算实拉渠道 model_mapping / models 清单，供目录收敛与补条目消费）
# 随同一契约删除；渠道新模型的可见性由设置页拉取（list_upstream_models）与用户
# 显式配置承担。


def reset_gateway_caps_cache() -> None:
    """清能力声明缓存（设置页改完渠道后调用，以及测试隔离）。"""

    global _gateway_caps_cache, _gateway_caps_next_attempt

    _gateway_caps_cache = None
    _gateway_caps_next_attempt = 0.0


# ---------------------------------------------------------------------------
# [aipdd fork delta] vres「真·跟随网关声明」：从 OpenAI 兼容渠道的 /v1/models
# 拉逐模型能力元数据。
#
# NewAPI 与 AIPDD 是两个独立渠道：NewAPI 渠道字段（models / model_mapping /
# setting / param_override）不含任何逐模型能力声明；而 AIPDD 这类网关会在自己的
# ``/v1/models``（鉴权只认 Bearer）回每个模型的 ``aipdd.capabilities.media_metadata``
# —— 分辨率 / 比例 / 时长的权威来源。这里把这份声明接进发现层，让画布视频
# 下拉严格跟随网关声明，不再一律 480p/720p/1080p。
# ---------------------------------------------------------------------------


def _extract_gateway_media_capabilities(entry: dict[str, Any]) -> dict[str, Any] | None:
    """从单个 /v1/models 条目里拽出媒体能力，归一成目录档位。

    容错：兼容 ``{model}.aipdd.capabilities.media_metadata`` 与直接的
    ``{model}.capabilities.media_metadata`` / ``{model}.capabilities`` 几种位置。
    只返回确实声明了的字段，没声明的留给 default_video_capabilities 退回通用默认。
    """

    # 延迟导入避开循环（upstream_catalog 的分类口径反向依赖本模块）。
    from novelvideo.catalog.upstream_catalog import declared_media_count

    containers: list[dict[str, Any]] = []

    def _walk(node: Any) -> None:
        if isinstance(node, dict):
            containers.append(node)
            for nested_key in ("capabilities", "media_metadata"):
                _walk(node.get(nested_key))

    _walk(entry.get("aipdd"))
    _walk(entry.get("capabilities"))
    caps: dict[str, Any] = {}
    for container in containers:
        if "resolutionOptions" not in caps:
            resolutions = container.get("resolutions") or container.get("resolutionOptions")
            if isinstance(resolutions, list):
                cleaned = [
                    str(item or "").strip()
                    for item in resolutions
                    if str(item or "").strip()
                ]
                if cleaned:
                    caps["resolutionOptions"] = cleaned
        # [aipdd fork delta] 图像模型的档位声明键位是 supportedSizes（档位词
        # 1K/2K/4K 或像素清单），与视频的 resolutions 不同；同一份 media_metadata
        # 里两者互不冲突，一起归一到 resolutionOptions 供协议适配消费。
        if "resolutionOptions" not in caps:
            sizes = container.get("supportedSizes")
            if isinstance(sizes, list):
                cleaned_sizes = [
                    str(item or "").strip()
                    for item in sizes
                    if str(item or "").strip()
                ]
                if cleaned_sizes:
                    caps["resolutionOptions"] = cleaned_sizes
        if "ratioOptions" not in caps:
            ratios = (
                container.get("ratios")
                or container.get("aspect_ratios")
                or container.get("ratioOptions")
                # [aipdd fork delta] 图像模型的比例声明键位是 supportedAspectRatios。
                or container.get("supportedAspectRatios")
            )
            if isinstance(ratios, list):
                cleaned_ratios = [
                    str(item or "").strip() for item in ratios if str(item or "").strip()
                ]
                if cleaned_ratios:
                    caps["ratioOptions"] = cleaned_ratios
        if "minDuration" not in caps:
            min_duration = container.get("minDurationSeconds")
            if _positive_int(min_duration) is not None:
                caps["minDuration"] = min_duration
        if "maxDuration" not in caps:
            max_duration = container.get("maxDurationSeconds")
            if _positive_int(max_duration) is not None:
                caps["maxDuration"] = max_duration
        # [aipdd fork delta] 网关对每个视频模型权威声明了「能吃什么参考输入」：
        # AIPDD 的 media_metadata 用 maxImageInputs / maxAudioInputs / maxVideoInputs
        # （另配 supportsReferenceAudio / supportsReferenceVideo 两个布尔）。这些是网关
        # 明确写出的**数量**、不是对模式的猜测，所以要接进目录 —— 否则 ap-minimax-h3 这类
        # 本就支持多参数参考的模型在画布上拿不到全能参考、加音频直接被拦（提交禁用 /
        # omni 端点 400）。数量与「没说」是两回事：三个键位都走 ``declared_media_count``
        # （别名表见 ``upstream_catalog._MEDIA_COUNT_ALIASES``），读到的 0 是「明说没有这类
        # 输入」，读不到才是「没说话」。图 / 音频仍只把 >0 写进目录、视频连 0 一起透，
        # 差别与理由见 :func:`_declared_video_ref_cap`。
        if "referenceImageMax" not in caps:
            # [aipdd fork delta req44] 走 ``declared_media_count`` 而不是直拿一个键：同一渠道里
            # 图槽上限有 ``maxImageInputs``（H3）与 ``maxInputImages``（LTX 2.5）两种写法，
            # 只认一种会把 LTX 申报的「就一个图槽」读成未申报，首尾帧两张图因此能提交
            # 出去并在上游被默默截掉。
            max_images = _positive_int(declared_media_count(container, "maxImageInputs"))
            if max_images is not None:
                caps["referenceImageMax"] = max_images
        if "referenceAudioMax" not in caps:
            max_audio = _positive_int(declared_media_count(container, "maxAudioInputs"))
            if max_audio is not None:
                caps["referenceAudioMax"] = max_audio
        if "referenceVideoMax" not in caps:
            # [aipdd fork delta req46] 视频这一类连 0 一起收：渠道明说不支持要能被下游读成
            # 「不支持」，不是「没说话」。
            #
            # 但**只认显式申报的数量**，绝不拿 ``supportsReferenceVideo`` 这个布尔归零：
            # 本仓已把这个布尔定性为会骗人（见 ``freezone/catalog.py`` 的
            # ``_require_omni_reference_inputs_supported`` 里【已更正的旧口径】那条），而
            # ``referenceVideoMax`` 下游不只门控参考视频 —— ``video_gen.py`` 的 edit /
            # extend 两条路径用它门控**源视频**槽，一个针对参考视频的假 false 会把还能用
            # 的编辑入口一并打死。布尔不参与归零，要关这一类得由渠道把数量写成 0。
            max_video = _declared_video_ref_cap(declared_media_count(container, "maxVideoInputs"))
            if max_video is not None:
                caps["referenceVideoMax"] = max_video
    return caps or None


def _gateway_pricing_url(base_url: str) -> str:
    """版本化网关根（…/v1）对应的 /api/pricing 地址；非该形态返回空串。"""

    base = str(base_url or "").strip().rstrip("/")
    if base.endswith("/v1"):
        return f"{base[:-3].rstrip('/')}/api/pricing"
    return ""


def _extract_pricing_capabilities(payload: Any) -> dict[str, dict[str, Any]]:
    """NewAPI 系 /api/pricing 响应 → {模型名: {"resolutionOptions": [...]}}。

    [aipdd fork delta] vres 扩展：NewAPI 系网关的 /v1/models 只回模型名与
    supported_endpoint_types，不含分辨率档位；但其 /api/pricing（前端定价页
    同源接口）逐模型声明 task_pricing_resolutions / task_pricing.by_resolution
    —— 这正是网关提交时强校验的「已发布分辨率」（不在列表 = unsupported_resolution
    拒单，req08 实测）。没声明分辨率的条目（文本 / 图片定价）不进结果。
    """

    entries = payload.get("data") if isinstance(payload, dict) else payload
    result: dict[str, dict[str, Any]] = {}
    for item in entries or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("model_name") or item.get("name") or "").strip()
        if not name:
            continue
        resolutions = item.get("task_pricing_resolutions")
        if not isinstance(resolutions, list) or not resolutions:
            task_pricing = item.get("task_pricing")
            by_resolution = (
                task_pricing.get("by_resolution")
                if isinstance(task_pricing, dict)
                else None
            )
            resolutions = list(by_resolution) if isinstance(by_resolution, dict) else []
        cleaned = [
            str(option or "").strip()
            for option in resolutions
            if str(option or "").strip()
        ]
        if cleaned:
            result[name] = {"resolutionOptions": cleaned}
    return result


def _collect_gateway_capabilities_sync() -> dict[str, dict[str, Any]]:
    """逐 OpenAI 兼容渠道拉 /v1/models 能力元数据（含 /api/pricing 分辨率声明）。

    失败 / 超时静默跳过该渠道；pricing 拉取受同一总预算约束，不让新增请求把
    整个收集拖过外层 wait_for 预算。
    """

    import httpx

    from novelvideo.model_gateway_settings import get_newapi_provider_channels

    try:
        channels = get_newapi_provider_channels()
    except Exception:  # noqa: BLE001 - 拿不到渠道配置不能影响画布开目录
        logger.warning(
            "gateway capability discovery: failed to read provider channels",
            exc_info=True,
        )
        return {}

    started = time.monotonic()
    result: dict[str, dict[str, Any]] = {}
    for channel in channels:
        if not isinstance(channel, dict):
            continue
        provider = str(channel.get("provider") or "").strip().lower()
        if provider == "comfyui":
            # ComfyUI 能力由工作流配置决定，不跑 OpenAI 兼容 /models。
            continue
        base_url = str(channel.get("baseUrl") or "").strip().rstrip("/")
        api_key = str(channel.get("upstreamKey") or "").strip()
        if not base_url or not api_key:
            continue
        models_url = base_url if base_url.endswith("/models") else f"{base_url}/models"
        raw = None
        try:
            with httpx.Client(timeout=GATEWAY_CAPS_TIMEOUT_SECONDS) as client:
                resp = client.get(
                    models_url,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Accept": "application/json",
                    },
                )
            if resp.status_code < 400:
                payload = resp.json()
                raw = payload.get("data") if isinstance(payload, dict) else None
                if raw is None and isinstance(payload, dict):
                    raw = payload.get("models")
        except Exception:  # noqa: BLE001 - best-effort，单渠道挂不影响其他
            logger.debug(
                "gateway capability discovery failed for provider %s", provider, exc_info=True
            )
        for item in raw or []:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or item.get("model") or "").strip()
            if not model_id:
                continue
            caps = _extract_gateway_media_capabilities(item)
            if caps:
                merged = result.setdefault(model_id, {})
                merged.update(caps)
        # [aipdd fork delta] vres 扩展：NewAPI 系网关的分辨率声明不在 /v1/models
        # （只回 supported_endpoint_types），而在 /api/pricing——逐模型就是提交时
        # 强校验的「已发布分辨率」。只补空：/v1/models 的权威声明优先，定价声明
        # 兜底；拿不到静默跳过。预算受限：模型段已花超时就不再打拿不到的请求。
        pricing_url = _gateway_pricing_url(base_url)
        remaining = GATEWAY_CAPS_TIMEOUT_SECONDS - (time.monotonic() - started)
        if pricing_url and remaining > 1.0:
            try:
                with httpx.Client(timeout=min(4.0, remaining)) as client:
                    pricing_resp = client.get(
                        pricing_url,
                        headers={
                            "Authorization": f"Bearer {api_key}",
                            "Accept": "application/json",
                        },
                    )
                if pricing_resp.status_code < 400:
                    for model_id, caps in _extract_pricing_capabilities(
                        pricing_resp.json()
                    ).items():
                        merged = result.setdefault(model_id, {})
                        for key, value in caps.items():
                            merged.setdefault(key, value)
            except Exception:  # noqa: BLE001 - best-effort，单渠道挂不影响其他
                logger.debug(
                    "gateway pricing discovery failed for provider %s",
                    provider,
                    exc_info=True,
                )
    return result


def _caps_ttl(caps: dict[str, Any] | None) -> float:
    return GATEWAY_CAPS_TTL_SECONDS if caps else GATEWAY_CAPS_FAILURE_TTL_SECONDS


async def gateway_video_capabilities_by_name() -> dict[str, dict[str, Any]]:
    """网关声明的逐模型媒体能力（模型名 -> 档位）；拿不到返回空 dict。

    带 TTL 缓存 + 墙钟预算：custom 模式下画布每次开目录都要走发现链路，不能因为
    一次外部 /models 探测拖住出图。

    [aipdd fork delta] stale-while-revalidate：一旦成功拉到过非空能力，后续
    偶发超时 / 失败就沿用上次的好结果，而不是回落成通用默认。AIPDD /v1/models
    冷启动常在 6~8s 抖动，旧逻辑一次超时就锁 720p 十五秒、画布 768p 会闪烁。
    仍按失败短 TTL 节流重试，网络恢复后自动刷新；从未成功拉过时才回退空 dict。
    """

    global _gateway_caps_cache, _gateway_caps_lock, _gateway_caps_next_attempt

    def _serve_or_empty(cached: tuple[float, dict[str, dict]] | None) -> dict[str, Any]:
        return cached[1] if (cached is not None and cached[1]) else {}

    now = time.monotonic()
    cached = _gateway_caps_cache
    if cached is not None and cached[1] and now - cached[0] < GATEWAY_CAPS_TTL_SECONDS:
        return cached[1]
    if now < _gateway_caps_next_attempt:
        return _serve_or_empty(cached)

    if _gateway_caps_lock is None:
        _gateway_caps_lock = asyncio.Lock()
    async with _gateway_caps_lock:
        now = time.monotonic()
        cached = _gateway_caps_cache
        if (
            cached is not None
            and cached[1]
            and now - cached[0] < GATEWAY_CAPS_TTL_SECONDS
        ):
            return cached[1]
        if now < _gateway_caps_next_attempt:
            return _serve_or_empty(cached)
        # 先占下次重试时间，即使这次报错也不会每个请求都猛打网关。
        _gateway_caps_next_attempt = now + GATEWAY_CAPS_FAILURE_TTL_SECONDS
        try:
            caps = await asyncio.wait_for(
                asyncio.to_thread(_collect_gateway_capabilities_sync),
                timeout=GATEWAY_CAPS_TIMEOUT_SECONDS + 2.0,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "gateway capability discovery timed out; keeping last-known-good capabilities"
            )
            caps = {}
        except Exception:  # noqa: BLE001 - capability discovery is best-effort
            logger.warning(
                "gateway capability discovery raised; keeping last-known-good capabilities",
                exc_info=True,
            )
            caps = {}
        if caps:
            _gateway_caps_cache = (time.monotonic(), caps)
            _gateway_caps_next_attempt = 0.0
            return caps
        # 这次没拿到：有旧的好结果就继续用（stale），没有才回退空。
        return _serve_or_empty(cached)


async def gateway_image_capabilities_by_name() -> dict[str, dict[str, Any]]:
    """[aipdd fork delta] 图像协议适配用的逐模型能力声明（模型名 -> 档位/比例）。

    与视频共用同一份 /v1/models 拉取缓存（图像模型的 supportedSizes /
    supportedAspectRatios 已在 _extract_gateway_media_capabilities 里归一为
    resolutionOptions / ratioOptions）；名字分开只是为了让调用点的意图可读。
    """

    return await gateway_video_capabilities_by_name()
