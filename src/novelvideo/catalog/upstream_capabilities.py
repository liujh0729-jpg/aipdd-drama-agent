"""[aipdd fork delta req43] 渠道「能力绑定」真源：一个模型在某条提交路径上实际能吃什么输入。

背景（实测取证，2026-10-07）：AIPDD 的 ``/v1/models`` 给 ``ap-minimax-h3`` 静态声明了
``supportsReferenceVideo: true``，我们就据此放行了「替换视频产品 / 替换视频人物」，
结果产物与原视频毫不相干。去渠道自己的 ``/v1/capabilities`` 里看**真正在线的绑定**：

    aipdd_minimax_h3_t2v_speed_test          text_to_video             提示词 / 时长 / 种子
    aipdd_minimax_h3_first_last_frame_speed  first_last_frame_to_video 提示词 / 首帧图 / 尾帧图 / …
    aipdd_minimax_h3_ref2va_test             first_last_frame_to_video 提示词 / 首帧图 / 尾帧图 / …

**口径更正（先前一版把结论说过头了）**：这三条绑定里没有任何参考视频 / 参考音频输入
节点，说明的是**渠道当前的接线不足**，不是「H3 不支持参考视频 / 音频」。MiniMax H3 的
Ref2VA 就是全能参考形态（多图 + 多视频 + 多音频入参，输出才带原生音频），渠道
``/v1/models`` 当时（req43，2026-10-07）也照样申报了 ``supportsReferenceVideo/Audio: true``
（这个布尔第二天就翻了，见下面【布尔声明会漂】）；只是那条名字写着
``ref2va`` 的绑定被接成了「双参考图首尾帧」（连 taskKind 都写成 first_last_frame_to_video）。
所以判定的正确粒度是**按提交模式对上具体绑定**：一个模型名背后是几条 taskKind 不同、
入参集也不同的绑定（文生 / 首尾帧 / 全能参考），按模型名取并集在两个方向都会错。

【布尔声明会漂】2026-10-08 复拉同一份 ``/v1/models``，``ap-minimax-h3`` 的
``supportsReferenceVideo`` 已经从 req43 看到的 ``true`` 改成了 ``false``，并配上了
``maxVideoInputs: 0``（总闸 ``maxReferenceInputs: 12`` 刚好 = 9 图 + 3 音频）。同一个键两天
给两个答案是渠道侧实况，不是哪个版本读错了 —— 这正是要把这类布尔排除在能力判定之外
的原因（参考上限只照**数量**申报取，见 ``newapi._aipdd_video_input_caps``）。

本模块因此做两件事：

1. 把 ``/v1/capabilities``（暴露该接口的渠道才有意见）读回来，归一化成
   「某模型某模式下的在线绑定各自接受哪些输入类型」的事实；
2. 提供按**名字族 + 模式**关联的判定：只有当渠道明确声明了该模式下的绑定、且这些
   绑定没有一个吃参考视频时，才判定「这条路径递不进去」。**关联不上就不表态** ——
   有些渠道的 ``/v1/models`` 会列第三方代理模型（不在这份自托管绑定清单里），拿渠道
   局部的绑定去否掉全局的清单是另一种误伤；反过来也不能因为渠道今天没接线，就把
   模型的能力面在界面上抹掉。

刻意不做的事：不写死「H3 全能参考接几图几视频」、不写死「AIPDD 只有 5 条视频绑定」。
渠道把全能参考绑定补上，判定会自动放开；把事实抄进代码正是 req40/req41 一路在拆的病根。
"""

from __future__ import annotations

import re
from typing import Any

__all__ = [
    "CAPABILITIES_PATH",
    "AIPDD_VIDEOS_MODE_TEXT",
    "AIPDD_VIDEOS_MODE_FRAMES",
    "AIPDD_VIDEOS_MODE_REFERENCE",
    "aipdd_videos_mode_selector",
    "video_bindings_need_mode_selector",
    "fetch_upstream_capabilities",
    "normalize_request_mode",
    "summarize_video_bindings",
    "video_input_facts_for_model",
    "video_binding_modes_for_model",
    "bindings_accepting_input",
    "binding_accepts_input",
    "name_family_tokens",
]

# ``base_url`` 已经是 OpenAI 兼容根（``…/v1``），能力接口就挂在同一根下。
CAPABILITIES_PATH = "capabilities"

# video 类绑定的 endpointType 取值（渠道自述，不是我们造的词）。
_VIDEO_ENDPOINT_TYPES = frozenset({"openai-video", "video", "video-generation"})

# 输入类型 → 绑定里的判据。``uiType`` 是渠道给前端的控件类型，``inputModalities``
# 是它给编排侧的模态声明；两个口径任一命中即算「吃这种输入」。
_VIDEO_UI_TYPES = frozenset({"video_url", "video"})
_AUDIO_UI_TYPES = frozenset({"video_audio", "audio_url", "audio"})
_IMAGE_UI_TYPES = frozenset({"image_url", "image"})

# 提交模式（omni 路由的 gen_mode）→ 该模式下应当被选中的绑定特征。
#
# 为什么需要这一层：同一个模型名可以挂着几条 taskKind 不同、入参集也不同的绑定
# （H3 就是文生 / 首尾帧 / ref2va 三条）。按模型名取并集，既会把只接在首尾帧路上的
# 控件当成「全能参考也能吃」，也会因为某一条窄绑定不吃参考视频就把整个模型判残。
# 特征只包渠道自己写的词（taskKind 与 code 里的模式缩写），不包任何模型名。
_MODE_ALIASES = {
    "texttovideo": "text_to_video",
    "firstframe": "first_frame",
    "firstlastframe": "first_last_frame",
    "imagetovideo": "image_to_video",
    "allreference": "all_reference",
    "videoreference": "all_reference",
    "videoedit": "video_edit",
    "videoextend": "video_extend",
}
_MODE_TASK_KIND_TOKENS = {
    "text_to_video": ("text_to_video",),
    # [aipdd fork delta req44] 画布侧的路由口径（2026-10-08）：**图生视频 / 首帧 / 首尾帧
    # 三种模式都走渠道的 ``first_last_frame_to_video`` 绑定**（尾帧可选，单图时只填首帧
    # 槽）。因此这三个模式共同的判据是这个 taskKind，同时保留各自的旧 taskKind 词 ——
    # LTX 2.5 那种渠道自己就写着 ``image_to_video`` 的绑定照样要能被图生视频认到，
    # 「只加不减」。渠道若哪天补出独立的 i2v 绑定，判定自动会多一个候选。
    "first_frame": ("first_last_frame", "first_frame", "image_to_video"),
    "image_to_video": ("first_last_frame", "image_to_video", "first_frame"),
    # [aipdd fork delta req44] 首尾帧也算进 ``image_to_video`` 那条帧路：渠道给 LTX 2.5
    # 只接了一条 ``taskKind=image_to_video`` 的绑定，而产品口径上 LTX 是有首尾帧的
    # （2026-10-08）。把入口藏掉等于拿渠道接线不足当能力边界；能不能真递进两张帧图
    # 由图槽上限（渠道 ``maxInputImages`` 声明）在提交时门控，那是另一条独立事实。
    "first_last_frame": ("first_last_frame", "image_to_video"),
    # 全能参考的 taskKind 带 reference 而不带 first/last frame；首尾帧绑定因此不会
    # 被当成全能参考候选（H3 今天就是这个坑：写着 ref2va 的绑定接成了首尾帧）。
    "all_reference": ("reference_to_video", "multimodal_reference", "all_reference"),
    "video_edit": ("video_edit", "video_to_video"),
    "video_extend": ("video_extend",),
}
_MODE_CODE_TOKENS = {
    "text_to_video": ("t2v", "t2va"),
    "first_frame": ("i2v", "i2va", "firstframe"),
    "image_to_video": ("i2v", "i2va"),
    "first_last_frame": ("fl2v", "fl2va", "firstlast", "firstlastframe"),
    "all_reference": ("ref2va", "ref2v", "ref2v2a", "r2v", "allref", "allreference"),
    "video_edit": ("edit", "videoedit"),
    "video_extend": ("extend", "videoextend"),
}
# 全能参考专用的绑定缩写：这些 code token 一旦出现，这条绑定就归「全能参考」那条路，
# 不再被首帧 / 首尾帧 / 图生视频抢去当候选。H3 就是必须这么判的活例：两条绑定 taskKind
# 同写 ``first_last_frame_to_video``，不按 code 把 ref2va 那条推出去，首尾帧的入参事实
# 会把全能参考那条也算进来（反过来也会）。
_REFERENCE_ONLY_CODE_TOKENS = frozenset(_MODE_CODE_TOKENS["all_reference"])
# 按路由口径归到 ``first_last_frame_to_video`` 那一路的提交模式。
_FRAME_ROUTE_MODES = frozenset({"first_frame", "first_last_frame", "image_to_video"})

# [aipdd fork delta req44] AIPDD ``/v1/videos`` 的**模式选择子**（用户给的渠道路由口径，
# 2026-10-08）：图生视频 / 首帧 / 首尾帧 → ``first_last_frame_to_video``，文生视频 →
# ``text_to_video``，全能参考 → ``ref2va``。
#
# 为什么非把模式说出去不可（2026-10-08 实拉渠道 ``/v1/capabilities``）：aipdd 目前在线
# 6 条视频绑定，其中 H3 的三条里 ``…_first_last_frame_speed_test`` 与 ``…_ref2va_test``
# **入参面板一模一样**（prompt / image 首帧图片 / referenceImage 尾帧图片 / durationSeconds /
# seed），连 taskKind 都被渠道写成了同一个 ``first_last_frame_to_video``；两者真正差别在
# 权重与自定义节点（fl2va + TESpeedMiniMaxH3 vs ref2va + MiniMaxH3AudioConditioningT8 等
# 音频条件节点）。``/v1/models`` 又只给一个 ``ap-minimax-h3`` 位、没有 supportedModes。
# 所以光按输入形状递素材，渠道没法在两条工作流里分辨 —— 必须把模式显式说出来。
#
# 表里只有用户确认过的三种取值；**认不出的模式返回空串**（视频编辑 / 延长 / 未传模式），
# 不发渠道没说的字段。
AIPDD_VIDEOS_MODE_TEXT = "text_to_video"
AIPDD_VIDEOS_MODE_FRAMES = "first_last_frame_to_video"
AIPDD_VIDEOS_MODE_REFERENCE = "ref2va"
_AIPDD_VIDEOS_MODE_SELECTOR = {
    "text_to_video": AIPDD_VIDEOS_MODE_TEXT,
    "first_frame": AIPDD_VIDEOS_MODE_FRAMES,
    "first_last_frame": AIPDD_VIDEOS_MODE_FRAMES,
    "image_to_video": AIPDD_VIDEOS_MODE_FRAMES,
    "all_reference": AIPDD_VIDEOS_MODE_REFERENCE,
}

# 名字族匹配时要洗掉的「变体后缀」：这些 token 描述的是同一模型的不同部署形态，
# 参与匹配只会把 ``ap-minimax-h3`` 和它自己的绑定推开。
_VARIANT_TOKENS = frozenset(
    {
        "t2v",
        "i2v",
        "r2v",
        "f2v",
        "fl2v",
        "ref2va",
        "ref2v",
        "first",
        "last",
        "frame",
        "frames",
        "speed",
        "fast",
        "flash",
        "lite",
        "mini",
        "pro",
        "max",
        "ultra",
        "standard",
        "value",
        "test",
        "dev",
        "vip",
        "audio",
        "audioless",
        "silent",
        "omni",
        "with",
        "without",
        # 渠道 / 前缀噪声
        "ap",
        "api",
        "aipdd",
        "newapi",
        "custom",
        "model",
        "models",
    }
)

_TOKEN_SPLIT = re.compile(r"[^a-zA-Z0-9.]+")


def _split_tokens(value: Any) -> set[str]:
    """名字 / code → 小写 token 集（丢掉纯数字与过短碎片，版本数字不参与匹配）。"""

    text = str(value or "").strip().lower()
    if not text:
        return set()
    tokens: set[str] = set()
    for raw in _TOKEN_SPLIT.split(text):
        if not raw:
            continue
        for part in (raw, *raw.split(".")):
            if len(part) < 2 or part.isdigit():
                continue
            tokens.add(part)
    return tokens


def name_family_tokens(value: Any) -> set[str]:
    """模型名 / 绑定 code 的「名字族」token 集（洗掉变体后缀与渠道前缀）。"""

    return {token for token in _split_tokens(value) if token not in _VARIANT_TOKENS}


def _families_match(model_tokens: set[str], binding_tokens: set[str]) -> bool:
    """名字族是否同指一个模型：**双向包含 + 至少两个共有 token**。

    ``ap-minimax-h3`` → ``{minimax, h3}`` 与
    ``aipdd_minimax_h3_t2v_speed_test`` → ``{minimax, h3}`` 精确相等，命中；
    ``aipdd_latentsync1.5`` → ``{latentsync, 1?}`` 与 H3 无交集，不命中。
    要求两个共有 token，是为了避免 ``{video}``、``{2}`` 这类公共碎片把不相干
    的模型撮合到一起。
    """

    if not model_tokens or not binding_tokens:
        return False
    shared = model_tokens & binding_tokens
    if len(shared) < 2:
        return False
    return model_tokens <= binding_tokens or binding_tokens <= model_tokens


def fetch_upstream_capabilities(
    base_url: str,
    upstream_key: str,
    *,
    timeout: float | None = None,
) -> tuple[list[dict[str, Any]], str]:
    """拉渠道的 ``/v1/capabilities``，返回 ``(条目, 错误)``。

    与 ``list_upstream_catalog`` 同一降级口径：任何失败（超时 / 401 / 404 / 结构
    不符）都只回错误串、返回空表，调用方据此「不表态」，绝不把远程故障当用户错误。
    """

    import httpx

    base = str(base_url or "").strip().rstrip("/")
    key = str(upstream_key or "").strip()
    if not base or not key:
        return [], "base_url and upstream_key are required"
    url = f"{base}/{CAPABILITIES_PATH}"
    try:
        response = httpx.get(
            url,
            headers={
                "Authorization": f"Bearer {key}",
                "Accept": "application/json",
            },
            timeout=10.0 if timeout is None else float(timeout),
        )
        response.raise_for_status()
        payload = response.json()
    except Exception as exc:  # noqa: BLE001 - 能力接口是加分项，拿不到就不表态
        return [], str(exc)
    return _capability_items(payload), ""


def _capability_items(payload: Any) -> list[dict[str, Any]]:
    """兼容 ``{data: [...]}`` / ``{data: {list: [...]}}`` / 裸数组三种响应形态。"""

    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "result"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            for nested in ("list", "items", "data"):
                rows = value.get(nested)
                if isinstance(rows, list):
                    return [item for item in rows if isinstance(item, dict)]
    return []


def _ui_types(entry: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for param in entry.get("params") or []:
        if not isinstance(param, dict):
            continue
        ui = str(param.get("uiType") or "").strip().lower()
        if ui:
            out.add(ui)
    return out


def _modalities(entry: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for key in ("inputModalities", "outputModalities"):
        if key != "inputModalities":
            continue
        value = entry.get(key)
        if isinstance(value, list):
            out.update(str(item).strip().lower() for item in value if str(item).strip())
    return out


def _param_labels(entry: dict[str, Any]) -> list[str]:
    """绑定实际收到的入参清单（渠道自己给的名字）。

    报错里要摊证据：只说「入参只有 textarea / image_url」用户还是不知道缺什么；把
    paramName（没名就退 paramKey）列出来才能看出「全能参考需要的参考视频 / 参考音频
    节点根本不在」。必填项排在前面，因为用户最先要补的就是它们。
    """

    required: list[str] = []
    optional: list[str] = []
    for param in entry.get("params") or []:
        if not isinstance(param, dict):
            continue
        label = str(param.get("paramName") or param.get("paramKey") or "").strip()
        if not label:
            continue
        (required if param.get("isRequired") else optional).append(label)
    seen: set[str] = set()
    ordered = [label for label in [*required, *optional] if not (label in seen or seen.add(label))]
    return ordered


def normalize_request_mode(mode: Any) -> str:
    """请求里的提交模式归一（前端 camelCase / 后端 snake_case 都接）。"""

    raw = re.sub(r"[^a-z0-9]+", "", str(mode or "").strip().lower())
    if raw in _MODE_ALIASES:
        return _MODE_ALIASES[raw]
    return str(mode or "").strip().lower().replace("-", "_")


def aipdd_videos_mode_selector(mode: Any) -> str:
    """提交模式 → 该递给 AIPDD ``/v1/videos`` 的模式选择子；**认不出返回空串**。

    空串 = 不发 ``mode`` 字段：视频编辑 / 延长这些渠道今天没给出对应绑定的模式，
    自己发明一个取值只会被上游拒单或默默走错工作流。

    这个取值该不递还要过 :func:`video_bindings_need_mode_selector` 那道闸 —— 只有渠道
    自己分辨不了时才替它说，否则维持该模型原有的建单体形状（Seedance / LTX 今天就怎样
    怎样）。
    """

    return _AIPDD_VIDEOS_MODE_SELECTOR.get(normalize_request_mode(mode), "")


def video_bindings_need_mode_selector(
    bindings: list[dict[str, Any]],
    model: str,
) -> bool:
    """该模型在渠道名下的在线绑定是否多到光看 taskKind 分不出该跑哪一条。

    [aipdd fork delta req44] 为什么需要这个限定：模式选择子是给**渠道自己分不清**的场合
    用的（H3 三条绑定里两条的 taskKind 都被渠道写成 ``first_last_frame_to_video``，入参
    面板还一模一样）。反过来 LTX 2.5 名下是 ``text_to_video`` + ``image_to_video`` 两条互不
    重名的绑定，光按输入形状就能路由 —— 再替它发一个 ``first_last_frame_to_video`` 就是拿
    minimax 的口径去改别的模型的提交形状（用户 2026-10-08：「我说的是 minimax 模型，
    seedance 该怎样就怎样」）。

    判据只看渠道自述的 taskKind 是否重复，不看模型名。名册关联不上（空表）时返回
    ``False`` —— 没读到事实就不改变任何现有行为。
    """

    model_tokens = name_family_tokens(model)
    if not model_tokens or not bindings:
        return False
    seen: set[str] = set()
    for binding in bindings:
        if not isinstance(binding, dict) or not binding.get("available", True):
            continue
        if not (
            _families_match(model_tokens, name_family_tokens(binding.get("code")))
            or _families_match(model_tokens, name_family_tokens(binding.get("name")))
        ):
            continue
        task_kind = str(binding.get("taskKind") or "").strip().lower()
        if not task_kind:
            continue
        if task_kind in seen:
            return True
        seen.add(task_kind)
    return False


def _binding_code_tokens(binding: dict[str, Any]) -> set[str]:
    return _split_tokens(f"{binding.get('code') or ''} {binding.get('name') or ''}")


def _binding_matches_mode(binding: dict[str, Any], mode_key: str) -> bool:
    """这条绑定是不是该提交模式要走的哪一条（只读渠道自述的 taskKind / code 缩写）。"""

    tokens = _MODE_TASK_KIND_TOKENS.get(mode_key)
    if not tokens:
        return False
    # 全能参考专用的绑定不参与帧类模式（H3 两条绑定 taskKind 完全同名的唯一区分点）。
    if mode_key in _FRAME_ROUTE_MODES and (
        _binding_code_tokens(binding) & _REFERENCE_ONLY_CODE_TOKENS
    ):
        return False
    task_kind = str(binding.get("taskKind") or "").strip().lower()
    if any(token in task_kind for token in tokens):
        return True
    return bool(_binding_code_tokens(binding) & set(_MODE_CODE_TOKENS.get(mode_key, ())))


def summarize_video_bindings(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """把能力条目里「视频类」的绑定归一成判定用的事实表。

    每条留：``code``（绑定标识，进报错文案当证据）、``taskKind``、``uiTypes``、
    ``inputModalities``、``paramNames``（渠道给这个入参起的中文名，报错里直接引用
    比只给控件名好读）。非视频 endpointType 的条目一律不进表，免得让图片/音频绑定
    干扰判定。
    """

    bindings: list[dict[str, Any]] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        endpoint_type = str(entry.get("endpointType") or "").strip().lower()
        adapter = str(entry.get("adapterCode") or "").strip().lower()
        if endpoint_type not in _VIDEO_ENDPOINT_TYPES and "video" not in adapter:
            continue
        code = str(entry.get("code") or entry.get("id") or "").strip()
        if not code:
            continue
        bindings.append(
            {
                "code": code,
                "name": str(entry.get("name") or "").strip(),
                "taskKind": str(entry.get("taskKind") or "").strip(),
                "uiTypes": sorted(_ui_types(entry)),
                "inputModalities": sorted(_modalities(entry)),
                "paramNames": _param_labels(entry),
                "available": bool(entry.get("available", True)),
            }
        )
    return bindings


def binding_accepts_input(binding: dict[str, Any], kind: str) -> bool:
    """某条绑定吃不吃这类参考素材（video / audio / image）。"""

    ui_map = {
        "video": _VIDEO_UI_TYPES,
        "audio": _AUDIO_UI_TYPES,
        "image": _IMAGE_UI_TYPES,
    }
    wanted = ui_map.get(str(kind or "").strip().lower())
    if not wanted:
        return False
    ui_types = {str(item).strip().lower() for item in binding.get("uiTypes") or []}
    if ui_types & wanted:
        return True
    modalities = {
        str(item).strip().lower() for item in binding.get("inputModalities") or []
    }
    return str(kind or "").strip().lower() in modalities


def video_input_facts_for_model(
    bindings: list[dict[str, Any]],
    model: str,
    *,
    mode: str | None = None,
) -> dict[str, Any] | None:
    """按名字族（可选再按提交模式）取该模型绑定的输入事实；**关联不上返回 None（不表态）**。

    给 ``mode`` 时只在「该模式要走的绑定」里判：一个模型名下可能挂着文生 / 首尾帧 /
    全能参考几条入参集不同的绑定，取并集会把首尾帧那几条的入参当成全能参考也能吃，
    或反过来因为一条窄绑定不吃参考视频就把整个模型判残。认不出属于该模式的绑定时
    同样不表态 —— 「我们的词表没匹配上」读不出「渠道不支持」。

    不给 ``mode`` 时退回「该模型全部在线绑定的并集」，供只想问「这个模型整体上有没有
    参考视频通道」的调用方使用。
    """

    model_tokens = name_family_tokens(model)
    if not model_tokens or not bindings:
        return None
    matched = [
        binding
        for binding in bindings
        if _families_match(model_tokens, name_family_tokens(binding.get("code")))
        or _families_match(model_tokens, name_family_tokens(binding.get("name")))
    ]
    # 只认**在线**的绑定：渠道把绑定摘下去（``available=False``）之后，那条工作流
    # 根本调度不到，拿它的入参声明去放开界面同样是骗人。命中项全部离线时不表态
    # （返回 None）——「下线」读不出「不支持」，事实表里没有可用答案。
    matched = [item for item in matched if item.get("available", True)]
    if not matched:
        return None
    mode_key = normalize_request_mode(mode)
    scoped = matched
    if mode_key:
        scoped = [item for item in matched if _binding_matches_mode(item, mode_key)]
        if not scoped:
            return None
    return {
        "matched": True,
        "model": str(model or "").strip(),
        "mode": mode_key,
        "bindingCount": len(scoped),
        "bindings": [str(item.get("code") or "") for item in scoped],
        "taskKinds": sorted(
            {str(item.get("taskKind") or "") for item in scoped if item.get("taskKind")}
        ),
        "acceptsReferenceVideo": any(
            binding_accepts_input(item, "video") for item in scoped
        ),
        "acceptsReferenceAudio": any(
            binding_accepts_input(item, "audio") for item in scoped
        ),
        "acceptsReferenceImage": any(
            binding_accepts_input(item, "image") for item in scoped
        ),
        # 在线绑定的入参控件清单，进报错文案当证据（用户/运维要看「它到底吃什么」
        # 才知道参考素材为什么递不进去，而不是只看到一句「不支持」）。
        "inputUiTypes": sorted(
            {
                str(ui)
                for item in scoped
                for ui in (item.get("uiTypes") or [])
                if str(ui or "").strip()
            }
        ),
        # 入参名清单（逐条绑定的顺序保留，渠道给的名字就是最直接的证据）。
        "inputParams": [
            str(item.get("code") or "") + ": " + " / ".join(item.get("paramNames") or [])
            for item in scoped
            if item.get("paramNames")
        ],
    }


def video_binding_modes_for_model(
    bindings: list[dict[str, Any]],
    model: str,
) -> list[str]:
    """该模型在渠道**在线绑定**里确实备好的提交模式键（升序；关联不上返回空表）。

    [aipdd fork delta req44] 与 :func:`video_input_facts_for_model` 用同一套判定，只是
    方向相反：那条问「这个模式该走哪条绑定」，这条问「渠道今天为这个模型备了哪几种
    模式的绑定」。存在意义是把「按模型名猜」的模式限制换成渠道自述的事实 —— H3 的首尾帧
    页签过去被前端一句 ``isSeedance2VideoModel`` 藏掉，而渠道明明挂着一条
    ``first_last_frame_to_video`` 的在线绑定。

    这份证据**只用来放开入口，绝不参与收紧**：渠道今天没暴露能力接口、快照还没落、
    或把模型代理给第三方（自托管绑定清单里根本没有这个名字）都会返回空表，调用侧必须
    照常走原有判定 —— 「我们没读到绑定」读不出「渠道不支持」，把它当上限就是拿渠道
    接线的完备程度当用户的能力边界。
    """

    model_tokens = name_family_tokens(model)
    if not model_tokens or not bindings:
        return []
    modes: set[str] = set()
    for binding in bindings:
        if not isinstance(binding, dict):
            continue
        # 只认在线绑定：渠道摘下去（``available=False``）的工作流调度不到，它备着的
        # 模式不算这个模型今天能走通的路。
        if not binding.get("available", True):
            continue
        if not (
            _families_match(model_tokens, name_family_tokens(binding.get("code")))
            or _families_match(model_tokens, name_family_tokens(binding.get("name")))
        ):
            continue
        for mode_key in _MODE_TASK_KIND_TOKENS:
            if _binding_matches_mode(binding, mode_key):
                modes.add(mode_key)
    return sorted(modes)


def bindings_accepting_input(
    bindings: list[dict[str, Any]],
    kind: str,
    *,
    exclude_codes: set[str] | None = None,
) -> list[dict[str, Any]]:
    """某渠道在线绑定里确实吃这类参考素材的那些（报错时用来指出出路）。

    只返回 ``available`` 的；``exclude_codes`` 用来剔掉当前模型自己已经命中的那些，
    剩下的才是「换一条就能用」的真备选。绑定 code 原样带出，不做任何名字美化 ——
    这段文字是给运维对着渠道配置排查用的证据。
    """

    skip = {str(item or "") for item in (exclude_codes or set())}
    out: list[dict[str, Any]] = []
    for binding in bindings or []:
        if not isinstance(binding, dict):
            continue
        if not binding.get("available", True):
            continue
        code = str(binding.get("code") or "").strip()
        if not code or code in skip:
            continue
        if binding_accepts_input(binding, kind):
            out.append(binding)
    return out
