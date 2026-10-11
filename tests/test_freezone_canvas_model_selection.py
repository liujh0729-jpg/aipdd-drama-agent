"""[aipdd fork delta] 画布组件按类型选模型的后端契约回归。

覆盖音频（speech）与文本（story-script）两条链路的四条不变量：

1. ``GET /projects/{p}/freezone/audio/models`` 透传后台 audio 目录，且
   空目录是合法状态（没有可硬编码的默认列表，面板展示「默认引擎」即可）。
2. speech 传了 model 就必须命中 audio 目录，未命中在**入队之前**定型为 409，
   命中则把目录里的 newapi 内部名（apiModel）写进 payload。
3. ``GET /model-gateway/text-models`` 拍平清单（零远程）：候选 = 默认文本模型
   首项 + 每个渠道名下真名的复合名 ``{provider}::{model}``；DC- 内部功能位、
   媒体目录登记名、媒体/视觉分类不呈现；按大小写无关去重；渠道配置读取失败
   只回默认。
4. story-script 对非白名单模型不再拒绝，透传为 ``explicit`` 选择，且 Agent
   按解析后的模型 id 分键缓存（不再命中全局单例）。
5. 「默认图」那个请求体默认值是**选择键**（``newapi_gpt_image2``）而不是模型名：入队前的
   目录门禁必须先把它换成面板默认图位（其次才是目录自己的第一条），不让它去比目录 id。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from novelvideo.api.routes import freezone as freezone_routes
from novelvideo.api.routes import model_gateway as model_gateway_routes
from novelvideo.api.schemas import FreezoneAudioSpeechRequest, FreezoneAudioVoiceRef


def _ctx() -> SimpleNamespace:
    return SimpleNamespace(
        project_id="proj_freezone",
        project_name="demo",
        owner_username="admin",
        requester_user_id="owner_1",
        requester_username="admin",
    )


def _patch_resolve(monkeypatch: pytest.MonkeyPatch, project_dir: Path) -> None:
    async def fake_resolve(*_args, **_kwargs):
        return _ctx(), "admin", "demo", project_dir, str(project_dir)

    monkeypatch.setattr(freezone_routes, "_resolve_freezone_project", fake_resolve)


# ---------------------------------------------------------------------------
# audio/models catalog endpoint
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_freezone_audio_models_passes_through_backend_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_resolve(monkeypatch, tmp_path / "project")
    catalog = [
        {
            "catalogId": "tts-1",
            "id": "tts-1",
            "providerId": "newapi",
            "apiModel": "tts-1",
            "label": "TTS One",
        }
    ]

    seen: dict[str, object] = {}

    async def fake_catalog(media_type: str, *, requester_user_id: str):
        seen["media_type"] = media_type
        seen["requester_user_id"] = requester_user_id
        return catalog

    monkeypatch.setattr(freezone_routes, "_scoped_media_model_catalog", fake_catalog)

    result = await freezone_routes.freezone_audio_models(
        project="proj_freezone", user={"username": "admin"}
    )

    assert result == {"ok": True, "data": catalog}
    assert seen == {"media_type": "audio", "requester_user_id": "owner_1"}


@pytest.mark.asyncio
async def test_freezone_audio_models_allows_empty_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No hard-coded fallback for audio: an empty catalog is a legal success."""
    _patch_resolve(monkeypatch, tmp_path / "project")

    async def no_catalog(_media_type: str, *, requester_user_id: str):
        return None

    monkeypatch.setattr(freezone_routes, "_scoped_media_model_catalog", no_catalog)

    result = await freezone_routes.freezone_audio_models(
        project="proj_freezone", user={"username": "admin"}
    )
    assert result == {"ok": True, "data": []}


# ---------------------------------------------------------------------------
# speech model validation (409 before enqueue; apiModel into payload on hit)
# ---------------------------------------------------------------------------


def _speech_body(model: str | None) -> FreezoneAudioSpeechRequest:
    body: dict[str, object] = {
        "text": "旁白响起。",
        "voice_ref": FreezoneAudioVoiceRef(scope="character_default", character_name="小明"),
    }
    # model 为非 Optional str：未选时直接省略 key，走后端默认引擎。
    if model is not None:
        body["model"] = model
    return FreezoneAudioSpeechRequest(**body)


@pytest.mark.asyncio
async def test_freezone_audio_speech_rejects_model_absent_from_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fastapi import HTTPException

    _patch_resolve(monkeypatch, tmp_path / "project")

    async def catalog_without_ghost(_media_type: str, *, requester_user_id: str):
        return [{"catalogId": "tts-1", "id": "tts-1", "apiModel": "tts-1"}]

    monkeypatch.setattr(
        freezone_routes, "_scoped_media_model_catalog", catalog_without_ghost
    )

    # Store creation must never happen: the guard fires before the enqueue side.
    def _boom(*_a, **_k):
        raise AssertionError("store should not open for a rejected model")

    monkeypatch.setattr(freezone_routes, "make_sqlite_store_for_context", _boom)

    with pytest.raises(HTTPException) as excinfo:
        await freezone_routes.freezone_audio_speech(
            project="proj_freezone",
            body=_speech_body("ghost-model"),
            user={"username": "bob"},
        )
    assert excinfo.value.status_code == 409


@pytest.mark.asyncio
async def test_freezone_audio_speech_resolves_selected_model_to_api_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_resolve(monkeypatch, tmp_path / "project")

    async def catalog(_media_type: str, *, requester_user_id: str):
        return [
            {
                "catalogId": "tts-1",
                "id": "tts-1",
                "apiModel": "newapi-tts-internal",
            }
        ]

    class _FakeStore:
        async def close(self) -> None:
            return None

    async def fake_store(_ctx):
        return _FakeStore()

    async def fake_voice(**_kwargs):
        return "first_person", SimpleNamespace(source="character_default")

    async def fake_projection(**_kwargs):
        return {}

    payloads: list[dict] = []

    async def fake_enqueue(**kwargs):
        payloads.append(kwargs["payload"])
        return {"ok": True, "data": {"task_type": kwargs["task_type"]}}

    monkeypatch.setattr(freezone_routes, "_scoped_media_model_catalog", catalog)
    monkeypatch.setattr(freezone_routes, "make_sqlite_store_for_context", fake_store)
    monkeypatch.setattr(freezone_routes, "resolve_speech_voice", fake_voice)
    monkeypatch.setattr(freezone_routes, "_task_projection_payload", fake_projection)
    monkeypatch.setattr(
        freezone_routes, "_enqueue_freezone_background_job", fake_enqueue
    )

    await freezone_routes.freezone_audio_speech(
        project="proj_freezone",
        body=_speech_body("tts-1"),
        user={"username": "bob"},
    )
    assert payloads[-1]["model"] == "newapi-tts-internal"


@pytest.mark.asyncio
async def test_freezone_audio_speech_without_model_keeps_empty_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No model selected → payload model stays '' so the default engine path runs."""
    _patch_resolve(monkeypatch, tmp_path / "project")

    def _catalog_should_not_be_read(*_a, **_k):
        raise AssertionError("catalog must not be consulted when no model is sent")

    class _FakeStore:
        async def close(self) -> None:
            return None

    async def fake_store(_ctx):
        return _FakeStore()

    async def fake_voice(**_kwargs):
        return "first_person", SimpleNamespace(source="character_default")

    async def fake_projection(**_kwargs):
        return {}

    payloads: list[dict] = []

    async def fake_enqueue(**kwargs):
        payloads.append(kwargs["payload"])
        return {"ok": True, "data": {"task_type": kwargs["task_type"]}}

    monkeypatch.setattr(
        freezone_routes,
        "_scoped_media_model_catalog",
        _catalog_should_not_be_read,
    )
    monkeypatch.setattr(freezone_routes, "make_sqlite_store_for_context", fake_store)
    monkeypatch.setattr(freezone_routes, "resolve_speech_voice", fake_voice)
    monkeypatch.setattr(freezone_routes, "_task_projection_payload", fake_projection)
    monkeypatch.setattr(
        freezone_routes, "_enqueue_freezone_background_job", fake_enqueue
    )

    await freezone_routes.freezone_audio_speech(
        project="proj_freezone",
        body=_speech_body(None),
        user={"username": "bob"},
    )
    assert payloads[-1]["model"] == ""


# ---------------------------------------------------------------------------
# text-models aggregation endpoint
# ---------------------------------------------------------------------------


@pytest.fixture
def clear_text_cache():
    previous = model_gateway_routes._text_models_cache
    model_gateway_routes._text_models_cache = None
    yield
    model_gateway_routes._text_models_cache = previous


@pytest.mark.asyncio
async def test_text_models_puts_default_first_and_excludes_internal_and_media(
    monkeypatch: pytest.MonkeyPatch, clear_text_cache
) -> None:
    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.get_effective_default_text_model",
        lambda: "aipdd::ap-glm-5.2",
    )
    # DC- 前缀是内部功能位命名空间、img-model 是媒体登记名：都不该出现在候选里；
    # 拍平后候选 = 「渠道::模型」复合名并列，默认项与清单同条去重且留在首位。
    monkeypatch.setattr(
        model_gateway_routes,
        "get_newapi_provider_channels",
        lambda: [{"provider": "aipdd"}, {"provider": "openai"}],
    )
    monkeypatch.setattr(
        "novelvideo.newapi_provisioner.upstream_passthrough_models",
        lambda provider, **_k: {
            "aipdd": ["ap-glm-5.2", "DC-hermes-LLM", "img-model"],
            "openai": ["ap-glm-5.2"],
        }[provider],
    )
    # Mirrors the real ``_non_text_model_names``: media names plus the fixed
    # embedding internal name are excluded from the text candidates.
    monkeypatch.setattr(
        model_gateway_routes,
        "_non_text_model_names",
        lambda: {"img-model", "dc-cognee-embedding"},
    )

    result = await model_gateway_routes.list_text_model_options()

    ids = [entry["id"] for entry in result["data"]]
    # 默认项排首；aipdd 清单里的同条去重；openai 同名模型是另一条（拍平并列）。
    assert ids == ["aipdd::ap-glm-5.2", "openai::ap-glm-5.2"]
    assert result["data"][0] == {
        "id": "aipdd::ap-glm-5.2",
        "providerId": "newapi",
        "provider": "newapi",
        "channel": "aipdd",
        "apiModel": "aipdd::ap-glm-5.2",
        "api_model": "aipdd::ap-glm-5.2",
        "label": "aipdd::ap-glm-5.2",
        # [aipdd fork delta] 当前默认文本模型带 isDefault 标记（工具页展示具体默认名）。
        "isDefault": True,
    }


@pytest.mark.asyncio
async def test_text_models_annotates_flattened_channel(
    monkeypatch: pytest.MonkeyPatch, clear_text_cache
) -> None:
    """[aipdd fork delta] 拍平：候选渠道 = 复合名前缀；同名跨渠道并列各一条。"""
    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.get_effective_default_text_model",
        lambda: "",
    )
    monkeypatch.setattr(
        model_gateway_routes,
        "get_newapi_provider_channels",
        lambda: [{"provider": "aipdd"}, {"provider": "openai"}],
    )
    monkeypatch.setattr(
        "novelvideo.newapi_provisioner.upstream_passthrough_models",
        lambda provider, **_k: {
            "aipdd": ["ap-glm-5.2"],
            "openai": ["ap-glm-5.2", "ap-deepseek-v4-pro"],
        }[provider],
    )
    monkeypatch.setattr(
        model_gateway_routes, "_non_text_model_names", lambda: set()
    )

    result = await model_gateway_routes.list_text_model_options()

    channels = {entry["id"]: entry["channel"] for entry in result["data"]}
    assert channels == {
        "aipdd::ap-glm-5.2": "aipdd",
        "openai::ap-glm-5.2": "openai",
        "openai::ap-deepseek-v4-pro": "openai",
    }
    # providerId 保持执行身份不随展示渠道漂移。
    assert {entry["providerId"] for entry in result["data"]} == {"newapi"}


@pytest.mark.asyncio
async def test_text_models_keeps_only_selectable_kinds(
    monkeypatch: pytest.MonkeyPatch, clear_text_cache
) -> None:
    """DC- 命名空间与媒体 / 视觉分类（分类真源）全部排除；text 保留。"""
    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.get_effective_default_text_model",
        lambda: "",
    )
    monkeypatch.setattr(
        model_gateway_routes,
        "get_newapi_provider_channels",
        lambda: [{"provider": "aipdd"}],
    )
    monkeypatch.setattr(
        "novelvideo.newapi_provisioner.upstream_passthrough_models",
        lambda provider, **_k: [
            "ap-glm-5.2",  # text：保留
            "DC-hermes-LLM",  # 内部功能位：排除
            "doubao-vision",  # vision 分类：排除
            "jimeng-4-0",  # image 分类：排除
            "doubao-seedance-1-0",  # video 分类：排除
        ],
    )
    monkeypatch.setattr(
        model_gateway_routes, "_non_text_model_names", lambda: set()
    )

    result = await model_gateway_routes.list_text_model_options()

    assert [entry["id"] for entry in result["data"]] == ["aipdd::ap-glm-5.2"]


@pytest.mark.asyncio
async def test_text_models_reads_snapshot_only_without_remote(
    monkeypatch: pytest.MonkeyPatch, clear_text_cache
) -> None:
    """[aipdd fork delta] 拍平候选零远程：只读本地快照 + 配置声明，不拉上游清单。"""
    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.get_effective_default_text_model",
        lambda: "",
    )
    monkeypatch.setattr(
        model_gateway_routes,
        "get_newapi_provider_channels",
        lambda: [{"provider": "aipdd"}],
    )
    seen: list[dict] = []

    def fake_passthrough(provider, **kwargs):
        seen.append({"provider": provider, **kwargs})
        return ["ap-glm-5.2"]

    monkeypatch.setattr(
        "novelvideo.newapi_provisioner.upstream_passthrough_models", fake_passthrough
    )
    monkeypatch.setattr(
        model_gateway_routes, "_non_text_model_names", lambda: set()
    )

    result = await model_gateway_routes.list_text_model_options()

    assert [entry["id"] for entry in result["data"]] == ["aipdd::ap-glm-5.2"]
    assert seen == [{"provider": "aipdd", "from_snapshot": True}]


@pytest.mark.asyncio
async def test_text_models_serves_default_only_when_channels_unavailable(
    monkeypatch: pytest.MonkeyPatch, clear_text_cache
) -> None:
    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.get_effective_default_text_model",
        lambda: "aipdd::ap-glm-5.2",
    )

    def _raise():
        raise RuntimeError("channels unavailable")

    monkeypatch.setattr(
        model_gateway_routes, "get_newapi_provider_channels", _raise
    )

    result = await model_gateway_routes.list_text_model_options()

    assert [entry["id"] for entry in result["data"]] == ["aipdd::ap-glm-5.2"]


@pytest.mark.asyncio
async def test_text_models_never_leaks_dc_default_text(
    monkeypatch: pytest.MonkeyPatch, clear_text_cache
) -> None:
    """默认文本位即使被配成 DC- 内部名，也不该漏进用户候选。"""
    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.get_effective_default_text_model",
        lambda: "DC-hermes-LLM",
    )
    monkeypatch.setattr(
        model_gateway_routes, "get_newapi_provider_channels", lambda: []
    )

    result = await model_gateway_routes.list_text_model_options()

    assert result["data"] == []


# ---------------------------------------------------------------------------
# story-script resolve + per-model agent cache
# ---------------------------------------------------------------------------


def test_story_script_resolve_falls_back_to_default_when_empty() -> None:
    from novelvideo.freezone.text_node import (
        FREEZONE_STORY_SCRIPT_MODEL,
        resolve_freezone_story_script_model,
    )

    assert resolve_freezone_story_script_model(None) == dict(FREEZONE_STORY_SCRIPT_MODEL)
    assert (
        resolve_freezone_story_script_model(
            FREEZONE_STORY_SCRIPT_MODEL["id"]
        )
        == dict(FREEZONE_STORY_SCRIPT_MODEL)
    )


def test_story_script_resolve_accepts_arbitrary_model_as_explicit() -> None:
    from novelvideo.freezone.text_node import resolve_freezone_story_script_model

    resolved = resolve_freezone_story_script_model("DC-deepseek-v3")
    assert resolved == {
        "id": "DC-deepseek-v3",
        "provider": "newapi",
        "model": "DC-deepseek-v3",
        "label": "DC-deepseek-v3",
        "explicit": "1",
    }


def test_story_script_agent_cached_per_model_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import novelvideo.freezone.text_node as text_node

    text_node._story_script_agents.clear()
    created: list[str] = []

    class _FakeAgent:
        def __init__(self, *_a, **_k):
            pass

    # Non-organization scope so the shared cache branch is exercised.
    monkeypatch.setattr(text_node, "current_model_gateway_context", lambda: None)
    monkeypatch.setattr(text_node, "Agent", _FakeAgent)
    real_create = text_node.create_freezone_story_script_agent

    def spy_create(model=None):
        created.append(str(model))
        return real_create(model)

    monkeypatch.setattr(text_node, "create_freezone_story_script_agent", spy_create)
    monkeypatch.setattr(
        "novelvideo.config.get_newapi_text_pydantic_model",
        lambda *a, **k: object(),
    )
    monkeypatch.setattr(
        "novelvideo.config.get_newapi_structured_output_model_settings",
        lambda: None,
    )

    first = text_node.get_freezone_story_script_agent("model-a")
    again = text_node.get_freezone_story_script_agent("model-a")
    second = text_node.get_freezone_story_script_agent("model-b")

    assert first is again  # cache hit on same id
    assert second is not first  # distinct id → distinct agent
    assert created == ["model-a", "model-b"]
    text_node._story_script_agents.clear()


def test_default_image_selection_key_resolves_before_the_catalog_gate(monkeypatch):
    """「默认图」选择键进门禁前先换成真名，不要拿一个键去比目录 id。

    [aipdd fork delta wave2] 官方目录下线 Acme 一族后，再也没人拿 aliases 兜住这个
    键：用户什么都没改，路由却回 409「该媒体模型未对当前组织开放、已停用或不存在」。
    现在先问「设置 → 模型与渠道」的主/副位，面板空着就退到目录自己的第一条。
    """
    import novelvideo.model_gateway_settings as mgs
    from novelvideo.api.routes.freezone.catalog import (
        _default_image_selection_slot,
        _normalize_requested_media_model,
    )

    catalog = [
        {
            "catalogId": "seedream-4.5",
            "id": "seedream-4.5",
            "apiModel": "seedream-4.5",
            "gatewayModel": "seedream-4.5",
        }
    ]
    monkeypatch.setattr(mgs, "get_effective_default_image_model", lambda: "ap-gpt-image-2")
    monkeypatch.setattr(mgs, "get_effective_default_nanobanana_model", lambda: "ap-nanobanana-2")

    # 主位 / 副位各自对号，旧项目里存的 huimeng_gpt_image2 / nanobanana 这类别名键同口径。
    assert _default_image_selection_slot("newapi_gpt_image2") == "primary"
    assert _default_image_selection_slot("huimeng_gpt_image2") == "primary"
    assert _default_image_selection_slot("nanobanana") == "secondary"
    assert _default_image_selection_slot("seedream-4.5") == ""
    assert _normalize_requested_media_model("image", "newapi_gpt_image2", catalog) == "ap-gpt-image-2"
    assert _normalize_requested_media_model("image", "huimeng_gpt_image2", catalog) == "ap-gpt-image-2"
    assert _normalize_requested_media_model("image", "nanobanana", catalog) == "ap-nanobanana-2"

    # 面板没配 → 退到目录自己的第一条；目录也空 → 空串（由调用点说「当前没有可用的图片模型」）。
    monkeypatch.setattr(mgs, "get_effective_default_image_model", lambda: "")
    assert _normalize_requested_media_model("image", "newapi_gpt_image2", catalog) == "seedream-4.5"
    assert _normalize_requested_media_model("image", "newapi_gpt_image2", []) == ""

    # 用户显式点名的模型原样进门禁：不在目录里照旧 409，这条防线不放宽。
    assert (
        _normalize_requested_media_model("image", "someone-elses-model", catalog)
        == "someone-elses-model"
    )
    # 非图片类型不参与这次归一。
    assert (
        _normalize_requested_media_model("video", "newapi_gpt_image2", catalog)
        == "newapi_gpt_image2"
    )
