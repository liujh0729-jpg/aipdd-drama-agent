from __future__ import annotations

import pytest

from novelvideo import ports
from novelvideo.ports import registry
from novelvideo.ports.local.project import AllowAllProjectAccess


class FakeProjectAccess:
    async def count_project_task_eligible_users(self, **kwargs):
        return 999


@pytest.mark.asyncio
async def test_01_port_registry_isolation_probe_registers_fake_after_bootstrap(monkeypatch):
    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)

    registry.ensure_bootstrap()
    registry.register_port("project_access", FakeProjectAccess())

    assert await ports.get_project_access().count_project_task_eligible_users(
        project_id="proj",
        owner_type="user",
        owner_id="owner",
    ) == 999
    assert registry._BOOTSTRAPPED is True


@pytest.mark.asyncio
async def test_02_port_registry_isolation_probe_restores_default_and_bootstrap_flag(
    monkeypatch,
):
    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)

    registry.ensure_bootstrap()
    project_access = ports.get_project_access()

    assert isinstance(project_access, AllowAllProjectAccess)
    assert await project_access.count_project_task_eligible_users(
        project_id="proj",
        owner_type="user",
        owner_id="owner",
    ) == 1
    assert registry._BOOTSTRAPPED is True
