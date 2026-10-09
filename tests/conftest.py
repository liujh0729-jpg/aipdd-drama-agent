from __future__ import annotations

import os
import tempfile

import pytest

# [测试卫生] 全局隔离运行时目录（``runtime/api.log`` / json-render 错误日志等）：
# ``novelvideo.api`` 在 import 时即挂 RotatingFileHandler 写 ``RUNTIME_DIR/api.log``，
# 测试里刻意触发的预期错误路径（fake 抛错 / 路由校验分支）会把开发日志刷成
# 噪声。必须在任何 novelvideo 模块 import 之前设置环境变量（config 在 import
# 时求值一次）；已显式配置 ``NOVELVIDEO_RUNTIME_DIR`` 的调用方（CI / 定制
# 环境）不受影响。
_TEST_RUNTIME_DIR = tempfile.mkdtemp(prefix="novelvideo-test-runtime-")
os.environ.setdefault("NOVELVIDEO_RUNTIME_DIR", _TEST_RUNTIME_DIR)
# 同源机制：ui-spec 校验失败的诊断记录默认写 ``runtime/jr_error.log``
# （``JR_ERROR_LOG`` 可覆盖，service 侧既有契约；路径函数本身仍按原语义
# 断言 runtime 默认值），测试把预期内的校验失败记录一并隔离到临时目录。
os.environ.setdefault("JR_ERROR_LOG", f"{_TEST_RUNTIME_DIR}/jr_error.log")


@pytest.fixture(autouse=True)
def restore_ports_registry_globals():
    from novelvideo.ports import registry
    from novelvideo.ports.local import LocalAuthz
    from novelvideo.ports.product_surface_access import LocalProductSurfaceAccess

    ports_snapshot = dict(registry._PORTS)
    bootstrapped_snapshot = registry._BOOTSTRAPPED
    if "product_surface_access" not in registry._PORTS:
        registry.register_port("product_surface_access", LocalProductSurfaceAccess())
    # [测试卫生] 镜像真实进程：app 启动即 ensure_bootstrap()，CE 单机把 ``authz``
    # 注册为 ``LocalAuthz``（``admit_model_task`` 返回 ``kind="local"``→非组织）。
    # 请求路径的 ``request_egress_scope`` 因此解析为「非组织→None→不绑任何身份」，
    # 平台/CE 路径逐字节不变——这正是生产语义。刻意不放宽 egress_binding 对
    # 「authz 在场但 admission 失败」的 fail-closed：缺席端口只可能是无组织部署，
    # 不存在被蒙混的组织租户。需要特定 admission 的用例仍自行 patch
    # ``get_authz_port`` 或 ``ensure_bootstrap``（在本夹具之后运行，覆盖之）。
    if "authz" not in registry._PORTS:
        registry.register_port("authz", LocalAuthz())
    try:
        yield
    finally:
        registry._PORTS.clear()
        registry._PORTS.update(ports_snapshot)
        registry._BOOTSTRAPPED = bootstrapped_snapshot


@pytest.fixture(autouse=True)
def isolate_global_state_dir(tmp_path, monkeypatch):
    """[测试卫生] 全局隔离 settings DB（``<STATE_DIR>/local/settings.db``）。

    任何测试都不应读写真实部署的 gateway 模式 / 渠道 / 媒体映射配置：默认把
    ``config.STATE_DIR`` 指到 per-test 临时目录（空配置 ⇔ 内置默认行为）。需要
    具体配置的测试自行往隔离库里写（如 ``save_newapi_media_model_mappings``）。
    """
    from novelvideo import config

    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "_global_settings_state"))


@pytest.fixture(autouse=True)
async def close_sqlite_stores_created_by_test(monkeypatch):
    from novelvideo.sqlite_store import SQLiteStore

    stores = []
    original_init = SQLiteStore.__init__

    def tracked_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        stores.append(self)

    monkeypatch.setattr(SQLiteStore, "__init__", tracked_init)
    yield
    for store in reversed(stores):
        if not store.is_closed():
            await store.close()


@pytest.fixture(scope="session", autouse=True)
def api_coverage_testclient_patch():
    if not os.environ.get("ST_API_COVERAGE_FILE"):
        yield
        return

    from novelvideo.shared.api_coverage import (
        install_httpx_asgi_transport_api_coverage_patch,
        install_testclient_api_coverage_patch,
    )

    restore_testclient_patch = install_testclient_api_coverage_patch()
    restore_asgi_transport_patch = install_httpx_asgi_transport_api_coverage_patch()
    try:
        yield
    finally:
        restore_asgi_transport_patch()
        restore_testclient_patch()


@pytest.fixture(params=("ce", pytest.param("ee", marks=pytest.mark.ee)))
def app_client(request, monkeypatch):
    """Testing.md dual-mode fixture skeleton.

    T1 uses this only as a mode anchor; full app assembly starts in later slices.
    CE delenv does not affect an already-instantiated control_plane.config.settings
    singleton. Full app assembly (L2 from T3 onward) must rebuild via the app
    factory or a subprocess; do not use this fixture to assert settings behavior.
    """
    mode = str(request.param)
    if mode == "ce":
        monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)
        monkeypatch.setenv("ST_EDITION", "ce")
    return {"mode": mode}
