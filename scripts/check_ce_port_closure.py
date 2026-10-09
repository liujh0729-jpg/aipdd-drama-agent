#!/usr/bin/env python3
"""端口闭合守栏（cp-port）。

本仓库须能在 ST_EDITION=ce / 无 DSN 下独立运行——所有 ports.* 能力都由本地实现
（novelvideo.ports.local.*）满足，不依赖任何外部适配器。本守栏做端口闭合校验：

  1. ensure_bootstrap() 成功（不缺端口、不报矛盾配置）；
  2. 11 个门面 getter 全部解析，且实现类落在 novelvideo.ports.local.*
     （命中 novelvideo_ee / supertale_admin / novelvideo.control_plane 即外部实现）；
  3. 入口点组 novelvideo.ports_bootstrap 为空——安装里不得混入外部适配器包。

任一不满足 → 退出码 1。与 import-lint（lint_ce_imports.py）互补：
import-lint 防「源码层」直接 import，本守栏防「运行期/打包层」端口被外部实现兜底。
"""
from __future__ import annotations

import os
import sys
from importlib.metadata import entry_points

# 端口实现必须落在此前缀下（本地实现）
EXPECTED_LOCAL_PREFIX = "novelvideo.ports.local"
# 实现类模块命中以下任一前缀即判定为外部实现
FORBIDDEN_IMPL_PREFIXES = (
    "novelvideo_ee",
    "supertale_admin",
    "novelvideo.control_plane",
)
# novelvideo.ports.__all__ 暴露的 10 个门面 getter（与 _EE_REQUIRED_PORTS 一一对应）
# [aipdd fork delta] WP-D: get_credit_quote 随 CE 额度体系一起删除，不再要求闭合。
FACADE_GETTERS = (
    "get_auth_port",
    "get_auth_session_port",
    "get_project_registry",
    "get_project_access",
    "get_usage_meter",
    "get_provider_instrumentation",
    "get_task_backend",
    "get_cancellation_store",
    "get_audit_sink",
    "get_lifecycle_port",
)


def _impl_module(impl: object) -> str:
    return type(impl).__module__


def _classify(module: str) -> str | None:
    """返回违规原因；合规返回 None。"""
    for prefix in FORBIDDEN_IMPL_PREFIXES:
        if module == prefix or module.startswith(f"{prefix}."):
            return f"外部实现（{module}）"
    if module != EXPECTED_LOCAL_PREFIX and not module.startswith(f"{EXPECTED_LOCAL_PREFIX}."):
        return f"非本地实现（{module}，期望 {EXPECTED_LOCAL_PREFIX}.*）"
    return None


def main() -> int:
    # 强制纯 CE 模式，确保确定性（不受调用方 env 干扰）
    os.environ["ST_EDITION"] = "ce"
    os.environ.pop("ST_CONTROL_PLANE_DSN", None)

    import novelvideo.ports as ports
    from novelvideo.ports.registry import ensure_bootstrap

    try:
        ensure_bootstrap()
    except Exception as exc:  # noqa: BLE001 — 启动失败本身即闭合失败
        print(f"✖ ensure_bootstrap() 在纯 CE 模式下失败：{exc}", file=sys.stderr)
        return 1

    failures: list[str] = []
    for name in FACADE_GETTERS:
        getter = getattr(ports, name, None)
        if getter is None:
            failures.append(f"{name}: 门面缺失（novelvideo.ports 未暴露）")
            continue
        try:
            impl = getter()
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{name}: 解析抛错 {exc.__class__.__name__}: {exc}")
            continue
        module = _impl_module(impl)
        reason = _classify(module)
        if reason:
            failures.append(f"{name}: {reason}")
        else:
            print(f"  ✓ {name} → {type(impl).__name__} ({module})")

    eps = list(entry_points(group="novelvideo.ports_bootstrap"))
    if eps:
        joined = ", ".join(f"{ep.name}={ep.value}" for ep in eps)
        failures.append(
            f"入口点组 novelvideo.ports_bootstrap 非空（安装混入外部适配器）：{joined}"
        )

    if failures:
        print(f"\n✖ 端口闭合失败，{len(failures)} 项：", file=sys.stderr)
        for item in failures:
            print(f"  {item}", file=sys.stderr)
        print(
            "\n所有端口须由 novelvideo.ports.local.* 满足，"
            "不得依赖外部适配器或 novelvideo.ports_bootstrap 入口点。",
            file=sys.stderr,
        )
        return 1

    print(f"\n✓ 端口闭合：{len(FACADE_GETTERS)} 个端口均由本地实现满足，无外部依赖。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
