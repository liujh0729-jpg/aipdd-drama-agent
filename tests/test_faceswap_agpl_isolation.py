# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 换脸直播的 AGPL 进程隔离门禁（CI 硬闸）。

上游 Deep-Live-Cam 是 AGPL-3.0，与本项目 Elastic-2.0 分发不兼容。融合采用**进程隔离边车**：
换脸引擎是自研代码放在 ``services/faceswap``（Elastic-2.0），主后端 ``src/novelvideo`` 只经
localhost HTTP 控制它，**绝不 import 边车、也绝不把换脸重依赖拉进主环境**。本测试用 AST 静态
扫描把这条边界钉死，防止日后有人图省事直接 import 边车模块或把 insightface 塞进主依赖图。
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAIN_SRC = REPO_ROOT / "src" / "novelvideo"
SIDECAR = REPO_ROOT / "services" / "faceswap"

# 只允许出现在边车独立 venv、绝不能进入主后端 import 图的换脸重依赖。
SIDECAR_ONLY_MODULES = {"insightface", "pyvirtualcam"}


def _imported_modules(py_file: Path) -> set[str]:
    """收集一个 .py 文件里所有 import 的顶层模块名。"""
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def _main_backend_py_files() -> list[Path]:
    return [
        p
        for p in MAIN_SRC.rglob("*.py")
        if "__pycache__" not in p.parts and p.suffix == ".py"
    ]


def test_main_backend_never_imports_the_sidecar_package():
    """src/novelvideo 不得 import services.*——边车只能经 localhost HTTP 控制。"""
    offenders: list[str] = []
    for py in _main_backend_py_files():
        if "services" in _imported_modules(py):
            offenders.append(str(py.relative_to(REPO_ROOT)))
    assert offenders == [], "主后端不得 import 边车包（破坏 AGPL 进程隔离）：\n" + "\n".join(offenders)


def test_main_backend_does_not_pull_swap_heavy_deps():
    """insightface / pyvirtualcam 只属于边车 venv，不能被主后端 import（否则污染主依赖图）。"""
    offenders: list[tuple[str, str]] = []
    for py in _main_backend_py_files():
        hits = _imported_modules(py) & SIDECAR_ONLY_MODULES
        for mod in hits:
            offenders.append((str(py.relative_to(REPO_ROOT)), mod))
    assert offenders == [], "换脸重依赖不得出现在主后端：\n" + "\n".join(
        f"{p} imports {m}" for p, m in offenders
    )


def test_sidecar_engine_entry_exists():
    assert (SIDECAR / "server.py").is_file(), "换脸边车引擎入口缺失"
    assert (SIDECAR / "requirements.txt").is_file(), "边车依赖清单缺失"


def test_sidecar_source_is_elastic_not_agpl():
    """services/faceswap 下的自研源码必须全部声明 Elastic-2.0，不含上游 AGPL 代码。"""
    offenders: list[str] = []
    for py in sorted(SIDECAR.rglob("*.py")):
        if "__pycache__" in py.parts:
            continue
        head = py.read_text(encoding="utf-8")[:400]
        if "SPDX-License-Identifier: Elastic-2.0" not in head:
            offenders.append(str(py.relative_to(REPO_ROOT)))
    assert offenders == [], "边车源码应声明 Elastic-2.0：\n" + "\n".join(offenders)


def test_sidecar_dir_contains_no_agpl_copied_source():
    """边车目录不得出现 AGPL 许可声明（我们只用宽松依赖自研，不复制上游源码）。"""
    for py in SIDECAR.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        text = py.read_text(encoding="utf-8")
        # 允许在文档/注释里提及 AGPL 做隔离说明，但不得作为本文件的 SPDX 许可标识。
        for line in text.splitlines()[:5]:
            assert "SPDX-License-Identifier: AGPL" not in line, (
                f"边车文件不应以 AGPL 声明：{py}"
            )
