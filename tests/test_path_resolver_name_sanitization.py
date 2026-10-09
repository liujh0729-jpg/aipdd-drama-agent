# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""O16 纵深防御：path_resolver 的调用方名字段必须被自净，不能借目录名片段穿越出项目根。

合法磁盘名逐字节不变（单段不可能含 ``/``），只有能逃单目录的字符（``/``、``\\``、
裸 ``.``/``..``）被改写。这里钉住「敌意名字仍落在 project_dir 之内」。
"""

from __future__ import annotations

from novelvideo.utils import path_resolver as pr


def test_safe_component_rewrites_only_traversal_chars() -> None:
    # 合法名逐字节不变（含 mac 合法的 ``:``、空格、中文、点号后缀）。
    for legit in ("Hero", "角色 A", "Foo:Bar", "v1.2", "a b"):
        assert pr._safe_component(legit) == legit
    # 真正能逃单目录的字符被中和。
    assert pr._safe_component("../../etc/passwd") == ".._.._etc_passwd"
    assert pr._safe_component("a/b/c") == "a_b_c"
    assert pr._safe_component("a\\b") == "a_b"
    assert pr._safe_component("\x00x") == "_x"
    # 裸点段回退，绝不产出 ``.``/``..`` 目录段。
    assert pr._safe_component("..") == "_"
    assert pr._safe_component(".") == "_"
    assert pr._safe_component("") == "_"


def test_canonical_paths_stay_under_project_dir(tmp_path) -> None:
    hostiles = ["../../escape", "a/../../../x", "..", "."]
    for hostile in hostiles:
        portrait = pr.canonical_portrait_path(tmp_path, hostile)
        scene = pr.canonical_scene_master_path(tmp_path, hostile)
        prop = pr.canonical_prop_reference_path(tmp_path, hostile)
        identity = pr.canonical_identity_path(tmp_path, hostile, "id")
        for path in (portrait, scene, prop, identity):
            assert path.is_relative_to(tmp_path), (hostile, str(path))
            # 名字段本身绝不能是 ``..``（否则 resolve 会逃出一层）。
            assert ".." not in path.relative_to(tmp_path).parts


def test_resolver_class_dirs_stay_under_output_dir(tmp_path) -> None:
    resolver = pr.PathResolver(tmp_path, 1)
    hostile = "../evil"
    for path in (
        resolver.grids_dir(hostile),
        resolver.grid(hostile, 1),
        resolver.preset_dir(hostile),
        resolver.character_assets_dir(hostile),
        resolver.final_video(hostile),
    ):
        assert path.is_relative_to(tmp_path), str(path)
