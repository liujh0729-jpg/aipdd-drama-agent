from __future__ import annotations

from urllib.parse import parse_qs, urlparse


import pytest


@pytest.mark.parametrize("env_name", ["DIRECTOR_VIEWER_URL", "VITE_DIRECTOR_VIEWER_URL"])
def test_3gs_stage_url_uses_configured_director_viewer_origin(tmp_path, monkeypatch, env_name):
    from novelvideo.director_world import stage_manifest
    from novelvideo.director_world.service import DirectorWorldService

    # Both env vars are honoured by the service (DIRECTOR_VIEWER_URL first),
    # so clear any inherited values and set only the one under test to make
    # the resulting netloc deterministic and machine-independent.
    expected_netloc = "127.0.0.1:9024"
    for name in ("DIRECTOR_VIEWER_URL", "VITE_DIRECTOR_VIEWER_URL"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(env_name, f"http://{expected_netloc}")
    stage_dir = stage_manifest.stage_dir(tmp_path, "地下室")
    stage_dir.mkdir(parents=True)
    (stage_dir / "master_sharp.ply").write_bytes(b"ply")
    (stage_dir / "master_sharp.sog").write_bytes(b"sog")
    stage_manifest.update_manifest(
        tmp_path,
        "地下室",
        source="uploaded_master",
        ply_path="master_sharp.ply",
        master_ply_path="master_sharp.ply",
    )

    url = DirectorWorldService(tmp_path).make_3gs_editor_url(
        episode=1,
        scene_id="地下室",
        user="admin",
        project="shiguangshuwu",
        slate_beat=2,
    )

    parsed = urlparse(url or "")
    params = parse_qs(parsed.query)
    assert parsed.scheme == "http"
    assert parsed.netloc == expected_netloc
    assert parsed.path == "/app/viewer/supertale_playcanvas_3gs_stage.html"
    assert params["scene_id"] == ["地下室"]
    assert params["beat"] == ["2"]
    assert params["scene_3gs_ply_fs"][0].endswith("/master_sharp.sog")
    assert params["scene_3gs_master_ply_fs"][0].endswith("/master_sharp.sog")


def test_director_viewer_url_has_no_guessed_localhost(tmp_path, monkeypatch):
    """[req41] 导演台地址不猜主机名：没配置时报可执行错误，不兜底 127.0.0.1:9024。

    旧实现在非本机 / 域名反代部署下会拼出一堆永远打不开的链接，还把内部端口
    暴露给用户。现在只认显式配置。
    """
    from novelvideo.director_world import stage_manifest
    from novelvideo.director_world.service import DirectorWorldService

    for name in ("DIRECTOR_VIEWER_URL", "VITE_DIRECTOR_VIEWER_URL"):
        monkeypatch.delenv(name, raising=False)

    stage_dir = stage_manifest.stage_dir(tmp_path, "地下室")
    stage_dir.mkdir(parents=True)
    (stage_dir / "master_sharp.ply").write_bytes(b"ply")
    (stage_dir / "master_sharp.sog").write_bytes(b"sog")
    stage_manifest.update_manifest(
        tmp_path,
        "地下室",
        source="uploaded_master",
        ply_path="master_sharp.ply",
        master_ply_path="master_sharp.ply",
    )

    service = DirectorWorldService(tmp_path)
    # 构造本身不该因为未配导演台而失败（路径 / session 助手跟地址无关）。
    assert service.world_path("地下室").name == "world.json"

    with pytest.raises(ValueError, match="DIRECTOR_VIEWER_URL"):
        service.make_3gs_editor_url(
            episode=1,
            scene_id="地下室",
            user="admin",
            project="shiguangshuwu",
            slate_beat=2,
        )
    with pytest.raises(ValueError, match="DIRECTOR_VIEWER_URL"):
        service.make_editor_url(
            episode=1,
            scene_id="地下室",
            user="admin",
            project="shiguangshuwu",
        )
