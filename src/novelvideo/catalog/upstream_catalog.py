"""上游渠道模型目录：``{BaseURL}/models`` 响应的解析与归一（单一真源）。

[aipdd fork delta] 原则：**渠道里有什么模型，平台不预先知道，只能拿渠道 BaseURL
去拉 ``/models`` 才知道**。本模块把拉到的响应归一成结构化条目，之后所有环节
（网关注册投影、设置页候选、默认值有效性、能力/端点判定、参数档位）都只读这份
目录，不允许再出现「代码里写死的模型名 / 按名字猜能力」。

上游（AIPDD）在每个模型条目上带 ``aipdd`` 元数据块，本模块吸收的字段：

- ``kind``            llm / image / video / audio / embedding / decision
- ``endpoint``        字符串或列表，形如 ``/v1/images/edits``（去掉 ``/v1`` 后存）
- ``modalities``      ``{input: [...], output: [...]}`` —— 视觉资格靠 ``input`` 里
                      有没有 ``image`` 判定，不再靠模型名正则猜（旧坑：漏网面）
- ``embedding_dimensions``
- ``available``       渠道侧是否真的提供该模型（false 直接不进目录）
- ``capabilities``    ``protocols`` / ``operations`` / ``media_metadata``
                      （分辨率、比例、时长、参考输入上限、异步、支持格式等档位）

没有元数据的通用 OpenAI 兼容渠道退化成 ``inferred=True`` 的名字条目：kind 由既有
关键词分类兜底推断，能力档位保持未知 —— 推断值只用于分组展示，不做「能不能调」
的裁决（那交给实测缓存与目录 ``available``）。
"""

from __future__ import annotations

import re
from typing import Any

# 归一后的内部媒体口径（与设置页 TAB / 媒体映射 mediaType 同义）。
MEDIA_TEXT = "text"
MEDIA_VISION = "vision"
MEDIA_IMAGE = "image"
MEDIA_VIDEO = "video"
MEDIA_AUDIO = "audio"  # 语音合成（TTS）
MEDIA_ASR = "asr"  # 语音识别转写
# [aipdd fork delta req46] 音乐生成：与语音合成不同协议（直连 music_generation），
# 必须是独立口径，否则目录与设置页 TAB 会把音乐混进配音候选。
MEDIA_MUSIC = "music"
MEDIA_EMBEDDING = "embedding"
MEDIA_DECISION = "decision"
MEDIA_UNKNOWN = "other"

# 目录里可参与「默认值 / 功能位」选择的口径（other 只在推断时出现）。
KNOWN_MEDIA_TYPES = frozenset(
    {
        MEDIA_TEXT,
        MEDIA_VISION,
        MEDIA_IMAGE,
        MEDIA_VIDEO,
        MEDIA_AUDIO,
        MEDIA_ASR,
        MEDIA_MUSIC,
        MEDIA_EMBEDDING,
        MEDIA_DECISION,
    }
)

# 端点相对路径（已去 ``/v1`` 前缀）常量：调用方按这些键位判定往哪条协议打。
EP_CHAT = "chat/completions"
EP_RESPONSES = "responses"
EP_MESSAGES = "messages"
EP_EMBEDDINGS = "embeddings"
EP_IMAGES_GENERATIONS = "images/generations"
EP_IMAGES_EDITS = "images/edits"
EP_VIDEOS = "videos"
EP_AUDIO_SPEECH = "audio/speech"
EP_AUDIO_TRANSCRIPTIONS = "audio/transcriptions"
# [aipdd fork delta req46] 音乐生成端点（厂商直连形态，不经 OpenAI 兼容 audio/speech）。
EP_MUSIC_GENERATION = "music_generation"
EP_SYSTEMONE = "systemone"


def normalize_endpoint(value: Any) -> str:
    """把上游端点归一成「相对路径」：``https://x/v1/images/edits`` → ``images/edits``。

    保留 ``{taskId}`` 这类路径参数占位符，调用方自己替换；空值返回空串。
    """

    text = str(value or "").strip()
    if not text:
        return ""
    # 绝对 URL：只取 path 部分。
    if text.startswith("http://") or text.startswith("https://"):
        _, _, rest = text.partition("://")
        _, _, text = rest.partition("/")
    while text.startswith("/"):
        text = text[1:]
    if text.startswith("v1/"):
        text = text[len("v1/") :]
    return text.strip("/")


def _endpoints_of(raw: dict[str, Any]) -> list[str]:
    value = raw.get("endpoint") or raw.get("endpoints")
    items = value if isinstance(value, (list, tuple)) else [value]
    seen: list[str] = []
    for item in items:
        path = normalize_endpoint(item)
        if path and path not in seen:
            seen.append(path)
    return seen


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        value = [value]
    return [str(item).strip() for item in (value or []) if str(item).strip()]


def _media_type_from(kind: str, endpoints: list[str]) -> str:
    """上游 kind + 端点 → 内部媒体口径。

    ``audio`` 一个 kind 里同时有「语音合成」和「识别转写」两类，靠端点区分；
    上游把转写写成 ``audio/transcriptions`` 就是 ASR，否则按合成处理。
    """

    folded = kind.strip().lower()
    if folded in {"llm", "chat", "text"}:
        return MEDIA_TEXT
    if folded == "embedding":
        return MEDIA_EMBEDDING
    if folded == "image":
        return MEDIA_IMAGE
    if folded == "video":
        return MEDIA_VIDEO
    # [aipdd fork delta req46] 渠道显式声明的 music kind（MiniMax 目录形态）直接采信，
    # 不再落到名字推断 —— 推断只在没元数据时才用。
    if folded in {"music", "song"}:
        return MEDIA_MUSIC
    if folded == "decision":
        return MEDIA_DECISION
    if folded in {"audio", "speech", "asr", "transcription"}:
        if any(EP_AUDIO_TRANSCRIPTIONS in path for path in endpoints):
            return MEDIA_ASR
        return MEDIA_AUDIO
    return ""


def _infer_media_type(name: str) -> tuple[str, bool]:
    """无元数据条目：退回既有关键词分类，返回 (mediaType, 是否推断)。

    延迟导入避开循环依赖（分类真源在 media_model_discovery）。
    """

    try:
        from novelvideo.catalog.media_model_discovery import guess_model_kind
    except Exception:  # noqa: BLE001 - 分类真源不可用时不猜，交给调用面按 other 处理
        return MEDIA_UNKNOWN, True
    kind = str(guess_model_kind(name) or "").strip()
    if kind in {"vision", "text", "image", "video", "audio", "music", "embedding"}:
        # 关键词表把「合成 + 转写」都归 audio，这里没端点可分辨，统一按 audio 存；
        # ASR 选择面另有 endpoint 判定兜底（有元数据的渠道不会走到这儿）。
        # music 是 req46 新增的独立类（不走 audio/speech），不会再被归到语音。
        return kind, True
    return MEDIA_UNKNOWN, True


def _raw_display_name(item: dict[str, Any], meta: dict[str, Any], name: str) -> str:
    """原始条目上的展示名（上游没有 ``aipdd`` 元数据时的取词链）。

    [aipdd fork delta req48] Anthropic 的 ``/v1/models`` 用 ``display_name``、
    Gemini 适配层与 OpenAI 用 ``name``：只看 ``aipdd.display_name`` 会把三个厂商都
    有的可读名丢掉，设置页候选就只剩一串机器名。优先级先厂商元数据、后原生字段。
    """

    for value in (
        meta.get("display_name"),
        item.get("displayName"),
        item.get("display_name"),
        item.get("name"),
    ):
        text = str(value or "").strip()
        if text:
            return text
    return name


def parse_catalog_response(payload: Any) -> list[dict[str, Any]]:
    """OpenAI 兼容 ``/models`` 响应 → 结构化目录条目列表（按名字排序、去重）。

    条目形状由 :func:`coerce_entry` 唯一定义，落库快照与运行时解析共用一份。
    ``available`` 明确为 false 的条目**丢弃**（渠道已下架 / 未提供），兼容只回
    名字数组的旧形态响应。
    """

    raw_items: Any
    if isinstance(payload, dict):
        raw_items = payload.get("data")
        if raw_items is None:
            raw_items = payload.get("models")
    elif isinstance(payload, (list, tuple)):
        raw_items = payload
    else:
        # 字符串 / 数字 / None 都不是清单形态：绝不当作「一串名字」逐字符解析。
        return []
    entries: dict[str, dict[str, Any]] = {}
    for item in raw_items or []:
        if isinstance(item, dict) and not (
            str(item.get("id") or "").strip() or str(item.get("model") or "").strip()
        ):
            # 只带 ``name`` 的 dict 是归一后的快照形态，不是上游 /models 响应；在解析
            # 上游这一步凭空认它，等于让「响应里出现过的字样」变成模型名。
            continue
        entry = coerce_entry(item)
        if entry is None:
            continue
        entries[str(entry.get("name") or "").strip()] = entry
    return [entries[name] for name in sorted(entries)]


def _is_normalized_form(item: dict[str, Any]) -> bool:
    """是否已经是归一后的快照条目（而不是上游 OpenAI 原始形态）。"""

    return "mediaType" in item or "endpoints" in item or "inputModalities" in item


def coerce_entry(item: Any) -> dict[str, Any] | None:
    """任意形态的目录条目 → 归一条目；无效 / 已下架返回 ``None``。

    幂等是重点：快照里存的就是归一条目，读回来时必须原样保留 kind / 端点 /
    维度 / 能力档位，不能因为「再解析一次」退化成靠名字猜（旧坑：重解析把上游
    声明抹平，分辨率 / 比例 / 视觉资格全部丢失）。
    """

    if isinstance(item, str):
        name = item.strip()
        if not name:
            return None
        media_type, _ = _infer_media_type(name)
        return _make_entry(
            name=name,
            display_name=name,
            kind="",
            media_type=media_type,
            endpoints=[],
            protocols=[],
            operations=[],
            input_modalities=[],
            output_modalities=[],
            dimensions=None,
            available=True,
            inferred=True,
            media_metadata={},
        )
    if not isinstance(item, dict):
        return None
    if _is_normalized_form(item):
        if item.get("available") is False:
            return None
        endpoints = [normalize_endpoint(path) for path in (item.get("endpoints") or [])]
        return _make_entry(
            name=str(item.get("name") or item.get("id") or "").strip(),
            display_name=str(item.get("displayName") or item.get("name") or "").strip(),
            kind=str(item.get("kind") or "").strip(),
            media_type=str(item.get("mediaType") or "").strip().lower()
            or _media_type_from(str(item.get("kind") or ""), endpoints)
            or MEDIA_UNKNOWN,
            endpoints=[path for path in endpoints if path],
            protocols=_string_list(item.get("protocols")),
            operations=_string_list(item.get("operations")),
            input_modalities=_string_list(item.get("inputModalities")),
            output_modalities=_string_list(item.get("outputModalities")),
            dimensions=item.get("dimensions")
            if type(item.get("dimensions")) is int and item.get("dimensions") > 0
            else None,
            available=True,
            inferred=bool(item.get("inferred")),
            media_metadata=item.get("mediaMetadata")
            if isinstance(item.get("mediaMetadata"), dict)
            else {},
        )
    name = str(item.get("id") or item.get("model") or item.get("name") or "").strip()
    if not name:
        return None
    meta = item.get("aipdd") if isinstance(item.get("aipdd"), dict) else {}
    meta = meta or {}
    if meta.get("available") is False:
        return None
    endpoints = _endpoints_of(meta) if meta else []
    kind = str(meta.get("kind") or "").strip()
    capabilities = meta.get("capabilities")
    capabilities = capabilities if isinstance(capabilities, dict) else {}
    media_metadata = capabilities.get("media_metadata")
    media_metadata = media_metadata if isinstance(media_metadata, dict) else {}
    modalities = meta.get("modalities")
    modalities = modalities if isinstance(modalities, dict) else {}
    dimensions = meta.get("embedding_dimensions")
    dimensions = dimensions if type(dimensions) is int and dimensions > 0 else None
    media_type = _media_type_from(kind, endpoints)
    inferred = False
    if not media_type:
        media_type, inferred = _infer_media_type(name)
    return _make_entry(
        name=name,
        display_name=_raw_display_name(item, meta, name),
        kind=kind,
        media_type=media_type,
        endpoints=endpoints,
        protocols=_string_list(capabilities.get("protocols")),
        operations=_string_list(capabilities.get("operations")),
        input_modalities=_string_list(modalities.get("input")),
        output_modalities=_string_list(modalities.get("output")),
        dimensions=dimensions,
        available=True,
        inferred=inferred,
        media_metadata=media_metadata,
    )


def _make_entry(
    *,
    name: str,
    display_name: str,
    kind: str,
    media_type: str,
    endpoints: list[str],
    protocols: list[str],
    operations: list[str],
    input_modalities: list[str],
    output_modalities: list[str],
    dimensions: int | None,
    available: bool,
    inferred: bool,
    media_metadata: dict[str, Any],
) -> dict[str, Any]:
    return {
        "name": name,
        "displayName": display_name,
        "kind": kind,
        "mediaType": media_type,
        "endpoints": list(endpoints),
        "protocols": list(protocols),
        "operations": list(operations),
        "inputModalities": list(input_modalities),
        "outputModalities": list(output_modalities),
        "dimensions": dimensions,
        "available": bool(available),
        "inferred": bool(inferred),
        "mediaMetadata": dict(media_metadata or {}),
    }


def catalog_names(entries: list[dict[str, Any]]) -> list[str]:
    """目录条目 → 名字数组（快照兼容视图与候选列表用）。"""

    return sorted(
        {
            str(entry.get("name") or "").strip()
            for entry in (entries or [])
            if isinstance(entry, dict) and str(entry.get("name") or "").strip()
        }
    )


def entry_available(entry: dict[str, Any]) -> bool:
    """条目是否仍在渠道提供：只有上游明确写 ``available: false`` 才算下架。"""

    return entry.get("available") is not False


def entry_has_declared_modality(entry: dict[str, Any]) -> bool:
    """渠道是否**自己声明**了模态（而不是我们按名字猜出来的）。

    判定「该不该采信目录」的开关：有声明就以 ``modalities`` 为准、关键词正则彻底
    靠边；没声明（通用 OpenAI 兼容渠道只回名字）才允许调用方退回关键词推断。
    这一层区分是遗留项「视觉能力位关键词漏网面」的收口点 —— 否则调用方无法判断
    目录里的空 ``inputModalities`` 到底是「渠道说不支持」还是「渠道压根没说」。
    """

    if _string_list(entry.get("inputModalities")):
        return True
    return bool(_string_list(entry.get("outputModalities")))


def supports_vision(entry: dict[str, Any]) -> bool:
    """能否读图：优先看目录 ``modalities.input``，无元数据时才退关键词。

    旧坑（req35/遗留⑥）：按模型名正则判视觉必然漏网（多模态模型名不一定带
    vision/vl，反而 embedding 名字里带 vision）。有元数据的渠道以声明为准。
    """

    modalities = _string_list(entry.get("inputModalities"))
    if modalities:
        return "image" in modalities or "video" in modalities
    name = str(entry.get("name") or "").strip()
    if not name:
        return False
    try:
        from novelvideo.catalog.media_model_discovery import is_vision_capable
    except Exception:  # noqa: BLE001 - 分类真源不可用时不宣称支持
        return False
    return is_vision_capable(name)


def declares_endpoint(entry: dict[str, Any], purpose: str) -> bool:
    """条目是否**声明**了某个用途端点（区别于 :func:`endpoint_for` 拿首个端点兜底）。

    「渠道说它能干这件事」的判据：转写 / 编辑一类的候选资格用它，避免把「只写了
    一个首页端点」的条目误判成具备该能力。
    """

    wanted = normalize_endpoint(purpose)
    if not wanted:
        return False
    paths = [normalize_endpoint(str(path or "")) for path in (entry.get("endpoints") or [])]
    return any(path and (path == wanted or wanted in path) for path in paths)


def supports_asr(entry: dict[str, Any]) -> bool:
    """能否做语音转写：目录声明 ``mediaType == asr`` 或有 ``audio/transcriptions`` 端点。

    [aipdd fork delta req40] 与 :func:`supports_vision` 的分工：这里**不带关键词兜底**，
    渠道没声明就返回 False，由调用方（抖音文案提取的候选清单）自己决定要不要退回名字
    正则 —— 目录层只回答「渠道有没有明说」，不替调用面决定选不选。
    """

    if str(entry.get("mediaType") or "").strip().lower() == MEDIA_ASR:
        return True
    return declares_endpoint(entry, EP_AUDIO_TRANSCRIPTIONS)


def text_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """纯文本 LLM 条目（不含向量 / 媒体 / 决策）。"""

    return [
        entry
        for entry in entries or []
        if entry_available(entry)
        and str(entry.get("mediaType") or "") in {MEDIA_TEXT, MEDIA_VISION}
    ]


def vision_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """能读图的 LLM 条目：「视觉理解」候选的唯一来源。"""

    return [
        entry
        for entry in text_entries(entries)
        if supports_vision(entry)
    ]


def entries_by_media_type(
    entries: list[dict[str, Any]],
    media_type: str,
) -> list[dict[str, Any]]:
    wanted = str(media_type or "").strip().lower()
    return [
        entry
        for entry in entries or []
        if entry_available(entry)
        and str(entry.get("mediaType") or "").strip().lower() == wanted
    ]


def endpoint_for(entry: dict[str, Any], purpose: str) -> str:
    """按用途挑端点：命中 ``purpose`` 子串就返回，否则回条目第一个端点。

    ``purpose`` 用归一后的相对路径片段（``images/edits``、``videos``、
    ``audio/transcriptions``…），调用面不再自带硬编码 URL —— 上游换端点只需重
    新拉目录。
    """

    paths = [str(path or "").strip() for path in (entry.get("endpoints") or [])]
    paths = [path for path in paths if path]
    wanted = normalize_endpoint(purpose)
    if wanted:
        for path in paths:
            if path == wanted or wanted in path:
                return path
    return paths[0] if paths else ""


_TASK_PLACEHOLDER = re.compile(r"\{[^/{}]+\}")


# [aipdd fork delta req44] 参考输入数量的声明键位在**同一个渠道里就有两种写法**（
# 2026-10-08 实拉 ``/v1/models``：``ap-minimax-h3`` 写 ``maxImageInputs: 9``，
# ``ap-ltx-2.5`` 写 ``maxInputImages: 1``）。只认前者会把 LTX 的图槽上限读成「渠道没说」，
# 于是首尾帧两张图一路放到上游才被默默截成一张 —— 那是丢素材，不是拒单。
# 表里只放**实测到过的**拼法；出现新写法时往这里加一条并带上取证，不允许在调用点各自猜。
_MEDIA_COUNT_ALIASES: dict[str, tuple[str, ...]] = {
    "maxImageInputs": ("maxInputImages",),
}


def task_lookup_endpoint(entry: dict[str, Any], submit_endpoint: str = "") -> str:
    """从目录里找「按任务号回查」的那条端点，把路径参数换成 ``<TASK_ID>``。

    [aipdd fork delta req41] 确定渠道会把轮询路径也声明在 ``endpoint`` 里（AIPDD：
    ``videos`` + ``videos/{taskId}`` + ``videos/{taskId}/content``）。调用示例要是还按
    老习惯拼 ``video/generations/<TASK_ID>``，用户复制到 AIPDD 上就是 404 —— 轮询地址
    同样是上游事实，不能由代码猜。

    只挑「以提交端点为前缀 + 带路径参数」的最短一条（带 ``content`` 后缀的那条是取成品，
    不是查状态）；目录没声明就回空串，由调用方按 ``{submit}/<TASK_ID>`` 兜底。
    """

    wanted = normalize_endpoint(submit_endpoint)
    paths = [normalize_endpoint(raw) for raw in (entry.get("endpoints") or [])]
    candidates: list[str] = []
    for path in paths:
        if not path or "{" not in path:
            continue
        head = path.split("{", 1)[0].rstrip("/")
        if wanted and head != wanted:
            continue
        candidates.append(_TASK_PLACEHOLDER.sub("<TASK_ID>", path))
    return min(candidates, key=len) if candidates else ""


def media_metadata(entry: dict[str, Any]) -> dict[str, Any]:
    """条目的媒体声明（参考输入上限 / 档位等），并把**已实测到的别名键位**归到规范键下。"""

    meta = entry.get("mediaMetadata")
    out = dict(meta) if isinstance(meta, dict) else {}
    for canonical, aliases in _MEDIA_COUNT_ALIASES.items():
        if type(out.get(canonical)) is int:
            continue
        for alias in aliases:
            value = out.get(alias)
            if type(value) is int and value >= 0:
                out[canonical] = value
                break
    return out


def declared_media_count(container: dict[str, Any], key: str) -> int | None:
    """从一个声明容器里读一个参考数量，兼容同一渠道里的不同拼法；没写就回 ``None``。

    ``None`` = 渠道没说（调用方不该自锁），与「明说 0 = 没有这类输入」是两回事。
    """

    if not isinstance(container, dict):
        return None
    for candidate in (key, *_MEDIA_COUNT_ALIASES.get(key, ())):
        value = container.get(candidate)
        if type(value) is int and value >= 0:
            return value
    return None


def dimensions_for(entry: dict[str, Any]) -> int | None:
    value = entry.get("dimensions")
    return value if type(value) is int and value > 0 else None
