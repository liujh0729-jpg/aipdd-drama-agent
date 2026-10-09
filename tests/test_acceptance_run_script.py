from pathlib import Path


def test_acceptance_runner_avoids_bash4_uppercase_expansion() -> None:
    script = Path("scripts/acceptance/run.sh").read_text(encoding="utf-8")

    assert "${MODE^^}" not in script
    assert "MODE_UPPER=" in script
