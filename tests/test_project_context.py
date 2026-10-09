from pathlib import Path

import pytest
from fastapi import HTTPException

from novelvideo.ports.project import Principal, ProjectRecord
from novelvideo.project_context import ProjectContext, _ctx_from_record, require_project_home_node


def _ctx(tmp_path: Path, *, is_home_node: bool) -> ProjectContext:
    return ProjectContext(
        project_id="proj_123",
        project_name="demo",
        owner_type="user",
        owner_id="user_owner",
        owner_username="alice",
        requester_user_id="user_editor",
        requester_username="bob",
        requester_principals=(("user", "user_editor"),),
        effective_role="editor",
        home_node_id="node_a",
        output_dir=tmp_path / "output" / "alice" / "demo",
        state_dir=tmp_path / "state" / "alice" / "demo",
        runtime_dir=tmp_path / "runtime" / "alice" / "demo",
        is_home_node=is_home_node,
    )


def test_require_project_home_node_allows_local_project(tmp_path):
    ctx = _ctx(tmp_path, is_home_node=True)

    assert require_project_home_node(ctx) is ctx


def test_require_project_home_node_rejects_remote_project(tmp_path):
    ctx = _ctx(tmp_path, is_home_node=False)

    with pytest.raises(HTTPException) as exc:
        require_project_home_node(ctx, operation="read project files")

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "project_not_on_this_node"
    assert exc.value.detail["home_node_id"] == "node_a"


def test_ctx_from_record_treats_ce_local_home_node_as_local(monkeypatch, tmp_path):
    import novelvideo.project_context as project_context

    monkeypatch.setattr(project_context, "resolve_worker_id", lambda: "node_other")
    record = ProjectRecord(
        id="proj_local",
        owner_type="user",
        owner_id="local",
        owner_username="alice",
        name="demo",
        home_node_id="local",
        output_dir=str(tmp_path / "output"),
        state_dir=str(tmp_path / "state"),
        runtime_dir=str(tmp_path / "runtime"),
        status="active",
    )

    ctx = _ctx_from_record(
        project=record,
        requester_user_id="local",
        requester_username="alice",
        principals=[Principal("user", "local")],
        role="owner",
    )

    assert ctx.is_home_node is True
    assert require_project_home_node(ctx) is ctx
