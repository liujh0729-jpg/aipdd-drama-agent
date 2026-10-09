from __future__ import annotations

from io import BytesIO

from PIL import Image

from novelvideo.services.background_anchor_service import (
    build_background_anchors_payload,
    crop_background_anchor_to_selected,
    save_uploaded_background_anchor_image,
    select_background_anchor,
)
from novelvideo.utils.background_anchor import (
    ANCHOR_MASTER,
    ANCHOR_SELECTED_BACKGROUND,
)


def _api_url(_path, rel_path: str) -> str:
    return f"/media/{rel_path}"


def _selected_background_path(project_dir):
    return (
        project_dir
        / "director_control_frames"
        / "ep001"
        / "beat_04"
        / "selected_background.png"
    )


def test_select_background_anchor_snapshots_master_and_preserves_source(tmp_path):
    beat = {"beat_number": 4, "scene_ref": {"scene_id": "地下室", "render_anchor_id": "master"}}
    master = tmp_path / "assets" / "scenes" / "地下室" / "master.png"
    master.parent.mkdir(parents=True)
    master.write_bytes(b"fake master")

    payload = select_background_anchor(
        project_dir=tmp_path,
        username="admin",
        project="demo",
        beat=beat,
        episode_num=1,
        beat_num=4,
        anchor_id=ANCHOR_MASTER,
        reference_url_builder=_api_url,
        anchor_url_builder=_api_url,
    )

    selected = _selected_background_path(tmp_path)
    assert selected.read_bytes() == b"fake master"
    assert beat["scene_ref"]["render_anchor_id"] == ANCHOR_SELECTED_BACKGROUND
    assert beat["scene_ref"]["render_anchor_source_id"] == ANCHOR_MASTER
    assert payload["render_anchor_id"] == ANCHOR_SELECTED_BACKGROUND
    assert payload["current_source"] == ANCHOR_MASTER
    assert payload["display_reference"]["rel_path"] == "assets/scenes/地下室/master.png"
    assert (
        payload["render_input"]["rel_path"]
        == "director_control_frames/ep001/beat_04/selected_background.png"
    )


def test_build_background_anchors_payload_infers_legacy_selected_source(tmp_path):
    beat = {
        "beat_number": 4,
        "scene_ref": {"scene_id": "地下室", "render_anchor_id": ANCHOR_SELECTED_BACKGROUND},
    }
    master = tmp_path / "assets" / "scenes" / "地下室" / "master.png"
    master.parent.mkdir(parents=True)
    master.write_bytes(b"same frozen master")
    selected = _selected_background_path(tmp_path)
    selected.parent.mkdir(parents=True)
    selected.write_bytes(b"same frozen master")

    payload = build_background_anchors_payload(
        project_dir=tmp_path,
        username="admin",
        project="demo",
        beat=beat,
        episode_num=1,
        beat_num=4,
        reference_url_builder=_api_url,
        anchor_url_builder=_api_url,
    )

    assert payload["render_anchor_id"] == ANCHOR_SELECTED_BACKGROUND
    assert payload["current_source"] == ANCHOR_MASTER
    assert payload["display_reference"]["id"] == ANCHOR_MASTER
    assert payload["render_input"]["id"] == ANCHOR_SELECTED_BACKGROUND
    master_anchor = [item for item in payload["anchors"] if item["id"] == ANCHOR_MASTER][0]
    assert master_anchor["anchor_id"] == ANCHOR_MASTER
    assert master_anchor["current"] is True


def test_crop_background_anchor_writes_selected_and_records_source(tmp_path):
    beat = {"beat_number": 4, "scene_ref": {"scene_id": "地下室", "render_anchor_id": "master"}}
    master = tmp_path / "assets" / "scenes" / "地下室" / "master.png"
    master.parent.mkdir(parents=True)
    Image.new("RGB", (8, 8), color=(255, 0, 0)).save(master)

    payload = crop_background_anchor_to_selected(
        project_dir=tmp_path,
        username="admin",
        project="demo",
        beat=beat,
        episode_num=1,
        beat_num=4,
        anchor_id=ANCHOR_MASTER,
        crop={"x": 1, "y": 1, "width": 4, "height": 4},
        reference_url_builder=_api_url,
        anchor_url_builder=_api_url,
    )

    selected = _selected_background_path(tmp_path)
    assert selected.exists()
    assert Image.open(selected).size == (4, 4)
    assert beat["scene_ref"]["render_anchor_id"] == ANCHOR_SELECTED_BACKGROUND
    assert beat["scene_ref"]["render_anchor_source_id"] == ANCHOR_MASTER
    assert payload["current_source"] == ANCHOR_MASTER


def test_save_uploaded_background_anchor_image_writes_selected_as_external(tmp_path):
    beat = {"beat_number": 4, "scene_ref": {"scene_id": "地下室", "render_anchor_id": "master"}}
    content = BytesIO()
    Image.new("RGB", (4, 4), color=(0, 255, 0)).save(content, format="PNG")
    content.seek(0)
    image = Image.open(content)

    payload = save_uploaded_background_anchor_image(
        project_dir=tmp_path,
        username="admin",
        project="demo",
        beat=beat,
        episode_num=1,
        beat_num=4,
        image=image,
        reference_url_builder=_api_url,
        anchor_url_builder=_api_url,
    )

    selected = _selected_background_path(tmp_path)
    assert selected.exists()
    assert beat["scene_ref"]["render_anchor_id"] == ANCHOR_SELECTED_BACKGROUND
    assert beat["scene_ref"]["render_anchor_source_id"] == ANCHOR_SELECTED_BACKGROUND
    assert payload["current_source"] == ANCHOR_SELECTED_BACKGROUND
