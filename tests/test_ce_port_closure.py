from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "check_ce_port_closure.py"

sys.path.insert(0, str(REPO_ROOT / "scripts"))

from check_ce_port_closure import _classify  # noqa: E402


def test_ce_port_closure_passes() -> None:
    """纯 CE 模式下 11 个端口须全部由 novelvideo.ports.local.* 满足、无 EE 依赖。
    用子进程跑，隔离 ensure_bootstrap() 对模块级 _BOOTSTRAPPED/_PORTS 的改动，
    避免污染同进程内其他测试。CI 同步守门见 .github/workflows/ci.yml。"""
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert proc.returncode == 0, f"CE 端口闭合失败：\n{proc.stdout}\n{proc.stderr}"


@pytest.mark.parametrize(
    "module",
    [
        "novelvideo_ee.auth_service",
        "supertale_admin.main",
        "novelvideo.control_plane.auth",
        "novelvideo.services.foo",  # 非 local 前缀也算违规
    ],
)
def test_classify_flags_non_ce_impl(module: str) -> None:
    assert _classify(module) is not None


@pytest.mark.parametrize(
    "module",
    ["novelvideo.ports.local", "novelvideo.ports.local.auth"],
)
def test_classify_allows_ce_local_impl(module: str) -> None:
    assert _classify(module) is None
