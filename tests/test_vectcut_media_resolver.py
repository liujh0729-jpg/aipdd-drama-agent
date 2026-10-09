"""[aipdd fork delta] Unit tests for the VectCut media resolver.

``services/vectcut/aipdd/media_resolver.py`` is the pure-Python path translation
behind ``/aipdd/resolve_media``: it maps Dramaclaw ``/static/projects/...``
references (plus plain paths and public URLs) to files on the VectCut host.
These tests pin that contract without starting the Flask service.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from urllib.parse import quote

import pytest


def _load_resolver():
    path = (
        Path(__file__).resolve().parents[1]
        / "services"
        / "vectcut"
        / "aipdd"
        / "media_resolver.py"
    )
    spec = importlib.util.spec_from_file_location("test_vectcut_media_resolver_mod", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclass 在解析注解时需要经 cls.__module__ 找到已注册的模块。
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def media_tree(tmp_path: Path) -> Path:
    """One media root laid out as {root}/{username}/{project}/{file_path}."""
    target = tmp_path / "output" / "local" / "test" / "frames" / "ep001"
    target.mkdir(parents=True)
    (target / "beat_01.png").write_bytes(b"png")
    return tmp_path / "output"


def test_empty_reference_is_invalid():
    resolver = _load_resolver()
    result = resolver.resolve_media_reference("  ", roots=[])
    assert result.ok is False
    assert result.code == "invalid"


def test_public_urls_pass_through():
    """http(s) 直链由上游 ffmpeg 自行下载，解析层不碰。"""
    resolver = _load_resolver()
    for url in ("http://cdn.example.com/a.png", "https://cdn.example.com/a.png?v=1"):
        result = resolver.resolve_media_reference(url, roots=[])
        assert result.ok is True
        assert result.mode == "passthrough"
        assert result.path == url


def test_existing_local_path_passes_through(tmp_path):
    """已存在的本地文件路径原样返回（归一为绝对路径）。"""
    resolver = _load_resolver()
    media = tmp_path / "clip.mp4"
    media.write_bytes(b"mp4")
    result = resolver.resolve_media_reference(str(media), roots=[])
    assert result.ok is True
    assert result.mode == "passthrough"
    assert result.path == str(media.resolve())


def test_static_url_resolves_via_username_scan(media_tree):
    """token 命中 {root}/*/{token}/{file_path}（'*' 为用户名目录）。"""
    resolver = _load_resolver()
    result = resolver.resolve_media_reference(
        "/static/projects/test/frames/ep001/beat_01.png", roots=[media_tree]
    )
    assert result.ok is True
    assert result.mode == "resolved"
    assert result.path.endswith("local/test/frames/ep001/beat_01.png")


def test_project_dir_param_resolves_disk_name_hint(media_tree):
    """URL 里是项目 id、磁盘目录是项目名时，project_dir 提示优先命中。"""
    resolver = _load_resolver()
    result = resolver.resolve_media_reference(
        "/static/projects/abc123/frames/ep001/beat_01.png",
        roots=[media_tree],
        project_dir="test",
    )
    assert result.ok is True
    assert result.path.endswith("local/test/frames/ep001/beat_01.png")


def test_wildcard_fallback_when_project_token_misses(media_tree):
    """URL 项目 id 在磁盘上不存在时回退到 root/*/*/{file} 通配搜索。"""
    resolver = _load_resolver()
    result = resolver.resolve_media_reference(
        "/static/projects/unknown-id/frames/ep001/beat_01.png", roots=[media_tree]
    )
    assert result.ok is True
    assert result.mode == "resolved"
    assert result.path.endswith("local/test/frames/ep001/beat_01.png")


def test_ambiguous_candidates_are_rejected(media_tree):
    """同名文件命中多处时报 ambiguous，绝不猜测。"""
    other = media_tree / "other" / "test" / "frames" / "ep001"
    other.mkdir(parents=True)
    (other / "beat_01.png").write_bytes(b"png")
    resolver = _load_resolver()
    result = resolver.resolve_media_reference(
        "/static/projects/test/frames/ep001/beat_01.png", roots=[media_tree]
    )
    assert result.ok is False
    assert result.code == "ambiguous"
    assert "project_dir" in result.error


def test_missing_media_reports_roots(media_tree):
    resolver = _load_resolver()
    result = resolver.resolve_media_reference(
        "/static/projects/test/frames/ep001/nope.png", roots=[media_tree]
    )
    assert result.ok is False
    assert result.code == "not_found"
    assert str(media_tree) in result.error


def test_path_traversal_is_rejected(media_tree):
    """file_path 含 ..（含 URL 编码形式）一律拒绝。"""
    resolver = _load_resolver()
    for reference in (
        "/static/projects/test/../../etc/passwd",
        "/static/projects/test/%2e%2e/secret.txt",
    ):
        result = resolver.resolve_media_reference(reference, roots=[media_tree])
        assert result.ok is False
        assert result.code == "invalid"
        assert "traversal" in result.error


def test_non_static_reference_is_invalid(media_tree):
    resolver = _load_resolver()
    result = resolver.resolve_media_reference("/api/projects/p/a.png", roots=[media_tree])
    assert result.ok is False
    assert result.code == "invalid"


def test_query_string_and_percent_encoding(media_tree):
    """URL query 被剥离、百分号编码解码后仍能命中真实文件。"""
    resolver = _load_resolver()
    target = media_tree / "local" / "test" / "frames" / "ep001"
    (target / "分镜 01.png").write_bytes(b"png")
    reference = "/static/projects/test/frames/ep001/" + quote("分镜 01.png") + "?v=1"
    result = resolver.resolve_media_reference(reference, roots=[media_tree])
    assert result.ok is True
    assert result.path.endswith("local/test/frames/ep001/分镜 01.png")

def _load_server_module():
    """按文件路径加载 aipdd/server.py（模块顶层不依赖 Flask，可安全导入）。"""
    path = (
        Path(__file__).resolve().parents[1]
        / "services"
        / "vectcut"
        / "aipdd"
        / "server.py"
    )
    spec = importlib.util.spec_from_file_location("test_vectcut_server_mod", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_internal_token_guard_default_off_is_backward_compatible() -> None:
    server = _load_server_module()
    f = server._internal_token_required
    # expected 为空（默认）：任何入口都放行。
    assert f("/aipdd/drafts", None, "") is False
    assert f("/aipdd/save_draft", "", "") is False


def test_internal_token_guard_rejects_missing_or_wrong_header() -> None:
    server = _load_server_module()
    f = server._internal_token_required
    # 设了密钥：/aipdd/* 缺头或错头都拒。
    assert f("/aipdd/drafts", None, "s3cret") is True
    assert f("/aipdd/drafts", "", "s3cret") is True
    assert f("/aipdd/drafts", "wrong", "s3cret") is True
    # 匹配则放行。
    assert f("/aipdd/drafts", "s3cret", "s3cret") is False


def test_internal_token_guard_exempts_health_and_non_aipdd() -> None:
    server = _load_server_module()
    f = server._internal_token_required
    # 健康检查与非 /aipdd/ 路径即便没带头也不拦。
    assert f("/aipdd/health", None, "s3cret") is False
    assert f("/other", None, "s3cret") is False
    assert f("/static/x.png", None, "s3cret") is False
