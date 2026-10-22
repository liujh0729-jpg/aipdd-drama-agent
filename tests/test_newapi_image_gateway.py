import base64
import importlib
import logging
from types import SimpleNamespace

import httpx
import pytest

# [aipdd fork delta] WP-D: 「积分不足」错误类型随 CE 额度体系一起拆除。这些用例
# 改守真正的防线：上游账户额度文案必须原样冒泡，绝不被吞成返回值。
_QUOTA_ERROR_TEXT = "insufficient credits for user usr_1: required 40, available 8"

pytestmark = pytest.mark.m04


def _isolate_settings_db(monkeypatch, tmp_path):
    import novelvideo.config as config

    state_dir = str(tmp_path / "state")
    monkeypatch.delenv("MODEL_GATEWAY_MODE", raising=False)
    monkeypatch.setenv("NOVELVIDEO_STATE_DIR", state_dir)
    monkeypatch.setattr(config, "STATE_DIR", state_dir)


def _reload_isolated(monkeypatch, module):
    """reload 模块并把环境还原登记给 monkeypatch，测试结束自动回滚。

    裸 ``importlib.reload`` 会把当前（monkeypatch 注入的）env 钉进模块常量：
    env 由 pytest 恢复，但常量不回退，污染后续测试（历史缺陷：子集顺序下
    ``test_scene_360_model_rejects_unknown_*`` 因 ``config.NEWAPI_IMAGE_MODEL``
    残留非空而不再拒绝未知模型）。

    做法：reload **之前**先给每个现存属性登记一次原地 setattr——monkeypatch
    的 undo 会在测试结束时把属性设回登记时刻的值，等价于撤销随后的 reload；
    运行期仍然看到 reload 后的新值。
    """

    for key, value in list(vars(module).items()):
        if key.startswith("__"):
            continue
        monkeypatch.setattr(module, key, value)
    return importlib.reload(module)


@pytest.fixture(autouse=True)
def _isolated_model_gateway(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    # This module tests low-level environment-driven gateway adapters. CE
    # database precedence is covered in test_model_gateway_settings.py.
    monkeypatch.setenv("ST_CONTROL_PLANE_DSN", "postgresql://test-control-plane")


def _patch_scene_newapi_gateway(
    monkeypatch,
    *,
    api_key: str = "newapi-token",
    base_url: str = "http://newapi.test/v1",
) -> None:
    import novelvideo.config as config

    monkeypatch.setattr(
        config,
        "get_effective_newapi_gateway_config",
        lambda: SimpleNamespace(api_key=api_key, base_url=base_url),
    )


def _as_ce_local(monkeypatch) -> None:
    """把这条用例切回「CE 自建、设置库自己说话」。

    本模块的 autouse fixture 设了 ``ST_CONTROL_PLANE_DSN``，守的是 env 驱动的底层适配器；
    而媒体模型映射在控制面模式下恒为空。默认图模型的名字现在**只**来自那张映射表，所以要先
    把控制面摘掉才谈得上验证来源。
    """
    monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)
    monkeypatch.setenv("ST_EDITION", "ce")


def _seed_image_mappings(*names: str) -> None:
    """按面板顺序写「默认生成模型」的图片映射（第 1 条 = 主位，第 2 条 = 副位）。"""
    from novelvideo.model_gateway_settings import save_newapi_media_model_mappings

    save_newapi_media_model_mappings(
        {
            name: {
                "provider": "newapi",
                "upstreamModel": name,
                "mediaType": "image",
                "sortOrder": index,
            }
            for index, name in enumerate(names)
        }
    )


def _pin_default_image_models(
    monkeypatch, *, primary: str = "", secondary: str = ""
) -> None:
    """把「设置 → 模型与渠道」的默认图主/副位钉成给定名字。

    [aipdd fork delta wave2] 本模块钉在控制面模式下（autouse fixture 设了
    ``ST_CONTROL_PLANE_DSN``，媒体映射恒为空），守的是底层适配器；用途位现在**只**从
    设置的这两个读取函数取名，所以在这里把它们钉住。同名 env 照设也不用。
    """
    import novelvideo.model_gateway_settings as mgs

    monkeypatch.setattr(mgs, "get_effective_default_image_model", lambda: primary)
    monkeypatch.setattr(mgs, "get_effective_default_nanobanana_model", lambda: secondary)


def test_dc_image_2_selection_maps_to_newapi_gpt_image2(monkeypatch, tmp_path):
    """默认图主位的名字只来自「设置 → 模型与渠道」，同名 env 完全不进配置。

    [aipdd fork delta] 旧断言写的是 ``NEWAPI_IMAGE_MODEL=Acme-IMG-1``：一个设置页看不见、
    渠道下架也不会自动取消的名字排在面板前面。现在同名 env 照设，但它不再参与解析。
    """
    _isolate_settings_db(monkeypatch, tmp_path)
    _as_ce_local(monkeypatch)
    monkeypatch.setenv("NEWAPI_IMAGE_MODEL", "Acme-IMG-1")
    monkeypatch.setenv("DEFAULT_CHARACTER_IMAGE_SELECTION", "newapi_gpt_image2")

    import novelvideo.config as config

    config = _reload_isolated(monkeypatch, config)

    # 「默认生成模型」面板还没图源时，下拉退回两张通用槽位：label 里不带发行方模型名。
    assert config.character_image_selection_options() == {
        "newapi_gpt_image2": "NewAPI Image",
        "newapi_nanobanana2": "NewAPI Nano Banana 2",
    }
    assert config.get_character_image_selection() == "newapi_gpt_image2"

    # 主位还没配 = 模型名是空串，由调用点按「未绑定可用模型」拦住；env 的名字不参与。
    image_config = config.get_grid_generation_config(selection_override="newapi_gpt_image2")
    assert image_config["provider"] == "newapi"
    assert image_config["model"] == ""

    # 配上以后：下拉以面板为准，配置里的模型名也只从面板来。
    _seed_image_mappings("ap-gpt-image-2")
    assert config.character_image_selection_options() == {
        "ap-gpt-image-2": "newapi · ap-gpt-image-2"
    }
    image_config = config.get_grid_generation_config(selection_override="newapi_gpt_image2")
    assert image_config["model"] == "ap-gpt-image-2"
    assert image_config["model"] != "Acme-IMG-1"


def test_dc_banana_2_selection_maps_to_newapi_nanobanana2(monkeypatch, tmp_path):
    """``newapi_nanobanana2`` 选项 = 面板里的**第二张**默认图，不再由 env 点名。

    [aipdd fork delta] 两个可见选项的差别落在面板顺序上：主位取第一条，副位取第二条
    （只配一条时两个位同名）。``NEWAPI_NANOBANANA2_MODEL`` 照设也不用。
    """
    _isolate_settings_db(monkeypatch, tmp_path)
    _as_ce_local(monkeypatch)
    _seed_image_mappings("ap-gpt-image-2", "ap-nanobanana-2")
    monkeypatch.setenv("NEWAPI_NANOBANANA2_MODEL", "Acme-IMG-2")
    monkeypatch.setenv("DEFAULT_CHARACTER_IMAGE_SELECTION", "newapi_nanobanana2")

    import novelvideo.config as config

    config = _reload_isolated(monkeypatch, config)

    assert config.get_character_image_selection() == "newapi_nanobanana2"

    image_config = config.get_grid_generation_config(selection_override="newapi_nanobanana2")
    assert image_config["provider"] == "newapi"
    assert image_config["model"] == "ap-nanobanana-2"
    assert image_config["model"] != "Acme-IMG-2"
    # 主位仍然是第一张：两个选项的差别只在面板顺序上。
    primary = config.get_grid_generation_config(selection_override="newapi_gpt_image2")
    assert primary["model"] == "ap-gpt-image-2"


def test_fixed_asset_image_providers_default_to_newapi_when_env_is_empty(monkeypatch):
    """按用途钉图的那几个 PROVIDER 留空时跟随 newapi；模型名来自设置的默认图位。

    [aipdd fork delta] ``NEWAPI_IMAGE_MODEL`` 不再提供任何模型名：默认图位没配就是空，
    由调用点按「未绑定可用模型」拦住，而不是拿一个 env 名字去打网关。
    """
    for key in (
        "PROP_REF_IMAGE_PROVIDER",
        "SCENE_MASTER_IMAGE_PROVIDER",
        "SCENE_REVERSE_MASTER_IMAGE_PROVIDER",
        "SCENE_360_IMAGE_PROVIDER",
    ):
        monkeypatch.setenv(key, "")
    _as_ce_local(monkeypatch)
    monkeypatch.setenv("NEWAPI_IMAGE_MODEL", "Acme-IMG-1")

    import novelvideo.config as config

    config = _reload_isolated(monkeypatch, config)

    assert config.PROP_REF_IMAGE_PROVIDER == "newapi"
    assert config.SCENE_MASTER_IMAGE_PROVIDER == "newapi"
    assert config.SCENE_REVERSE_MASTER_IMAGE_PROVIDER == "newapi"
    assert config.SCENE_360_IMAGE_PROVIDER == "newapi"

    from novelvideo.generators import prop_generation, scene_reference_images

    prop_generation = _reload_isolated(monkeypatch, prop_generation)
    scene_reference_images = _reload_isolated(monkeypatch, scene_reference_images)

    # 默认图位还没配：解析结果是空串（调用点负责拒绝），env 那个名字不参与。
    assert prop_generation.resolve_prop_reference_image_model() == ""

    _seed_image_mappings("ap-gpt-image-2")
    assert prop_generation.resolve_prop_reference_image_model() == "ap-gpt-image-2"
    assert scene_reference_images._scene_image_provider("master", None) == "newapi"
    assert scene_reference_images._scene_image_provider("reverse_master", None) == "newapi"


def test_purpose_image_envs_are_inert_and_settings_decide(monkeypatch, tmp_path):
    """按用途钉图的那批 env 整批退出解析链：名字只由「设置 → 模型与渠道」的主/副位决定。

    [aipdd fork delta wave2] ``SCENE_MASTER_IMAGE_MODEL`` / ``SCENE_REVERSE_MASTER_IMAGE_MODEL``
    / ``SCENE_360_IMAGE_MODEL`` / ``SCENE_ASSET_MODEL`` / ``PROP_REF_IMAGE_MODEL`` /
    ``CHARACTER_IMAGE_MODEL`` 以前各自钉住一条生成链：它们在设置页上看不见，渠道下架时也
    不会自动取消绑定 —— 生产上的 ``No available channel for model Acme-IMG-2`` 就是这么
    来的。现在用途只决定取面板的主位还是副位，模型名一律只从设置来。
    """
    _isolate_settings_db(monkeypatch, tmp_path)
    _as_ce_local(monkeypatch)
    # 厂商选择（provider）仍是部署级参数，这里把它钉成网关路径，只谈模型名的来源。
    for key in (
        "PROP_REF_IMAGE_PROVIDER",
        "SCENE_MASTER_IMAGE_PROVIDER",
        "SCENE_REVERSE_MASTER_IMAGE_PROVIDER",
        "SCENE_360_IMAGE_PROVIDER",
        "SCENE_ASSET_PROVIDER",
    ):
        monkeypatch.setenv(key, "newapi")
    for key in (
        "NEWAPI_IMAGE_MODEL",
        "NEWAPI_NANOBANANA2_MODEL",
        "SCENE_MASTER_IMAGE_MODEL",
        "SCENE_REVERSE_MASTER_IMAGE_MODEL",
        "SCENE_360_IMAGE_MODEL",
        "SCENE_ASSET_MODEL",
        "PROP_REF_IMAGE_MODEL",
        "CHARACTER_IMAGE_MODEL",
    ):
        monkeypatch.setenv(key, "Acme-IMG-2")
    for key in (
        "CHARACTER_IMAGE_SELECTION",
        "DEFAULT_CHARACTER_IMAGE_SELECTION",
        "DEFAULT_RENDER_IMAGE_SELECTION",
        "DEFAULT_SKETCH_IMAGE_SELECTION",
    ):
        monkeypatch.delenv(key, raising=False)

    import novelvideo.config as config
    from novelvideo.generators import prop_generation, scene_reference_images

    config = _reload_isolated(monkeypatch, config)
    prop_generation = _reload_isolated(monkeypatch, prop_generation)
    scene_reference_images = _reload_isolated(monkeypatch, scene_reference_images)

    # 常量层面就没读 env：这批键留空，只为让旧的导入和旧选项表继续可用。
    assert config.NEWAPI_IMAGE_MODEL == config.NEWAPI_NANOBANANA2_MODEL == ""
    assert config.SCENE_MASTER_IMAGE_MODEL == config.SCENE_REVERSE_MASTER_IMAGE_MODEL == ""
    assert config.SCENE_360_IMAGE_MODEL == config.SCENE_ASSET_MODEL == ""
    assert config.PROP_REF_IMAGE_MODEL == config.CHARACTER_IMAGE_MODEL == ""

    # 面板还没图源：每个用途位都是空串，由调用点按「未绑定可用模型」拦住。
    assert prop_generation.resolve_prop_reference_image_model() == ""
    assert scene_reference_images.resolve_scene_reference_image_model("master") == ""
    assert scene_reference_images.resolve_scene_reference_image_model("spatial_layout") == ""
    assert scene_reference_images.resolve_scene_reference_image_model("reverse_master") == ""
    assert config.get_character_image_selection() == "newapi_gpt_image2"

    # 配上以后：主位给道具 / 空间布局，副位给场景主图与反向主图；env 那个名字一个都不出现。
    _seed_image_mappings("ap-gpt-image-2", "ap-nanobanana-2")
    assert prop_generation.resolve_prop_reference_image_model() == "ap-gpt-image-2"
    assert (
        scene_reference_images.resolve_scene_reference_image_model("spatial_layout")
        == "ap-gpt-image-2"
    )
    assert (
        scene_reference_images.resolve_scene_reference_image_model("master")
        == "ap-nanobanana-2"
    )
    assert (
        scene_reference_images.resolve_scene_reference_image_model("reverse_master")
        == "ap-nanobanana-2"
    )
    assert config.get_character_image_selection() == "newapi_gpt_image2"


def test_newapi_sketch_config_defaults_to_dc_image2_low_quality(monkeypatch, tmp_path):
    """草图位跟随面板默认图（主位），画质按名字的历史档位下发。"""
    import httpx
    import novelvideo.config as config
    from novelvideo.generators import grid_generation

    posted: dict[str, object] = {}

    class FakeResponse:
        headers = {"x-newapi-request-id": "req-sketch"}

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "id": "resp-sketch",
                "data": [{"b64_json": base64.b64encode(b"sketch").decode()}],
            }

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            posted["timeout"] = kwargs.get("timeout")

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            posted["json"] = json
            return FakeResponse()

    _isolate_settings_db(monkeypatch, tmp_path)
    _as_ce_local(monkeypatch)
    _seed_image_mappings("ap-gpt-image-2")
    monkeypatch.setenv("NEWAPI_IMAGE_MODEL", "Acme-IMG-1")
    monkeypatch.setenv("DEFAULT_SKETCH_IMAGE_SELECTION", "newapi_gpt_image2")
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    config = _reload_isolated(monkeypatch, config)
    # CE 本地时网关凭据来自设置库，按本模块惯例钉上网关。
    _patch_scene_newapi_gateway(monkeypatch)
    sketch_config = config.get_sketch_generation_config()

    assert sketch_config["provider"] == "newapi"
    assert sketch_config["api_key"] == "newapi-token"
    assert sketch_config["base_url"] == "http://newapi.test/v1"
    assert sketch_config["model"] == "ap-gpt-image-2"
    assert sketch_config["image_size"] == "1K"
    assert sketch_config["openai_image_quality"] == "low"

    trace = {}
    image_bytes, _text, error = run_async(
        grid_generation._call_newapi_image_api(
            api_key=sketch_config["api_key"],
            model=sketch_config["model"],
            prompt="sketch prompt",
            image_config={
                "aspect_ratio": "2:3",
                "image_size": sketch_config["image_size"],
                "quality": sketch_config["openai_image_quality"],
            },
            base_url=sketch_config["base_url"],
            trace=trace,
        )
    )
    assert posted["json"]["response_format"] == "url"

    assert image_bytes == b"sketch"
    assert error == ""
    assert posted["timeout"] == grid_generation.NEWAPI_IMAGE_HTTP_TIMEOUT_SECONDS == 1800.0
    assert posted["json"]["model"] == "ap-gpt-image-2"
    assert posted["json"]["quality"] == "low"
    assert (posted["json"]["width"], posted["json"]["height"]) == (688, 1024)
    assert posted["json"]["metadata"] == {"ratio": "2:3", "resolution": "1k"}
    assert "extra_fields" not in posted["json"]
    assert trace == {"request_id": "req-sketch", "response_id": "resp-sketch"}


def test_newapi_sketch_config_can_use_dc_banana2_without_quality(monkeypatch, tmp_path):
    """副位（面板第二张默认图）不下发画质档位；名字同样只由设置决定。"""
    import httpx
    import novelvideo.config as config
    from novelvideo.generators import grid_generation

    posted = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"sketch").decode()}]}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            posted["json"] = json
            return FakeResponse()

    _isolate_settings_db(monkeypatch, tmp_path)
    _as_ce_local(monkeypatch)
    _seed_image_mappings("ap-gpt-image-2", "ap-nanobanana-2")
    monkeypatch.setenv("NEWAPI_NANOBANANA2_MODEL", "Acme-IMG-2")
    monkeypatch.setenv("DEFAULT_SKETCH_IMAGE_SELECTION", "newapi_nanobanana2")
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    config = _reload_isolated(monkeypatch, config)
    _patch_scene_newapi_gateway(monkeypatch)
    sketch_config = config.get_sketch_generation_config()

    assert sketch_config["provider"] == "newapi"
    assert sketch_config["model"] == "ap-nanobanana-2"
    assert sketch_config["image_size"] == "1K"

    image_bytes, _text, error = run_async(
        grid_generation._call_newapi_image_api(
            api_key=sketch_config["api_key"],
            model=sketch_config["model"],
            prompt="sketch prompt",
            image_config={
                "aspect_ratio": "2:3",
                "image_size": sketch_config["image_size"],
                "quality": sketch_config["openai_image_quality"],
            },
            base_url=sketch_config["base_url"],
        )
    )

    assert image_bytes == b"sketch"
    assert error == ""
    assert posted["json"]["model"] == "ap-nanobanana-2"
    assert "quality" not in posted["json"]
    assert (posted["json"]["width"], posted["json"]["height"]) == (688, 1024)
    assert posted["json"]["metadata"] == {"ratio": "2:3", "resolution": "1k"}
    assert "extra_fields" not in posted["json"]


def test_catalog_can_enable_arbitrary_newapi_image_quality(monkeypatch):
    import httpx
    from novelvideo.generators import grid_generation

    posted = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"image").decode()}]}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            posted["json"] = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    image_bytes, _text, error = run_async(
        grid_generation._call_newapi_image_api(
            api_key="newapi-token",
            model="Seedream-Custom",
            prompt="prompt",
            image_config={
                "aspect_ratio": "adaptive",
                "image_size": "3K",
                "quality": "ultra",
                "request_schema": {
                    "endpoint": "images/generations",
                    "parameters": [
                        {
                            "key": "watermark",
                            "requestPath": "watermark",
                            "control": "switch",
                            "default": False,
                        }
                    ],
                    "includeQuality": True,
                },
                "model_params": {"watermark": True},
            },
            base_url="http://newapi.test/v1",
        )
    )

    assert image_bytes == b"image"
    assert error == ""
    assert posted["json"]["model"] == "Seedream-Custom"
    assert posted["json"]["quality"] == "ultra"
    # [aipdd fork delta #5] 图片网关（ap-image-2.0 等）要求请求始终带尺寸：
    # adaptive 比例不剥离宽高，缺省时回退 IMAGE_DEFAULT_WIDTH/HEIGHT。
    assert (posted["json"]["width"], posted["json"]["height"]) == (1440, 2560)
    assert posted["json"]["metadata"] == {"ratio": "auto", "resolution": "3k"}
    assert posted["json"]["watermark"] is True
    assert "extra_fields" not in posted["json"]


class _CaptureImagePostClient:
    """把 newapi 图片请求体抓下来，不断言其他行为。"""

    def __init__(self, posted: dict, payload: bytes = b"image") -> None:
        self._posted = posted
        self._payload = payload

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def post(self, url, *, headers, json):
        self._posted["json"] = json
        payload = self._payload

        class _Response:
            def raise_for_status(_self):
                return None

            def json(_self):
                return {
                    "data": [{"b64_json": base64.b64encode(payload).decode()}]
                }

        return _Response()


@pytest.mark.parametrize(
    ("model", "request_schema", "expect_quality"),
    [
        # [aipdd fork delta] 画质下发以目录声明为准，不再看模型名白名单：
        # 目录里配了 qualityOptions（→ includeQuality）的自建渠道模型能拿到画质；
        ("ap-image-2.0", {"includeQuality": True}, True),
        # 目录命中但没配画质档位的模型不能吃 quality —— 哪怕名字落在旧白名单里。
        ("gpt-image-2", {}, False),
        ("acme-img-1", {"parameters": []}, False),
        # 完全没走目录的旧链路（主线渲染等配置驱动路径）才保留按名字兜底。
        ("gpt-image-2", None, True),
        ("ap-image-2.0", None, False),
    ],
)
def test_newapi_image_quality_follows_catalog_declaration(
    monkeypatch,
    model,
    request_schema,
    expect_quality,
):
    from novelvideo.generators import grid_generation

    posted: dict = {}
    monkeypatch.setattr(httpx, "AsyncClient", _CaptureImagePostClient(posted))

    image_config = {
        "aspect_ratio": "1:1",
        "image_size": "2K",
        "quality": "medium",
    }
    if request_schema is not None:
        image_config["request_schema"] = {
            "endpoint": "images/generations",
            **request_schema,
        }

    _bytes, _text, error = run_async(
        grid_generation._call_newapi_image_api(
            api_key="newapi-token",
            model=model,
            prompt="prompt",
            image_config=image_config,
            base_url="http://newapi.test/v1",
        )
    )

    assert error == ""
    if expect_quality:
        assert posted["json"]["quality"] == "medium"
    else:
        assert "quality" not in posted["json"]


@pytest.mark.parametrize(
    ("model", "resolution", "ratio"),
    [
        ("custom-seedream-alias", "2K", "3:2"),
        ("future-image-model", "2K", "16:9"),
        ("future-image-model", "2K", "21:9"),
        ("future-image-model", "3K", "16:9"),
        ("future-image-model", "2048x1376", "3:2"),
    ],
)
def test_admin_min_pixels_applies_to_named_and_explicit_resolutions(
    model,
    resolution,
    ratio,
):
    from novelvideo.generators.grid_generation import resolve_openai_image_size

    width, height = (
        int(value)
        for value in resolve_openai_image_size(
            ratio,
            resolution,
            model,
            allow_dynamic_resolution=True,
            min_pixels=3_686_400,
        ).split("x")
    )

    assert width * height >= 3_686_400


@pytest.mark.parametrize(
    ("resolution", "ratio", "expected"),
    [
        ("8K", "16:9", "8192x4608"),
        ("5.5K", "1:1", "5632x5632"),
        ("2048x1152", "1:1", "2048x1152"),
    ],
)
def test_newapi_image_size_supports_dynamic_admin_resolutions(
    resolution,
    ratio,
    expected,
):
    from novelvideo.generators.grid_generation import resolve_openai_image_size

    assert (
        resolve_openai_image_size(
            ratio,
            resolution,
            "future-image-model",
            allow_dynamic_resolution=True,
        )
        == expected
    )


def test_newapi_image_call_rejects_unrecognized_admin_resolution():
    from novelvideo.generators import grid_generation

    image_bytes, _text, error = run_async(
        grid_generation._call_newapi_image_api(
            api_key="newapi-token",
            model="future-image-model",
            prompt="prompt",
            image_config={"aspect_ratio": "16:9", "image_size": "ultra"},
            base_url="http://newapi.test/v1",
        )
    )

    assert image_bytes is None
    assert "unsupported image resolution: ultra" in error


def test_newapi_image_call_applies_admin_min_pixels(monkeypatch):
    import httpx
    from novelvideo.generators import grid_generation

    posted = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"image").decode()}]}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            posted["json"] = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    image_bytes, _text, error = run_async(
        grid_generation._call_newapi_image_api(
            api_key="newapi-token",
            model="arbitrary-admin-model-id",
            prompt="prompt",
            image_config={
                "aspect_ratio": "3:2",
                "image_size": "2048x1376",
                "request_schema": {
                    "endpoint": "images/generations",
                    "parameters": [],
                    "minPixels": 3_686_400,
                },
            },
            base_url="http://newapi.test/v1",
        )
    )

    assert image_bytes == b"image"
    assert error == ""
    width, height = posted["json"]["width"], posted["json"]["height"]
    assert width * height >= 3_686_400
    assert posted["json"]["metadata"] == {
        "ratio": "3:2",
        "resolution": "2048x1376",
    }
    assert posted["json"]["watermark"] is False
    assert "extra_fields" not in posted["json"]


def test_newapi_image_call_sends_gpt_image2_params(monkeypatch):
    import httpx
    from novelvideo.generators import grid_generation

    posted = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"image-bytes").decode()}]}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            self.kwargs = kwargs

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            posted["url"] = url
            posted["headers"] = headers
            posted["json"] = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    image_bytes, _text, error = run_async(
        grid_generation._call_newapi_image_api(
            api_key="newapi-token",
            # [aipdd fork delta wave2] 以前这里写的是 上游私有别名（一个目录与渠道都不再携带
            # 的名字）。用例守的是 gpt-image 家族的历史画质兜底，名字就用它的本名。
            model="gpt-image-2",
            prompt="portrait prompt",
            image_config={
                "aspect_ratio": "3:4",
                "image_size": "0.5K",
                "quality": "medium",
            },
            base_url="http://newapi.test/v1",
        )
    )

    assert image_bytes == b"image-bytes"
    assert error == ""
    assert posted["url"] == "http://newapi.test/v1/images/generations"
    assert posted["headers"]["Authorization"] == "Bearer newapi-token"
    assert posted["json"]["model"] == "gpt-image-2"
    assert posted["json"]["prompt"] == "portrait prompt"
    assert posted["json"]["quality"] == "medium"
    assert (posted["json"]["width"], posted["json"]["height"]) == (768, 1024)
    assert posted["json"]["metadata"] == {"ratio": "3:4", "resolution": "1k"}
    assert "extra_fields" not in posted["json"]


def test_newapi_image_auto_ratio_keeps_resolution_with_default_dimensions(monkeypatch):
    import httpx
    from novelvideo.generators import grid_generation

    posted = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"image-bytes").decode()}]}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            posted["json"] = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    image_bytes, _text, error = run_async(
        grid_generation._call_newapi_image_api(
            api_key="newapi-token",
            model="Acme-IMG-1",
            prompt="follow the reference geometry",
            image_config={"aspect_ratio": "auto", "image_size": "2K"},
            base_url="http://newapi.test/v1",
        )
    )

    assert image_bytes == b"image-bytes"
    assert error == ""
    # [aipdd fork delta #5] 图片网关（ap-image-2.0 等）要求请求必须带尺寸：
    # auto 比例不剥离/回退 IMAGE_DEFAULT_WIDTH/HEIGHT（上游原版会删掉宽高）。
    assert (posted["json"]["width"], posted["json"]["height"]) == (1440, 2560)
    assert "size" not in posted["json"]
    assert posted["json"]["metadata"] == {"ratio": "auto", "resolution": "2k"}


def test_newapi_image_call_reports_transport_exception_type(monkeypatch):
    import httpx
    from novelvideo.generators import grid_generation

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            raise httpx.ReadTimeout("")

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    image_bytes, _text, error = run_async(
        grid_generation._call_newapi_image_api(
            api_key="newapi-token",
            model="Acme-IMG-1",
            prompt="portrait prompt",
            image_config={"aspect_ratio": "16:9", "image_size": "1K"},
            base_url="http://newapi.test/v1",
        )
    )

    assert image_bytes is None
    assert "请求异常: ReadTimeout" in error
    assert "endpoint=http://newapi.test/v1" in error
    assert "model=Acme-IMG-1" in error


class _Transient400Response:
    """aipdd 网关把上游偶发失败统一包成的 HTTP 400「未通过上游校验」。"""

    status_code = 400
    headers: dict = {}
    text = '{"code":400,"message":"请求参数或媒体素材未通过上游校验，请检查后重试。","data":null}'

    def raise_for_status(self):
        request = httpx_request()
        response = httpx.Response(self.status_code, request=request, text=self.text)
        raise httpx.HTTPStatusError("400", request=request, response=response)


class _BadRequestResponse:
    """真正的坏参数 400：非瞬态，不应重试。"""

    status_code = 400
    headers: dict = {}
    text = '{"code":400,"message":"Invalid model name"}'

    def raise_for_status(self):
        request = httpx_request()
        response = httpx.Response(self.status_code, request=request, text=self.text)
        raise httpx.HTTPStatusError("400", request=request, response=response)


def httpx_request():
    import httpx

    return httpx.Request("POST", "http://newapi.test/v1/images/generations")


def _make_ok_response():
    class _OkResponse:
        status_code = 200
        headers: dict = {}
        text = ""

        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"ok-image").decode()}]}

    return _OkResponse()


def test_newapi_image_retries_transient_upstream_400(monkeypatch):
    """[aipdd fork delta] 瞬态 400「未通过上游校验」被指数退避重试后成功。"""
    import httpx
    from novelvideo.generators import grid_generation

    async def _no_sleep(_delay):
        return None

    monkeypatch.setattr(grid_generation.asyncio, "sleep", _no_sleep)

    calls = {"n": 0}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            calls["n"] += 1
            if calls["n"] < 3:
                return _Transient400Response()
            return _make_ok_response()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    image_bytes, _text, error = run_async(
        grid_generation._call_newapi_image_api(
            api_key="newapi-token",
            model="ap-image-2.0",
            prompt="sketch prompt",
            image_config={"aspect_ratio": "2:3", "image_size": "1K"},
            base_url="http://newapi.test/v1",
        )
    )

    assert calls["n"] == 3
    assert image_bytes == b"ok-image"
    assert error == ""


def test_newapi_image_does_not_retry_non_transient_400(monkeypatch):
    """非瞬态 400（真正的坏参数）只请求一次即失败，不空耗额度。"""
    import httpx
    from novelvideo.generators import grid_generation

    async def _no_sleep(_delay):
        return None

    monkeypatch.setattr(grid_generation.asyncio, "sleep", _no_sleep)

    calls = {"n": 0}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            calls["n"] += 1
            return _BadRequestResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    image_bytes, _text, error = run_async(
        grid_generation._call_newapi_image_api(
            api_key="newapi-token",
            model="ap-image-2.0",
            prompt="sketch prompt",
            image_config={"aspect_ratio": "2:3", "image_size": "1K"},
            base_url="http://newapi.test/v1",
        )
    )

    assert calls["n"] == 1
    assert image_bytes is None
    assert "HTTP 400" in error


def test_newapi_image_call_reraises_upstream_quota_error(monkeypatch):
    import httpx
    from novelvideo.generators import grid_generation

    class QuotaRejectedClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            raise RuntimeError(_QUOTA_ERROR_TEXT)

    monkeypatch.setattr(httpx, "AsyncClient", QuotaRejectedClient)

    with pytest.raises(RuntimeError, match="insufficient credits"):
        run_async(
            grid_generation._call_newapi_image_api(
                api_key="newapi-token",
                model="Acme-IMG-1",
                prompt="portrait prompt",
                base_url="http://newapi.test/v1",
            )
        )


def test_newapi_sketch_grid_reraises_upstream_quota_error(
    monkeypatch, tmp_path
):
    from novelvideo.generators import grid_generation

    async def fake_call_newapi_image_api(**_kwargs):
        raise RuntimeError(_QUOTA_ERROR_TEXT)

    monkeypatch.setattr(
        grid_generation,
        "_call_newapi_image_api",
        fake_call_newapi_image_api,
    )

    generator = grid_generation.GridGenerator(
        api_key="newapi-token",
        config={
            "provider": "newapi",
            "api_key": "newapi-token",
            "base_url": "http://newapi.test/v1",
            "model": "Acme-IMG-1",
            "rows": 1,
            "cols": 1,
            "batch_size": 1,
            "total_panels": 1,
            "mode": "1x1",
            "image_size": "1K",
            "openai_sketch_image_quality": "low",
        },
    )

    with pytest.raises(RuntimeError, match="insufficient credits"):
        run_async(
            generator.generate_grid(
                beats=[
                    {
                        "beat_number": 3,
                        "visual_description": "女主站在竹林中回头。",
                        "narration": "她终于察觉身后有人。",
                    }
                ],
                character_map={},
                style="chinese_period_drama",
                output_path=str(tmp_path / "sketch.png"),
                rows=1,
                cols=1,
                sketch=True,
                mode_key="1x1_2-3_sketch",
                location_beat_numbers=[3],
            )
        )


def test_newapi_image_call_omits_quality_for_nanobanana2(monkeypatch):
    import httpx
    from novelvideo.generators import grid_generation

    posted = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"image-bytes").decode()}]}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            posted["json"] = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    image_bytes, _text, error = run_async(
        grid_generation._call_newapi_image_api(
            api_key="newapi-token",
            model="Acme-IMG-2",
            prompt="portrait prompt",
            image_config={
                "aspect_ratio": "3:4",
                "image_size": "1K",
                "quality": "medium",
            },
            base_url="http://newapi.test/v1",
        )
    )

    assert image_bytes == b"image-bytes"
    assert error == ""
    assert posted["json"]["model"] == "Acme-IMG-2"
    assert "quality" not in posted["json"]
    assert (posted["json"]["width"], posted["json"]["height"]) == (768, 1024)
    assert posted["json"]["metadata"] == {"ratio": "3:4", "resolution": "1k"}
    assert "extra_fields" not in posted["json"]


def test_newapi_image_call_relays_reference_images(monkeypatch):
    import httpx
    from novelvideo.generators import grid_generation

    posted = {}
    relayed = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"image-bytes").decode()}]}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            posted["url"] = url
            posted["json"] = json
            return FakeResponse()

    def fake_upload_image_bytes(data, *, ext="png", ttl=None, image_transform=None):
        relayed.append((data, ext, ttl, image_transform))
        return f"https://relay.test/{len(relayed)}.png"

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(grid_generation, "upload_image_bytes", fake_upload_image_bytes)

    image_bytes, _text, error = run_async(
        grid_generation._call_newapi_image_api(
            api_key="newapi-token",
            model="Acme-IMG-1",
            prompt="identity prompt",
            reference_images=[b"ref-a", b"ref-b"],
            image_config={
                "aspect_ratio": "3:4",
                "image_size": "1K",
                "quality": "medium",
                "output_format": "png",
                "input_fidelity": "high",
            },
            base_url="http://newapi.test/v1",
        )
    )

    assert image_bytes == b"image-bytes"
    assert error == ""
    assert posted["url"] == "http://newapi.test/v1/images/edits"
    assert relayed == [
        (
            b"ref-a",
            "png",
            grid_generation.NEWAPI_MEDIA_INPUT_MIN_TTL_SECONDS,
            grid_generation.IMAGE_TRANSFORM_AI_REFERENCE_JPEG,
        ),
        (
            b"ref-b",
            "png",
            grid_generation.NEWAPI_MEDIA_INPUT_MIN_TTL_SECONDS,
            grid_generation.IMAGE_TRANSFORM_AI_REFERENCE_JPEG,
        ),
    ]
    assert posted["json"]["image"] == [
        "https://relay.test/1.png",
        "https://relay.test/2.png",
    ]
    assert "images" not in posted["json"]
    assert posted["json"]["output_format"] == "png"
    assert posted["json"]["input_fidelity"] == "high"
    assert "extra_fields" not in posted["json"]


def test_newapi_image_call_preserves_reference_image_extensions(monkeypatch):
    import httpx
    from novelvideo.generators import grid_generation

    relayed = []

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"image-bytes").decode()}]}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            return FakeResponse()

    def fake_upload_image_bytes(data, *, ext="png", ttl=None, image_transform=None):
        relayed.append((data, ext, ttl, image_transform))
        return f"https://relay.test/{len(relayed)}.{ext}"

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(grid_generation, "upload_image_bytes", fake_upload_image_bytes)

    image_bytes, _text, error = run_async(
        grid_generation._call_newapi_image_api(
            api_key="newapi-token",
            model="Acme-IMG-1",
            prompt="identity prompt",
            reference_images=[
                ("face.jpg", b"jpg-bytes", "image/jpeg"),
                (b"webp-bytes", "image/webp"),
            ],
            image_config={"aspect_ratio": "3:4", "image_size": "1K", "quality": "medium"},
            base_url="http://newapi.test/v1",
        )
    )

    assert image_bytes == b"image-bytes"
    assert error == ""
    assert relayed == [
        (
            b"jpg-bytes",
            "jpg",
            grid_generation.NEWAPI_MEDIA_INPUT_MIN_TTL_SECONDS,
            grid_generation.IMAGE_TRANSFORM_AI_REFERENCE_JPEG,
        ),
        (
            b"webp-bytes",
            "webp",
            grid_generation.NEWAPI_MEDIA_INPUT_MIN_TTL_SECONDS,
            grid_generation.IMAGE_TRANSFORM_AI_REFERENCE_JPEG,
        ),
    ]


def test_newapi_image_http_error_logs_redacted_request_context(monkeypatch, caplog):
    import httpx
    from novelvideo.generators import grid_generation

    posted: dict[str, object] = {}

    class FakeResponse:
        status_code = 400
        text = '{"error":{"message":"openai_error","type":"bad_response_status_code"}}'
        headers = {
            "x-newapi-request-id": "req-123",
            "cf-ray": "cf-ray-456",
            "date": "Fri, 22 May 2026 03:00:00 GMT",
            "authorization": "Bearer should-not-leak",
        }

        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "bad response",
                request=httpx.Request("POST", "http://newapi.test/v1/images/generations"),
                response=self,
            )

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            posted["url"] = url
            posted["headers"] = headers
            posted["json"] = json
            return FakeResponse()

    def fake_upload_image_bytes(data, *, ext="png", ttl=None, image_transform=None):
        return f"https://relay.test/signed-{data.decode()}?token=secret"

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(grid_generation, "upload_image_bytes", fake_upload_image_bytes)
    caplog.set_level(logging.WARNING, logger="novelvideo.generators.grid_generation")

    image_bytes, _text, error = run_async(
        grid_generation._call_newapi_image_api(
            api_key="newapi-token",
            model="Acme-IMG-1",
            prompt="sensitive prompt body",
            reference_images=[b"ref-a"],
            image_config={"aspect_ratio": "2:1", "image_size": "2K", "quality": "medium"},
            base_url="http://newapi.test/v1",
        )
    )

    log_text = "\n".join(record.getMessage() for record in caplog.records)

    assert image_bytes is None
    assert "request_id=req-123" in error
    assert "cf-ray-456" in error
    assert "model=Acme-IMG-1" in error
    assert "width=2048" in error
    assert "height=1024" in error
    assert "geometry_metadata={'ratio': '2:1', 'resolution': '2k'}" in error
    assert "reference_image_count=1" in error
    assert "request_id=req-123" in log_text
    assert "http://newapi.test/v1/images/edits" in log_text
    assert "prompt_sha256=" in log_text
    assert "sensitive prompt body" not in error
    assert "sensitive prompt body" not in log_text
    assert "newapi-token" not in error
    assert "newapi-token" not in log_text
    assert "token=secret" not in error
    assert "token=secret" not in log_text
    # [aipdd fork delta] WP-D: 这里原本还断言失败时退还积分预约；额度体系已拆除，
    # 改为继续守住「上游响应头里的凭据不外泄」这条红线。
    assert "should-not-leak" not in error
    assert "should-not-leak" not in log_text


def test_newapi_image_http_5xx_does_not_retry_in_app(monkeypatch):
    import httpx
    from novelvideo.generators import grid_generation

    attempts = 0

    class FailingResponse:
        status_code = 502
        text = '{"error":{"message":"error","type":"bad_response"}}'
        headers = {"x-oneapi-request-id": "req-fail"}

        def raise_for_status(self):
            raise httpx.HTTPStatusError(
                "bad gateway",
                request=httpx.Request("POST", "http://newapi.test/v1/images/generations"),
                response=self,
            )

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            nonlocal attempts
            attempts += 1
            return FailingResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    image_bytes, _text, error = run_async(
        grid_generation._call_newapi_image_api(
            api_key="newapi-token",
            model="Acme-IMG-1",
            prompt="retry prompt",
            image_config={"aspect_ratio": "2:1", "image_size": "2K", "quality": "medium"},
            base_url="http://newapi.test/v1",
        )
    )

    assert image_bytes is None
    assert "HTTP 502" in error
    assert "request_id=req-fail" in error
    assert attempts == 1


def test_newapi_identity_image_sends_portrait_then_costume_references(
    monkeypatch,
    tmp_path,
):
    from novelvideo.generators import character_generation

    captured = {}

    async def fake_call_newapi_image_api(**kwargs):
        captured.update(kwargs)
        return b"identity-image", "", ""

    monkeypatch.setattr(
        character_generation,
        "_call_newapi_image_api",
        fake_call_newapi_image_api,
    )

    generator = character_generation.CharacterGenerator(
        config={
            "provider": "newapi",
            "api_key": "newapi-token",
            "model": "Acme-IMG-2",
            "base_url": "http://newapi.test/v1",
        }
    )
    output_path = tmp_path / "identity_body_temp.png"

    image_bytes = run_async(
        generator._generate_with_reference(
            client=None,
            prompt="identity prompt",
            reference_image=None,
            output_path=str(output_path),
            reference_image_bytes=b"portrait-bytes",
            reference_image_name="/project/characters/李雷/reference_portrait.jpg",
            aspect_ratio="16:9",
            image_size="1K",
            additional_image_bytes=[b"costume-bytes"],
            additional_image_names=["/project/characters/李雷/学生_costume.png"],
        )
    )

    assert image_bytes == b"identity-image"
    assert output_path.read_bytes() == b"identity-image"
    assert captured["model"] == "Acme-IMG-2"
    assert captured["base_url"] == "http://newapi.test/v1"
    assert captured["image_config"] == {
        "aspect_ratio": "16:9",
        "image_size": "1K",
        "quality": "medium",
    }
    assert captured["reference_images"] == [
        ("reference_portrait.jpg", b"portrait-bytes", "image/jpeg"),
        ("学生_costume.png", b"costume-bytes", "image/png"),
    ]


def test_newapi_character_portrait_reraises_upstream_quota_error(
    monkeypatch, tmp_path
):
    from novelvideo.generators import character_generation

    async def fake_call_newapi_image_api(**_kwargs):
        raise RuntimeError(_QUOTA_ERROR_TEXT)

    monkeypatch.setattr(
        character_generation,
        "_call_newapi_image_api",
        fake_call_newapi_image_api,
    )

    generator = character_generation.CharacterGenerator(
        config={
            "provider": "newapi",
            "api_key": "newapi-token",
            "model": "Acme-IMG-1",
            "base_url": "http://newapi.test/v1",
        }
    )

    with pytest.raises(RuntimeError, match="insufficient credits"):
        run_async(
            generator.generate_character_portrait(
                character_name="李雷",
                character_prompt="young man",
                output_dir=str(tmp_path),
            )
        )


def test_newapi_character_portrait_raise_on_error_preserves_provider_detail(monkeypatch, tmp_path):
    import novelvideo.config as config
    from novelvideo.generators import image_generator, character_generation

    async def fake_call_newapi_image_api(**_kwargs):
        return None, "", "HTTP 504: request_id=req-123; body=provider timeout"

    monkeypatch.setenv("LOCALAPI_API_KEY", "newapi-token")
    monkeypatch.setenv("LOCALAPI_BASE_URL", "http://newapi.test/v1")
    monkeypatch.setenv("NEWAPI_IMAGE_MODEL", "Acme-IMG-1")
    monkeypatch.setenv("DEFAULT_CHARACTER_IMAGE_SELECTION", "newapi_gpt_image2")
    _reload_isolated(monkeypatch, config)
    monkeypatch.setattr(
        character_generation,
        "_call_newapi_image_api",
        fake_call_newapi_image_api,
    )

    with pytest.raises(RuntimeError, match="HTTP 504: request_id=req-123"):
        run_async(
            image_generator.generate_character_reference_unified(
                character_name="李雷",
                appearance_prompt="young man",
                output_dir=str(tmp_path),
                count=1,
                model="newapi_gpt_image2",
                raise_on_error=True,
            )
        )


def test_newapi_scene_master_uses_text_only_nanobanana2(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    from novelvideo.generators import scene_reference_images
    from novelvideo.models import NovelScene

    captured = {}

    async def fake_call_newapi_image_api(**kwargs):
        captured.update(kwargs)
        return b"scene-master", "", ""

    monkeypatch.setenv("LOCALAPI_API_KEY", "newapi-token")
    monkeypatch.setenv("LOCALAPI_BASE_URL", "http://newapi.test/v1")
    monkeypatch.setenv("SCENE_MASTER_IMAGE_PROVIDER", "newapi")
    monkeypatch.setenv("SCENE_MASTER_IMAGE_MODEL", "Acme-IMG-2")
    _patch_scene_newapi_gateway(monkeypatch)
    monkeypatch.setattr(
        scene_reference_images,
        "_call_newapi_image_api",
        fake_call_newapi_image_api,
    )
    monkeypatch.setattr(scene_reference_images, "SCENE_MASTER_IMAGE_PROVIDER", "newapi")
    # [aipdd fork delta wave2] master 取面板副位；以前这里是 SCENE_MASTER_IMAGE_MODEL
    # env 钉一个设置页看不见、渠道下架也不会自动取消的名字。
    _pin_default_image_models(monkeypatch, secondary="ap-nanobanana-2")

    scene = NovelScene(
        name="古董店",
        scene_type="interior",
        environment_prompt="从店门可以直接看到收银台，周围堆放着一些古董",
    )

    output_path = run_async(
        scene_reference_images.generate_scene_reference_image(
            project_dir=tmp_path,
            scene=scene,
            kind="master",
            style_name="live_action",
            style_prompt="grounded realism",
            avoid_instructions="no people",
        )
    )

    assert output_path == tmp_path / "assets" / "scenes" / "古董店" / "master.png"
    assert output_path.read_bytes() == b"scene-master"
    assert captured["api_key"] == "newapi-token"
    assert captured["base_url"] == "http://newapi.test/v1"
    assert captured["model"] == "ap-nanobanana-2"
    assert captured["reference_images"] is None
    assert captured["image_config"] == {
        "aspect_ratio": "16:9",
        "image_size": "1K",
        "output_format": "png",
    }
    assert "SCENE NAME: 古董店" in captured["prompt"]
    assert "从店门可以直接看到收银台" in captured["prompt"]


@pytest.mark.asyncio
async def test_newapi_scene_reference_accepts_copied_file_without_image_bytes(
    monkeypatch, tmp_path
):
    from pathlib import Path

    from novelvideo.generators import scene_reference_images
    from novelvideo.models import NovelScene

    async def copied_image(**kwargs):
        assert kwargs["read_copied_bytes"] is False
        Path(kwargs["delivery_path"]).write_bytes(b"copied-scene")
        kwargs["delivery_state"]["copied"] = True
        return None, "", ""

    _patch_scene_newapi_gateway(monkeypatch)
    monkeypatch.setattr(scene_reference_images, "SCENE_MASTER_IMAGE_PROVIDER", "newapi")
    _pin_default_image_models(monkeypatch, secondary="ap-nanobanana-2")
    monkeypatch.setattr(scene_reference_images, "_call_newapi_image_api", copied_image)
    scene = NovelScene(name="古董店", scene_type="interior", environment_prompt="古董店室内")

    output = await scene_reference_images.generate_scene_reference_image(
        project_dir=tmp_path,
        scene=scene,
        kind="master",
    )
    assert output.read_bytes() == b"copied-scene"


def test_newapi_scene_time_plate_master_injects_time_and_base_reference(monkeypatch, tmp_path):
    from novelvideo.generators import scene_reference_images
    from novelvideo.models import NovelScene

    captured = {}

    async def fake_call_newapi_image_api(**kwargs):
        captured.update(kwargs)
        return b"scene-night-master", "", ""

    base_master_path = tmp_path / "assets" / "scenes" / "古董店" / "master.png"
    base_master_path.parent.mkdir(parents=True)
    base_master_path.write_bytes(b"base-master-bytes")

    monkeypatch.setattr(
        scene_reference_images,
        "_call_newapi_image_api",
        fake_call_newapi_image_api,
    )
    _patch_scene_newapi_gateway(monkeypatch)
    monkeypatch.setattr(scene_reference_images, "SCENE_MASTER_IMAGE_PROVIDER", "newapi")
    _pin_default_image_models(monkeypatch, secondary="ap-nanobanana-2")

    scene = NovelScene(
        name="古董店_夜晚",
        base_scene_id="古董店",
        time_of_day="夜晚",
        scene_type="interior",
        environment_prompt="正面：收银台与古董柜\n光源：中性基础光",
    )

    output_path = run_async(
        scene_reference_images.generate_scene_reference_image(
            project_dir=tmp_path,
            scene=scene,
            kind="master",
        )
    )

    assert output_path == tmp_path / "assets" / "scenes" / "古董店_夜晚" / "master.png"
    assert output_path.read_bytes() == b"scene-night-master"
    assert captured["reference_images"] == [
        ("base_scene_master_master.png", b"base-master-bytes", "image/png")
    ]
    assert "TARGET TIME-OF-DAY PLATE: 夜晚" in captured["prompt"]
    assert "overall lighting must read as 夜晚" in captured["prompt"]
    assert "Keep the same architecture" in captured["prompt"]


def test_newapi_scene_variant_plate_master_keeps_described_lighting(monkeypatch, tmp_path):
    from novelvideo.generators import scene_reference_images
    from novelvideo.models import NovelScene

    captured = {}

    async def fake_call_newapi_image_api(**kwargs):
        captured.update(kwargs)
        return b"scene-variant-master", "", ""

    base_master_path = tmp_path / "assets" / "scenes" / "城市街道" / "master.png"
    base_master_path.parent.mkdir(parents=True)
    base_master_path.write_bytes(b"base-master-bytes")

    monkeypatch.setattr(
        scene_reference_images,
        "_call_newapi_image_api",
        fake_call_newapi_image_api,
    )
    _patch_scene_newapi_gateway(monkeypatch)
    monkeypatch.setattr(scene_reference_images, "SCENE_MASTER_IMAGE_PROVIDER", "newapi")
    _pin_default_image_models(monkeypatch, secondary="ap-nanobanana-2")

    scene = NovelScene(
        name="城市街道_雨夜版",
        base_scene_id="城市街道",
        variant_id="雨夜版",
        scene_type="exterior",
        environment_prompt="正面：湿漉沥青马路\n光源：路灯昏暗，积水反光，雨夜氛围",
    )

    output_path = run_async(
        scene_reference_images.generate_scene_reference_image(
            project_dir=tmp_path,
            scene=scene,
            kind="master",
        )
    )

    assert output_path == tmp_path / "assets" / "scenes" / "城市街道_雨夜版" / "master.png"
    assert captured["reference_images"] == [
        ("base_scene_master_master.png", b"base-master-bytes", "image/png")
    ]
    assert "STRUCTURED VARIANT PLATE" in captured["prompt"]
    assert "variant_id=雨夜版" in captured["prompt"]
    assert "do NOT neutralize" in captured["prompt"]
    # The base-scene neutralizer must not fire for variant plates.
    assert "IGNORE mood/time-of-day phrases" not in captured["prompt"]


def test_newapi_reverse_master_uses_master_reference_nanobanana2(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    from novelvideo.generators import scene_reference_images
    from novelvideo.models import NovelScene

    captured = {}

    async def fake_call_newapi_image_api(**kwargs):
        captured.update(kwargs)
        return b"scene-reverse", "", ""

    master_path = tmp_path / "assets" / "scenes" / "古董店" / "master.png"
    master_path.parent.mkdir(parents=True)
    master_path.write_bytes(b"master-bytes")

    monkeypatch.setattr(
        scene_reference_images,
        "_call_newapi_image_api",
        fake_call_newapi_image_api,
    )
    _patch_scene_newapi_gateway(monkeypatch)
    monkeypatch.setattr(scene_reference_images, "SCENE_REVERSE_MASTER_IMAGE_PROVIDER", "newapi")
    # [aipdd fork delta wave2] 旧写法是 SCENE_REVERSE_MASTER_IMAGE_MODEL env 钉一个
    # 设置页看不见的名字；现在 reverse_master 就是面板副位。
    _pin_default_image_models(monkeypatch, secondary="ap-nanobanana-2")

    scene = NovelScene(
        name="古董店",
        scene_type="interior",
        environment_prompt="从店门可以直接看到收银台，周围堆放着一些古董",
    )

    output_path = run_async(
        scene_reference_images.generate_scene_reference_image(
            project_dir=tmp_path,
            scene=scene,
            kind="reverse_master",
            style_name="live_action",
            style_prompt="grounded realism",
            avoid_instructions="no people",
        )
    )

    assert output_path == tmp_path / "assets" / "scenes" / "古董店" / "reverse_master.png"
    assert output_path.read_bytes() == b"scene-reverse"
    assert captured["api_key"] == "newapi-token"
    assert captured["base_url"] == "http://newapi.test/v1"
    assert captured["model"] == "ap-nanobanana-2"
    assert captured["reference_images"] == [
        ("scene_master_master.png", b"master-bytes", "image/png")
    ]
    assert captured["image_config"] == {
        "aspect_ratio": "16:9",
        "image_size": "1K",
        "output_format": "png",
    }
    assert "REFERENCE 1 = the scene's FRONT-FACING master" in captured["prompt"]


def test_newapi_reverse_master_can_use_gpt_image2_quality_medium(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    from novelvideo.generators import scene_reference_images
    from novelvideo.models import NovelScene

    captured = {}

    async def fake_call_newapi_image_api(**kwargs):
        captured.update(kwargs)
        return b"scene-reverse", "", ""

    master_path = tmp_path / "assets" / "scenes" / "古董店" / "master.png"
    master_path.parent.mkdir(parents=True)
    master_path.write_bytes(b"master-bytes")

    monkeypatch.setattr(
        scene_reference_images,
        "_call_newapi_image_api",
        fake_call_newapi_image_api,
    )
    _patch_scene_newapi_gateway(monkeypatch)
    monkeypatch.setattr(scene_reference_images, "SCENE_REVERSE_MASTER_IMAGE_PROVIDER", "newapi")
    # [aipdd fork delta wave2] reverse_master 取面板的**副位**；名字不再由 env 点名。
    _pin_default_image_models(monkeypatch, secondary="gpt-image-2")

    scene = NovelScene(
        name="古董店",
        scene_type="interior",
        environment_prompt="从店门可以直接看到收银台，周围堆放着一些古董",
    )

    run_async(
        scene_reference_images.generate_scene_reference_image(
            project_dir=tmp_path,
            scene=scene,
            kind="reverse_master",
        )
    )

    assert captured["model"] == "gpt-image-2"
    assert captured["reference_images"] == [
        ("scene_master_master.png", b"master-bytes", "image/png")
    ]
    assert captured["image_config"] == {
        "aspect_ratio": "16:9",
        "image_size": "1K",
        "output_format": "png",
        "quality": "medium",
    }


def test_newapi_prop_reference_gpt_image2_sends_quality_medium(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    import httpx
    import novelvideo.config as config
    from novelvideo.generators import prop_generation

    posted = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"prop-ref").decode()}]}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            posted["url"] = url
            posted["headers"] = headers
            posted["json"] = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setenv("LOCALAPI_API_KEY", "newapi-token")
    monkeypatch.setenv("LOCALAPI_BASE_URL", "http://newapi.test/v1")
    monkeypatch.setenv("PROP_REF_IMAGE_PROVIDER", "newapi")
    # [aipdd fork delta wave2] ``PROP_REF_IMAGE_MODEL`` 已退出解析链，只认设置的主位。
    monkeypatch.setenv("PROP_REF_IMAGE_MODEL", "Acme-IMG-1")
    _pin_default_image_models(monkeypatch, primary="gpt-image-2")
    _reload_isolated(monkeypatch, config)
    prop_generation = _reload_isolated(monkeypatch, prop_generation)
    monkeypatch.setattr(
        prop_generation,
        "get_grid_generation_config",
        lambda: {"openai_image_quality": "medium"},
    )

    output_path = tmp_path / "assets" / "props" / "玉佩" / "reference_3view.png"
    result = run_async(
        prop_generation.generate_prop_reference(
            visual_prompt="青绿色玉佩，边缘有金色纹路",
            output_path=str(output_path),
        )
    )

    assert result == str(output_path)
    assert output_path.read_bytes() == b"prop-ref"
    assert posted["url"] == "http://newapi.test/v1/images/generations"
    assert posted["headers"]["Authorization"] == "Bearer newapi-token"
    assert posted["json"]["model"] == "gpt-image-2"
    assert posted["json"]["quality"] == "medium"
    assert (posted["json"]["width"], posted["json"]["height"]) == (1088, 608)
    assert posted["json"]["metadata"] == {"ratio": "16:9", "resolution": "1k"}
    assert "extra_fields" not in posted["json"]


def test_newapi_prop_reference_nanobanana2_omits_quality(monkeypatch, tmp_path):
    _isolate_settings_db(monkeypatch, tmp_path)
    import httpx
    import novelvideo.config as config
    from novelvideo.generators import prop_generation

    posted = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"data": [{"b64_json": base64.b64encode(b"prop-ref").decode()}]}

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        async def post(self, url, *, headers, json):
            posted["json"] = json
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setenv("LOCALAPI_API_KEY", "newapi-token")
    monkeypatch.setenv("LOCALAPI_BASE_URL", "http://newapi.test/v1")
    monkeypatch.setenv("PROP_REF_IMAGE_PROVIDER", "newapi")
    # [aipdd fork delta wave2] 同名 env 照设，已不再进解析链：模型名只从设置来。
    monkeypatch.setenv("PROP_REF_IMAGE_MODEL", "Acme-IMG-2")
    _pin_default_image_models(monkeypatch, primary="ap-nanobanana-2")
    _reload_isolated(monkeypatch, config)
    prop_generation = _reload_isolated(monkeypatch, prop_generation)
    monkeypatch.setattr(
        prop_generation,
        "get_grid_generation_config",
        lambda: {"openai_image_quality": "medium"},
    )

    output_path = tmp_path / "assets" / "props" / "玉佩" / "reference_3view.png"
    result = run_async(
        prop_generation.generate_prop_reference(
            visual_prompt="青绿色玉佩，边缘有金色纹路",
            output_path=str(output_path),
        )
    )

    assert result == str(output_path)
    assert output_path.read_bytes() == b"prop-ref"
    assert posted["json"]["model"] == "ap-nanobanana-2"
    assert "quality" not in posted["json"]
    assert (posted["json"]["width"], posted["json"]["height"]) == (1088, 608)
    assert posted["json"]["metadata"] == {"ratio": "16:9", "resolution": "1k"}
    assert "extra_fields" not in posted["json"]


def test_newapi_prop_reference_reraises_upstream_quota_error(
    monkeypatch, tmp_path
):
    _isolate_settings_db(monkeypatch, tmp_path)
    import novelvideo.config as config
    from novelvideo.generators import prop_generation

    async def fake_call_newapi_image_api(**_kwargs):
        raise RuntimeError(_QUOTA_ERROR_TEXT)

    monkeypatch.setenv("LOCALAPI_API_KEY", "newapi-token")
    monkeypatch.setenv("LOCALAPI_BASE_URL", "http://newapi.test/v1")
    monkeypatch.setenv("PROP_REF_IMAGE_PROVIDER", "newapi")
    monkeypatch.setenv("PROP_REF_IMAGE_MODEL", "Acme-IMG-1")
    _reload_isolated(monkeypatch, config)
    prop_generation = _reload_isolated(monkeypatch, prop_generation)
    monkeypatch.setattr(prop_generation, "_call_newapi_image_api", fake_call_newapi_image_api)
    monkeypatch.setattr(
        prop_generation,
        "get_grid_generation_config",
        lambda: {"openai_image_quality": "medium"},
    )

    with pytest.raises(RuntimeError, match="insufficient credits"):
        run_async(
            prop_generation.generate_prop_reference(
                visual_prompt="青绿色玉佩，边缘有金色纹路",
                output_path=str(tmp_path / "reference_3view.png"),
            )
        )


def test_freezone_single_image_generation_routes_newapi(monkeypatch, tmp_path):
    from novelvideo.generators import grid_generation

    captured = {}

    async def fake_call_newapi_image_api(**kwargs):
        captured.update(kwargs)
        return b"freezone-image", "", ""

    monkeypatch.setattr(
        grid_generation, "_call_newapi_image_api", fake_call_newapi_image_api
    )

    output_path = tmp_path / "freezone.png"
    image_path = run_async(
        grid_generation.generate_text_to_image(
            prompt="freezone prompt",
            output_path=str(output_path),
            aspect_ratio="1:1",
            image_size="2K",
            quality="medium",
            config={
                "provider": "newapi",
                "api_key": "newapi-token",
                "model": "Acme-IMG-1",
                "base_url": "http://newapi.test/v1",
                "openai_image_quality": "medium",
                "openai_sketch_image_quality": "low",
                "image_size": "2K",
                "mode": "1x1",
                "rows": 1,
                "cols": 1,
                "total_panels": 1,
            },
        )
    )

    assert image_path == output_path
    assert output_path.read_bytes() == b"freezone-image"
    assert captured["api_key"] == "newapi-token"
    assert captured["model"] == "Acme-IMG-1"
    assert captured["prompt"] == "freezone prompt"
    assert captured["reference_images"] is None
    assert captured["base_url"] == "http://newapi.test/v1"
    assert captured["image_config"] == {
        "aspect_ratio": "1:1",
        "image_size": "2K",
        "quality": "medium",
    "request_schema": {},
        "model_params": {},
    }


def run_async(coro):
    import asyncio

    return asyncio.run(coro)
