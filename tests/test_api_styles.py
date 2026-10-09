import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.m04


def test_custom_style_lifecycle_uses_explicit_state_dir(tmp_path):
    from novelvideo.models import StyleConfig
    from novelvideo.services.style_service import StyleService

    state_dir = tmp_path / "state" / "_scopes" / "scope_123" / "alice" / "demo"
    style = StyleConfig(id="scope_style", name="Scope Style")

    assert StyleService.save_custom_style(
        "scope_style",
        style,
        username="alice",
        project="demo",
        project_dir=tmp_path / "output",
        state_dir=state_dir,
    )
    assert (state_dir / "project_config.json").is_file()
    assert StyleService.list_custom_styles(state_dir=state_dir) == ["scope_style"]
    assert StyleService.delete_custom_style(
        "scope_style",
        username="alice",
        project="demo",
        project_dir=tmp_path / "output",
        state_dir=state_dir,
    )
    assert StyleService.list_custom_styles(state_dir=state_dir) == []


def test_style_preview_upload_is_staged_and_finalized(tmp_path):
    from novelvideo.services.style_service import StyleService

    staged = StyleService.stage_style_preview(tmp_path, b"image-bytes", ".png")
    assert staged.startswith("assets/styles/.staging/")
    assert (tmp_path / staged).read_bytes() == b"image-bytes"

    final = StyleService.finalize_style_preview(tmp_path, "custom_drama", staged)
    assert final == "assets/styles/custom_drama/reference.png"
    assert (tmp_path / final).read_bytes() == b"image-bytes"
    assert not (tmp_path / staged).exists()


def test_finalizing_style_preview_removes_previous_extension(tmp_path):
    from novelvideo.services.style_service import StyleService

    old_preview = tmp_path / "assets/styles/custom_drama/reference.png"
    old_preview.parent.mkdir(parents=True)
    old_preview.write_bytes(b"old-image")
    staged = StyleService.stage_style_preview(tmp_path, b"new-image", ".jpg")

    final = StyleService.finalize_style_preview(tmp_path, "custom_drama", staged)

    assert final == "assets/styles/custom_drama/reference.jpg"
    assert not old_preview.exists()
    assert (tmp_path / final).read_bytes() == b"new-image"


def test_remove_style_previews_removes_all_supported_variants(tmp_path):
    from novelvideo.services.style_service import StyleService

    style_dir = tmp_path / "assets/styles/custom_drama"
    style_dir.mkdir(parents=True)
    for extension in (".png", ".jpg", ".webp"):
        (style_dir / f"reference{extension}").write_bytes(b"image")
    unrelated = style_dir / "notes.txt"
    unrelated.write_text("keep", encoding="utf-8")

    StyleService.remove_style_previews(tmp_path, "custom_drama")

    assert not list(style_dir.glob("reference.*"))
    assert unrelated.read_text(encoding="utf-8") == "keep"


def test_style_reference_upload_returns_final_path_without_ai_analysis(monkeypatch, tmp_path):
    from novelvideo.api.deps import ProjectResolution
    from novelvideo.api.routes import styles

    async def fake_resolve_project_scope(project, user, *, required_role="viewer"):
        assert required_role == "editor"
        return ProjectResolution(
            ctx=None,
            username="alice",
            project_name=project,
            project_dir=tmp_path,
            output_dir=str(tmp_path),
            state_dir=str(tmp_path / "state"),
            runtime_dir=str(tmp_path / "runtime"),
        )

    monkeypatch.setattr(styles, "resolve_project_scope", fake_resolve_project_scope)

    response = _client().post(
        "/projects/demo/styles/preview-upload",
        data={"style_id": "custom_drama"},
        files={"file": ("reference.png", b"image-bytes", "image/png")},
    )

    assert response.status_code == 200
    preview_path = response.json()["data"]["preview_path"]
    assert preview_path == "assets/styles/custom_drama/reference.png"
    assert (tmp_path / preview_path).read_bytes() == b"image-bytes"


@pytest.mark.parametrize(
    ("filename", "content_type"),
    [
        ("reference.avif", "image/avif"),
        ("reference.heic", "image/heic"),
    ],
)
def test_style_reference_upload_rejects_unsupported_format(
    monkeypatch,
    tmp_path,
    filename,
    content_type,
):
    from novelvideo.api.deps import ProjectResolution
    from novelvideo.api.routes import styles

    async def fake_resolve_project_scope(project, user, *, required_role="viewer"):
        return ProjectResolution(
            ctx=None,
            username="alice",
            project_name=project,
            project_dir=tmp_path,
            output_dir=str(tmp_path),
            state_dir=str(tmp_path / "state"),
            runtime_dir=str(tmp_path / "runtime"),
        )

    monkeypatch.setattr(styles, "resolve_project_scope", fake_resolve_project_scope)

    response = _client().post(
        "/projects/demo/styles/preview-upload",
        data={"style_id": "custom_drama"},
        files={"file": (filename, b"image-bytes", content_type)},
    )

    assert response.status_code == 415
    assert response.json()["detail"] == "Unsupported style preview image type"


def test_style_reference_upload_rejects_empty_file(monkeypatch, tmp_path):
    from novelvideo.api.deps import ProjectResolution
    from novelvideo.api.routes import styles

    async def fake_resolve_project_scope(project, user, *, required_role="viewer"):
        return ProjectResolution(
            ctx=None,
            username="alice",
            project_name=project,
            project_dir=tmp_path,
            output_dir=str(tmp_path),
            state_dir=str(tmp_path / "state"),
            runtime_dir=str(tmp_path / "runtime"),
        )

    monkeypatch.setattr(styles, "resolve_project_scope", fake_resolve_project_scope)

    response = _client().post(
        "/projects/demo/styles/preview-upload",
        data={"style_id": "custom_drama"},
        files={"file": ("reference.png", b"", "image/png")},
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "No preview image uploaded"


def _client():
    from novelvideo.api.routes import styles

    app = FastAPI()
    app.include_router(styles.router)
    app.dependency_overrides[styles.get_api_user] = lambda: {"username": "alice"}
    return TestClient(app)


def test_style_preview_get_returns_image_without_generation():
    response = _client().get("/styles/anime/preview")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")


def test_custom_style_list_includes_project_media_preview_url(monkeypatch, tmp_path):
    from novelvideo.api.deps import ProjectResolution
    from novelvideo.api.routes import styles
    from novelvideo.services.style_service import StyleService

    captured: dict[str, object] = {}

    async def fake_resolve_project_scope(project, user, *, required_role="viewer"):
        return ProjectResolution(
            ctx=None,
            username="alice",
            project_name="demo-project",
            project_dir=tmp_path,
            output_dir=str(tmp_path),
            state_dir=str(tmp_path / "state"),
            runtime_dir=str(tmp_path / "runtime"),
        )

    monkeypatch.setattr(styles, "resolve_project_scope", fake_resolve_project_scope)
    def fake_list_all_styles(**kwargs):
        captured.update(kwargs)
        return [
            {
                "id": "custom_drama",
                "name": "Custom drama",
                "type": "custom",
                "preview_path": "assets/styles/custom_drama/reference.png",
            }
        ]

    monkeypatch.setattr(StyleService, "list_all_styles", fake_list_all_styles)

    response = _client().get("/styles", params={"project": "demo"})

    assert response.status_code == 200
    assert captured["state_dir"] == str(tmp_path / "state")
    assert response.json()["data"][0]["preview_url"] == (
        "/api/v1/projects/demo/media/"
        "assets/styles/custom_drama/reference.png"
    )


def test_custom_style_detail_includes_project_media_preview_url(monkeypatch, tmp_path):
    from novelvideo.api.deps import ProjectResolution
    from novelvideo.api.routes import styles
    from novelvideo.models import StyleConfig
    from novelvideo.services.style_service import StyleService

    captured: dict[str, object] = {}

    async def fake_resolve_project_scope(project, user, *, required_role="viewer"):
        return ProjectResolution(
            ctx=None,
            username="alice",
            project_name="demo-project",
            project_dir=tmp_path,
            output_dir=str(tmp_path),
            state_dir=str(tmp_path / "state"),
            runtime_dir=str(tmp_path / "runtime"),
        )

    monkeypatch.setattr(styles, "resolve_project_scope", fake_resolve_project_scope)
    def fake_get_style(*args, **kwargs):
        captured.update(kwargs)
        return StyleConfig(
            id="custom_drama",
            name="Custom drama",
            preview_path="assets/styles/custom_drama/reference.png",
        )

    monkeypatch.setattr(StyleService, "get_style", fake_get_style)

    response = _client().get("/styles/custom_drama", params={"project": "demo"})

    assert response.status_code == 200
    assert captured["state_dir"] == str(tmp_path / "state")
    assert response.json()["data"]["preview_url"] == (
        "/api/v1/projects/demo/media/"
        "assets/styles/custom_drama/reference.png"
    )


def test_style_preview_post_route_still_exists(monkeypatch, tmp_path):
    from novelvideo.generators import image_generator

    preview_path = tmp_path / "preview.png"
    preview_path.write_bytes(b"\x89PNG\r\n\x1a\n")

    async def fake_generate_character_reference_unified(**kwargs):
        return [str(preview_path)]

    monkeypatch.setattr(
        image_generator,
        "generate_character_reference_unified",
        fake_generate_character_reference_unified,
    )

    response = _client().post("/styles/anime/preview", json={})

    assert response.status_code == 200


def test_guoman_fantasy_is_listed_as_3d_animation_preset():
    response = _client().get("/styles")

    assert response.status_code == 200
    styles = response.json()["data"]
    guoman = next(style for style in styles if style["id"] == "guoman_fantasy")
    assert guoman["label"] == "3D玄幻国漫"
    assert guoman["style_family"] == "animation"
    assert guoman["animation_subtype"] == "3d"


def test_create_style_accepts_frontend_payload_with_top_level_id_and_name(monkeypatch, tmp_path):
    from novelvideo.api.deps import ProjectResolution
    from novelvideo.api.routes import styles
    from novelvideo.services.style_service import StyleService

    saved = []

    async def fake_resolve_project_scope(project, user, *, required_role="viewer"):
        assert project == "demo"
        assert user == {"username": "alice"}
        assert required_role == "editor"
        return ProjectResolution(
            ctx=None,
            username="alice",
            project_name="demo",
            project_dir=tmp_path,
            output_dir=str(tmp_path),
            state_dir=str(tmp_path / "state"),
            runtime_dir=str(tmp_path / "runtime"),
        )

    def fake_save_custom_style(style_id, config, **kwargs):
        saved.append((style_id, config, kwargs))
        return True

    monkeypatch.setattr(styles, "resolve_project_scope", fake_resolve_project_scope)
    monkeypatch.setattr(StyleService, "save_custom_style", fake_save_custom_style)

    response = _client().post(
        "/styles",
        json={
            "id": "custom_drama",
            "name": "自定义剧集风格",
            "project": "demo",
            "config": {
                "label": "自定义剧集风格",
                "style_instructions": "cinematic live action",
                "avoid_instructions": "anime",
                "style_tag": "LIVE-ACTION",
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["ok"] is True
    assert len(saved) == 1
    style_id, config, kwargs = saved[0]
    assert style_id == "custom_drama"
    assert config.id == "custom_drama"
    assert config.name == "自定义剧集风格"
    assert config.label == "自定义剧集风格"
    assert kwargs == {
        "username": "alice",
        "project": "demo",
        "project_dir": tmp_path,
        "state_dir": str(tmp_path / "state"),
    }


def test_create_style_accepts_existing_published_preview_path(monkeypatch, tmp_path):
    from novelvideo.api.deps import ProjectResolution
    from novelvideo.api.routes import styles
    from novelvideo.services.style_service import StyleService

    saved = []

    async def fake_resolve_project_scope(project, user, *, required_role="viewer"):
        return ProjectResolution(
            ctx=None,
            username="alice",
            project_name=project,
            project_dir=tmp_path,
            output_dir=str(tmp_path),
            state_dir=str(tmp_path / "state"),
            runtime_dir=str(tmp_path / "runtime"),
        )

    monkeypatch.setattr(styles, "resolve_project_scope", fake_resolve_project_scope)
    monkeypatch.setattr(
        StyleService,
        "save_custom_style",
        lambda style_id, config, **kwargs: saved.append(config) or True,
    )
    preview = tmp_path / "assets/styles/custom_drama/reference.png"
    preview.parent.mkdir(parents=True)
    preview.write_bytes(b"image")

    response = _client().post(
        "/styles",
        json={
            "id": "custom_drama",
            "name": "自定义剧集风格",
            "project": "demo",
            "preview_path": "assets/styles/custom_drama/reference.png",
            "config": {"style_instructions": "cinematic live action"},
        },
    )

    assert response.status_code == 200
    assert saved[0].preview_path == "assets/styles/custom_drama/reference.png"


def test_create_style_associates_published_preview_without_request_path(monkeypatch, tmp_path):
    from novelvideo.api.deps import ProjectResolution
    from novelvideo.api.routes import styles
    from novelvideo.services.style_service import StyleService

    saved = []
    preview = tmp_path / "assets/styles/custom_drama/reference.webp"
    preview.parent.mkdir(parents=True)
    preview.write_bytes(b"image")

    async def fake_resolve_project_scope(project, user, *, required_role="viewer"):
        return ProjectResolution(
            ctx=None,
            username="alice",
            project_name=project,
            project_dir=tmp_path,
            output_dir=str(tmp_path),
            state_dir=str(tmp_path / "state"),
            runtime_dir=str(tmp_path / "runtime"),
        )

    monkeypatch.setattr(styles, "resolve_project_scope", fake_resolve_project_scope)
    monkeypatch.setattr(
        StyleService,
        "save_custom_style",
        lambda style_id, config, **kwargs: saved.append(config) or True,
    )

    response = _client().post(
        "/styles",
        json={
            "id": "custom_drama",
            "name": "Custom drama",
            "project": "demo",
            "config": {},
        },
    )

    assert response.status_code == 200
    assert saved[0].preview_path == "assets/styles/custom_drama/reference.webp"


def test_create_style_rejects_missing_published_preview_path(monkeypatch, tmp_path):
    from novelvideo.api.deps import ProjectResolution
    from novelvideo.api.routes import styles

    async def fake_resolve_project_scope(project, user, *, required_role="viewer"):
        return ProjectResolution(
            ctx=None,
            username="alice",
            project_name=project,
            project_dir=tmp_path,
            output_dir=str(tmp_path),
            state_dir=str(tmp_path / "state"),
            runtime_dir=str(tmp_path / "runtime"),
        )

    monkeypatch.setattr(styles, "resolve_project_scope", fake_resolve_project_scope)

    response = _client().post(
        "/styles",
        json={
            "id": "custom_drama",
            "name": "Custom drama",
            "project": "demo",
            "preview_path": "assets/styles/custom_drama/reference.png",
            "config": {},
        },
    )

    assert response.status_code == 400
    assert response.json()["detail"] == "Custom style preview does not exist"


def test_config_style_helpers_do_not_fallback_to_hardcoded_presets(monkeypatch):
    from novelvideo import config

    class BrokenStyleService:
        @staticmethod
        def get_style(*args, **kwargs):
            raise RuntimeError("style service unavailable")

        @staticmethod
        def get_style_labels(*args, **kwargs):
            raise RuntimeError("style service unavailable")

        @staticmethod
        def list_all_styles(*args, **kwargs):
            raise RuntimeError("style service unavailable")

    real_import = __import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "novelvideo.services.style_service":

            class Module:
                StyleService = BrokenStyleService

            return Module
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr("builtins.__import__", fake_import)

    with pytest.raises(RuntimeError, match="style service unavailable"):
        config.get_style_preset("chinese_period_drama")
    with pytest.raises(RuntimeError, match="style service unavailable"):
        config.get_style_labels()
    with pytest.raises(RuntimeError, match="style service unavailable"):
        config.list_available_styles()


def test_style_preview_generation_cleans_up_temp_dir(monkeypatch, tmp_path):
    """ST-1 回归：AI 预览生成必须回收自己的临时目录（旧实现每次遗留一个残留 PNG）。"""
    from pathlib import Path

    from novelvideo.api.deps import ProjectResolution
    from novelvideo.api.routes import styles
    from novelvideo.generators import image_generator
    from novelvideo.services.style_service import StyleService

    async def fake_resolve(project, user, *, required_role="viewer"):
        return ProjectResolution(
            ctx=None,
            username="alice",
            project_name=project,
            project_dir=tmp_path,
            output_dir=str(tmp_path),
            state_dir=str(tmp_path / "state"),
            runtime_dir=str(tmp_path / "runtime"),
        )

    monkeypatch.setattr(styles, "resolve_project_scope", fake_resolve)

    class _FakeStyle:
        is_preset = False
        preview_path = None

    monkeypatch.setattr(
        StyleService, "get_style", staticmethod(lambda *a, **k: _FakeStyle())
    )

    captured: dict = {}

    async def fake_gen(**kwargs):
        out = Path(kwargs["output_dir"])
        captured["tmp_dir"] = out
        f = out / "preview.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 8)
        return [str(f)]

    monkeypatch.setattr(
        image_generator, "generate_character_reference_unified", fake_gen
    )

    response = _client().post(
        "/styles/custom_drama/preview",
        json={"project": "demo", "prompt": "a hero"},
    )

    assert response.status_code == 200
    assert response.content.startswith(b"\x89PNG\r\n\x1a\n")
    # 临时目录必须在响应前被 rmtree 回收，绝不残留。
    assert captured["tmp_dir"].exists() is False


def test_preview_style_with_project_binds_request_egress_scope(monkeypatch, tmp_path):
    """[C3] 带项目的预览生成必须以请求出网身份绑定，且 task_type 正确。"""
    from contextlib import asynccontextmanager
    from pathlib import Path
    from types import SimpleNamespace

    from novelvideo.api import egress_binding
    from novelvideo.api.deps import ProjectResolution
    from novelvideo.api.routes import styles
    from novelvideo.generators import image_generator
    from novelvideo.services.style_service import StyleService

    async def fake_resolve(project, user, *, required_role="viewer"):
        return ProjectResolution(
            ctx=SimpleNamespace(project_id="proj-1", requester_user_id="u-1"),
            username="alice",
            project_name=project,
            project_dir=tmp_path,
            output_dir=str(tmp_path),
            state_dir=str(tmp_path / "state"),
            runtime_dir=str(tmp_path / "runtime"),
        )

    monkeypatch.setattr(styles, "resolve_project_scope", fake_resolve)

    class _FakeStyle:
        is_preset = False
        preview_path = None

    monkeypatch.setattr(
        StyleService, "get_style", staticmethod(lambda *a, **k: _FakeStyle())
    )

    async def fake_gen(**kwargs):
        f = Path(kwargs["output_dir"]) / "preview.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n")
        return [str(f)]

    monkeypatch.setattr(
        image_generator, "generate_character_reference_unified", fake_gen
    )

    recorded: dict[str, object] = {}

    @asynccontextmanager
    async def fake_scope(*, requester_user_id, project_id, task_type):
        recorded["requester_user_id"] = requester_user_id
        recorded["project_id"] = project_id
        recorded["task_type"] = task_type
        yield None

    monkeypatch.setattr(egress_binding, "request_egress_scope", fake_scope)

    response = _client().post(
        "/styles/custom_drama/preview",
        json={"project": "demo", "prompt": "a hero"},
    )

    assert response.status_code == 200
    assert recorded == {
        "requester_user_id": "u-1",
        "project_id": "proj-1",
        "task_type": "style_preview",
    }


def test_analyze_style_binds_request_egress_scope(monkeypatch, tmp_path):
    """[C3] Vision 风格分析必须以请求出网身份绑定，且 task_type 正确。"""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    from novelvideo.api import egress_binding
    from novelvideo.api.deps import ProjectResolution
    from novelvideo.api.routes import styles
    from novelvideo.generators import style_analyzer

    async def fake_resolve(project, user, *, required_role="viewer"):
        assert required_role == "editor"
        return ProjectResolution(
            ctx=SimpleNamespace(project_id="proj-2", requester_user_id="u-2"),
            username="alice",
            project_name=project,
            project_dir=tmp_path,
            output_dir=str(tmp_path),
            state_dir=str(tmp_path / "state"),
            runtime_dir=str(tmp_path / "runtime"),
        )

    monkeypatch.setattr(styles, "resolve_project_scope", fake_resolve)

    class _FakeAnalyzer:
        async def analyze(self, content, *, mime_type):
            return {"palette": ["#000"]}

    monkeypatch.setattr(style_analyzer, "StyleAnalyzer", _FakeAnalyzer)

    recorded: dict[str, object] = {}

    @asynccontextmanager
    async def fake_scope(*, requester_user_id, project_id, task_type):
        recorded["requester_user_id"] = requester_user_id
        recorded["project_id"] = project_id
        recorded["task_type"] = task_type
        yield None

    monkeypatch.setattr(egress_binding, "request_egress_scope", fake_scope)

    response = _client().post(
        "/projects/demo/styles/analyze",
        data={"style_id": ""},
        files={"file": ("ref.png", b"\x89PNG\r\n\x1a\n", "image/png")},
    )

    assert response.status_code == 200
    assert response.json()["data"]["palette"] == ["#000"]
    assert recorded == {
        "requester_user_id": "u-2",
        "project_id": "proj-2",
        "task_type": "style_analyze",
    }
