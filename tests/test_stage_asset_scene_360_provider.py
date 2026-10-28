from types import SimpleNamespace

import pytest


def test_scene_360_provider_defaults_to_newapi_when_env_is_empty(monkeypatch):
    from novelvideo import stage_asset_tasks

    monkeypatch.setenv("SCENE_360_IMAGE_PROVIDER", "")
    monkeypatch.setenv("SCENE_360_PROVIDER", "")
    monkeypatch.setenv("NANOBANANA_PROVIDER", "")

    assert stage_asset_tasks.resolve_scene_360_image_provider() == "newapi"


def test_scene_360_model_accepts_registered_gateway_model_for_provider(monkeypatch):
    # [aipdd fork delta] WP-D: 原来这条用例拿 config.LOCALAPI_IMAGE_MODEL 当期望值，而这个
    # 常量在没配 env 的环境里是空串，断言退化成「空 == 空」的巧合，既守不住注册模型
    # 也守不住选择键。改成把在册选择显式注入 IMAGE_GENERATION_SELECTIONS，两种入口
    # （真实模型名 / 选择键）都必须解析到同一个上游模型。
    from novelvideo import config, stage_asset_tasks

    model = "organization-authorized-gateway-image"
    monkeypatch.setitem(
        config.IMAGE_GENERATION_SELECTIONS,
        "newapi_registered",
        {"label": "registered gateway image", "provider": "newapi", "model": model},
    )

    assert (
        stage_asset_tasks.resolve_scene_360_image_model(
            provider="newapi",
            model=model,
        )
        == model
    )
    assert (
        stage_asset_tasks.resolve_scene_360_image_model(
            provider="newapi",
            model="newapi_registered",
        )
        == model
    )


def test_scene_360_model_rejects_unknown_selection_instead_of_raw_passthrough():
    from novelvideo import stage_asset_tasks

    with pytest.raises(
        stage_asset_tasks.Scene360ImageModelSelectionError,
        match="unknown scene 360 image model selection",
    ):
        stage_asset_tasks.resolve_scene_360_image_model(
            provider="newapi",
            model="attacker-controlled-model",
        )


def test_scene_360_model_rejects_provider_mismatch_instead_of_raw_passthrough():
    from novelvideo import stage_asset_tasks

    with pytest.raises(
        stage_asset_tasks.Scene360ImageModelSelectionError,
        match="does not use provider",
    ):
        stage_asset_tasks.resolve_scene_360_image_model(
            provider="openai",
            model="newapi_gpt_image2",
        )


def test_scene_360_runs_catalog_model_with_verified_authority(monkeypatch, tmp_path):
    from novelvideo import stage_asset_tasks

    model = "organization-authorized-pano-model"
    # [aipdd fork delta] WP-D: 预约 / 确认这两个额度样板已随体系一起拆除，链路上只留
    # 一次「记过上游调用」的观测。这里把观测面收成探针，顺便钉死它只被调用一次。
    recorded: list[dict[str, object]] = []

    def _observe(model_name: str, **kwargs) -> None:
        recorded.append({"model": model_name, **kwargs})

    monkeypatch.setattr(stage_asset_tasks, "_record_scene_360_model_call", _observe)

    def fake_run(cmd, **_kwargs):
        output_dir = stage_asset_tasks.Path(cmd[cmd.index("--output-dir") + 1])
        output_dir.mkdir(parents=True, exist_ok=True)
        (output_dir / "scene_panorama_2to1.png").write_bytes(b"png")
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(stage_asset_tasks, "run_project_subprocess", fake_run)

    result = stage_asset_tasks.run_scene_360(
        tmp_path / "project",
        "Hall",
        source="text",
        provider="newapi",
        model=model,
        artifact_dir=tmp_path / "candidate",
        update_manifest=False,
        model_authority=stage_asset_tasks.Scene360CatalogModelAuthority(
            catalog_id="catalog-pano",
            provider="newapi",
            model=model,
        ),
    )

    assert result["ok"] is True
    assert result["model"] == model
    assert len(recorded) == 1
    assert recorded[0]["model"] == model
    # 观测记录里不许再长出结算句柄。
    assert not {"reservation_id", "credit_reservation_id", "cost"} & set(recorded[0])
