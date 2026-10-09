from __future__ import annotations

from starlette.requests import Request

from novelvideo.api.app import (
    MAX_REQUEST_BODY_BYTES,
    MAX_UPLOAD_REQUEST_BODY_BYTES,
    _rejects_undeclared_chunked_body,
    _request_body_limit,
)


def _request(
    path: str,
    *,
    method: str = "POST",
    headers: list[tuple[bytes, bytes]] | None = None,
) -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": headers or [],
        }
    )


def _multipart_request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "scheme": "http",
            "server": ("testserver", 80),
            "client": ("testclient", 50000),
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "headers": [(b"content-type", b"multipart/form-data; boundary=test")],
        }
    )


def test_generic_freezone_upload_uses_upload_body_limit() -> None:
    request = _multipart_request("/api/v1/projects/project-1/freezone/upload")

    assert _request_body_limit(request) == MAX_UPLOAD_REQUEST_BODY_BYTES


def test_reference_file_upload_uses_upload_body_limit() -> None:
    request = _multipart_request(
        "/api/v1/projects/project-1/freezone/reference-file-upload"
    )

    assert _request_body_limit(request) == MAX_UPLOAD_REQUEST_BODY_BYTES


def test_non_upload_request_keeps_default_body_limit() -> None:
    request = _multipart_request("/api/v1/projects/project-1/freezone/gen")

    assert _request_body_limit(request) == MAX_REQUEST_BODY_BYTES


def test_chunked_small_json_body_is_rejected() -> None:
    # A body-method JSON request with chunked transfer-encoding and no
    # Content-Length must be rejected (411 at the middleware) to close the
    # unbounded-buffer DoS on the small-JSON class.
    request = _request(
        "/api/v1/projects/project-1/freezone/gen",
        method="POST",
        headers=[
            (b"content-type", b"application/json"),
            (b"transfer-encoding", b"chunked"),
        ],
    )

    assert _rejects_undeclared_chunked_body(request) is True


def test_content_length_json_body_is_allowed() -> None:
    request = _request(
        "/api/v1/projects/project-1/freezone/gen",
        method="POST",
        headers=[
            (b"content-type", b"application/json"),
            (b"content-length", b"128"),
        ],
    )

    assert _rejects_undeclared_chunked_body(request) is False


def test_get_with_chunked_is_allowed() -> None:
    request = _request(
        "/api/v1/projects/project-1/freezone/gen",
        method="GET",
        headers=[(b"transfer-encoding", b"chunked")],
    )

    assert _rejects_undeclared_chunked_body(request) is False


def test_upload_multipart_chunked_is_allowed() -> None:
    # Streaming upload class has its own disk/stream limiter; the small-JSON
    # chunked guard must not reject it.
    request = _request(
        "/api/v1/projects/project-1/freezone/upload",
        method="POST",
        headers=[
            (b"content-type", b"multipart/form-data; boundary=test"),
            (b"transfer-encoding", b"chunked"),
        ],
    )

    assert _rejects_undeclared_chunked_body(request) is False


def test_openai_proxy_chunked_is_allowed() -> None:
    # The gateway passthrough streams the body, so chunked must pass.
    request = _request(
        "/api/v1/openai/chat/completions",
        method="POST",
        headers=[
            (b"content-type", b"application/json"),
            (b"transfer-encoding", b"chunked"),
        ],
    )

    assert _rejects_undeclared_chunked_body(request) is False


def test_middleware_answers_411_for_chunked_json() -> None:
    """End-to-end proof that the guard fires before routing/auth.

    A body-method request with chunked transfer-encoding and no
    Content-Length must short-circuit to 411 at the middleware, never reaching
    a handler that would buffer an unbounded body into memory.
    """
    from fastapi.testclient import TestClient

    from novelvideo.api.app import create_app

    def _body_iter():
        yield b'{"x": 1}'

    client = TestClient(create_app(), raise_server_exceptions=False)
    response = client.post(
        "/api/v1/projects/project-1/freezone/gen", content=_body_iter()
    )

    assert response.status_code == 411
    assert response.json()["detail"]["code"] == "content_length_required"
