"""[aipdd fork delta] 分镜视频后端列表改走视频媒体模型目录（与画布同源）的契约测试。

覆盖 ``_api_video_backend_options_from_catalog``：可选模型 = 目录里的全部视频模型，
分辨率 / 比例 / 时长优先取目录声明，目录没声明时回落按家族名常量 / bounds，家族标记
与参考素材上限仍按名字派生，并保留 hidden mainline 过滤与去重。
"""

from novelvideo.api.routes.generation import (
    GROK_VIDEO_RATIO_OPTIONS,
    GROK_VIDEO_RESOLUTION_OPTIONS,
    GROK_VIDEO_SUPPORTED_MODES,
    VideoGenerateRequest,
    _api_video_backend_options_from_catalog,
)


def _dump(options):
    return {item.value: item.model_dump() for item in options}


def test_video_backend_options_prefer_catalog_declared_tiers():
    catalog = [
        {
            "apiModel": "newapi_seedance-2.0",
            "label": "AP Seedance-2.0 高性价比版",
            "resolutionOptions": ["720p", "1080p"],
            "ratioOptions": ["16:9", "9:16"],
            "minDuration": 4,
            "maxDuration": 15,
        },
    ]
    by_value = _dump(_api_video_backend_options_from_catalog(catalog))
    option = by_value["newapi_seedance-2.0"]
    # 目录声明压过任何按模型名写死的回落表。
    assert option["resolution_options"] == ["720p", "1080p"]
    assert option["ratio_options"] == ["16:9", "9:16"]
    assert option["min_duration"] == 4
    assert option["max_duration"] == 15
    assert option["label"] == "AP Seedance-2.0 高性价比版"
    assert option["is_seedance2"] is True
    # supported_modes 仍不由目录承载（词汇不同），非家族为 None。
    assert option["supported_modes"] is None


def test_video_backend_options_dedup_and_hide_mainline_variants():
    catalog = [
        {"apiModel": "newapi_seedance-2.0", "label": "std"},
        {"apiModel": "newapi_seedance-2.0", "label": "dup"},
        {"apiModel": "newapi_seedance-2.0-value", "label": "hidden-value"},
        {"apiModel": "newapi_seedance-2.0-fast-value", "label": "hidden-fast-value"},
        {"apiModel": "newapi_happyhorse-1.0", "label": "hidden-happyhorse"},
    ]
    options = _api_video_backend_options_from_catalog(catalog)
    values = [item.value for item in options]
    assert values == ["newapi_seedance-2.0"]


def test_video_backend_options_fall_back_to_family_constants_when_catalog_empty():
    # grok 目录条目没声明档位：回落按家族名的分辨率 / 比例 / 模式与时长 bounds (6, 30)。
    catalog = [{"apiModel": "newapi_grok-video-channel", "label": "Grok Video"}]
    option = _dump(_api_video_backend_options_from_catalog(catalog))[
        "newapi_grok-video-channel"
    ]
    assert option["is_grok_video"] is True
    assert option["resolution_options"] == list(GROK_VIDEO_RESOLUTION_OPTIONS)
    assert option["ratio_options"] == list(GROK_VIDEO_RATIO_OPTIONS)
    assert option["supported_modes"] == list(GROK_VIDEO_SUPPORTED_MODES)
    assert (option["min_duration"], option["max_duration"]) == (6, 30)
    assert option["reference_image_max"] == 7
    assert option["reference_video_max"] == 0
    assert option["reference_audio_max"] == 0


def test_video_backend_options_default_flag_tracks_request_default():
    default_value = VideoGenerateRequest().video_backend
    catalog = [
        {"apiModel": default_value, "label": "default"},
        {"apiModel": "newapi_seedance-2.0", "label": "other"},
    ]
    by_value = _dump(_api_video_backend_options_from_catalog(catalog))
    assert by_value[default_value]["is_default"] is True
    assert by_value["newapi_seedance-2.0"]["is_default"] is False


def test_video_backend_options_skips_entries_without_api_model():
    catalog = [
        {"label": "no value"},
        {"apiModel": "   ", "label": "blank"},
        {"apiModel": "newapi_seedance-2.0", "label": "ok"},
    ]
    options = _api_video_backend_options_from_catalog(catalog)
    assert [item.value for item in options] == ["newapi_seedance-2.0"]
