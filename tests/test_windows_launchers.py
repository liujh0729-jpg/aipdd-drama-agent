"""Windows 一键安装/启动产物的守卫测试。

这些 .bat/.ps1 只能在 Windows 上真正执行，本仓库的 CI/开发机是 macOS，所以用
scripts/check_windows_launchers.py 把「只有运行时才暴露」的问题前移成文本判据。
负例测试是必要的：守卫若咬不住任何一种真实破坏（BOM 丢失、裸 LF、bat 里塞中文、
函数名写错、bat/ps1 退出码契约脱钩），它本身就是个假绿。
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_windows_launchers.py"

PS_FILES = ("scripts/windows_common.ps1", "scripts/windows_setup.ps1", "scripts/windows_start.ps1")
BAT_FILES = ("安装环境-Setup.bat", "启动系统-Start.bat")


def _load_module():
    spec = importlib.util.spec_from_file_location("check_windows_launchers", SCRIPT_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _mini_root(tmp_path: Path) -> Path:
    """把真实产物复制进 tmp_path，便于逐条破坏后确认守卫会报。"""
    root = tmp_path / "repo"
    (root / "scripts").mkdir(parents=True)
    for rel in (*PS_FILES, *BAT_FILES):
        shutil.copy2(REPO_ROOT / rel, root / rel)
    return root


def test_artifacts_exist() -> None:
    for rel in (*PS_FILES, *BAT_FILES):
        assert (REPO_ROOT / rel).is_file(), f"缺少交付物 {rel}"
    # 交付纪律：入口在仓库根（用户双击得到的就是这个路径下的文件）。
    for rel in BAT_FILES:
        assert (REPO_ROOT / rel).stat().st_size > 0


def test_shipped_artifacts_pass_guard() -> None:
    module = _load_module()
    assert module.collect_problems(REPO_ROOT) == []


def test_guard_flags_missing_bom(tmp_path: Path) -> None:
    module = _load_module()
    root = _mini_root(tmp_path)
    path = root / "scripts/windows_common.ps1"
    path.write_bytes(path.read_bytes().removeprefix(b"\xef\xbb\xbf"))
    assert any("BOM" in p for p in module.collect_problems(root))


def test_guard_flags_lf_line_endings(tmp_path: Path) -> None:
    module = _load_module()
    root = _mini_root(tmp_path)
    path = root / "启动系统-Start.bat"
    path.write_bytes(path.read_bytes().replace(b"\r\n", b"\n"))
    assert any("裸 LF" in p for p in module.collect_problems(root))


def test_guard_flags_non_ascii_bat(tmp_path: Path) -> None:
    module = _load_module()
    root = _mini_root(tmp_path)
    path = root / "安装环境-Setup.bat"
    body = path.read_bytes().decode("utf-8") + "echo 中文\r\n"
    path.write_bytes(body.encode("utf-8"))
    assert any("非 ASCII" in p for p in module.collect_problems(root))


def test_guard_flags_dangling_command(tmp_path: Path) -> None:
    module = _load_module()
    root = _mini_root(tmp_path)
    path = root / "scripts/windows_start.ps1"
    raw = path.read_bytes()
    path.write_bytes(raw + b"\n$null = Get-NoSuchHelper 1\r\n")
    problems = module.collect_problems(root)
    assert any("Get-NoSuchHelper" in p for p in problems), problems


def test_guard_flags_unclosed_brace(tmp_path: Path) -> None:
    module = _load_module()
    root = _mini_root(tmp_path)
    path = root / "scripts/windows_setup.ps1"
    raw = path.read_bytes()
    path.write_bytes(raw + b"\n$null = (1 + 2\r\n")
    assert any("没有闭合" in p for p in module.collect_problems(root))


def test_guard_flags_broken_exit_code_contract(tmp_path: Path) -> None:
    module = _load_module()
    root = _mini_root(tmp_path)
    path = root / "启动系统-Start.bat"
    body = path.read_bytes().decode("utf-8").replace("GEQ 3", "GEQ 9")
    path.write_bytes(body.encode("utf-8"))
    assert any("GEQ 3" in p for p in module.collect_problems(root))


def test_guard_flags_missing_execution_policy(tmp_path: Path) -> None:
    module = _load_module()
    root = _mini_root(tmp_path)
    path = root / "安装环境-Setup.bat"
    body = path.read_bytes().decode("utf-8").replace("-ExecutionPolicy Bypass", "")
    path.write_bytes(body.encode("utf-8"))
    assert any("ExecutionPolicy" in p for p in module.collect_problems(root))


def test_multiline_prompt_parentheses_are_not_false_positives(tmp_path: Path) -> None:
    """回归：多行提示字符串里的 `1) 2) 3)` 不能被当结构括号（朴素删引号会误报）。"""
    module = _load_module()
    root = _mini_root(tmp_path)
    path = root / "scripts/windows_start.ps1"
    raw = path.read_bytes()
    path.write_bytes(
        raw
        + (
            "\r\nWait-Here \"定位顺序：\r\n  1) 先看 A\r\n  2) 再看 B\r\n  3) 最后看 C\"\r\n"
            "$value = $value.Trim('\"').Trim(\"'\")\r\n"
        ).encode("utf-8")
    )
    assert module.collect_problems(root) == []
