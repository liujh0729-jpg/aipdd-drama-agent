import pytest

from novelvideo.verification.image_verifier import ImageVerifier
from novelvideo.verification.image_verifier import resolve_verification_scene_context
from novelvideo.verification.models import VerificationResult


class _FakeAgent:
    def __init__(self) -> None:
        self.task = ""

    async def run(self, payload):
        self.task = payload[0]
        return type(
            "RunResult",
            (),
            {
                "output": VerificationResult(
                    passed=True,
                    score=9,
                    issues=[],
                    summary="ok",
                    suggested_action="none",
                )
            },
        )()


@pytest.mark.asyncio
async def test_image_verifier_marks_time_baked_scene_as_locked_light(monkeypatch, tmp_path):
    image_path = tmp_path / "sketch.jpg"
    image_path.write_bytes(b"image")
    monkeypatch.setattr("novelvideo.verification.image_verifier.compress_image", lambda _p: b"jpeg")

    verifier = ImageVerifier()
    fake_agent = _FakeAgent()
    verifier._agent = fake_agent

    await verifier.verify_sketch(
        str(image_path),
        "夜晚的卫生间里地面积水。",
        [],
        "卫生间",
        "夜晚",
        resolved_scene_name="卫生间_漏水_夜晚",
        time_baked=True,
        prompt_time_of_day="夜晚",
    )

    assert "解析场景: 卫生间_漏水_夜晚" in fake_agent.task
    assert "时间/光线验证: 已命中烘焙时间版场景图" in fake_agent.task
    assert "锁定该图自带的夜晚光照" in fake_agent.task
    assert "不要要求额外 relight" in fake_agent.task


@pytest.mark.asyncio
async def test_image_verifier_marks_unbaked_scene_as_relight_target(monkeypatch, tmp_path):
    image_path = tmp_path / "sketch.jpg"
    image_path.write_bytes(b"image")
    monkeypatch.setattr("novelvideo.verification.image_verifier.compress_image", lambda _p: b"jpeg")

    verifier = ImageVerifier()
    fake_agent = _FakeAgent()
    verifier._agent = fake_agent

    await verifier.verify_sketch(
        str(image_path),
        "白天的卫生间里地面积水。",
        [],
        "卫生间",
        "白天",
        resolved_scene_name="卫生间_漏水",
        time_baked=False,
        prompt_time_of_day="白天",
    )

    assert "解析场景: 卫生间_漏水" in fake_agent.task
    assert "时间/光线验证: 当前场景图不是烘焙时间版" in fake_agent.task
    assert "画面应通过生成/relight 呈现白天" in fake_agent.task


def test_resolve_verification_scene_context_uses_time_baked_plate(tmp_path):
    plate_dir = tmp_path / "assets" / "scenes" / "卫生间_夜晚"
    plate_dir.mkdir(parents=True)
    (plate_dir / "master.png").write_bytes(b"night")

    context = resolve_verification_scene_context(
        tmp_path,
        {
            "beat_number": 1,
            "scene_ref": {"scene_id": "卫生间", "variant_id": ""},
            "time_of_day": "夜晚",
        },
    )

    assert context == {
        "scene_id": "卫生间",
        "resolved_scene_name": "卫生间_夜晚",
        "time_baked": True,
        "prompt_time_of_day": "夜晚",
    }
