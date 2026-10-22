"""[aipdd fork delta] req38：视频复刻「规划逐镜复刻提示词」404 的运行期自愈。


现场事实（api:2.0.94 容器实测）：``volcengine::doubao-seed-1-6-lite-251015`` 在该
火山方舟账号确实没开通推理服务，直连复现回 404
``InvalidEndpointOrModel.NotFound``。实测缓存里它早已是 ``not-granted``，但**运行期
真实失败本身不回写**，于是两类坑必须让用户反复踩：

1. 候选目录只有 60 秒进程内缓存 + 后台探测，一次真实失败不会让下拉立刻收敛；
2. 本轮 ``unknown``（限流 / 鉴权 / 没测过）的名字按「拿不准就保留」留在下拉里，
   用户选中就必然踩坑，而这条账号级结论本该当场固化。

纪律：回写只认上游当场说死的原话与错误码；网关的 ``model_not_found``（本地没绑定）、
鉴权 / 限流 / 5xx、以及「方舟渠道 + 裸 404」一律不回写——裸 404 也可能是我们的请求
路径打错了，那是我们的 bug，不该把模型判死。回写全程 best-effort，坏了不许盖掉诊断文案。
"""

from __future__ import annotations

import pytest
from pydantic_ai.exceptions import ModelHTTPError

from novelvideo import config
from novelvideo import model_call_diagnostics
from novelvideo import model_gateway_settings
from novelvideo import text_model_probe
from novelvideo.api.routes import model_gateway as model_gateway_routes

DEAD = "doubao-seed-1-6-lite-251015"
DEAD_CALL = f"volcengine::{DEAD}"

ARK_404_BODY = {
    "message": (
        f"The model or endpoint {DEAD} does not exist or you do not have access to it. "
        "Request id: 0217912718369054f08adabc2a0b7ee0ad3a2fcb671c8c4b2ba79"
    ),
    "type": "Not Found",
    "param": "",
    "code": "InvalidEndpointOrModel.NotFound",
}


@pytest.fixture(autouse=True)
def _isolate_settings_db(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("ST_EDITION", "ce")
    for key in ("ST_CONTROL_PLANE_DSN", "MODEL_GATEWAY_MODE", "LOCALAPI_API_KEY", "LOCALAPI_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    previous_text = model_gateway_routes._text_models_cache
    previous_vision = model_gateway_routes._vision_models_cache
    model_gateway_routes._text_models_cache = None
    model_gateway_routes._vision_models_cache = None
    yield
    model_gateway_routes._text_models_cache = previous_text
    model_gateway_routes._vision_models_cache = previous_vision


def _ark_error(model_name: str = DEAD_CALL) -> ModelHTTPError:
    return ModelHTTPError(404, model_name, dict(ARK_404_BODY))


def _states(kind: str, provider: str) -> dict[str, str]:
    getter = (
        model_gateway_settings.get_text_model_probe
        if kind == "text"
        else model_gateway_settings.get_vision_model_probe
    )
    entry = getter().get(provider) or {}
    return dict(entry.get("models") or {})


def test_ark_404_writeback_marks_both_probe_caches() -> None:
    """一次真实 404：文本与视觉两份缓存同时固化（这条措辞与模态无关）。"""

    error = model_call_diagnostics.model_call_failure(_ark_error(), model_name=DEAD_CALL)

    assert isinstance(error, RuntimeError)
    assert _states("text", "volcengine")[DEAD] == "not-granted"
    assert _states("vision", "volcengine")[DEAD] == "not-granted"


def test_writeback_does_not_bump_channel_checked_at_or_touch_siblings() -> None:
    """偶发一次失败不该把整渠道的 TTL 复测时钟拨到当下，也不该顺手改别人的结论。"""

    model_gateway_settings.save_text_model_probe(
        "volcengine",
        {
            "doubao-seed-2-0-lite-260215": "ok",
            "kimi-k2": "unknown",
        },
    )
    before = (
        model_gateway_settings.get_text_model_probe()
        .get("volcengine", {})
        .get("checkedAt", "")
    )
    assert before

    assert text_model_probe.record_runtime_not_granted(DEAD_CALL) is True

    entry = model_gateway_settings.get_text_model_probe()["volcengine"]
    assert entry["checkedAt"] == before
    assert entry["models"]["doubao-seed-2-0-lite-260215"] == "ok"
    assert entry["models"]["kimi-k2"] == "unknown"
    assert entry["models"][DEAD] == "not-granted"


@pytest.mark.parametrize(
    ("status", "model_name", "body"),
    [
        # 裸 404：没有方舟那句原话，可能是我们请求路径打错，不判死模型。
        (404, DEAD_CALL, {"message": "Not Found", "type": "Not Found", "code": ""}),
        # 网关（new-api）的 model_not_found 是本地没绑定，直连上游可能照样能用。
        (
            503,
            DEAD_CALL,
            {
                "error": {
                    "message": f"No available channel for model {DEAD} under group default",
                    "type": "new_api_error",
                    "code": "model_not_found",
                }
            },
        ),
        # 鉴权 / 限流 / 上游故障都不是「这个名字不存在」的结论。
        (401, DEAD_CALL, {"message": "invalid api key", "code": ""}),
        (429, DEAD_CALL, {"message": "Too Many Requests", "code": "rate_limit"}),
        (500, DEAD_CALL, {"message": "internal server error", "code": ""}),
    ],
)
def test_only_account_level_rejection_writes_back(
    status: int, model_name: str, body: dict
) -> None:
    """回写门槛比文案门槛更严：只有上游当场判死这个名字才写缓存。"""

    error = model_call_diagnostics.model_call_failure(
        ModelHTTPError(status, model_name, body), model_name=model_name
    )

    assert isinstance(error, RuntimeError)
    assert _states("text", "volcengine") == {}
    assert _states("vision", "volcengine") == {}


def test_bare_name_is_written_only_when_a_single_channel_owns_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """归属不明一律不写：系统不做隐式裁决。"""

    monkeypatch.setattr(model_gateway_settings, "model_owner_providers", lambda _m: ["aipdd"])
    assert text_model_probe.record_runtime_not_granted(DEAD) is True
    assert _states("text", "aipdd")[DEAD] == "not-granted"
    assert _states("text", "volcengine") == {}

    ambiguous = "glm-4.6"  # 两个渠道都有这个名字：歧义裸名一律不写
    monkeypatch.setattr(
        model_gateway_settings,
        "model_owner_providers",
        lambda _m: ["aipdd", "volcengine"],
    )
    assert text_model_probe.record_runtime_not_granted(ambiguous) is False
    assert "glm-4.6" not in _states("text", "aipdd")
    assert "glm-4.6" not in _states("text", "volcengine")

    monkeypatch.setattr(model_gateway_settings, "model_owner_providers", lambda _m: [])
    assert text_model_probe.record_runtime_not_granted("kimi-k2") is False
    assert _states("text", "aipdd") == {DEAD: "not-granted"}


@pytest.mark.parametrize("call_name", ["", "::", "volcengine::"])
def test_unusable_call_names_never_write(call_name: str) -> None:
    assert text_model_probe.record_runtime_not_granted(call_name) is False


async def test_writeback_clears_candidate_caches_so_the_dead_name_disappears(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """用户可见的那一下：按原样重试之前，下拉就该把这个名字摘掉，而不是多等 60 秒。"""

    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.get_effective_default_text_model",
        lambda: "volcengine::doubao-seed-2-0-lite-260215",
    )
    monkeypatch.setattr(
        model_gateway_routes,
        "get_newapi_provider_channels",
        lambda: [{"provider": "volcengine"}],
    )
    monkeypatch.setattr(
        "novelvideo.newapi_provisioner.upstream_passthrough_models",
        lambda _provider, **_k: [
            "doubao-seed-2-0-lite-260215",
            DEAD,  # 本轮没测过 → unknown → 按「拿不准就保留」留在下拉
        ],
    )
    monkeypatch.setattr(model_gateway_routes, "_non_text_model_names", lambda: set())
    monkeypatch.setattr(
        "novelvideo.text_model_probe.schedule_probe_refresh", lambda **_kw: []
    )

    first = await model_gateway_routes.list_text_model_options()
    assert DEAD_CALL in [entry["id"] for entry in first["data"]]
    assert model_gateway_routes._text_models_cache is not None

    model_call_diagnostics.model_call_failure(_ark_error(), model_name=DEAD_CALL)
    assert model_gateway_routes._text_models_cache is None

    second = await model_gateway_routes.list_text_model_options()
    assert [entry["id"] for entry in second["data"]] == [
        "volcengine::doubao-seed-2-0-lite-260215"
    ]


def test_probe_write_failure_keeps_the_diagnostic_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """缓存不可写只是少了一次加速收敛，绝不能盖掉用户本该看到的中文诊断。"""

    def boom(*_a, **_k):  # noqa: ANN002, ANN003
        raise RuntimeError("state 写不了")

    monkeypatch.setattr(model_gateway_settings, "save_text_model_probe", boom)
    monkeypatch.setattr(model_gateway_settings, "save_vision_model_probe", boom)

    error = model_call_diagnostics.model_call_failure(_ark_error(), model_name=DEAD_CALL)

    assert "未开通推理服务" in str(error)
    assert DEAD_CALL in str(error)
    assert _states("text", "volcengine") == {}


def test_cache_invalidation_is_tolerant(monkeypatch: pytest.MonkeyPatch) -> None:
    """路由层没加载（任务进程）或清理函数坏了，都不影响回写本身。"""

    monkeypatch.setattr(
        model_gateway_routes,
        "invalidate_model_candidate_caches",
        lambda: (_ for _ in ()).throw(RuntimeError("炸了")),
    )
    assert text_model_probe.record_runtime_not_granted(DEAD_CALL) is True
    assert _states("text", "volcengine")[DEAD] == "not-granted"
