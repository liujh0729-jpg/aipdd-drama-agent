from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import ce_allowlist  # noqa: E402


def test_manifest_well_formed() -> None:
    """每条豁免必须写明 path / guards / reason，且 guards 取自已知集合。"""
    data = ce_allowlist.load()
    assert data["marker"]["inline"].strip()
    known = set(ce_allowlist.known_guards())
    assert known, "guards.known 不应为空"
    for entry in ce_allowlist.skip_entries():
        assert {"path", "guards", "reason"} <= entry.keys(), f"豁免缺字段: {entry}"
        assert entry["reason"].strip(), f"豁免缺 reason: {entry}"
        assert entry["guards"], f"豁免缺 guards: {entry}"
        assert set(entry["guards"]) <= known, f"未知 guard: {entry}"


def test_no_stale_skip_paths() -> None:
    """登记的豁免路径必须真实存在——防止白名单随文件删改而腐烂。"""
    stale = [
        entry["path"]
        for entry in ce_allowlist.skip_entries()
        if not (REPO_ROOT / entry["path"]).is_file()
    ]
    assert stale == [], "白名单指向不存在的文件（请清理）：\n" + "\n".join(stale)


def test_linters_consume_manifest() -> None:
    """漂移守卫：两个 linter 的有效豁免必须来自清单，且关键自指条目在册。"""
    from lint_banned_words import ALLOW_MARKER as BW_MARKER
    from lint_banned_words import SKIP_PATHS as BW_SKIPS
    from lint_ee_terms import ALLOW_MARKER as EE_MARKER
    from lint_ee_terms import SKIP_PATHS as EE_SKIPS

    marker = ce_allowlist.inline_marker()
    assert BW_MARKER == marker
    assert EE_MARKER == marker
    assert BW_SKIPS == ce_allowlist.skip_paths("banned-words")
    assert EE_SKIPS == ce_allowlist.skip_paths("ee-terms")
    # 规则文件与清单自身必须自豁免，否则会自我命中
    assert "scripts/lint_ee_terms.py" in EE_SKIPS
    assert "ce-allowlist.toml" in EE_SKIPS
    assert "scripts/lint_banned_words.py" in BW_SKIPS
