"""A4: the backend must add security response headers only when it self-serves the SPA.

Without nginx there is no reverse proxy to inject ``X-Frame-Options``/``nosniff``/
COOP/CORP/Referrer/Permissions, so the backend serving ``AIPDD_DRAMA_FRONTEND_DIST``
has to supply them itself. When the variable is unset (Docker/EE, nginx already
handles this) the app must be byte-for-byte unchanged and add none of these headers.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from novelvideo.api.app import _SPA_SECURITY_HEADERS, create_app


def test_self_serve_mode_adds_security_headers(monkeypatch, tmp_path) -> None:
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><body>spa</body></html>", "utf-8")
    monkeypatch.setenv("AIPDD_DRAMA_FRONTEND_DIST", str(dist))

    client = TestClient(create_app(), raise_server_exceptions=False)
    response = client.get("/healthz")

    assert response.status_code == 200
    for header, value in _SPA_SECURITY_HEADERS.items():
        assert response.headers[header] == value


def test_self_serve_headers_apply_to_spa_asset_responses(monkeypatch, tmp_path) -> None:
    # The mount itself (a real served file, not the API) must also carry the guards.
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><body>spa</body></html>", "utf-8")
    monkeypatch.setenv("AIPDD_DRAMA_FRONTEND_DIST", str(dist))

    client = TestClient(create_app(), raise_server_exceptions=False)
    response = client.get("/index.html")

    assert response.status_code == 200
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


def test_managed_mode_adds_no_security_headers(monkeypatch) -> None:
    monkeypatch.delenv("AIPDD_DRAMA_FRONTEND_DIST", raising=False)
    monkeypatch.delenv("DRAMACLAW_FRONTEND_DIST", raising=False)

    client = TestClient(create_app(), raise_server_exceptions=False)
    response = client.get("/healthz")

    assert response.status_code == 200
    for header in _SPA_SECURITY_HEADERS:
        assert header not in response.headers
