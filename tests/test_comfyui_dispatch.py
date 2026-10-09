"""[aipdd fork delta] ComfyUI 渠道驱动分发测试。

覆盖方案第七节的分发口径：
- 图片入口 ``_generate_image`` 在 ``provider==comfyui`` 时命中通用执行器（媒体类型
  ``image``、宽高比+档位推导尺寸、仅本地参考图注入、以 prompt_id 记一次观测），并在组织
  上下文先过出网闸门；缺渠道配置明确报错。
- ``create_video_generator`` 对 comfyui 渠道模型返回渠道驱动封装，绝不落到
  NewApi 桥接或旧的硬编码分支。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from novelvideo.comfyui_executor import ComfyUIExecutionResult
from novelvideo.egress_context import TrustedEgressContext
from novelvideo.ports.authz import BillingPrincipal
from novelvideo.ports.model_credentials import CredentialReference


def _context(*, kind: str) -> TrustedEgressContext:
    organization = kind == "organization"
    return TrustedEgressContext(
        envelope_id=f"env-{kind}",
        project_id="project-comfy",
        task_type="image_generate",
        requester_user_id="user-comfy",
        root_task_id="root-comfy",
        admission_id="admission-comfy",
        admitted_at="2026-08-03T04:05:00Z",
        membership_id="membership-comfy" if organization else None,
        authz_version=3,
        billing_principal=BillingPrincipal(
            kind=kind,
            id="org-comfy" if organization else "user-comfy",
        ),
        credential=CredentialReference(
            source="organization" if organization else "platform",
            credential_id="credential-comfy" if organization else "platform-newapi",
            key_version=7 if organization else 1,
            org_id="org-comfy" if organization else None,
        ),
    )


_IMAGE_WF = {"1": {"class_type": "SaveImage", "inputs": {}}}


class _FakeMeter:
    """[aipdd fork delta] WP-D: 观测面只剩「记一次上游调用」，没有预约与退款。"""

    def __init__(self) -> None:
        self.observed: list[str] = []

    async def bump_model_call(self, **kwargs) -> None:
        self.observed.append(str(kwargs.get("provider_request_id") or ""))


@pytest.mark.asyncio
async def test_image_provider_comfyui_hits_executor(monkeypatch, tmp_path) -> None:
    """图片入口 provider==comfyui：命中通用执行器，媒体类型 image、参数注入正确。"""
    from novelvideo.generators import grid_generation as gg

    ref_local = tmp_path / "ref.png"
    ref_local.write_bytes(b"ref")
    captured: dict[str, object] = {}

    async def fake_execute(self, **kwargs):
        captured.update(kwargs)
        output = Path(kwargs["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"image-bytes")
        return ComfyUIExecutionResult(
            success=True, data=b"image-bytes", output_path=str(output), prompt_id="p-1"
        )

    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.resolve_comfyui_channel_execution",
        lambda model, media_type: (
            ("http://comfy.example:8188", _IMAGE_WF)
            if (model == "my-comfy-img" and media_type == "image")
            else None
        ),
    )
    monkeypatch.setattr(
        "novelvideo.comfyui_executor.ComfyUIWorkflowExecutor",
        type(
            "Stub",
            (),
            {
                "__init__": lambda self, base_url, **kw: None,
                "execute": fake_execute,
            },
        ),
    )

    output = await gg._generate_image(
        prompt="a red panda",
        reference_image_paths=[str(ref_local), "https://remote.example/x.png"],
        output_path=str(tmp_path / "out" / "image.png"),
        aspect_ratio="16:9",
        image_size="2k",
        quality="high",
        api_key=None,
        config={"provider": "comfyui", "model": "my-comfy-img"},
        egress_context=_context(kind="platform"),
        egress_capability="image_generate",
    )

    assert output.exists()
    assert captured["media_type"] == "image"
    assert captured["prompt"] == "a red panda"
    # 仅本地存在的参考图会注入，远程 URL 被过滤。
    assert captured["reference_image_paths"] == [str(ref_local)]
    # 16:9 + 2k（long_edge=2048）推导：width=2048, height=1152。
    assert captured["width"] == 2048
    assert captured["height"] == 1152


@pytest.mark.asyncio
async def test_image_comfyui_org_denied_before_network(monkeypatch, tmp_path) -> None:
    """组织上下文下 comfyui 图片在触达网络前被拒绝（ORG_EGRESS_DENIED）。"""
    from novelvideo.ports.egress import EgressError
    from novelvideo.generators import grid_generation as gg

    called = {"executor": False}

    def _no_executor(*_args, **_kwargs):
        called["executor"] = True
        raise AssertionError("executor must not be constructed")

    monkeypatch.setattr(
        "novelvideo.comfyui_executor.ComfyUIWorkflowExecutor", _no_executor
    )

    with pytest.raises(EgressError) as exc_info:
        await gg._generate_image(
            prompt="p",
            reference_image_paths=[],
            output_path=str(tmp_path / "image.png"),
            aspect_ratio="1:1",
            image_size="1k",
            quality=None,
            api_key=None,
            config={"provider": "comfyui", "model": "my-comfy-img"},
            egress_context=_context(kind="organization"),
            egress_capability="image_generate",
        )
    assert exc_info.value.code == "ORG_EGRESS_DENIED"
    assert called["executor"] is False


@pytest.mark.asyncio
async def test_image_comfyui_observes_prompt_id_as_request_handle(monkeypatch, tmp_path) -> None:
    """comfyui 直连无 provider request_id：以本地 prompt_id 作为上游调用凭据。"""
    from novelvideo.generators import grid_generation as gg

    meter = _FakeMeter()

    async def fake_execute(self, **kwargs):
        output = Path(kwargs["output_path"])
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"image-bytes")
        return ComfyUIExecutionResult(
            success=True, data=b"image-bytes", output_path=str(output), prompt_id="p-99"
        )

    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.resolve_comfyui_channel_execution",
        lambda model, media_type: ("http://comfy:8188", _IMAGE_WF),
    )
    monkeypatch.setattr(
        "novelvideo.comfyui_executor.ComfyUIWorkflowExecutor",
        type(
            "Stub",
            (),
            {
                "__init__": lambda self, base_url, **kw: None,
                "execute": fake_execute,
            },
        ),
    )
    monkeypatch.setattr(gg, "get_usage_meter", lambda: meter)

    await gg._generate_image(
        prompt="p",
        reference_image_paths=[],
        output_path=str(tmp_path / "image.png"),
        aspect_ratio="1:1",
        image_size="1k",
        quality=None,
        api_key=None,
        config={"provider": "comfyui", "model": "my-comfy-img"},
        egress_context=_context(kind="platform"),
        egress_capability="image_generate",
    )
    assert meter.observed == ["p-99"]
    assert (tmp_path / "image.png").read_bytes() == b"image-bytes"


@pytest.mark.asyncio
async def test_image_comfyui_failure_raises_readable_error(
    monkeypatch, tmp_path
) -> None:
    """执行失败：抛出可读错误（WP-D 起不再有任何退款动作）。"""
    from novelvideo.generators import grid_generation as gg

    meter = _FakeMeter()

    async def fake_execute(self, **kwargs):
        return ComfyUIExecutionResult(success=False, error="boom")

    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.resolve_comfyui_channel_execution",
        lambda model, media_type: ("http://comfy:8188", _IMAGE_WF),
    )
    monkeypatch.setattr(
        "novelvideo.comfyui_executor.ComfyUIWorkflowExecutor",
        type(
            "Stub",
            (),
            {
                "__init__": lambda self, base_url, **kw: None,
                "execute": fake_execute,
            },
        ),
    )
    monkeypatch.setattr(gg, "get_usage_meter", lambda: meter)

    with pytest.raises(ValueError, match="boom"):
        await gg._generate_image(
            prompt="p",
            reference_image_paths=[],
            output_path=str(tmp_path / "image.png"),
            aspect_ratio="1:1",
            image_size="1k",
            quality=None,
            api_key=None,
            config={"provider": "comfyui", "model": "my-comfy-img"},
            egress_context=_context(kind="platform"),
            egress_capability="image_generate",
        )
    # 失败的上游调用不进观测账（记的是成功调用）。
    assert meter.observed == []


@pytest.mark.asyncio
async def test_image_comfyui_missing_channel_raises(monkeypatch, tmp_path) -> None:
    """未配置 comfyui 图片渠道工作流：明确报错引导，不落 NewApi。"""
    from novelvideo.generators import grid_generation as gg

    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.resolve_comfyui_channel_execution",
        lambda model, media_type: None,
    )

    with pytest.raises(ValueError) as exc_info:
        await gg._generate_image(
            prompt="p",
            reference_image_paths=[],
            output_path=str(tmp_path / "image.png"),
            aspect_ratio="1:1",
            image_size="1k",
            quality=None,
            api_key=None,
            config={"provider": "comfyui", "model": "unconfigured"},
            egress_context=_context(kind="platform"),
            egress_capability="image_generate",
        )
    assert "comfyui" in str(exc_info.value)


def test_create_video_generator_comfyui_channel_not_newapi(monkeypatch) -> None:
    """create_video_generator 对 comfyui 渠道模型返回渠道驱动封装，不落 NewApi。"""
    from novelvideo.generators import video_generator as vg

    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.default_comfyui_channel_model",
        lambda media_type: "my-comfy-wan" if media_type == "video" else None,
    )
    monkeypatch.setattr(
        "novelvideo.model_gateway_settings.is_comfyui_channel_model",
        lambda model, media_type=None: model == "my-comfy-wan",
    )

    generator = vg.create_video_generator(
        "comfyui", egress_context=_context(kind="platform")
    )
    assert isinstance(generator, vg.ComfyUIVideoGenerator)
    assert not isinstance(generator, vg.NewApiVideoGenerator)
    assert generator.model == "my-comfy-wan"
