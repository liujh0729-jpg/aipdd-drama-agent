from __future__ import annotations

import ast
import csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROVENANCE = ROOT / "docs/test-governance/ee-split-migrated-tests.tsv"


def _rows() -> list[dict[str, str]]:
    with PROVENANCE.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        assert reader.fieldnames == ["id", "source_node", "target_node"]
        return list(reader)


def test_ee_split_migrated_nodes_remain_present_in_ce() -> None:
    rows = _rows()
    # [aipdd fork delta] WP-D: 额度体系拆除后，6 条纯 credit 主题迁移记录
    # （CE-001~004 / CE-013 / CE-029）连同文件一并下线，36 → 30；CE-037 重指向
    # 改名后的观测面用例（源端 EE 节点名保留不变）。
    assert len(rows) == 30
    assert len({row["id"] for row in rows}) == len(rows)
    assert len({row["source_node"] for row in rows}) == len(rows)
    assert len({row["target_node"] for row in rows}) == len(rows)

    missing: list[str] = []
    for row in rows:
        path_text, separator, function_name = row["target_node"].partition("::")
        assert separator and function_name.startswith("test_")
        path = ROOT / path_text
        if not path.is_file():
            missing.append(f'{row["id"]}: missing file {path_text}')
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path_text)
        functions = {
            node.name
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        if function_name not in functions:
            missing.append(f'{row["id"]}: missing node {row["target_node"]}')

    assert missing == []
