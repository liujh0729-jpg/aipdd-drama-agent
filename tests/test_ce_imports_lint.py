from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from lint_ce_imports import scan  # noqa: E402


def test_ce_has_no_ee_imports() -> None:
    """本仓不得 import 不属于它的包（novelvideo_ee / supertale_admin /
    novelvideo.control_plane）。硬零：出现即失败。CI 同步守门见
    .github/workflows/ce-import-lint.yml。"""
    findings = scan(REPO_ROOT)
    assert findings == [], "CE 出现禁止的 EE 导入：\n" + "\n".join(
        f.describe() for f in findings
    )
