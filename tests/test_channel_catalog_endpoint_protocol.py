# [aipdd fork delta req41] 视频协议判定改口径的回归：先看渠道目录声明，再看 host。
#
# AIPDD / 火山这类「渠道定了端点就定了」的上游会在目录里给自己的模型写明
# ``videos`` / ``videos/{taskId}``，所以「走不走 /v1/videos」是上游事实，不再由代码
# 拿 host 猜；只有目录压根没声明端点（自建 newapi 网关清单只有名字）时才回 host 兜底。
from __future__ import annotations

from novelvideo.generators.video_generator import NewApiVideoGenerator as G

AIPDD_BASE = "https://api.aipdd.work/v1"


def _seed_catalog(monkeypatch, tmp_path, entries, *, provider: str = "aipdd") -> None:
    """造出「这个渠道的目录拉到过」这件事：CE settings.db 里写该渠道的快照。"""
    from novelvideo import config
    from novelvideo.model_gateway_settings import save_upstream_models_snapshot

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)
    monkeypatch.delenv("AIPDD_VIDEO_API_HOST_HINT", raising=False)
    save_upstream_models_snapshot(provider, entries, base_url=AIPDD_BASE)


def test_videos_protocol_follows_catalog_declaration(monkeypatch, tmp_path) -> None:
    _seed_catalog(
        monkeypatch,
        tmp_path,
        [
            {
                "name": "vid-declared",
                "mediaType": "video",
                "endpoints": ["videos", "videos/{taskId}", "videos/{taskId}/content"],
            }
        ],
    )
    # 声明了 videos：哪怕 host 完全不像 aipdd 也按目录走该协议。
    assert G._uses_aipdd_videos_api("https://gw.partner.example/v1", "vid-declared") is True
    # 复合名（{provider}::{model}）一样查得到。
    assert G._uses_aipdd_videos_api("https://gw.partner.example/v1", "aipdd::vid-declared") is True


def test_videos_protocol_respects_declared_generic_endpoint(monkeypatch, tmp_path) -> None:
    """目录写明是 ``video/generations`` 时，host 像 aipdd 也不切到 /videos。"""
    _seed_catalog(
        monkeypatch,
        tmp_path,
        [{"name": "vid-generic", "mediaType": "video", "endpoints": ["video/generations"]}],
    )
    assert G._uses_aipdd_videos_api(AIPDD_BASE, "vid-generic") is False


def test_videos_protocol_falls_back_to_host_when_catalog_silent(monkeypatch, tmp_path) -> None:
    """目录只有名字（没带元数据）= 目录没说话，才按 host 子串兜底。"""
    _seed_catalog(monkeypatch, tmp_path, [{"name": "vid-silent", "mediaType": "video"}])
    assert G._uses_aipdd_videos_api(AIPDD_BASE, "vid-silent") is True
    assert G._uses_aipdd_videos_api("https://susciyuan.example/v1", "vid-silent") is False
    # 模型不在目录里同理：回 host 子串。
    assert G._uses_aipdd_videos_api(AIPDD_BASE, "not-in-catalog") is True
    assert G._uses_aipdd_videos_api("https://gw.partner.example/v1", "not-in-catalog") is False
    # host 提示可改：改成别的子串后，aipdd 这个 host 就不再命中。
    monkeypatch.setenv("AIPDD_VIDEO_API_HOST_HINT", "partner")
    assert G._uses_aipdd_videos_api(AIPDD_BASE, "not-in-catalog") is False
    assert G._uses_aipdd_videos_api("https://gw.partner.example/v1", "not-in-catalog") is True


def test_agent_gateway_videos_follow_catalog(monkeypatch, tmp_path) -> None:
    """``agent/gateway`` 与生成器后端同一口径（两处协议开关不能各判各的）。"""
    from novelvideo.agent import gateway

    _seed_catalog(
        monkeypatch,
        tmp_path,
        [{"name": "vid-declared", "mediaType": "video", "endpoints": ["videos", "videos/{taskId}"]}],
    )
    assert gateway._uses_aipdd_videos_api("https://gw.partner.example/v1", "vid-declared") is True
    assert gateway._uses_aipdd_videos_api("https://gw.partner.example/v1") is False


def test_indextts_async_task_follows_catalog_declaration(monkeypatch, tmp_path) -> None:
    """音频侧：上游声明了 ``audio/speech/{task_id}`` 这类任务子路径才是异步协议。"""
    from novelvideo import config
    from novelvideo.generators import indextts2
    from novelvideo.model_gateway_settings import save_upstream_models_snapshot

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)
    monkeypatch.delenv("AIPDD_AUDIO_API_HOST_HINT", raising=False)

    save_upstream_models_snapshot(
        "aipdd",
        [
            {
                "name": "tts-task",
                "mediaType": "audio",
                "endpoints": ["audio/speech", "audio/speech/{task_id}"],
            },
            {"name": "tts-sync", "mediaType": "audio", "endpoints": ["audio/speech"]},
            {"name": "tts-silent", "mediaType": "audio"},
        ],
        base_url=AIPDD_BASE,
    )
    # 有任务轮询子路径 → 异步；只声明 audio/speech → 同步（host 像 aipdd 也不切）。
    assert indextts2._uses_aipdd_audio_api("https://gw.partner.example/v1", "tts-task") is True
    assert indextts2._uses_aipdd_audio_api(AIPDD_BASE, "tts-sync") is False
    # 目录没说话才回 host 兜底（原行为）。
    assert indextts2._uses_aipdd_audio_api(AIPDD_BASE, "tts-silent") is True
    assert indextts2._uses_aipdd_audio_api("https://susciyuan.example/v1", "tts-silent") is False


def test_task_lookup_endpoint_picks_declared_status_path() -> None:
    """轮询路径从目录里拿：拿带路径参数的那条，不拿取成品的 ``/content``。"""
    from novelvideo.upstream_catalog import task_lookup_endpoint

    entry = {
        "endpoints": ["videos", "videos/{taskId}", "videos/{taskId}/content"],
    }
    assert task_lookup_endpoint(entry, "videos") == "videos/<TASK_ID>"
    # 占位符写法不限（上游也可能写 task_id / :id）。
    assert task_lookup_endpoint({"endpoints": ["videos", "videos/{task_id}"]}, "videos") == (
        "videos/<TASK_ID>"
    )
    # 只声明了绝对 URL 也归一后再比对。
    assert task_lookup_endpoint(
        {"endpoints": ["https://x/v1/audio/speech", "https://x/v1/audio/speech/{id}"]},
        "audio/speech",
    ) == "audio/speech/<TASK_ID>"
    # 没声明回查子路径：回空串，由调用方自己拼。
    assert task_lookup_endpoint({"endpoints": ["videos"]}, "videos") == ""
    assert task_lookup_endpoint({}, "videos") == ""


def test_catalog_endpoint_path_prefers_exact_and_skips_placeholders(monkeypatch, tmp_path) -> None:
    """同一个 ``audio/speech`` 前缀下有提交端点和轮询子路径：只能拿前者。"""
    from novelvideo.model_gateway_settings import catalog_endpoint_path
    from novelvideo.upstream_catalog import EP_AUDIO_SPEECH, EP_IMAGES_EDITS

    _seed_catalog(
        monkeypatch,
        tmp_path,
        [
            {
                "name": "tts-task",
                "mediaType": "audio",
                "endpoints": ["audio/speech", "audio/speech/{task_id}"],
            },
            {
                "name": "img-both",
                "mediaType": "image",
                "endpoints": ["images/generations", "images/edits"],
            },
            {"name": "vid-silent", "mediaType": "video"},
        ],
    )
    assert catalog_endpoint_path("tts-task", EP_AUDIO_SPEECH) == "audio/speech"
    assert catalog_endpoint_path("img-both", EP_IMAGES_EDITS) == "images/edits"
    # 图生图没声明就不能逆推（ap-jimeng-image-4.0 只开了文生图）。
    assert catalog_endpoint_path("img-both", "images/unknown") == ""
    # 目录没说话 = 回空串，调用方自己保留通用 OpenAI 路径。
    assert catalog_endpoint_path("vid-silent", EP_AUDIO_SPEECH) == ""
    assert catalog_endpoint_path("not-in-catalog", EP_AUDIO_SPEECH) == ""
    assert catalog_endpoint_path("", EP_AUDIO_SPEECH) == ""


def test_audio_speech_suffix_never_submits_to_task_placeholder(monkeypatch, tmp_path) -> None:
    """indextts2 拼提交地址时不能拿着 ``audio/speech/{task_id}`` 去 POST。"""
    from novelvideo.generators import indextts2

    _seed_catalog(
        monkeypatch,
        tmp_path,
        [
            {
                "name": "tts-task",
                "mediaType": "audio",
                "endpoints": ["audio/speech", "audio/speech/{task_id}"],
            },
            {"name": "tts-silent", "mediaType": "audio"},
        ],
    )
    assert indextts2._audio_speech_suffix("tts-task") == "audio/speech"
    assert indextts2._audio_speech_suffix("tts-silent") == "audio/speech"
    assert indextts2._audio_speech_suffix("") == "audio/speech"


def test_videos_request_paths_take_declared_submit_and_lookup(monkeypatch, tmp_path) -> None:
    """提交与轮询两条路径都照目录；目录没说话时行为跟旧版逐字一致。"""
    from novelvideo.model_gateway_settings import videos_request_paths

    _seed_catalog(
        monkeypatch,
        tmp_path,
        [
            {
                "name": "vid-declared",
                "mediaType": "video",
                "endpoints": ["videos", "videos/{taskId}", "videos/{taskId}/content"],
            },
            {"name": "vid-silent", "mediaType": "video"},
        ],
    )
    assert videos_request_paths("vid-declared", True) == ("videos", "/videos/")
    # 复合名（passthrough 卡上的 ``{provider}::{model}``）同样查得到。
    assert videos_request_paths("aipdd::vid-declared", True) == ("videos", "/videos/")
    # 目录没声明端点：仍按 EP_VIDEOS 提交，轮询前缀回 ``{submit}/``。
    assert videos_request_paths("vid-silent", True) == ("videos", "/videos/")
    # 没命中异步协议：通用 NewAPI 路径，与旧实现逐字相同。
    assert videos_request_paths("vid-declared", False) == (
        "video/generations",
        "/video/generations/",
    )
