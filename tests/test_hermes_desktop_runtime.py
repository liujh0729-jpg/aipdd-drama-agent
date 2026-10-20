"""Desktop Hermes runtime wiring: launcher args, child env, copy fallback."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from novelvideo.chat import hermes_pool, hermes_sdk, hermes_workspace as hw


def test_cli_args_parsed_from_env(monkeypatch):
    monkeypatch.setenv(
        "HERMES_CLI_ARGS",
        '-c "from hermes_cli.main import main; main()"',
    )
    assert hermes_pool._hermes_cli_args() == [
        "-c",
        "from hermes_cli.main import main; main()",
    ]


def test_cli_args_empty_and_invalid(monkeypatch):
    monkeypatch.delenv("HERMES_CLI_ARGS", raising=False)
    assert hermes_pool._hermes_cli_args() == []
    monkeypatch.setenv("HERMES_CLI_ARGS", '-c "unterminated')
    assert hermes_pool._hermes_cli_args() == []


def test_availability_requires_site_packages(monkeypatch, tmp_path):
    cli = tmp_path / "python.exe"
    cli.write_text("", encoding="utf-8")
    monkeypatch.setenv("HERMES_CLI_PATH", str(cli))
    monkeypatch.setenv("HERMES_SITE_PACKAGES", str(tmp_path / "missing"))
    assert hermes_pool.is_hermes_backend_available() is False
    (tmp_path / "missing").mkdir()
    assert hermes_pool.is_hermes_backend_available() is True


def test_runtime_env_container_keeps_strict_path(monkeypatch):
    monkeypatch.delenv("APP_RESOURCE_PATH", raising=False)
    monkeypatch.setenv("HERMES_SITE_PACKAGES", "/opt/hermes/site-packages")
    assert hermes_pool._runtime_env() == {"PATH": "/usr/local/bin:/usr/bin:/bin"}


def test_runtime_env_desktop_uses_bundled_path_and_site(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_RESOURCE_PATH", str(tmp_path / "resources"))
    monkeypatch.setenv("HERMES_SITE_PACKAGES", str(tmp_path / "hermes"))
    monkeypatch.setenv("PATH", f"{tmp_path / 'bin'};/usr/bin")
    env = hermes_pool._runtime_env()
    assert env["PATH"].startswith(str(tmp_path / "bin"))
    assert env["PYTHONPATH"] == str(tmp_path / "hermes")


@pytest.mark.skipif(hermes_pool.os.name != "nt", reason="Windows-only child env")
def test_child_runtime_env_confines_windows_home(monkeypatch, tmp_path):
    monkeypatch.setenv("APP_RESOURCE_PATH", str(tmp_path / "resources"))
    monkeypatch.delenv("HERMES_SITE_PACKAGES", raising=False)
    home = tmp_path / "state" / "alice" / ".hermes"
    env = hermes_pool._child_runtime_env(home)
    assert env["USERPROFILE"] == str(home)
    assert env["LOCALAPPDATA"] == str(home / "AppData" / "Local")
    assert env["TEMP"] == str(home / "tmp")


@pytest.mark.asyncio
async def test_spawn_places_cli_args_before_acp(monkeypatch, tmp_path):
    captured: dict[str, list[str]] = {}

    async def fake_exec(*args, **_kwargs):
        captured["argv"] = list(args)
        raise RuntimeError("stop after capture")

    monkeypatch.setattr(hermes_sdk, "wrap_command", lambda cmd, _spec: list(cmd))
    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

    thread = hermes_sdk.HermesSdkThread(
        cli_path=tmp_path / "python.exe",
        cli_args=["-c", "from hermes_cli.main import main; main()"],
        cwd=tmp_path,
        env={},
        model=None,
        username="alice",
        session_id=None,
    )
    with pytest.raises(RuntimeError, match="stop after capture"):
        await thread._spawn()
    assert captured["argv"] == [
        str(tmp_path / "python.exe"),
        "-c",
        "from hermes_cli.main import main; main()",
        "acp",
    ]


def _seed_repo(monkeypatch, tmp_path: Path, name: str = "dramaclaw") -> tuple[Path, Path]:
    root = tmp_path / "repo"
    source = root / ".hermes" / "skills" / name
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("v1", encoding="utf-8")
    monkeypatch.setattr(hw, "DRAMACLAW_ROOT", root)
    skills_dir = tmp_path / "home" / "skills"
    skills_dir.mkdir(parents=True)
    return source, skills_dir


def test_workspace_falls_back_to_managed_copy(monkeypatch, tmp_path):
    source, skills_dir = _seed_repo(monkeypatch, tmp_path)
    monkeypatch.setattr(hw.os, "name", "nt")

    def no_symlink(self, _target, target_is_directory=False):
        raise OSError(1314, "A required privilege is not held by the client")

    monkeypatch.setattr(Path, "symlink_to", no_symlink)

    hw._materialize_skill_links(skills_dir)
    link = skills_dir / "dramaclaw"
    assert link.is_dir() and not link.is_symlink()
    assert (link / "SKILL.md").read_text(encoding="utf-8") == "v1"
    assert hw._is_managed_copy(link)

    # Unchanged source: keep the copy untouched (no per-spawn re-copy).
    (link / "local.txt").write_text("user addition", encoding="utf-8")
    hw._materialize_skill_links(skills_dir)
    assert (link / "local.txt").exists()

    # Changed source: refresh the managed copy.
    (source / "SKILL.md").write_text("v2", encoding="utf-8")
    hw._materialize_skill_links(skills_dir)
    assert (link / "SKILL.md").read_text(encoding="utf-8") == "v2"


def test_stale_managed_copy_removed(monkeypatch, tmp_path):
    _source, skills_dir = _seed_repo(monkeypatch, tmp_path)
    monkeypatch.setattr(hw.os, "name", "nt")
    monkeypatch.setattr(
        Path,
        "symlink_to",
        lambda self, _t, target_is_directory=False: (_ for _ in ()).throw(OSError(1314)),
    )
    hw._materialize_skill_links(skills_dir)
    assert (skills_dir / "dramaclaw").exists()

    monkeypatch.setattr(hw, "DEFAULT_HERMES_SKILLS", set())
    monkeypatch.setenv("ST_HERMES_SKILLS", "other")
    hw._materialize_skill_links(skills_dir)
    assert not (skills_dir / "dramaclaw").exists()
