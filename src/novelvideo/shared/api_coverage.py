"""Runtime API coverage recorder for pytest-driven acceptance runs."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Callable

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.types import ASGIApp

_APP_MARKER = "_novelvideo_api_coverage_file"
_TESTCLIENT_ORIGINAL_INIT = "_novelvideo_api_coverage_original_init"
_ASGI_TRANSPORT_ORIGINAL_INIT = "_novelvideo_api_coverage_original_init"


class ApiCoverageMiddleware(BaseHTTPMiddleware):
    """Append one NDJSON row per HTTP response when explicitly enabled."""

    def __init__(self, app: ASGIApp, *, coverage_file: str) -> None:
        super().__init__(app)
        self.coverage_file = Path(coverage_file)

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        route = request.scope.get("route")
        route_path = getattr(route, "path", None)
        root_path = str(request.scope.get("root_path") or "")
        if route_path is not None and root_path and not str(route_path).startswith(root_path):
            route_path = f"{root_path.rstrip('/')}/{str(route_path).lstrip('/')}"
        if (
            route_path is not None
            and request.url.path.startswith("/api/v1/")
            and not str(route_path).startswith("/api/v1/")
        ):
            route_path = f"/api/v1/{str(route_path).lstrip('/')}"
        row = {
            "method": request.method,
            "path": route_path,
            "status": response.status_code,
            "test": os.environ.get("PYTEST_CURRENT_TEST", ""),
        }
        if route_path is None:
            row["raw_path"] = request.url.path

        self.coverage_file.parent.mkdir(parents=True, exist_ok=True)
        with self.coverage_file.open("a", encoding="utf-8", buffering=1) as handle:
            handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
        return response


def mount_api_coverage_middleware(app: FastAPI, coverage_file: str | None = None) -> bool:
    """Mount API coverage middleware once when coverage recording is enabled."""
    coverage_file = coverage_file or os.environ.get("ST_API_COVERAGE_FILE")
    if not coverage_file:
        return False
    if getattr(app, _APP_MARKER, None):
        return False
    app.add_middleware(ApiCoverageMiddleware, coverage_file=coverage_file)
    setattr(app, _APP_MARKER, coverage_file)
    return True


def install_testclient_api_coverage_patch() -> Callable[[], None]:
    """Patch TestClient construction so pytest mini-apps are recorded."""
    from starlette.testclient import TestClient

    existing_original = getattr(TestClient, _TESTCLIENT_ORIGINAL_INIT, None)
    if existing_original is not None:
        return lambda: None

    original_init = TestClient.__init__

    def patched_init(self, app, *args, **kwargs):
        mount_api_coverage_middleware(app)
        original_init(self, app, *args, **kwargs)

    setattr(TestClient, _TESTCLIENT_ORIGINAL_INIT, original_init)
    TestClient.__init__ = patched_init

    def restore() -> None:
        current_original = getattr(TestClient, _TESTCLIENT_ORIGINAL_INIT, None)
        if current_original is not None:
            TestClient.__init__ = current_original
            delattr(TestClient, _TESTCLIENT_ORIGINAL_INIT)

    return restore


def install_httpx_asgi_transport_api_coverage_patch() -> Callable[[], None]:
    """Patch httpx ASGITransport construction so async mini-app tests are recorded."""
    import httpx

    existing_original = getattr(httpx.ASGITransport, _ASGI_TRANSPORT_ORIGINAL_INIT, None)
    if existing_original is not None:
        return lambda: None

    original_init = httpx.ASGITransport.__init__

    def patched_init(self, *args, **kwargs):
        app = kwargs.get("app")
        if app is None and args:
            app = args[0]
        if app is not None:
            mount_api_coverage_middleware(app)
        original_init(self, *args, **kwargs)

    setattr(httpx.ASGITransport, _ASGI_TRANSPORT_ORIGINAL_INIT, original_init)
    httpx.ASGITransport.__init__ = patched_init

    def restore() -> None:
        current_original = getattr(httpx.ASGITransport, _ASGI_TRANSPORT_ORIGINAL_INIT, None)
        if current_original is not None:
            httpx.ASGITransport.__init__ = current_original
            delattr(httpx.ASGITransport, _ASGI_TRANSPORT_ORIGINAL_INIT)

    return restore
