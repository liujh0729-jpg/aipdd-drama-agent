#!/usr/bin/env python3
"""Build a markdown API coverage report from runtime NDJSON traces."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

MODULE_ORDER = [
    "M01",
    "M02",
    "M03",
    "M04",
    "M05",
    "M06",
    "M07",
    "M08",
    "M09",
    "横切/M10",
    "未分类",
]


@dataclass(frozen=True)
class ReportResult:
    report_path: Path
    overview: str
    covered: int
    total: int
    normalized_hits: int


def _is_param(part: str) -> bool:
    return part.startswith("{") and part.endswith("}")


def classify_path(path: str) -> str:
    rest = path.removeprefix("/api/v1/")
    parts = rest.strip("/").split("/") if rest.strip("/") else []
    first = parts[0] if parts else ""

    if path == "/healthz" or first == "generation-credit-cost":
        return "横切/M10"
    if first in {"auth", "users"}:
        return "M01"
    if first in {"agent", "chat"}:
        return "M08"
    if first in {"styles"}:
        return "M04"
    if first == "freezone":
        return "M06"

    if first != "projects":
        return "未分类"

    if len(parts) == 1:
        return "M02"
    if len(parts) == 2 and _is_param(parts[1]):
        return "M02"
    if len(parts) >= 2 and parts[1] == "summaries":
        return "M03"
    if len(parts) < 3 or not _is_param(parts[1]):
        return "未分类"

    domain = parts[2]
    if domain in {"grants", "archive", "unarchive", "delete", "purge", "restore", "files"}:
        return "M02"
    if domain in {"episodes", "summaries", "chapters"}:
        return "M03"
    if (
        domain in {"characters", "props", "styles", "narrator-voice", "tts"}
        or domain.startswith("character-image-")
    ):
        return "M04"
    if domain in {"scenes", "sketch-settings", "director-stage"}:
        return "M05"
    if domain in {"ingest", "freezone"}:
        return "M06"
    if domain in {"tasks", "pipeline"}:
        return "M07"
    if domain in {"media", "render-settings", "video-backends", "assets"}:
        return "M09"
    return "未分类"


def _load_baseline_paths(path: Path) -> list[str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    paths = data.get("paths", {})
    if not isinstance(paths, dict):
        raise ValueError(f"{path} does not contain an OpenAPI paths object")
    return sorted(str(item) for item in paths)


def _canonical_recorded_path(
    recorded_path: str,
    baseline_set: set[str],
) -> tuple[str | None, bool]:
    if recorded_path in baseline_set:
        return recorded_path, False
    converter_normalized = _strip_path_converters(recorded_path)
    if converter_normalized in baseline_set:
        return converter_normalized, True
    if recorded_path.startswith("/") and not recorded_path.startswith("/api/v1/"):
        candidate = f"/api/v1{recorded_path}"
        if candidate in baseline_set:
            return candidate, True
        converter_normalized = _strip_path_converters(candidate)
        if converter_normalized in baseline_set:
            return converter_normalized, True
    return None, False


def _strip_path_converters(path: str) -> str:
    return path.replace(":path}", "}").replace(":str}", "}").replace(":int}", "}")


def _load_hits(
    path: Path,
    baseline_paths: list[str],
    *,
    exclude_test_prefixes: tuple[str, ...] = (),
) -> tuple[dict[str, set[str]], set[str], int]:
    hits: dict[str, set[str]] = defaultdict(set)
    baseline_external: set[str] = set()
    normalized_hits = 0
    baseline_set = set(baseline_paths)
    if not path.exists():
        return hits, baseline_external, normalized_hits

    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{line_number}: invalid NDJSON: {exc}") from exc

        test = str(row.get("test") or "")
        if exclude_test_prefixes and test.startswith(exclude_test_prefixes):
            continue

        recorded_path = row.get("path")
        if recorded_path:
            canonical_path, normalized = _canonical_recorded_path(str(recorded_path), baseline_set)
            if canonical_path is None:
                baseline_external.add(str(recorded_path))
                continue
            hits[canonical_path].add(test)
            if normalized:
                normalized_hits += 1
            continue

        raw_path = row.get("raw_path")
        if raw_path:
            baseline_external.add(str(raw_path))
    return hits, baseline_external, normalized_hits


def _coverage_rate(covered: int, total: int) -> str:
    if total == 0:
        return "0.0%"
    return f"{covered / total * 100:.1f}%"


def _overview_lines(
    baseline_paths: list[str],
    hits: dict[str, set[str]],
    by_module: dict[str, list[str]],
    normalized_hits: int,
) -> list[str]:
    covered = sum(1 for path in baseline_paths if path in hits)
    lines = [
        "## 总览",
        "",
        f"覆盖 {covered}/{len(baseline_paths)}",
        f"归一化命中数 {normalized_hits}",
        "",
        "| 模块 | 路径数 | 覆盖 | 覆盖率 |",
        "|---|---:|---:|---:|",
    ]
    for module in MODULE_ORDER:
        paths = by_module.get(module, [])
        if not paths:
            continue
        module_covered = sum(1 for path in paths if path in hits)
        lines.append(
            f"| {module} | {len(paths)} | {module_covered} | "
            f"{_coverage_rate(module_covered, len(paths))} |"
        )
    return lines


def _format_tests(tests: Iterable[str]) -> str:
    clean_tests = sorted(test for test in tests if test)
    if not clean_tests:
        return "未命中"
    visible = clean_tests[:5]
    suffix = "" if len(clean_tests) <= 5 else f" ...（共 {len(clean_tests)} 个）"
    return ", ".join(f"`{test}`" for test in visible) + suffix


def build_report_markdown(
    *,
    baseline_paths: list[str],
    hits: dict[str, set[str]],
    baseline_external: set[str],
    normalized_hits: int = 0,
) -> tuple[str, str, int, int]:
    by_module: dict[str, list[str]] = defaultdict(list)
    for path in baseline_paths:
        by_module[classify_path(path)].append(path)

    overview_lines = _overview_lines(baseline_paths, hits, by_module, normalized_hits)
    unclassified = by_module.get("未分类", [])
    baseline_external_paths = sorted(baseline_external)
    uncovered = sorted(path for path in baseline_paths if path not in hits)
    covered = len(baseline_paths) - len(uncovered)

    lines: list[str] = ["# API 覆盖对照表", ""]
    if unclassified:
        lines.append(
            f"> 告警：{len(unclassified)} 条基线路径未匹配模块前缀，已归入“未分类”。"
        )
        lines.append("")
    lines.extend(overview_lines)
    lines.extend(["", "## 逐模块", ""])

    for module in MODULE_ORDER:
        paths = by_module.get(module, [])
        if not paths:
            continue
        lines.extend([f"### {module}", ""])
        for path in paths:
            lines.append(f"- `{path}` -> {_format_tests(hits.get(path, set()))}")
        lines.append("")

    lines.extend(["## 零覆盖红名单", ""])
    if uncovered:
        lines.extend(f"- `{path}`" for path in uncovered)
    else:
        lines.append("无")
    lines.extend(["", "## 基线外路径告警", ""])
    if baseline_external_paths:
        lines.extend(f"- `{path}`" for path in baseline_external_paths)
    else:
        lines.append("无")
    lines.append("")

    return "\n".join(lines), "\n".join(overview_lines), covered, len(baseline_paths)


def write_report(
    *,
    ndjson_path: Path,
    baseline_path: Path,
    output_dir: Path,
    timestamp: str | None = None,
    exclude_test_prefixes: tuple[str, ...] = (),
) -> ReportResult:
    baseline_paths = _load_baseline_paths(baseline_path)
    hits, baseline_external, normalized_hits = _load_hits(
        ndjson_path,
        baseline_paths,
        exclude_test_prefixes=exclude_test_prefixes,
    )
    markdown, overview, covered, total = build_report_markdown(
        baseline_paths=baseline_paths,
        hits=hits,
        baseline_external=baseline_external,
        normalized_hits=normalized_hits,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    stamp = timestamp or datetime.now().strftime("%Y%m%d-%H%M%S")
    report_path = output_dir / f"api-coverage-{stamp}.md"
    report_path.write_text(markdown, encoding="utf-8")
    return ReportResult(
        report_path=report_path,
        overview=overview,
        covered=covered,
        total=total,
        normalized_hits=normalized_hits,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("ndjson_path", type=Path)
    parser.add_argument(
        "--baseline",
        type=Path,
        default=Path("docs/oss-split/openapi-baseline.json"),
    )
    parser.add_argument("--output-dir", type=Path, default=Path("acceptance-logs"))
    parser.add_argument("--timestamp", default=None)
    parser.add_argument(
        "--exclude-test-prefix",
        action="append",
        default=[],
        help="Ignore NDJSON rows whose pytest nodeid starts with this prefix.",
    )
    args = parser.parse_args()

    result = write_report(
        ndjson_path=args.ndjson_path,
        baseline_path=args.baseline,
        output_dir=args.output_dir,
        timestamp=args.timestamp,
        exclude_test_prefixes=tuple(args.exclude_test_prefix),
    )
    print(result.overview)
    print(f"REPORT: {result.report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
