from __future__ import annotations

import ast
from collections import Counter
from pathlib import Path


SOURCE_ROOT = Path("src/novelvideo")

# [aipdd fork delta] WP-D: 谓词改名后的同一条防回潮不变量 —— 扫描对象从
# `is_insufficient_credits_error` 变成 `is_upstream_quota_error`，允许表按新分布重建。
# 组合子在自己的模块里先算 typed 信封；run_core 的失败分类紧跟 typed 分支；
# video 保留两处带出网脱敏守卫的调用；chat 的额度分支前后各有 typed 分支兜底；
# cognee 只是日志过滤器，不是 fail-closed 路径。
ALLOWED_POSITIONAL_CALLS = {
    Path("shared/upstream_quota_errors.py"): 1,
    Path("task_backend/run_core.py"): 1,
    Path("generators/video_generator/backends/newapi.py"): 2,
    Path("api/routes/chat.py"): 1,
    Path("cognee/config.py"): 1,
}


def test_fail_closed_paths_do_not_use_narrow_upstream_quota_predicate() -> None:
    positional_calls: Counter[Path] = Counter()

    for source_path in SOURCE_ROOT.rglob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=source_path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            if not isinstance(node.func, ast.Name):
                continue
            if node.func.id != "is_upstream_quota_error":
                continue
            positional_calls[source_path.relative_to(SOURCE_ROOT)] += 1

    unexpected = {
        path: count
        for path, count in positional_calls.items()
        if path not in ALLOWED_POSITIONAL_CALLS
    }

    assert unexpected == {}
    assert positional_calls == Counter(ALLOWED_POSITIONAL_CALLS)
