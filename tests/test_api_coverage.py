from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from novelvideo.api.app import create_app

REPO_ROOT = Path(__file__).resolve().parents[1]
REPORT_SCRIPT = REPO_ROOT / "scripts/acceptance/api_coverage_report.py"


def _load_report_module():
    spec = importlib.util.spec_from_file_location("api_coverage_report", REPORT_SCRIPT)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_api_coverage_middleware_is_not_mounted_without_env(monkeypatch) -> None:
    monkeypatch.delenv("ST_API_COVERAGE_FILE", raising=False)

    app = create_app()

    middleware_names = {middleware.cls.__name__ for middleware in app.user_middleware}
    assert "ApiCoverageMiddleware" not in middleware_names


def test_api_coverage_records_template_path_and_pytest_nodeid(monkeypatch, tmp_path) -> None:
    coverage_file = Path(os.environ.get("ST_API_COVERAGE_FILE") or tmp_path / "api-coverage.ndjson")
    monkeypatch.setenv("ST_API_COVERAGE_FILE", str(coverage_file))

    app = create_app()
    client = TestClient(app)
    response = client.get("/api/v1/projects/demo/characters/lin/asset-history")

    assert response.status_code in {401, 503}
    rows = [json.loads(line) for line in coverage_file.read_text(encoding="utf-8").splitlines()]
    matching_rows = [
        row
        for row in rows
        if "test_api_coverage_records_template_path_and_pytest_nodeid" in row["test"]
    ]
    assert matching_rows
    row = matching_rows[-1]
    assert row["method"] == "GET"
    assert row["path"] == "/api/v1/projects/{project}/characters/{name}/asset-history"
    assert "demo" not in row["path"]


def test_api_coverage_testclient_patch_records_ad_hoc_app(monkeypatch, tmp_path) -> None:
    from novelvideo.shared.api_coverage import install_testclient_api_coverage_patch

    coverage_file = Path(os.environ.get("ST_API_COVERAGE_FILE") or tmp_path / "api-coverage.ndjson")
    monkeypatch.setenv("ST_API_COVERAGE_FILE", str(coverage_file))
    restore_patch = install_testclient_api_coverage_patch()

    app = FastAPI()
    @app.get("/local/{item}")
    def local_item(item: str) -> dict[str, str]:
        return {"item": item}

    try:
        client = TestClient(app)
        response = client.get("/local/demo")
    finally:
        restore_patch()

    assert response.status_code == 200
    rows = [json.loads(line) for line in coverage_file.read_text(encoding="utf-8").splitlines()]
    matching_rows = [
        row
        for row in rows
        if "test_api_coverage_testclient_patch_records_ad_hoc_app" in row["test"]
    ]
    assert matching_rows
    row = matching_rows[-1]
    assert row["method"] == "GET"
    assert row["path"] == "/local/{item}"
    assert "demo" not in row["path"]


async def test_api_coverage_asgi_transport_patch_records_ad_hoc_app(monkeypatch, tmp_path) -> None:
    from novelvideo.shared.api_coverage import install_httpx_asgi_transport_api_coverage_patch

    coverage_file = Path(os.environ.get("ST_API_COVERAGE_FILE") or tmp_path / "api-coverage.ndjson")
    monkeypatch.setenv("ST_API_COVERAGE_FILE", str(coverage_file))
    restore_patch = install_httpx_asgi_transport_api_coverage_patch()

    app = FastAPI()

    @app.get("/async-local/{item}")
    def async_local_item(item: str) -> dict[str, str]:
        return {"item": item}

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/async-local/demo")
    finally:
        restore_patch()

    assert response.status_code == 200
    rows = [json.loads(line) for line in coverage_file.read_text(encoding="utf-8").splitlines()]
    matching_rows = [
        row
        for row in rows
        if "test_api_coverage_asgi_transport_patch_records_ad_hoc_app" in row["test"]
    ]
    assert matching_rows
    row = matching_rows[-1]
    assert row["method"] == "GET"
    assert row["path"] == "/async-local/{item}"
    assert "demo" not in row["path"]


def test_api_coverage_report_groups_uncovered_and_baseline_external_paths(tmp_path) -> None:
    ndjson_path = tmp_path / "coverage.ndjson"
    baseline_path = tmp_path / "baseline.json"
    output_dir = tmp_path / "logs"
    ndjson_path.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "method": "GET",
                        "path": "/api/v1/projects/demo/custom",
                        "status": 200,
                        "test": "tests/test_demo.py::test_custom",
                    }
                ),
                json.dumps(
                    {
                        "method": "GET",
                        "path": "/projects/{project}/characters",
                        "status": 200,
                        "test": "tests/test_characters.py::test_list",
                    }
                ),
                json.dumps(
                    {
                        "method": "GET",
                        "path": None,
                        "raw_path": "/api/v1/missing",
                        "status": 404,
                        "test": "tests/test_missing.py::test_missing",
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    baseline_path.write_text(
        json.dumps(
            {
                "paths": {
                    "/api/v1/auth/login": {"post": {}},
                    "/api/v1/projects/{project}/characters": {"get": {}},
                    "/api/v1/projects/{project}/scenes": {"get": {}},
                }
            }
        ),
        encoding="utf-8",
    )

    report = _load_report_module()
    result = report.write_report(
        ndjson_path=ndjson_path,
        baseline_path=baseline_path,
        output_dir=output_dir,
        timestamp="20260612-120000",
    )
    text = result.report_path.read_text(encoding="utf-8")

    assert "覆盖 1/3" in text
    assert "归一化命中数 1" in text
    assert "| M04 | 1 | 1 | 100.0% |" in text
    assert "tests/test_characters.py::test_list" in text
    assert "## 零覆盖红名单" in text
    assert "- `/api/v1/auth/login`" in text
    assert "- `/api/v1/projects/{project}/scenes`" in text
    assert "## 基线外路径告警" in text
    assert "- `/api/v1/projects/demo/custom`" in text
    assert "- `/api/v1/missing`" in text


def test_api_coverage_report_normalizes_path_converters(tmp_path) -> None:
    ndjson_path = tmp_path / "coverage.ndjson"
    baseline_path = tmp_path / "baseline.json"
    output_dir = tmp_path / "logs"
    ndjson_path.write_text(
        json.dumps(
            {
                "method": "GET",
                "path": "/api/v1/projects/{project}/media/{file_path:path}",
                "status": 200,
                "test": "tests/test_m09.py::test_media",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    baseline_path.write_text(
        json.dumps({"paths": {"/api/v1/projects/{project}/media/{file_path}": {"get": {}}}}),
        encoding="utf-8",
    )

    report = _load_report_module()
    result = report.write_report(
        ndjson_path=ndjson_path,
        baseline_path=baseline_path,
        output_dir=output_dir,
        timestamp="20260620-path",
    )
    text = result.report_path.read_text(encoding="utf-8")

    assert result.covered == 1
    assert result.total == 1
    assert result.normalized_hits == 1
    assert "覆盖 1/1" in text
    assert "## 基线外路径告警\n\n无" in text
    assert "- `/api/v1/projects/{project}/media/{file_path}` -> `tests/test_m09.py::test_media`" in text


@pytest.mark.e2e
def test_dc_coverage_ce_generates_report_with_character_coverage() -> None:
    completed = subprocess.run(
        ["scripts/dev/dc", "coverage", "ce"],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTEST_ADDOPTS": "-m 'not ee and not e2e'"},
        timeout=600,
        check=False,
    )

    assert completed.returncode == 0, completed.stdout
    report_path_line = next(
        line for line in completed.stdout.splitlines() if line.startswith("REPORT: ")
    )
    report_path = Path(report_path_line.removeprefix("REPORT: ").strip())
    if not report_path.is_absolute():
        report_path = REPO_ROOT / report_path
    text = report_path.read_text(encoding="utf-8")
    coverage_line = next(line for line in text.splitlines() if line.startswith("覆盖 "))
    total_covered = int(coverage_line.removeprefix("覆盖 ").split("/", 1)[0])
    assert total_covered >= 49
    m04_line = next(line for line in text.splitlines() if line.startswith("| M04 |"))
    covered = int(m04_line.split("|")[3].strip())
    assert covered > 0
    m07_line = next(line for line in text.splitlines() if line.startswith("| M07 |"))
    assert int(m07_line.split("|")[3].strip()) > 0
    assert "tests/test_api_characters_asset_contract.py::" in text
    assert "tests/test_api_coverage.py::" not in text
