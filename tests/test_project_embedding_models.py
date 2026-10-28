from __future__ import annotations

import asyncio
import json

import pytest

from novelvideo import config
from novelvideo.embedding_models import (
    COGNEE_EMBEDDING_MODEL_LEGACY,
    COGNEE_EMBEDDING_MODEL_V1,
    COGNEE_EMBEDDING_MODEL_V2,
    PROJECT_EMBEDDING_DIMENSION_KEY,
    embedding_model_binding_for_new_project,
    embedding_model_for_legacy_project,
    embedding_model_for_new_project,
    embedding_gateway_credentials,
    embedding_model_scope,
    embedding_model_spec,
    require_current_embedding_model_spec,
)
from novelvideo.model_gateway_settings import (
    MODE_CUSTOM,
    MODE_OFFICIAL,
    save_custom_newapi_gateway,
    save_newapi_embedding_model_config,
    save_official_newapi_key,
    set_model_gateway_mode,
)
from novelvideo.project_config import (
    ensure_cognee_embedding_binding_in_state_dir,
    ensure_cognee_embedding_model_in_state_dir,
)


def _isolate_ce(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state-root"))
    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)
    # [aipdd fork delta req41] 官方模式地址只认专用 env：不把宿主 / .env 里可能存
    # 着的地址漏进用例，否则「没配就是没配」这件事测不出来。
    monkeypatch.delenv("OFFICIAL_LOCALAPI_BASE_URL", raising=False)


def test_new_and_legacy_official_projects_bind_v2_and_v1(monkeypatch, tmp_path):
    _isolate_ce(monkeypatch, tmp_path)
    set_model_gateway_mode(MODE_OFFICIAL)

    assert embedding_model_for_new_project() == COGNEE_EMBEDDING_MODEL_V2
    assert embedding_model_for_legacy_project() == COGNEE_EMBEDDING_MODEL_V1


def test_ce_custom_projects_bind_unversioned_model(monkeypatch, tmp_path):
    _isolate_ce(monkeypatch, tmp_path)
    save_custom_newapi_gateway(
        base_url="http://localapi:3000",
        api_key="sk-custom",
        activate=True,
    )
    # [aipdd fork delta req40] 维度不再有凭空默认值：先像真实用户那样绑好向量模型。
    save_newapi_embedding_model_config(
        provider="ali",
        upstream_model="Qwen3-Embedding-8B",
        dimension=1024,
    )

    assert embedding_model_for_new_project() == COGNEE_EMBEDDING_MODEL_LEGACY
    assert embedding_model_for_legacy_project() == COGNEE_EMBEDDING_MODEL_LEGACY


def test_ce_custom_new_project_snapshots_configured_dimensions(monkeypatch, tmp_path):
    _isolate_ce(monkeypatch, tmp_path)
    save_custom_newapi_gateway(
        base_url="http://localapi:3000",
        api_key="sk-custom",
        activate=True,
    )
    save_newapi_embedding_model_config(
        provider="ali",
        upstream_model="Qwen3-Embedding-8B",
        dimension=3072,
        send_dimensions=False,
    )

    binding = embedding_model_binding_for_new_project()

    assert binding.internal_model == COGNEE_EMBEDDING_MODEL_LEGACY
    assert binding.dimensions == 3072
    assert binding.send_dimensions is True


def test_project_model_keeps_its_gateway_after_active_mode_switch(monkeypatch, tmp_path):
    _isolate_ce(monkeypatch, tmp_path)
    # [aipdd fork delta req41] 官方栏的地址不再拿源码常量垫底：要它真的能用，
    # 就得由部署级 env（或数据库）提供。
    monkeypatch.setenv("OFFICIAL_LOCALAPI_BASE_URL", "https://official.test/v1")
    save_official_newapi_key(api_key="sk-official", activate=True)
    save_custom_newapi_gateway(
        base_url="http://localapi:3000",
        api_key="sk-custom",
        activate=True,
    )
    save_newapi_embedding_model_config(
        provider="ali",
        upstream_model="Qwen3-Embedding-8B",
        dimension=1024,
    )

    official = embedding_gateway_credentials(
        embedding_model_spec(COGNEE_EMBEDDING_MODEL_V1)
    )
    custom = embedding_gateway_credentials(
        embedding_model_spec(COGNEE_EMBEDDING_MODEL_LEGACY)
    )

    assert official == ("sk-official", "https://official.test/v1")
    assert custom == ("sk-custom", "http://localapi:3000/v1")


def test_ce_custom_dimension_comes_from_channel_catalog(monkeypatch, tmp_path):
    """[aipdd fork delta req40] 没填维度时读渠道目录声明，不再凭空按 1024 建库。"""

    _isolate_ce(monkeypatch, tmp_path)
    save_custom_newapi_gateway(
        base_url="http://localapi:3000",
        api_key="sk-custom",
        activate=True,
    )
    save_newapi_embedding_model_config(
        provider="ali",
        upstream_model="Qwen3-Embedding-8B",
        dimension=2048,
    )
    # 用户填过的维度是既有向量的尺寸，优先级高于目录声明。
    assert embedding_model_binding_for_new_project().dimensions == 2048

    from novelvideo.model_gateway_settings import save_upstream_models_snapshot

    save_upstream_models_snapshot(
        "ali",
        [
            {
                "name": "Qwen3-Embedding-0.6B",
                "kind": "embedding",
                "endpoints": ["/v1/embeddings"],
                "embedding_dimensions": 1024,
            }
        ],
    )
    save_newapi_embedding_model_config(
        provider="ali",
        upstream_model="Qwen3-Embedding-0.6B",
        dimension=1024,
    )
    assert embedding_model_binding_for_new_project().dimensions == 1024


def test_ce_custom_without_any_dimension_source_is_refused(monkeypatch, tmp_path):
    """[aipdd fork delta req40] 维度既没配置、目录也没声明 = 没绑向量模型，提交前拒。"""

    from novelvideo.model_slots import ModelSlotNotConfigured

    _isolate_ce(monkeypatch, tmp_path)
    save_custom_newapi_gateway(
        base_url="http://localapi:3000",
        api_key="sk-custom",
        activate=True,
    )

    with pytest.raises(ModelSlotNotConfigured) as caught:
        embedding_model_for_new_project()
    # 抛的是内部槽位键（供排错），给用户看的那行以中文功能名开头。
    assert caught.value.slot == "COGNEE_EMBEDDING_MODEL"
    assert caught.value.display_name == "向量模型"
    assert "未绑定可用模型" in caught.value.message
    assert "向量模型 · COGNEE_EMBEDDING_MODEL" in caught.value.message
    assert "设置 → 模型与渠道" in caught.value.message


def test_legacy_project_binding_is_backfilled_once(monkeypatch, tmp_path):
    _isolate_ce(monkeypatch, tmp_path)
    set_model_gateway_mode(MODE_OFFICIAL)
    state_dir = tmp_path / "project-state"
    state_dir.mkdir()
    config_path = state_dir / "project_config.json"
    config_path.write_text('{"visual_style":"cinematic"}', encoding="utf-8")

    assert (
        ensure_cognee_embedding_model_in_state_dir(state_dir)
        == COGNEE_EMBEDDING_MODEL_V1
    )
    persisted = json.loads(config_path.read_text(encoding="utf-8"))
    assert persisted == {
        "visual_style": "cinematic",
        "cognee_embedding_model": COGNEE_EMBEDDING_MODEL_V1,
        PROJECT_EMBEDDING_DIMENSION_KEY: 1024,
    }

    set_model_gateway_mode(MODE_CUSTOM)
    assert (
        ensure_cognee_embedding_model_in_state_dir(state_dir)
        == COGNEE_EMBEDDING_MODEL_V1
    )


@pytest.mark.parametrize(
    "payload",
    ["not-json", "[]", '{"cognee_embedding_model":"unknown"}'],
)
def test_invalid_project_embedding_config_fails_closed(
    monkeypatch,
    tmp_path,
    payload,
):
    _isolate_ce(monkeypatch, tmp_path)
    set_model_gateway_mode(MODE_OFFICIAL)
    state_dir = tmp_path / "project-state"
    state_dir.mkdir()
    (state_dir / "project_config.json").write_text(payload, encoding="utf-8")

    with pytest.raises(RuntimeError):
        ensure_cognee_embedding_model_in_state_dir(state_dir)


def test_fixed_official_model_specs():
    v1 = embedding_model_spec(COGNEE_EMBEDDING_MODEL_V1)
    v2 = embedding_model_spec(COGNEE_EMBEDDING_MODEL_V2)

    assert (v1.gateway, v1.dimensions, v1.send_dimensions) == (
        MODE_OFFICIAL,
        1024,
        True,
    )
    assert (v2.gateway, v2.dimensions, v2.send_dimensions) == (
        MODE_OFFICIAL,
        1024,
        True,
    )


def test_official_project_dimension_mismatch_fails_closed():
    with pytest.raises(RuntimeError, match="does not match"):
        embedding_model_spec(
            COGNEE_EMBEDDING_MODEL_V2,
            dimensions=3072,
        )


def test_ce_custom_spec_uses_project_dimensions_and_internal_send_policy(
    monkeypatch,
    tmp_path,
):
    _isolate_ce(monkeypatch, tmp_path)
    save_newapi_embedding_model_config(
        provider="ali",
        upstream_model="Qwen3-Embedding-8B",
        dimension=1024,
        send_dimensions=False,
    )

    spec = embedding_model_spec(
        COGNEE_EMBEDDING_MODEL_LEGACY,
        dimensions=3072,
    )

    assert spec.gateway == MODE_CUSTOM
    assert spec.dimensions == 3072
    assert spec.send_dimensions is True


def test_existing_custom_project_keeps_backfilled_1024_dimensions(
    monkeypatch,
    tmp_path,
):
    _isolate_ce(monkeypatch, tmp_path)
    save_custom_newapi_gateway(
        base_url="http://localapi:3000",
        api_key="sk-custom",
        activate=True,
    )
    save_newapi_embedding_model_config(
        provider="ali",
        upstream_model="Qwen3-Embedding-8B",
        dimension=3072,
    )
    state_dir = tmp_path / "project-state"
    state_dir.mkdir()
    config_path = state_dir / "project_config.json"
    config_path.write_text(
        json.dumps({"cognee_embedding_model": COGNEE_EMBEDDING_MODEL_LEGACY}),
        encoding="utf-8",
    )

    binding = ensure_cognee_embedding_binding_in_state_dir(state_dir)

    assert binding.dimensions == 1024
    assert json.loads(config_path.read_text(encoding="utf-8"))[
        PROJECT_EMBEDDING_DIMENSION_KEY
    ] == 1024


@pytest.mark.asyncio
async def test_embedding_model_scope_is_isolated_between_concurrent_tasks():
    ready = asyncio.Event()
    seen: list[tuple[str, str]] = []

    async def read_scoped(model: str) -> None:
        with embedding_model_scope(model):
            before = require_current_embedding_model_spec().internal_model
            ready.set()
            await asyncio.sleep(0)
            after = require_current_embedding_model_spec().internal_model
            seen.append((before, after))

    await asyncio.gather(
        read_scoped(COGNEE_EMBEDDING_MODEL_V1),
        read_scoped(COGNEE_EMBEDDING_MODEL_V2),
    )
    await ready.wait()

    assert sorted(seen) == [
        (COGNEE_EMBEDDING_MODEL_V1, COGNEE_EMBEDDING_MODEL_V1),
        (COGNEE_EMBEDDING_MODEL_V2, COGNEE_EMBEDDING_MODEL_V2),
    ]
    with pytest.raises(RuntimeError, match="no project model context"):
        require_current_embedding_model_spec()


@pytest.mark.asyncio
async def test_custom_project_dimensions_are_isolated_between_concurrent_tasks(
    monkeypatch,
    tmp_path,
):
    _isolate_ce(monkeypatch, tmp_path)

    async def read_scoped(dimensions: int) -> int:
        with embedding_model_scope(
            COGNEE_EMBEDDING_MODEL_LEGACY,
            dimensions=dimensions,
        ):
            await asyncio.sleep(0)
            return require_current_embedding_model_spec().dimensions

    first, second = await asyncio.gather(
        read_scoped(768),
        read_scoped(3072),
    )

    assert (first, second) == (768, 3072)


def test_cognee_vector_size_follows_project_scope(monkeypatch, tmp_path):
    _isolate_ce(monkeypatch, tmp_path)
    from cognee.infrastructure.databases.vector.embeddings.LiteLLMEmbeddingEngine import (
        LiteLLMEmbeddingEngine,
    )

    from novelvideo.cognee import config as cognee_config

    cognee_config._patch_cognee_embedding_gateway()
    engine = LiteLLMEmbeddingEngine.__new__(LiteLLMEmbeddingEngine)
    engine.dimensions = 1024

    with embedding_model_scope(
        COGNEE_EMBEDDING_MODEL_LEGACY,
        dimensions=3072,
    ):
        assert engine.get_vector_size() == 3072

    assert engine.get_vector_size() == 1024


def test_litellm_kwargs_follow_project_model(monkeypatch):
    from novelvideo.cognee import config as cognee_config

    monkeypatch.setattr(
        cognee_config,
        "embedding_gateway_credentials",
        lambda spec: (f"key-{spec.gateway}", f"https://{spec.gateway}.example/v1"),
    )

    with embedding_model_scope(COGNEE_EMBEDDING_MODEL_V1):
        v1 = cognee_config._project_embedding_request_kwargs(
            {
                "model": "wrong",
                "dimensions": 999,
                "allowed_openai_params": ["dimensions"],
            }
        )
    with embedding_model_scope(COGNEE_EMBEDDING_MODEL_V2):
        v2 = cognee_config._project_embedding_request_kwargs({"model": "wrong"})

    assert v1["model"] == "openai/DC-cognee-embedding-v1"
    assert v1["dimensions"] == 1024
    assert v1["allowed_openai_params"] == ["dimensions"]
    assert v1["api_base"] == "https://official.example/v1"
    assert v2["model"] == "openai/DC-cognee-embedding-v2"
    assert v2["dimensions"] == 1024
    assert v2["allowed_openai_params"] == ["dimensions"]
    assert v2["api_key"] == "key-official"

    from litellm.utils import get_optional_params_embeddings

    optional_params = get_optional_params_embeddings(
        model=v2["model"],
        dimensions=v2["dimensions"],
        custom_llm_provider=v2["custom_llm_provider"],
        allowed_openai_params=v2["allowed_openai_params"],
    )
    assert optional_params["dimensions"] == 1024


@pytest.mark.asyncio
async def test_concurrent_embedding_requests_do_not_cross_models(monkeypatch):
    from novelvideo.cognee import config as cognee_config

    monkeypatch.setattr(
        cognee_config,
        "embedding_gateway_credentials",
        lambda spec: ("key", f"https://{spec.internal_model}.example/v1"),
    )

    async def route(model: str) -> dict:
        with embedding_model_scope(model):
            await asyncio.sleep(0)
            return cognee_config._project_embedding_request_kwargs(
                {"model": "global-default", "dimensions": 999}
            )

    v1, v2 = await asyncio.gather(
        route(COGNEE_EMBEDDING_MODEL_V1),
        route(COGNEE_EMBEDDING_MODEL_V2),
    )

    assert v1["model"] == "openai/DC-cognee-embedding-v1"
    assert v1["dimensions"] == 1024
    assert v1["allowed_openai_params"] == ["dimensions"]
    assert v2["model"] == "openai/DC-cognee-embedding-v2"
    assert v2["dimensions"] == 1024
    assert v2["allowed_openai_params"] == ["dimensions"]


# [aipdd fork delta] WP-D: 嵌入这条链路只剩「校验 + 观测」两件事——维度不符要照常抛错，
# 且抛错时不能留下一次成功调用的记录；成功时补一条非拦截的模型调用日志。
@pytest.mark.asyncio
async def test_dimension_mismatch_raises_without_recording_model_call(monkeypatch):
    from novelvideo.cognee import config as cognee_config

    class UsageMeter:
        def __init__(self):
            self.bumps: list[dict] = []

        async def bump_model_call(self, **kwargs):
            self.bumps.append(kwargs)

    meter = UsageMeter()
    monkeypatch.setattr(cognee_config, "get_usage_meter", lambda: meter)

    async def wrong_dimensions():
        return [[0.0] * 4096]

    with embedding_model_scope(COGNEE_EMBEDDING_MODEL_V2):
        with pytest.raises(
            RuntimeError,
            match="Embedding dimension mismatch: expected 1024, received 4096",
        ):
            await cognee_config._run_project_embedding_observed(
                wrong_dimensions,
                expected_count=1,
            )

    assert meter.bumps == []


@pytest.mark.asyncio
async def test_successful_embedding_records_one_observation(monkeypatch):
    from novelvideo.cognee import config as cognee_config

    class UsageMeter:
        def __init__(self):
            self.calls: list[dict] = []

        async def bump_model_call(self, **kwargs):
            self.calls.append(kwargs)

        async def __getattr__(self, name):
            raise AssertionError(f"embedding must not touch removed credit API: {name}")

    meter = UsageMeter()
    monkeypatch.setattr(cognee_config, "get_usage_meter", lambda: meter)

    async def valid_embedding():
        return [[0.0] * 1024]

    with embedding_model_scope(COGNEE_EMBEDDING_MODEL_V1):
        result = await cognee_config._run_project_embedding_observed(
            valid_embedding,
            expected_count=1,
        )

    assert len(result[0]) == 1024
    assert len(meter.calls) == 1
    bump = meter.calls[0]
    assert bump["model"] == COGNEE_EMBEDDING_MODEL_V1
    assert bump["metadata"]["source"] == "cognee_embedding_gateway"
    # 观测面不得重新长出计费字段。
    assert not {"credit_reservation_id", "reservation_id", "cost"} & set(bump)
    assert not {"credit_reservation_id", "reservation_id", "cost"} & set(bump["metadata"])
