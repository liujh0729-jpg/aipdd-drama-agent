from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from lint_ee_terms import collect_targets, scan  # noqa: E402


def test_repo_has_no_forbidden_package_names() -> None:
    """本仓不得出现不属于它的包名（字符串/注释/文档/配置层面）。
    硬零：出现即失败。与 import-lint 互补，CI 守门见
    .github/workflows/ee-terms.yml。"""
    offenders: list[str] = []
    for path in collect_targets([]):
        for lineno, label, _ in scan(path):
            offenders.append(f"{path}:{lineno}: {label}")
    assert offenders == [], "出现禁用包名：\n" + "\n".join(offenders)
