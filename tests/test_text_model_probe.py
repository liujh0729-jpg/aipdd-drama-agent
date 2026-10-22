"""req：上游模型「是否真开通」的最小实测与候选目录联动。

现场事实（api 容器实测）：火山方舟 ``GET /models`` 在本地快照里给了 135 个名字，
抽 24 个直连渠道实测只有 4 个能调通，其余全部 404
``InvalidEndpointOrModel.NotFound`` —— 清单是平台全量目录，不是本账号的可用清单。
所以候选目录必须按实测三态收敛，而实测本身绝不能反过来伤候选目录：拿不准的一律
保留，探测失败/未启用时行为与改造前完全一致。
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest

from novelvideo import model_gateway_settings, text_model_probe
from novelvideo.api.routes import model_gateway as model_gateway_routes
from novelvideo.api.routes.model_gateway.connection import list_vision_model_options

ARK_404 = (
    '{"error":{"message":"The model or endpoint deepseek-v4-flash-260425 does not '
    'exist or you do not have access to it. Request id: 021791","type":"Not Found",'
    '"code":"InvalidEndpointOrModel.NotFound"}}'
)
GATEWAY_404 = (
    '{"error":{"message":"No available channel for model ap-new under group default",'
    '"type":"new_api_error","code":"model_not_found"}}'
)


def _isolate_settings_db(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from novelvideo import config

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ST_EDITION", "ce")
    for key in (
        "ST_CONTROL_PLANE_DSN",
        "MODEL_GATEWAY_MODE",
        "MODEL_GATEWAY_RUNTIME_VERSION",
        "LOCALAPI_API_KEY",
        "LOCALAPI_BASE_URL",
    ):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(autouse=True)
def _clean_probe_state():
    text_model_probe.reset_probe_scheduler_state()
    yield
    text_model_probe.reset_probe_scheduler_state()


@pytest.fixture
def clear_catalog_caches():
    previous_text = model_gateway_routes._text_models_cache
    previous_vision = model_gateway_routes._vision_models_cache
    model_gateway_routes._text_models_cache = None
    model_gateway_routes._vision_models_cache = None
    yield
    model_gateway_routes._text_models_cache = previous_text
    model_gateway_routes._vision_models_cache = previous_vision


def test_classify_probe_response_only_404_family_is_not_granted() -> None:
    assert text_model_probe.classify_probe_response(200, "{}") == "ok"
    # 上游说「不存在或无权限」才判未开通。
    assert text_model_probe.classify_probe_response(404, ARK_404) == "not-granted"
    # 渠道侧网关说「没有可用渠道」同样是在讲这个名字调不通。
    assert text_model_probe.classify_probe_response(404, GATEWAY_404) == "not-granted"
    assert (
        text_model_probe.classify_probe_response(503, GATEWAY_404) == "not-granted"
    )
    # 限流 / 鉴权 / 过载 / 非 chat 协议都说明不了开通与否 —— 一律 unknown。
    assert text_model_probe.classify_probe_response(429, "too many") == "unknown"
    assert text_model_probe.classify_probe_response(401, "invalid api key") == "unknown"
    assert text_model_probe.classify_probe_response(500, "boom") == "unknown"
    # 「读不了图」对文本实测什么也说明不了：那是个能用的文本模型。
    assert (
        text_model_probe.classify_probe_response(400, "当前模型不支持图片能力")
        == "unknown"
    )


def test_classify_probe_response_vision_adds_not_vision() -> None:
    """视觉实测多一档：只认上游「明确不读图」的措辞。"""

    assert (
        text_model_probe.classify_probe_response(
            400, "当前模型不支持图片能力", kind="vision"
        )
        == "not-vision"
    )
    assert (
        text_model_probe.classify_probe_response(
            400, "This model does not support image input.", kind="vision"
        )
        == "not-vision"
    )
    # 404 那一族仍是 not-granted（没开通比不读图更前置）。
    assert (
        text_model_probe.classify_probe_response(404, ARK_404, kind="vision")
        == "not-granted"
    )
    # 尺寸 / 格式 / base64 之类的 400 是探测自己的问题，不能当成「不能读图」的结论。
    assert (
        text_model_probe.classify_probe_response(
            400, "image size is too small", kind="vision"
        )
        == "unknown"
    )
    assert (
        text_model_probe.classify_probe_response(
            400, "invalid base64 image data", kind="vision"
        )
        == "unknown"
    )
    assert (
        text_model_probe.classify_probe_response(429, "不支持图片", kind="vision")
        == "unknown"
    )


async def test_probe_provider_writes_three_states_to_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setattr(
        text_model_probe,
        "get_newapi_provider_channel",
        lambda _provider: {
            "baseUrl": "https://ark.test/api/v3",
            "upstreamKey": "channel-key",
        },
    )

    def handler(request: httpx.Request) -> httpx.Response:
        model = str(json.loads(request.content).get("model") or "")
        if model == "granted-model":
            return httpx.Response(200, json={"choices": []})
        if model == "closed-model":
            return httpx.Response(
                404, headers={"content-type": "application/json"}, text=ARK_404
            )
        if model == "throttled-model":
            return httpx.Response(429, text="too many requests")
        return httpx.Response(400, text="not a chat model")

    states = await text_model_probe.probe_provider(
        "volcengine",
        models=[
            "granted-model",
            "closed-model",
            "throttled-model",
            "doubao-seedance-2-0",
        ],
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ),
    )

    assert states == {
        "granted-model": "ok",
        "closed-model": "not-granted",
        "throttled-model": "unknown",
        "doubao-seedance-2-0": "unknown",
    }
    probe = model_gateway_settings.get_text_model_probe()
    assert probe["volcengine"]["models"]["closed-model"] == "not-granted"
    assert probe["volcengine"]["baseUrl"] == "https://ark.test/api/v3"
    # 没测过的名字保持 unknown（不因为缺记录就误判可用 / 不可用）。
    assert text_model_probe.model_probe_state("volcengine", "never-probed") == "unknown"
    assert (
        text_model_probe.model_probe_state("volcengine", "closed-model")
        == "not-granted"
    )


def test_probe_state_roundtrip_merges_and_ignores_junk(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)

    model_gateway_settings.save_text_model_probe(
        "AIPDD", {"ap-glm-5.2": "OK", "ap-new": "not-granted", "x": "bogus"},
        base_url="https://api.aipdd.work/v1/",
    )
    probe = model_gateway_settings.get_text_model_probe()
    # 渠道键小写归一；非法状态被丢掉；base 去尾斜杠。
    assert probe["aipdd"]["models"] == {"ap-glm-5.2": "ok", "ap-new": "not-granted"}
    assert probe["aipdd"]["baseUrl"] == "https://api.aipdd.work/v1"

    # 增量合并：只更新新测到的名字，旧的结论保留。
    model_gateway_settings.save_text_model_probe("aipdd", {"ap-glm-5.2": "not-granted"})
    assert model_gateway_settings.get_text_model_probe()["aipdd"]["models"] == {
        "ap-glm-5.2": "not-granted",
        "ap-new": "not-granted",
    }

    # 空渠道 / 空结论是 no-op（不会把已有缓存清掉）。
    model_gateway_settings.save_text_model_probe("", {"a": "ok"})
    model_gateway_settings.save_text_model_probe("aipdd", {})
    assert "aipdd" in model_gateway_settings.get_text_model_probe()


def test_needs_probe_covers_missing_cache_new_names_and_debounce(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setattr(
        text_model_probe, "_snapshot_names", lambda _p, *_a: ["a", "b"]
    )

    # 没缓存 → 需要实测。
    assert text_model_probe.provider_needs_probe("volcengine") is True

    model_gateway_settings.save_text_model_probe(
        "volcengine", {"a": "ok", "b": "unknown"}
    )
    # 刚记过一次 → 去抖窗口内不再重打整轮。
    assert text_model_probe.provider_needs_probe("volcengine") is False

    text_model_probe._last_attempt.clear()
    # 清单里冒出没测过的新名字 → 立刻需要重测。
    monkeypatch.setattr(
        text_model_probe, "_snapshot_names", lambda _p, *_a: ["a", "b", "deepseek-v5"]
    )
    assert text_model_probe.provider_needs_probe("volcengine") is True

    # 上游清单还没拉到（空快照）时不探测，也不报错。
    monkeypatch.setattr(
        text_model_probe, "_snapshot_names", lambda _p, *_a: []
    )
    assert text_model_probe.provider_needs_probe("volcengine") is False


def test_text_and_vision_probe_caches_are_independent(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """两份缓存互不污染：文本的 not-granted 不能摘掉视觉候选（反之亦然）。"""

    _isolate_settings_db(monkeypatch, tmp_path)
    model_gateway_settings.save_text_model_probe(
        "volcengine", {"doubao-vision-pro": "not-granted"}
    )
    model_gateway_settings.save_vision_model_probe(
        "volcengine", {"doubao-vision-pro": "ok"}
    )

    assert (
        text_model_probe.model_probe_state("volcengine", "doubao-vision-pro")
        == "not-granted"
    )
    assert (
        text_model_probe.model_probe_state(
            "volcengine", "doubao-vision-pro", kind="vision"
        )
        == "ok"
    )
    # 判定器读的是各自那一份：文本判器认为不可用，视觉判器认为可用。
    assert text_model_probe.build_not_granted_lookup("text")(
        "volcengine", "doubao-vision-pro"
    )
    assert not text_model_probe.build_not_granted_lookup("vision")(
        "volcengine", "doubao-vision-pro"
    )
    # 非法档位写不进去（视觉的 ``not-vision`` 也不得污染文本缓存）。
    model_gateway_settings.save_text_model_probe(
        "volcengine", {"doubao-vision-pro": "not-vision"}
    )
    assert (
        model_gateway_settings.get_text_model_probe()["volcengine"]["models"][
            "doubao-vision-pro"
        ]
        == "not-granted"
    )


def test_probe_env_name_prefers_model_probe_and_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """规范名 ``MODEL_PROBE_*`` 优先，旧名 ``TEXT_MODEL_PROBE_*`` 继续兼容。"""

    for key in (
        "MODEL_PROBE_ENABLED",
        "TEXT_MODEL_PROBE_ENABLED",
        "MODEL_PROBE_TTL_HOURS",
        "TEXT_MODEL_PROBE_TTL_HOURS",
    ):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_text_model_probe.py::x")
    assert text_model_probe.probe_enabled() is False
    monkeypatch.setenv("TEXT_MODEL_PROBE_ENABLED", "1")
    assert text_model_probe.probe_enabled() is True
    monkeypatch.setenv("MODEL_PROBE_ENABLED", "0")
    assert text_model_probe.probe_enabled() is False

    monkeypatch.setenv("TEXT_MODEL_PROBE_TTL_HOURS", "6")
    assert text_model_probe.probe_ttl_seconds() == 6 * 3600.0
    monkeypatch.setenv("MODEL_PROBE_TTL_HOURS", "1")
    assert text_model_probe.probe_ttl_seconds() == 3600.0
    monkeypatch.setenv("MODEL_PROBE_TTL_HOURS", "bogus")
    assert text_model_probe.probe_ttl_seconds() == 24 * 3600.0


def test_schedule_is_off_inside_pytest_unless_forced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MODEL_PROBE_ENABLED", raising=False)
    monkeypatch.delenv("TEXT_MODEL_PROBE_ENABLED", raising=False)
    monkeypatch.setenv("PYTEST_CURRENT_TEST", "tests/test_text_model_probe.py::x")
    assert text_model_probe.probe_enabled() is False
    assert text_model_probe.schedule_probe_refresh() == []
    assert text_model_probe.schedule_probe_refresh(kind="vision") == []

    monkeypatch.setenv("TEXT_MODEL_PROBE_ENABLED", "1")
    assert text_model_probe.probe_enabled() is True
    # 强制开但没有事件循环（同步调用）→ 静默跳过，不抛。
    assert text_model_probe.schedule_probe_refresh() == []

    monkeypatch.setenv("TEXT_MODEL_PROBE_ENABLED", "0")
    assert text_model_probe.probe_enabled() is False


async def test_text_catalog_hides_only_not_granted(
    monkeypatch: pytest.MonkeyPatch, clear_catalog_caches
) -> None:
    """候选目录：not-granted 摘掉，unknown / ok 保留，默认项永不被摘。"""

    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.get_effective_default_text_model",
        lambda: "aipdd::ap-glm-5.2",
    )
    monkeypatch.setattr(
        model_gateway_routes,
        "get_newapi_provider_channels",
        lambda: [{"provider": "aipdd"}, {"provider": "volcengine"}],
    )
    monkeypatch.setattr(
        "novelvideo.newapi_provisioner.upstream_passthrough_models",
        lambda provider, **_k: {
            "aipdd": ["ap-glm-5.2"],
            "volcengine": [
                "deepseek-v4-pro-260425",  # 实测 ok
                "deepseek-v4-flash-260425",  # 实测 not-granted → 摘掉
                "kimi-k2",  # 限流过 → unknown，保留
            ],
        }[provider],
    )
    monkeypatch.setattr(model_gateway_routes, "_non_text_model_names", lambda: set())
    monkeypatch.setattr(
        "novelvideo.text_model_probe.build_not_granted_lookup",
        lambda *_a, **_k: (
            lambda provider, model: (
                provider,
                model,
            )
            == ("volcengine", "deepseek-v4-flash-260425")
        ),
    )
    # 实测调度必须是幂等且不阻塞的：这里只确认它被调用、不改响应。
    scheduled: list[str] = []
    monkeypatch.setattr(
        "novelvideo.text_model_probe.schedule_probe_refresh",
        lambda **kw: scheduled.append(str(kw.get("kind") or "text")) or [],
    )

    result = await model_gateway_routes.list_text_model_options()

    assert [entry["id"] for entry in result["data"]] == [
        "aipdd::ap-glm-5.2",
        "volcengine::deepseek-v4-pro-260425",
        "volcengine::kimi-k2",
    ]
    assert result["data"][0]["isDefault"] is True
    # 文本目录只排文本实测，不能顺手把视觉那一轮也打出去。
    assert scheduled == ["text"]


async def test_vision_catalog_drops_embedding_and_unusable_probes(
    monkeypatch: pytest.MonkeyPatch, clear_catalog_caches
) -> None:
    """视觉候选两道门：类型资格（排除 embedding）+ 带图实测结论。

    现场事实：收敛后剩下的 2 项全是 ``doubao-embedding-vision-*``。它们是
    多模态 embedding，不是 chat 视觉理解模型，点下去只会报错 —— 必须被类型门拦住；
    带图实测里的 ``not-granted`` / ``not-vision`` 则被实测门拦住。
    """

    monkeypatch.setattr(
        model_gateway_routes,
        "get_newapi_provider_channels",
        lambda: [{"provider": "volcengine"}],
    )
    monkeypatch.setattr(
        "novelvideo.newapi_provisioner.upstream_passthrough_models",
        lambda provider, **_k: [
            "doubao-vision-pro",  # not-granted → 摘
            "doubao-seed-1-6-vision",  # 带图 ok → 保留
            "doubao-embedding-vision-250615",  # 向量模型 → 类型门拦下
            "text-embedding-v3",  # 向量模型 → 类型门拦下
            "kimi-k2-vl",  # unknown（上游回泛错）→ 保守保留
            "qwen-vl-plus",  # 类型上完全合法，但带图实测被判 not-vision → 摘
        ],
    )
    monkeypatch.setattr(
        "novelvideo.text_model_probe.build_not_granted_lookup",
        lambda *_a, **_k: (
            lambda _provider, model: model in {"doubao-vision-pro", "qwen-vl-plus"}
        ),
    )
    scheduled: list[str] = []
    monkeypatch.setattr(
        "novelvideo.text_model_probe.schedule_probe_refresh",
        lambda **kw: scheduled.append(str(kw.get("kind") or "text")) or [],
    )

    result = await list_vision_model_options()

    assert [entry["id"] for entry in result["data"]] == [
        "volcengine::doubao-seed-1-6-vision",
        "volcengine::kimi-k2-vl",
    ]
    # 视觉目录排的是视觉那一轮（带图实测、独立缓存）。
    assert scheduled == ["vision"]


def test_vision_probe_names_only_cover_vision_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """视觉实测只打有资格当视觉候选的名字：拿全渠道清单去带图请求既贵又错。"""

    monkeypatch.setattr(
        "novelvideo.newapi_provisioner.upstream_passthrough_models",
        lambda provider, **_k: [
            "doubao-seed-1-6-vision",
            "doubao-embedding-vision-250615",
            "deepseek-v4-pro-260425",
        ],
    )
    assert text_model_probe._snapshot_names("volcengine", "vision") == [
        "doubao-seed-1-6-vision"
    ]
    # 文本实测仍按全量清单（候选收敛靠的是实测 + 文本自己的类型过滤）。
    assert text_model_probe._snapshot_names("volcengine", "text") == [
        "doubao-seed-1-6-vision",
        "doubao-embedding-vision-250615",
        "deepseek-v4-pro-260425",
    ]


async def test_vision_probe_sends_an_image_request(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """带图实测的 payload：一条 text + 一张极小图，而且只花 max_tokens=1。"""

    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setattr(
        text_model_probe,
        "get_newapi_provider_channel",
        lambda _provider: {
            "baseUrl": "https://ark.test/api/v3",
            "upstreamKey": "channel-key",
        },
    )
    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.append(payload)
        model = str(payload.get("model") or "")
        if model == "text-only-model":
            return httpx.Response(400, text="当前模型不支持图片能力")
        return httpx.Response(200, json={"choices": []})

    states = await text_model_probe.probe_provider(
        "volcengine",
        kind="vision",
        models=["doubao-seed-1-6-vision", "text-only-model"],
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        ),
    )

    assert states == {
        "doubao-seed-1-6-vision": "ok",
        "text-only-model": "not-vision",
    }
    assert len(seen) == 2
    for payload in seen:
        content = payload["messages"][0]["content"]
        assert isinstance(content, list), "视觉实测必须是多模态数组"
        assert {part["type"] for part in content} == {"text", "image_url"}
        assert str(payload["max_tokens"]) == "1"
        image_url = content[1]["image_url"]["url"]
        assert image_url.startswith("data:image/png;base64,")
    # 结论落在视觉缓存，文本缓存保持空（两份互不污染）。
    assert (
        model_gateway_settings.get_vision_model_probe()["volcengine"]["models"][
            "text-only-model"
        ]
        == "not-vision"
    )
    assert model_gateway_settings.get_text_model_probe() == {}


async def test_probe_can_be_scheduled_from_running_loop(
    monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """强制开启时：候选目录响应先返回，实测在后台跑完并落缓存。"""

    _isolate_settings_db(monkeypatch, tmp_path)
    monkeypatch.setenv("TEXT_MODEL_PROBE_ENABLED", "1")

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text=ARK_404)

    monkeypatch.setattr(
        model_gateway_settings,
        "get_newapi_provider_channels",
        lambda: [
            {
                "provider": "volcengine",
                "baseUrl": "https://ark.test/api/v3",
                "upstreamKey": "channel-key",
            }
        ],
    )
    monkeypatch.setattr(
        "novelvideo.newapi_provisioner.upstream_passthrough_models",
        lambda provider, **_k: ["closed-model"],
    )

    triggered = text_model_probe.schedule_probe_refresh(
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(handler)
        )
    )
    assert triggered == ["volcengine"]
    tasks = list(text_model_probe._background_tasks)
    assert tasks, "实测任务没有被排队"
    await asyncio.gather(*tasks)

    assert (
        text_model_probe.model_probe_state("volcengine", "closed-model")
        == "not-granted"
    )
    # 刚测过的渠道在去抖窗口内不会被重复排队（幂等）。
    assert text_model_probe.schedule_probe_refresh() == []
