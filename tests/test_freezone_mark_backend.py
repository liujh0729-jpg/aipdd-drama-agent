import pytest
from PIL import Image

from novelvideo import ports
from novelvideo.api.routes import freezone as freezone_routes
from novelvideo.freezone import mark_node
from novelvideo.freezone.mark_node import build_mark_detection_task, crop_mark_focus_image
from novelvideo.freezone.vision_gateway import FREEZONE_MARK_TIMEOUT_SECONDS
from novelvideo.ports.local import LocalAuthz


def test_build_mark_detection_task_includes_point() -> None:
    task = build_mark_detection_task(
        point_x=0.2,
        point_y=0.45,
    )
    assert "点击点归一化坐标" in task


def test_build_mark_detection_task_includes_box() -> None:
    task = build_mark_detection_task(
        box_x=0.1,
        box_y=0.2,
        box_width=0.3,
        box_height=0.25,
    )
    assert "框选区域归一化坐标" in task


def test_crop_mark_focus_image_returns_png_bytes(tmp_path) -> None:
    path = tmp_path / "mark.png"
    Image.new("RGB", (100, 100), color="white").save(path)
    data = crop_mark_focus_image(path, point_x=0.5, point_y=0.5)
    assert data.startswith(b"\x89PNG")


@pytest.mark.asyncio
async def test_mark_detection_uses_shared_freezone_vision_model(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "mark.png"
    Image.new("RGB", (100, 100), color="white").save(path)
    captured = {}

    async def fake_call_freezone_vision_model(**kwargs):
        captured.update(kwargs)
        return "DC-freezone-vision-LLM", '{"label":"老人","note":"主体人物"}'

    monkeypatch.setattr(
        mark_node,
        "call_freezone_vision_model",
        fake_call_freezone_vision_model,
    )

    result = await mark_node.detect_freezone_mark(
        image_path=path,
        point_x=0.5,
        point_y=0.5,
    )

    assert result == {
        "label": "老人",
        "note": "主体人物",
        "provider": "newapi",
        "model": "DC-freezone-vision-LLM",
    }
    assert len(captured["images"]) == 2
    assert captured["timeout_seconds"] == FREEZONE_MARK_TIMEOUT_SECONDS


@pytest.mark.asyncio
async def test_mark_detection_preserves_source_image_media_type(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "mark.jpg"
    Image.new("RGB", (100, 100), color="white").save(path)
    captured = {}

    async def fake_call_freezone_vision_model(**kwargs):
        captured.update(kwargs)
        return "DC-freezone-vision-LLM", '{"label":"老人","note":""}'

    monkeypatch.setattr(
        mark_node,
        "call_freezone_vision_model",
        fake_call_freezone_vision_model,
    )

    await mark_node.detect_freezone_mark(image_path=path, point_x=0.5, point_y=0.5)

    assert captured["images"][0].media_type == "image/jpeg"
    assert captured["images"][1].media_type == "image/png"


@pytest.mark.asyncio
async def test_mark_detection_route_returns_500_on_detection_failure(
    tmp_path, monkeypatch
) -> None:
    path = tmp_path / "mark.png"
    Image.new("RGB", (100, 100), color="white").save(path)
    class FakeContext:
        project_id = "project_59"
        requester_user_id = "user_7"

    async def fake_resolve(*_args, **_kwargs):
        return FakeContext(), "admin", "project_59", tmp_path, str(tmp_path)

    async def failing_detect(**_kwargs):
        raise RuntimeError("vision unavailable")

    monkeypatch.setattr(freezone_routes, "_resolve_freezone_project", fake_resolve)
    monkeypatch.setattr(freezone_routes, "_resolve_url_list", lambda *_args: [str(path)])
    monkeypatch.setattr(freezone_routes, "detect_freezone_mark", failing_detect)
    # 路由现在在积分预留之前解析出网身份（OI-54 S1），而 `get_authz_port()` 刻意
    # 没有 PortNotRegistered 回落——有回落即 fail-open。直调路由函数的用例本就未
    # bootstrap 端口（生产由 `api/app.py:311` / `cli.py:55` 无条件执行）。写法照
    # `tests/test_p0g4c_video_egress.py:803`：打 `ports` 模块属性换真实实现。
    # `LocalAuthz` 产 kind="local" → 非组织 → 什么都不绑，平台路径逐字节不变。
    monkeypatch.setattr(ports, "get_authz_port", lambda: LocalAuthz())

    with pytest.raises(freezone_routes.HTTPException) as exc_info:
        await freezone_routes.freezone_mark_detect(
            project="project_59",
            body=freezone_routes.FreezoneMarkDetectRequest(
                source_url="/static/mark.png",
                point_x=0.5,
                point_y=0.5,
            ),
            user={"username": "admin"},
        )

    # [aipdd fork delta] WP-D: 检测失败照实抛 500，不再有「按证据结算」这一层。
    assert exc_info.value.status_code == 500
