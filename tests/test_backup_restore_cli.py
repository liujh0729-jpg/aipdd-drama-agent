"""restore-cell pure command construction tests."""

from novelvideo.backup.cli import build_rclone_files_cmd, build_restore_config, derive_region


def test_derive_region():
    assert derive_region("oss-cn-chengdu.aliyuncs.com") == "cn-chengdu"
    assert derive_region("oss-cn-chengdu-internal.aliyuncs.com") == "cn-chengdu"


def test_build_restore_config_contains_all_rels_and_region():
    cfg = build_restore_config(
        bucket="dramaclaw-staging",
        endpoint="oss-cn-chengdu.aliyuncs.com",
        region="cn-chengdu",
        prefix="backup/3060/node-3060",
        user="u1",
        project="p1",
        rels=("data.db", "chat.db"),
    )

    assert cfg.count("- path: /restore/") == 2
    assert "region: cn-chengdu" in cfg
    assert "cognee_system/databases/cognee_db" not in cfg
    assert "${OSS_ACCESS_KEY_ID}" in cfg
    assert "${OSS_SECRET_ACCESS_KEY}" in cfg


def test_rclone_files_cmd_excludes_sqlite(tmp_path):
    cmd = build_rclone_files_cmd(
        bucket="dramaclaw-staging",
        prefix="backup/3060/node-3060",
        user="u1",
        project="p1",
        dest=tmp_path,
        filter_file=tmp_path / "filter.txt",
    )

    assert cmd[:2] == ["rclone", "copy"]
    assert cmd[2] == "oss:dramaclaw-staging/backup/3060/node-3060/state/u1/p1"
    assert "--filter-from" in cmd
