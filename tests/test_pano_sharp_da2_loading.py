from __future__ import annotations

import importlib.util

import pytest


def test_pano_sharp_module_imports_without_world_extra(monkeypatch):
    from novelvideo.director_world import pano_sharp

    real_find_spec = importlib.util.find_spec

    def fake_find_spec(name: str, *args, **kwargs):
        if name in {"sharp", "da2"}:
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib.util, "find_spec", fake_find_spec)

    assert pano_sharp.sharp_available() is False
    assert pano_sharp.da2_available() is False


def test_pano_sharp_unavailable_is_handled_task_failure():
    from novelvideo.director_world.pano_sharp import Sharp3DUnavailable
    from novelvideo.task_backend.run_core import _project_task_failure_for_exception

    message, payload, handled = _project_task_failure_for_exception(Sharp3DUnavailable())

    assert handled is True
    assert payload == {"error_code": "SHARP_3D_UNAVAILABLE"}
    assert "world" in message


def test_run_pano_sharp_missing_sharp_fails_before_subprocess(tmp_path, monkeypatch):
    from PIL import Image

    from novelvideo import stage_asset_tasks
    from novelvideo.director_world import pano_sharp

    pano_path = tmp_path / "pano_360.png"
    Image.new("RGB", (8, 4), "white").save(pano_path)

    monkeypatch.setattr(pano_sharp, "sharp_available", lambda: False)
    monkeypatch.setattr(
        stage_asset_tasks,
        "run_project_subprocess",
        lambda *_args, **_kwargs: pytest.fail("SHARP subprocess should not be spawned"),
    )

    with pytest.raises(pano_sharp.Sharp3DUnavailable) as exc:
        stage_asset_tasks.run_pano_sharp(
            tmp_path,
            "scene_a",
            pano_path=pano_path,
            artifact_dir=tmp_path / "stage",
            update_manifest=False,
        )

    assert exc.value.error_code == "SHARP_3D_UNAVAILABLE"


def test_run_single_face_sharp_missing_sharp_fails_before_subprocess(tmp_path, monkeypatch):
    from PIL import Image

    from novelvideo import stage_asset_tasks
    from novelvideo.director_world import pano_sharp

    image_path = tmp_path / "master.png"
    Image.new("RGB", (4, 4), "white").save(image_path)

    monkeypatch.setattr(pano_sharp, "sharp_available", lambda: False)
    monkeypatch.setattr(
        stage_asset_tasks,
        "run_project_subprocess",
        lambda *_args, **_kwargs: pytest.fail("SHARP subprocess should not be spawned"),
    )

    with pytest.raises(pano_sharp.Sharp3DUnavailable) as exc:
        stage_asset_tasks.run_single_face_sharp(
            tmp_path,
            "scene_a",
            image_path=image_path,
            artifact_dir=tmp_path / "stage",
            update_manifest=False,
        )

    assert exc.value.error_code == "SHARP_3D_UNAVAILABLE"



def _load_pano_sharp_module():
    from novelvideo.director_world import pano_sharp

    return pano_sharp


def test_da2_loader_uses_huggingface_when_local_only_env_unset(monkeypatch):
    torch = pytest.importorskip("torch")
    pano_sharp = _load_pano_sharp_module()
    calls = []

    class FakeSphereViT:
        @classmethod
        def from_pretrained(cls, hub_id, **kwargs):
            calls.append((hub_id, kwargs))
            return cls()

        def eval(self):
            return self

        def to(self, device):
            return self

    monkeypatch.delenv("DA2_LOCAL_FILES_ONLY", raising=False)
    monkeypatch.setattr(pano_sharp, "load_da2_spherevit_class", lambda: FakeSphereViT)

    pano_sharp.build_da2_model(torch.device("cpu"))

    assert calls[0][1]["local_files_only"] is False


def test_da2_loader_keeps_explicit_local_only_env(monkeypatch):
    torch = pytest.importorskip("torch")
    pano_sharp = _load_pano_sharp_module()
    calls = []

    class FakeSphereViT:
        @classmethod
        def from_pretrained(cls, hub_id, **kwargs):
            calls.append((hub_id, kwargs))
            return cls()

        def eval(self):
            return self

        def to(self, device):
            return self

    monkeypatch.setenv("DA2_LOCAL_FILES_ONLY", "1")
    monkeypatch.setattr(pano_sharp, "load_da2_spherevit_class", lambda: FakeSphereViT)

    pano_sharp.build_da2_model(torch.device("cpu"))

    assert calls[0][1]["local_files_only"] is True
