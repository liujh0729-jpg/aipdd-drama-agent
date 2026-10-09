"""[aipdd fork delta] 画布媒体模型目录（配置驱动）与分类真源的回归。

钉住三件事：
1. custom 模式的媒体目录只回放用户显式配置且启用的条目：网关实拉清单不参与
   构建，未配置的模型（含发行方内置名）绝不进入画布候选。
2. 目录条目自带分辨率 / 比例档位（没声明就补默认档位）；视频档位优先跟随网关
   ``aipdd.capabilities.media_metadata`` 权威声明，拉不到退回通用默认。
3. 分类真源 ``guess_model_kind`` / ``is_vision_capable`` 与设置页 ``guessMediaType``
   同口径（八分类 + 视觉候选），前端镜像用例
   frontend/src/__tests__/components/settings/model-classification.test.ts 互钉。
"""

from __future__ import annotations

import asyncio


from novelvideo.catalog import media_model_discovery
from novelvideo.api.routes import freezone as freezone_routes


def test_guess_media_type_matches_settings_dialog() -> None:
    assert media_model_discovery.guess_media_type("ap-jimeng-image-4.0") == "image"
    assert media_model_discovery.guess_media_type("ap-qwen-image-edit") == "image"
    assert media_model_discovery.guess_media_type("ap-minimax-h3") == "video"
    assert media_model_discovery.guess_media_type("ap-ltx-2.5") == "video"
    assert media_model_discovery.guess_media_type("ap-indextts-2") == "audio"
    assert media_model_discovery.guess_media_type("DC-some-LLM") == "text"
    # [aipdd fork delta req46] 音乐从 audio 拆出：拆不干净就会出现「音频默认模型」
    # 指向一个只能生音乐、不能配音的条目。
    assert media_model_discovery.guess_media_type("music-3.0") == "music"
    assert media_model_discovery.guess_media_type("ap-music-cover") == "music"
    assert media_model_discovery.guess_media_type("speech-2.8-hd") == "audio"


def test_default_image_capabilities_only_preset_quality_for_known_families() -> None:
    plain = media_model_discovery.default_image_capabilities("ap-jimeng-image-4.0")
    assert plain["resolutionOptions"]
    assert plain["ratioOptions"]
    # 没把握上游吃不吃 quality 的模型一律不预置画质：下发一个不支持的参数会被
    # 网关顶回来。
    assert "qualityOptions" not in plain

    gpt = media_model_discovery.default_image_capabilities("gpt-image-2")
    assert gpt["qualityOptions"] == ["low", "medium", "high"]


def test_apply_default_image_capabilities_fills_gaps_without_overriding() -> None:
    entries = [
        {"apiModel": "ap-image-2.0"},
        {
            "apiModel": "configured-image",
            "resolutionOptions": ["3K"],
            "ratioOptions": [],
            "qualityOptions": ["draft"],
        },
    ]

    media_model_discovery.apply_default_image_capabilities(entries)

    assert entries[0]["resolutionOptions"] == list(
        media_model_discovery.DEFAULT_IMAGE_RESOLUTION_OPTIONS
    )
    assert entries[0]["ratioOptions"]
    assert "qualityOptions" not in entries[0]
    assert entries[0]["request"]["endpoint"] == "images/generations"
    # 已声明的档位一律以配置为准，空列表按「没声明」处理。
    assert entries[1]["resolutionOptions"] == ["3K"]
    assert entries[1]["qualityOptions"] == ["draft"]
    assert entries[1]["ratioOptions"] == list(
        media_model_discovery.DEFAULT_IMAGE_RATIO_OPTIONS
    )


def test_custom_catalog_returns_only_configured_enabled_image_entries() -> None:
    # 新契约下网关实拉清单完全不参与构建：目录只回放用户在设置页显式配置且启用
    # 的条目。
    configured = [
        {
            "catalogId": "ap-image-2.0",
            "id": "ap-image-2.0",
            "apiModel": "ap-image-2.0",
            "label": "AP Image 2.0",
        },
        {
            "catalogId": "ap-agnes-image-2.1-flash",
            "id": "ap-agnes-image-2.1-flash",
            "apiModel": "ap-agnes-image-2.1-flash",
            "enabled": False,
        },
    ]

    catalog = asyncio.run(
        freezone_routes._custom_gateway_media_catalog("image", configured)
    )

    # 只保留启用条目；停用条目和未配置的内置 / 网关模型都不进目录。
    ids = {entry["catalogId"] for entry in catalog}
    assert ids == {"ap-image-2.0"}
    assert "LingShan-G2" not in ids
    assert "ap-jimeng-image-4.0" not in ids
    # 图片条目补齐可编辑的默认档位，画布参数下拉照常可用。
    assert catalog[0]["resolutionOptions"]
    assert catalog[0]["request"]["endpoint"] == "images/generations"


def test_custom_video_catalog_survives_gateway_capability_outage(
    monkeypatch,
) -> None:
    async def unavailable() -> dict[str, dict] | None:
        return None

    monkeypatch.setattr(
        media_model_discovery,
        "gateway_video_capabilities_by_name",
        unavailable,
    )

    configured = [
        {
            "catalogId": "ap-ltx-2.5",
            "id": "ap-ltx-2.5",
            "apiModel": "newapi_ap-ltx-2.5",
        }
    ]

    catalog = asyncio.run(
        freezone_routes._custom_gateway_media_catalog("video", configured)
    )

    # 网关能力声明拉不到时退回通用默认；绝不能把画布打成空列表。
    assert [entry["catalogId"] for entry in catalog] == ["ap-ltx-2.5"]
    assert catalog[0]["resolutionOptions"] == list(
        media_model_discovery.DEFAULT_VIDEO_RESOLUTION_OPTIONS
    )
    assert catalog[0]["request"]["endpoint"] == "video/generations"


def test_custom_catalog_never_auto_discovers_gateway_models() -> None:
    # 新契约下网关清单完全不参与目录构建：未配置的模型绝不出现（即便网关确实提供）。
    configured = [
        {
            "catalogId": "ap-image-2.0",
            "id": "ap-image-2.0",
            "apiModel": "ap-image-2.0",
        }
    ]

    catalog = asyncio.run(
        freezone_routes._custom_gateway_media_catalog("image", configured)
    )

    assert [entry["catalogId"] for entry in catalog] == ["ap-image-2.0"]


# [aipdd fork delta] 带 LLM 标记的工具模型不能被 video/image 关键词抢进媒体分类。
def test_guess_media_type_excludes_llm_tool_models() -> None:
    # 这些名字里分别含 seedance / video / image，旧逻辑会被抢判成媒体模型；
    # 它们是语言模型工具，必须归 text。
    assert media_model_discovery.guess_media_type("DC-seedance2-prompt-composer-LLM") == "text"
    assert media_model_discovery.guess_media_type("DC-video-identity-detector-LLM") == "text"
    assert media_model_discovery.guess_media_type("DC-image-caption-LLM") == "text"


def test_guess_model_kind_matches_settings_dialog_media_classes() -> None:
    """[aipdd fork delta] 八分类真源（与设置页 ``guessMediaType`` 完全同口径）。

    函数名不绑数字：分类面会随需求继续增长（req46 就加了 music）。
    同一组用例钉在前端镜像测试：
    frontend/src/__tests__/components/settings/model-classification.test.ts
    """
    assert media_model_discovery.guess_model_kind("DC-freezone-vision-LLM") == "vision"
    assert media_model_discovery.guess_model_kind("ap-qwen-vl-max") == "vision"
    assert media_model_discovery.guess_model_kind("ap-doubao-seed-2.1-turbo") == "text"
    assert media_model_discovery.guess_model_kind("DC-seedance2-prompt-composer-LLM") == "text"
    assert media_model_discovery.guess_model_kind("ap-jimeng-image-4.0") == "image"
    assert media_model_discovery.guess_model_kind("ap-minimax-h3") == "video"
    assert media_model_discovery.guess_model_kind("ap-indextts-2") == "audio"
    assert media_model_discovery.guess_model_kind("text-embedding-3") == "embedding"
    assert media_model_discovery.guess_model_kind("brand-new-unknown") == "other"
    # [aipdd fork delta req46] 音乐独立成类，且不得抢走 TTS / 歌词工具名。
    assert media_model_discovery.guess_model_kind("music-3.0") == "music"
    assert media_model_discovery.guess_model_kind("ap-music-cover") == "music"
    assert media_model_discovery.guess_model_kind("speech-2.8-hd") == "audio"


def test_is_vision_capable_matches_settings_dialog() -> None:
    # doubao-seed 多模态系列算视觉候选；doubao-seedream（生成类）不被误吞。
    assert media_model_discovery.is_vision_capable("ap-doubao-seed-2.1-turbo") is True
    assert media_model_discovery.is_vision_capable("ap-doubao-seedream-4.0") is False
    assert media_model_discovery.is_vision_capable("ap-qwen-vl-max") is True
    assert media_model_discovery.is_vision_capable("plain-text") is False


def test_is_vision_candidate_name_excludes_embeddings() -> None:
    """[aipdd fork delta] 「视觉理解」下拉资格 = 视觉能力位 ∧ 非向量模型。

    现场事实：方舟清单里的 ``doubao-embedding-vision-*`` 是多模态 embedding，名字
    里的 vision 让 ``guess_model_kind`` 先命中视觉分支（那边顺序是「视觉 → LLM →
    媒体关键词」，绕过 embedding 判定），于是它们成了视觉下拉里只剩的全部候选。
    """

    predicate = media_model_discovery.is_vision_candidate_name
    # embedding 一律不当视觉理解候选，哪怕名字带 vision、哪怕显式声明 vision。
    assert predicate("doubao-embedding-vision-250615") is False
    assert predicate("doubao-embedding-vision-250615", "vision") is False
    assert predicate("text-embedding-3") is False
    assert predicate("ap-bge-m3") is False
    # 真视觉模型（能力位口径）保留，包括 seed 多模态系列 —— 只按
    # ``guess_model_kind == "vision"`` 筛会把它们全部漏掉，下拉会直接空掉。
    assert predicate("ap-qwen-vl-max") is True
    assert predicate("doubao-seed-1-6-vision-250815") is True
    assert predicate("ap-doubao-seed-2.1-turbo") is True
    # 显式声明优先于名字：声明别的就不当视觉。
    assert predicate("anything", "vision") is True
    assert predicate("anything", "video") is False
    assert predicate("anything", "embedding") is False
    assert predicate("") is False
    # 生成类模型不被误吞（seedream 是图片生成，不是视觉理解）。
    assert predicate("ap-doubao-seedream-4.0") is False


def test_default_video_capabilities_presets_editable_tiers() -> None:
    caps = media_model_discovery.default_video_capabilities("ap-ltx-2.5")
    assert caps["resolutionOptions"] == list(
        media_model_discovery.DEFAULT_VIDEO_RESOLUTION_OPTIONS
    )
    assert caps["ratioOptions"]
    assert caps["minDuration"] == media_model_discovery.DEFAULT_VIDEO_MIN_DURATION
    assert caps["maxDuration"] == media_model_discovery.DEFAULT_VIDEO_MAX_DURATION
    assert caps["request"]["endpoint"] == "video/generations"
    # 不预置 supportedModes / qualityOptions：猜错会让上游拒单，交给设置页显式声明。
    assert "supportedModes" not in caps

    assert "qualityOptions" not in caps


def test_apply_default_video_capabilities_fills_gaps_without_overriding() -> None:
    entries = [
        {"apiModel": "newapi_ap-ltx-2.5"},
        {
            "apiModel": "configured-video",
            "resolutionOptions": ["4K"],
            "ratioOptions": [],
            "minDuration": 2,
            "maxDuration": -1,
        },
    ]

    media_model_discovery.apply_default_video_capabilities(entries)

    assert entries[0]["resolutionOptions"] == list(
        media_model_discovery.DEFAULT_VIDEO_RESOLUTION_OPTIONS
    )
    assert entries[0]["ratioOptions"]
    assert entries[0]["minDuration"] == media_model_discovery.DEFAULT_VIDEO_MIN_DURATION
    assert entries[0]["request"]["endpoint"] == "video/generations"
    # 已声明的以配置为准；空比例列表补默认；非法时长（负数）补默认，正数保留。
    assert entries[1]["resolutionOptions"] == ["4K"]
    assert entries[1]["ratioOptions"] == list(
        media_model_discovery.DEFAULT_VIDEO_RATIO_OPTIONS
    )
    assert entries[1]["minDuration"] == 2
    assert entries[1]["maxDuration"] == media_model_discovery.DEFAULT_VIDEO_MAX_DURATION


def test_custom_video_catalog_presets_tiers_for_configured_entries() -> None:
    configured = [
        {
            "catalogId": "ap-ltx-2.5",
            "id": "ap-ltx-2.5",
            "apiModel": "newapi_ap-ltx-2.5",
        }
    ]

    catalog = asyncio.run(
        freezone_routes._custom_gateway_media_catalog("video", configured)
    )

    # 未配置的模型（含 LLM 工具模型）不会混进目录；配置条目预置可编辑档位。
    ids = {entry["catalogId"] for entry in catalog}
    assert ids == {"ap-ltx-2.5"}
    assert "DC-seedance2-prompt-composer-LLM" not in ids
    entry = catalog[0]
    assert entry["apiModel"] == "newapi_ap-ltx-2.5"
    assert entry["resolutionOptions"]
    assert entry["ratioOptions"]
    assert entry["minDuration"] and entry["maxDuration"]


# ---------------------------------------------------------------------------
# [aipdd fork delta] vres「真·跟随网关声明」：视频档位以网关 /v1/models 的
# aipdd.capabilities.media_metadata 为权威，不再一律套 480p/720p/1080p。
# ---------------------------------------------------------------------------


# 已核实的 AIPDD /v1/models 单条目形状（ap-minimax-h3 只有 768p、没有 720p）。
AIPDD_H3_ITEM = {
    "id": "ap-minimax-h3",
    "aipdd": {
        "display_name": "MiniMax H3",
        "kind": "video",
        "capabilities": {
            "media_metadata": {
                "resolutions": ["480p", "768p", "1080p"],
                "ratios": ["16:9", "9:16", "1:1"],
                "minDurationSeconds": 1,
                "maxDurationSeconds": 10,
            }
        },
    },
}


def test_extract_gateway_media_capabilities_parses_aipdd_shape() -> None:
    caps = media_model_discovery._extract_gateway_media_capabilities(AIPDD_H3_ITEM)
    assert caps is not None
    assert caps["resolutionOptions"] == ["480p", "768p", "1080p"]
    assert caps["ratioOptions"] == ["16:9", "9:16", "1:1"]
    assert caps["minDuration"] == 1
    assert caps["maxDuration"] == 10


def test_extract_gateway_media_capabilities_ignores_models_without_caps() -> None:
    # 没有任何能力元数据的裸名字条目不该被误判出档位。
    assert (
        media_model_discovery._extract_gateway_media_capabilities({"id": "gpt-x"})
        is None
    )


# 真实 AIPDD 条目（req43 时点，2026-10-07 实拉）：除分辨率外还权威声明了多参数参考的
# 引用输入上限（ap-minimax-h3 实测：maxImageInputs=9 / maxAudioInputs=3），参考视频那时
# **只给了布尔 true、没写数量**。
AIPDD_H3_ITEM_WITH_REFS = {
    "id": "ap-minimax-h3",
    "aipdd": {
        "kind": "video",
        "capabilities": {
            "media_metadata": {
                "resolutions": ["480p", "768p", "1080p"],
                "ratios": ["16:9", "9:16", "1:1"],
                "minDurationSeconds": 1,
                "maxDurationSeconds": 15,
                "maxImageInputs": 9,
                "minImageInputs": 0,
                "maxAudioInputs": 3,
                "minAudioInputs": 0,
                "supportsReferenceAudio": True,
                "supportsReferenceVideo": True,
            }
        },
    },
}


def test_extract_gateway_media_capabilities_parses_reference_inputs() -> None:
    caps = media_model_discovery._extract_gateway_media_capabilities(
        AIPDD_H3_ITEM_WITH_REFS
    )
    assert caps["referenceImageMax"] == 9
    assert caps["referenceAudioMax"] == 3
    # 网关用布尔表达视频参考、没给数量：不臆造 referenceVideoMax，留给 omni 端点默认。
    # [req46] 这个布尔也不参与判定（它在 2026-10-08 已被实测翻成 false）：要说「不支持」
    # 请把数量写成 0，只给布尔就按「渠道没说」处理。
    assert "referenceVideoMax" not in caps
    # 仍然不猜 supportedModes（引用上限是数量、不是模式猜测）。
    assert "supportedModes" not in caps


# 2026-10-08 复拉同一条目（实拉实况）：布尔翻成 false，并**显式写了** ``maxVideoInputs: 0``
# 与混投总闸 ``maxReferenceInputs: 12``。带外探针递 ``video_urls`` 收到的就是「当前 MiniMax
# H3 渠道不支持视频参考；本次请求未扣费」——这个 0 是确定的事实，必须透得下去。
AIPDD_H3_ITEM_VIDEO_ZERO = {
    "id": "ap-minimax-h3",
    "aipdd": {
        "kind": "video",
        "capabilities": {
            "media_metadata": {
                "supportedResolutions": ["480p", "768p", "1080p"],
                "maxImageInputs": 9,
                "maxAudioInputs": 3,
                "maxVideoInputs": 0,
                "maxReferenceInputs": 12,
                "supportsReferenceAudio": True,
                "supportsReferenceVideo": False,
            }
        },
    },
}


def test_declared_zero_video_cap_reaches_downstream_but_boolean_alone_does_not() -> None:
    """[req46] 「明说 0」与「没写」必须是两件事，且只以**数量**为凭。

    为什么这么分：拿布尔归零会把一个会骗人的声明烧成硬门禁 —— ``referenceVideoMax``
    下游不只门控参考视频，video_gen 的 edit / extend 一度还拿它门控**源视频**槽（本轮
    审查 H-3）。反过来把 0 当成「没说」，就等于把一条已确定的不支持推回未申报：
    前端照常让人挂参考视频，提交时才撞上上游拒单。
    """

    caps = media_model_discovery._extract_gateway_media_capabilities(
        AIPDD_H3_ITEM_VIDEO_ZERO
    )
    assert caps["referenceVideoMax"] == 0
    assert caps["referenceImageMax"] == 9
    assert caps["referenceAudioMax"] == 3

    # 只有布尔 false、没写数量：不表态（不写这个键），与上面那条显式 0 区分开。
    boolean_only = media_model_discovery._extract_gateway_media_capabilities(
        {
            "id": "ap-some-video",
            "aipdd": {
                "capabilities": {
                    "media_metadata": {"maxImageInputs": 9, "supportsReferenceVideo": False}
                }
            },
        }
    )
    assert boolean_only is not None
    assert "referenceVideoMax" not in boolean_only
    assert boolean_only["referenceImageMax"] == 9

    # 图 / 音频的 0 仍不透：它们的顶层 0 会按档位变化（H3 的 ``text`` 档位图槽就是 0，
    # 整模却支持 9 图），拿它当硬边界会误关图生视频。视频那侧那么确定是因为今日探针
    # 拿到了上游自己回的不扣费拒单。
    class_zero = media_model_discovery._extract_gateway_media_capabilities(
        {
            "id": "ap-some-video",
            "aipdd": {
                "capabilities": {
                    "media_metadata": {
                        "maxImageInputs": 0,
                        "maxAudioInputs": 0,
                        "maxVideoInputs": 2,
                    }
                }
            },
        }
    )
    assert class_zero is not None
    assert "referenceImageMax" not in class_zero
    assert "referenceAudioMax" not in class_zero
    assert class_zero["referenceVideoMax"] == 2


def test_apply_default_video_capabilities_keeps_declared_zero() -> None:
    """[req46] 补空那一跳不能把申报的 0 又折成「没声明」。

    ``apply_default_video_capabilities`` 对三个上限都只补空不覆盖，用的尺子不一样：
    图 / 音频用 ``_positive_int``（0 算空），视频用 :func:`_declared_video_ref_cap`（0 算声明）。
    用户手填过 0 的条目也要能原样留住，否则前端拿不到「这类递不进去」这条已确定的事实。
    """

    defaults = media_model_discovery.default_video_capabilities(
        "ap-minimax-h3",
        gateway_caps=media_model_discovery._extract_gateway_media_capabilities(
            AIPDD_H3_ITEM_VIDEO_ZERO
        ),
    )
    assert defaults["referenceVideoMax"] == 0

    entries = media_model_discovery.apply_default_video_capabilities(
        [{"apiModel": "ap-minimax-h3", "mediaType": "video"}],
        {"ap-minimax-h3": defaults},
    )
    assert entries[0]["referenceVideoMax"] == 0
    assert entries[0]["referenceImageMax"] == 9
    # 用户已经自己配过的值不被网关声明覆盖（只补空）。
    kept = media_model_discovery.apply_default_video_capabilities(
        [{"apiModel": "ap-minimax-h3", "mediaType": "video", "referenceVideoMax": 2}],
        {"ap-minimax-h3": defaults},
    )
    assert kept[0]["referenceVideoMax"] == 2


# 线上实况（2026-10-08 实拉 aipdd ``/v1/models``）：**同一个渠道里参考图数量有两种写法**
# —— ``ap-minimax-h3`` 写 ``maxImageInputs: 9``，``ap-ltx-2.5`` 写 ``maxInputImages: 1``。
# 别名表在 ``upstream_catalog._MEDIA_COUNT_ALIASES``，只收实测到的拼法。
AIPDD_LTX_ITEM = {
    "id": "ap-ltx-2.5",
    "aipdd": {
        "kind": "video",
        "available": True,
        "capabilities": {
            "media_metadata": {
                "minDurationSeconds": 1,
                "maxDurationSeconds": 20,
                "maxInputImages": 1,
            }
        },
    },
}


def test_gateway_reference_image_cap_reads_both_declared_spellings() -> None:
    """[req44] 只认一种拼法会把「就一个图槽」读成「渠道没说」。

    读不到的后果不是不拦，而是**静默丢素材**：首尾帧两张图一路发到上游，在只有一个
    「首帧图片」节点的绑定上被默默截成一张，片子照收计费。发现层与提交两侧共用同一
    张别名表，不会出现「目录读到了、拼装又读不到」。
    """

    from novelvideo.catalog.upstream_catalog import coerce_entry, declared_media_count, media_metadata

    caps = media_model_discovery._extract_gateway_media_capabilities(AIPDD_LTX_ITEM)
    assert caps["referenceImageMax"] == 1
    assert caps["maxDuration"] == 20

    entry = coerce_entry(AIPDD_LTX_ITEM) or {}
    meta = media_metadata(entry)
    assert declared_media_count(meta, "maxImageInputs") == 1
    # 规范键补上、原键位不删：调用方按哪个名字读都拿到同一份事实。
    assert meta["maxImageInputs"] == 1 and meta["maxInputImages"] == 1
    # 渠道没写的数量仍是 ``None``（不表态），与「明说 0」不同。
    assert declared_media_count(meta, "maxVideoInputs") is None
    assert declared_media_count({}, "maxImageInputs") is None


# [aipdd fork delta] req08：NewAPI 系网关的 /api/pricing 逐模型声明「已发布分辨率」
# （task_pricing_resolutions / task_pricing.by_resolution），提交时强校验——不在
# 列表内 = unsupported_resolution 拒单（实测 AP Seedance-2.0 高性价比版只发布
# 1080p/4k，UI 传 720p 被顶回）。这份声明要接进能力发现，供目录 / 提交链路吸附。
NEWAPI_PRICING_PAYLOAD = {
    "success": True,
    "data": [
        {
            "model_name": "AP Seedance-2.0 高性价比版",
            "billing_mode": "task_pricing",
            "supported_endpoint_types": ["openai-video"],
            "task_pricing_resolutions": ["1080p", "4k"],
            "task_pricing": {"by_resolution": {"1080p": {}, "4k": {}}},
        },
        {
            "model_name": "AP Seedance-2.0 轻量版",
            "task_pricing": {"by_resolution": {"480p": {}, "720p": {}}},
        },
        {
            # 与 /v1/models 的权威声明冲突：模型元数据应优先（收集器用例钉住）。
            "model_name": "ap-minimax-h3",
            "task_pricing_resolutions": ["1080p", "4k"],
        },
        {"model_name": "gpt-image-1", "quota_type": 0},
        {"model_name": ""},
    ],
}


def test_extract_pricing_capabilities_reads_declared_and_by_resolution() -> None:
    caps = media_model_discovery._extract_pricing_capabilities(
        NEWAPI_PRICING_PAYLOAD
    )
    assert caps["AP Seedance-2.0 高性价比版"]["resolutionOptions"] == ["1080p", "4k"]
    # task_pricing_resolutions 缺失时回落到 by_resolution 的档位键。
    assert caps["AP Seedance-2.0 轻量版"]["resolutionOptions"] == ["480p", "720p"]
    # 没声明分辨率的条目（文本 / 图片定价）不进结果。
    assert "gpt-image-1" not in caps


def test_gateway_pricing_url_only_for_versioned_v1_root() -> None:
    assert (
        media_model_discovery._gateway_pricing_url("https://susciyuan.com/v1")
        == "https://susciyuan.com/api/pricing"
    )
    # Ark 系 /api/v3 根没有 NewAPI 的定价接口，不尝试。
    assert (
        media_model_discovery._gateway_pricing_url("https://ark.example.com/api/v3")
        == ""
    )


def test_collector_merges_pricing_resolutions_fill_only(monkeypatch) -> None:
    import sys
    import types

    from novelvideo import model_gateway_settings

    class _FakeResponse:
        def __init__(self, payload, status_code=200):
            self._payload = payload
            self.status_code = status_code

        def json(self):
            return self._payload

    class _FakeClient:
        def __init__(self, timeout=None):
            self.timeout = timeout

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

        def get(self, url, headers=None):
            if url.endswith("/api/pricing"):
                return _FakeResponse(NEWAPI_PRICING_PAYLOAD)
            return _FakeResponse(
                {
                    "data": [
                        # 模型元数据声明了档位时是权威值，定价声明不得覆盖。
                        {
                            "id": "ap-minimax-h3",
                            "aipdd": {
                                "capabilities": {
                                    "media_metadata": {
                                        "resolutions": ["480p", "768p", "1080p"]
                                    }
                                }
                            },
                        },
                        {"id": "AP Seedance-2.0 高性价比版"},
                    ]
                }
            )

    monkeypatch.setitem(
        sys.modules, "httpx", types.SimpleNamespace(Client=_FakeClient)
    )
    monkeypatch.setattr(
        model_gateway_settings,
        "get_newapi_provider_channels",
        lambda: [
            {
                "provider": "newapi",
                "baseUrl": "https://relay.example.com/v1",
                "upstreamKey": "sk-x",
            }
        ],
    )

    caps = media_model_discovery._collect_gateway_capabilities_sync()
    # 定价声明补进「已发布分辨率」，让目录 / 提交链路吸附到 1080p。
    assert caps["AP Seedance-2.0 高性价比版"]["resolutionOptions"] == ["1080p", "4k"]
    # /v1/models 的权威声明优先，定价不得覆盖。
    assert caps["ap-minimax-h3"]["resolutionOptions"] == ["480p", "768p", "1080p"]


def test_default_video_capabilities_carries_reference_limits() -> None:
    caps = media_model_discovery.default_video_capabilities(
        "ap-minimax-h3",
        gateway_caps={"referenceImageMax": 9, "referenceAudioMax": 3},
    )
    assert caps["referenceImageMax"] == 9
    assert caps["referenceAudioMax"] == 3
    assert "supportedModes" not in caps


def test_default_video_capabilities_omits_zero_reference_limits() -> None:
    # 网关声明 0（没有该类输入）时不写键，交由 omni 端点按模式默认兜底。
    caps = media_model_discovery.default_video_capabilities(
        "plain-video",
        gateway_caps={"referenceAudioMax": 0},
    )
    assert "referenceAudioMax" not in caps


def test_apply_default_video_capabilities_fills_reference_limits_without_overriding() -> None:
    gateway = {
        "ap-minimax-h3": {"referenceImageMax": 9, "referenceAudioMax": 3},
        "configured-video": {"referenceAudioMax": 5},
    }
    entries = [
        {"apiModel": "newapi_ap-minimax-h3", "gatewayModel": "ap-minimax-h3"},
        # 用户在设置页显式配过 referenceImageMax 的保留；没配的 referenceAudioMax 按网关补。
        {
            "apiModel": "configured-video",
            "gatewayModel": "configured-video",
            "referenceImageMax": 4,
        },
    ]

    media_model_discovery.apply_default_video_capabilities(
        entries, gateway_capabilities=gateway
    )

    assert entries[0]["referenceImageMax"] == 9
    assert entries[0]["referenceAudioMax"] == 3
    # 已声明的 referenceImageMax=4 保留；referenceAudioMax 补网关声明的 5。
    assert entries[1]["referenceImageMax"] == 4
    assert entries[1]["referenceAudioMax"] == 5


# [req44] 渠道在线绑定反出的模式证据以**只加不减**的咨询字段 ``bindingModes`` 下发。
_H3_FL_BINDING = {
    "code": "aipdd_minimax_h3_first_last_frame_speed_test",
    "name": "MiniMax H3 首尾帧 Speed",
    "endpointType": "openai-video",
    "taskKind": "first_last_frame_to_video",
    "inputModalities": ["text", "image"],
    "params": [{"uiType": "image_url", "paramKey": "image"}],
    "available": True,
}


def _patch_h3_bindings(monkeypatch) -> None:
    from novelvideo import model_gateway_settings
    from novelvideo.catalog.upstream_capabilities import summarize_video_bindings

    bindings = summarize_video_bindings([_H3_FL_BINDING])
    monkeypatch.setattr(
        model_gateway_settings,
        "get_provider_capability_bindings",
        lambda provider: bindings if provider == "aipdd" else [],
    )
    monkeypatch.setattr(
        model_gateway_settings,
        "model_owner_providers",
        lambda name: ["aipdd"] if name == "ap-minimax-h3" else [],
    )


def test_apply_video_binding_mode_evidence_stamps_only_backed_entries(monkeypatch) -> None:
    """[req44] 有绑定证据才写 ``bindingModes``；关联不上的条目不写字段（不表态）。

    帧类三种模式（图生视频 / 首帧 / 首尾帧）按路由口径共用渠道那一条
    ``first_last_frame_to_video`` 绑定，所以反查出来就是三个键。
    关键边界：本函数**绝不写 ``supportedModes``** —— 那是双刃声明（后端
    ``_require_catalog_video_mode`` 一见到非空就严校验），把绑定并集写进去会
    拿渠道接线的完备程度当用户的能力边界，反而关掉 H3 的图片参考 / 视频编辑页签。
    """

    _patch_h3_bindings(monkeypatch)
    entries = [
        {"catalogId": "ap-minimax-h3", "apiModel": "newapi_ap-minimax-h3"},
        # 渠道把模型代理给第三方：自托管绑定清单里没这个名字，读不出「没能力」。
        {"catalogId": "proxied-model", "apiModel": "newapi_proxied-model"},
    ]

    media_model_discovery.apply_video_binding_mode_evidence(entries)

    assert entries[0]["bindingModes"] == [
        "first_frame",
        "first_last_frame",
        "image_to_video",
    ]
    assert "supportedModes" not in entries[0]
    assert "bindingModes" not in entries[1]
    assert "supportedModes" not in entries[1]


def test_custom_video_catalog_publishes_binding_modes(monkeypatch) -> None:
    """[req44] 出参面接上：``/freezone/video/models`` 的条目带上绑定模式证据。

    前端画布的首尾帧页签过去只按模型名猜（非 Seedance 2.0 一律藏掉），拿不到这份
    证据就永远看不见渠道明明接了绑定的入口 —— 所以必须钉住目录真的把它发出去。
    """

    async def fake_gateway_caps(*_args, **_kwargs):
        return {}

    monkeypatch.setattr(
        media_model_discovery, "gateway_video_capabilities_by_name", fake_gateway_caps
    )
    _patch_h3_bindings(monkeypatch)
    configured = [
        {"catalogId": "ap-minimax-h3", "id": "ap-minimax-h3", "apiModel": "newapi_ap-minimax-h3"},
        {"catalogId": "proxied-model", "id": "proxied-model", "apiModel": "newapi_proxied-model"},
    ]

    catalog = asyncio.run(freezone_routes._custom_gateway_media_catalog("video", configured))
    h3 = next(item for item in catalog if item["catalogId"] == "ap-minimax-h3")
    assert "first_last_frame" in h3["bindingModes"]
    proxied = next(item for item in catalog if item["catalogId"] == "proxied-model")
    assert "bindingModes" not in proxied


def test_default_video_capabilities_follows_gateway_declaration() -> None:
    caps = media_model_discovery.default_video_capabilities(
        "ap-minimax-h3",
        gateway_caps={"resolutionOptions": ["480p", "768p", "1080p"]},
    )
    # 分辨率严格跟随网关声明：拿不到 720p。
    assert caps["resolutionOptions"] == ["480p", "768p", "1080p"]
    assert "720p" not in caps["resolutionOptions"]
    # 网关没声明的字段（比例 / 时长）退回通用默认。
    assert caps["ratioOptions"] == list(
        media_model_discovery.DEFAULT_VIDEO_RATIO_OPTIONS
    )
    assert caps["minDuration"] == media_model_discovery.DEFAULT_VIDEO_MIN_DURATION


def test_gateway_caps_fetch_is_best_effort(monkeypatch) -> None:
    import novelvideo.model_gateway_settings as model_gateway_settings

    def boom() -> list[dict]:
        raise RuntimeError("settings unavailable")

    monkeypatch.setattr(
        model_gateway_settings, "get_newapi_provider_channels", boom
    )
    media_model_discovery.reset_gateway_caps_cache()
    caps = asyncio.run(media_model_discovery.gateway_video_capabilities_by_name())
    assert caps == {}


def test_gateway_caps_serves_last_good_on_transient_failure(monkeypatch) -> None:
    # [aipdd fork delta] stale-while-revalidate：成功拉过一次后，偶发超时/失败
    # 应沿用上次的好结果（768p），不能回落成空 dict（会让画布回到错误的 720p）。
    import time

    good = {"ap-minimax-h3": {"resolutionOptions": ["480p", "768p", "1080p"]}}
    calls = {"n": 0}

    def fake_collect() -> dict:
        calls["n"] += 1
        if calls["n"] == 1:
            return good
        raise RuntimeError("transient gateway failure")

    monkeypatch.setattr(
        media_model_discovery, "_collect_gateway_capabilities_sync", fake_collect
    )
    media_model_discovery.reset_gateway_caps_cache()
    assert asyncio.run(media_model_discovery.gateway_video_capabilities_by_name()) == good

    # 让上次的好结果过 TTL、并解除重试节流 -> 下次会重试且报错。
    media_model_discovery._gateway_caps_cache = (time.monotonic() - 1e9, good)
    media_model_discovery._gateway_caps_next_attempt = 0.0
    assert asyncio.run(media_model_discovery.gateway_video_capabilities_by_name()) == good


def test_custom_video_catalog_follows_gateway_declaration(
    monkeypatch,
) -> None:
    async def fake_gateway_caps() -> dict[str, dict]:
        return {
            "ap-minimax-h3": {
                "resolutionOptions": ["480p", "768p", "1080p"],
                "ratioOptions": ["16:9", "9:16", "1:1"],
                "minDuration": 1,
                "maxDuration": 10,
            }
        }

    monkeypatch.setattr(
        media_model_discovery,
        "gateway_video_capabilities_by_name",
        fake_gateway_caps,
    )

    configured = [
        {
            "catalogId": "ap-minimax-h3",
            "id": "ap-minimax-h3",
            "apiModel": "newapi_ap-minimax-h3",
        },
        {
            "catalogId": "ap-ltx-2.5",
            "id": "ap-ltx-2.5",
            "apiModel": "newapi_ap-ltx-2.5",
        },
    ]

    catalog = asyncio.run(
        freezone_routes._custom_gateway_media_catalog("video", configured)
    )
    h3 = next(e for e in catalog if e["catalogId"] == "ap-minimax-h3")
    # 网关声明的 768p 补上空档位，错误的 720p 默认被完全替掉。
    assert h3["resolutionOptions"] == ["480p", "768p", "1080p"]
    assert "720p" not in h3["resolutionOptions"]
    assert h3["maxDuration"] == 10
    # 没被网关声明覆盖的配置条目仍拿通用默认档位。
    ltx = next(e for e in catalog if e["catalogId"] == "ap-ltx-2.5")
    assert ltx["resolutionOptions"] == list(
        media_model_discovery.DEFAULT_VIDEO_RESOLUTION_OPTIONS
    )


def test_apply_default_video_caps_prefers_gateway_declaration_for_configured_gap() -> None:
    # 回归：用户在设置页建了映射但没填档位（生产里 ap-minimax-h3 就是这样）。
    # 网关声明必须补上这份空档位（给 768p），而不是落到通用默认 720p。
    entries = [
        {"catalogId": "ap-minimax-h3", "apiModel": "newapi_ap-minimax-h3"},
        {"catalogId": "other-vid", "apiModel": "newapi_other-vid"},
    ]
    caps = {
        "ap-minimax-h3": {
            "resolutionOptions": ["480p", "768p", "1080p"],
            "ratioOptions": ["16:9", "9:16"],
        }
    }

    media_model_discovery.apply_default_video_capabilities(entries, gateway_capabilities=caps)

    assert entries[0]["resolutionOptions"] == ["480p", "768p", "1080p"]
    assert "720p" not in entries[0]["resolutionOptions"]
    assert entries[0]["ratioOptions"] == ["16:9", "9:16"]
    # 网关没声明的模型仍退回通用默认。
    assert entries[1]["resolutionOptions"] == list(
        media_model_discovery.DEFAULT_VIDEO_RESOLUTION_OPTIONS
    )


def test_apply_default_video_caps_does_not_override_explicit_user_choice() -> None:
    # 用户显式填过的档位优先，网关声明不得覆盖。
    entries = [
        {
            "catalogId": "ap-minimax-h3",
            "apiModel": "newapi_ap-minimax-h3",
            "resolutionOptions": ["1080p"],
        }
    ]
    caps = {"ap-minimax-h3": {"resolutionOptions": ["480p", "768p", "1080p"]}}

    media_model_discovery.apply_default_video_capabilities(entries, gateway_capabilities=caps)

    assert entries[0]["resolutionOptions"] == ["1080p"]


def test_new_model_needs_only_configuration_end_to_end(monkeypatch) -> None:
    """[aipdd fork delta] 新模型进入架构只需「配置」，不改任何代码。

    渠道新增模型的全链：分类真源按名字直接归类（八分类）→ 用户在设置页保存映射后
    默认解析命中 → 画布目录回放该条目并补默认档位。
    """
    import novelvideo.model_gateway_settings as model_gateway_settings

    new_model = "ap-flux-9-turbo"  # 代码库中从未出现过的名字

    # ① 分类真源：无需登记任何名单。
    assert media_model_discovery.guess_model_kind(new_model) == "image"

    # ② 用户配置映射（设置页「默认生成模型」保存的形态）后，默认解析直接命中。
    monkeypatch.setattr(
        model_gateway_settings,
        "get_newapi_media_model_mappings",
        lambda: {new_model: {"mediaType": "image", "enabled": True, "sortOrder": 0}},
    )
    assert model_gateway_settings.resolve_default_media_model("image") == new_model

    # ③ 画布目录回放该条目（用户配置即目录），并补上可编辑的默认档位。
    configured = [{"catalogId": new_model, "id": new_model, "apiModel": new_model}]
    catalog = asyncio.run(
        freezone_routes._custom_gateway_media_catalog("image", configured)
    )
    assert [entry["catalogId"] for entry in catalog] == [new_model]
    assert catalog[0]["resolutionOptions"]
    assert catalog[0]["request"]["endpoint"] == "images/generations"
