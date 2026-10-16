# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 换脸模型下载器单测：地址拼装、大小校验、断点续传与失败清理。

不外连——用假的 urlopen 喂字节，验证：期望大小不符时删 .part 返回 False；相符时落盘并就绪；
默认走 hf-mirror、可覆盖端点。对齐上游 model_downloader 的「大小校验 + certifi」口径。
"""

from __future__ import annotations

import os
from pathlib import Path

from novelvideo.faceswap import model_download


class _FakeResp:
    def __init__(self, chunks: list[bytes], content_length: int):
        self._it = iter(chunks)
        self.headers = {"Content-Length": str(content_length)}

    def read(self, _n: int) -> bytes:
        return next(self._it, b"")

    def __enter__(self):
        return self

    def __exit__(self, *a):  # noqa: ANN002, ANN003
        return False


def test_resolve_url_defaults_to_mirror():
    url = model_download.resolve_url("inswapper_128_fp16.onnx")
    assert url.startswith("https://hf-mirror.com/hacksider/deep-live-cam/resolve/main/")
    assert url.endswith("inswapper_128_fp16.onnx")


def test_resolve_url_honors_huggingface_endpoint():
    url = model_download.resolve_url(
        "models/buffalo_l/det_10g.onnx", hf_endpoint="https://huggingface.co"
    )
    assert "huggingface.co/hacksider/deep-live-cam/resolve/main/" in url


def test_buffalo_l_repo_path_is_double_nested():
    # 回归：上游仓库里 buffalo_l 套了两层（buffalo_l/buffalo_l/*.onnx），照落盘名直取会 404，
    # 「下载并部署」会在第一个检测模型上失败。
    assert (
        model_download.remote_path("models/buffalo_l/det_10g.onnx")
        == "buffalo_l/buffalo_l/det_10g.onnx"
    )
    url = model_download.resolve_url("models/buffalo_l/1k3d68.onnx")
    assert url.endswith("/buffalo_l/buffalo_l/1k3d68.onnx")
    # 边车传 root=<models_dir>，insightface 只认 <root>/models/<pack>，落错一层它就自己重下。
    assert model_download.local_path("models/buffalo_l/1k3d68.onnx", "/m").replace(
        "\\", "/"
    ).endswith("/m/models/buffalo_l/1k3d68.onnx")
    for name in model_download.REQUIRED_MODELS:
        if "buffalo_l" in name:
            assert name.replace("\\", "/").startswith("models/buffalo_l/")
        assert model_download.expected_size(name)


def test_expected_size_known_and_unknown():
    assert model_download.expected_size("inswapper_128_fp16.onnx") == 277680638
    assert model_download.expected_size("not-a-model.onnx") is None


def test_is_present_size_gate(tmp_path):
    md = str(tmp_path)
    name = "models/buffalo_l/det_10g.onnx"
    os.makedirs(os.path.dirname(model_download.local_path(name, md)), exist_ok=True)
    path = model_download.local_path(name, md)
    # 空文件 → 未就绪
    Path(path).touch()
    assert model_download.is_present(name, md) is False
    # 大小不符 → 未就绪（有期望大小时据此判完整性）
    Path(path).write_bytes(b"partial")
    assert model_download.is_present(name, md) is False


def test_is_present_unknown_size_nonempty_is_ready(tmp_path):
    md = str(tmp_path)
    name = "some_extra.onnx"  # 不在 MODEL_SIZES，size None
    (tmp_path / name).write_bytes(b"x")
    assert model_download.is_present(name, md) is True


def test_download_one_wrong_size_removes_partial_and_fails(tmp_path, monkeypatch):
    md = str(tmp_path)
    name = "tiny.onnx"
    monkeypatch.setattr(model_download, "MODEL_SIZES", {name: 10})  # 期望 10 字节

    def _fake_urlopen(req, *a, **k):  # noqa: ANN001, ANN002, ANN003
        return _FakeResp([b"12345"], content_length=5)  # 只给 5 字节

    monkeypatch.setattr(model_download.urllib.request, "urlopen", _fake_urlopen)
    ok = model_download.download_one(name, md)
    assert ok is False
    assert not os.path.exists(model_download.local_path(name, md))  # 目标未生成
    assert not os.path.exists(model_download.local_path(name, md) + ".part")  # 残片已删


def test_download_one_correct_size_lands_file(tmp_path, monkeypatch):
    md = str(tmp_path)
    name = "tiny.onnx"
    monkeypatch.setattr(model_download, "MODEL_SIZES", {name: 5})

    def _fake_urlopen(req, *a, **k):  # noqa: ANN001, ANN002, ANN003
        return _FakeResp([b"12345"], content_length=5)

    monkeypatch.setattr(model_download.urllib.request, "urlopen", _fake_urlopen)
    ok = model_download.download_one(name, md)
    assert ok is True
    assert os.path.isfile(model_download.local_path(name, md))


def test_download_required_all_present_short_circuits(tmp_path, monkeypatch):
    md = str(tmp_path)
    # 伪造全部必需模型就位（清空期望大小，只看非空）。
    monkeypatch.setattr(model_download, "MODEL_SIZES", {})
    for name in model_download.REQUIRED_MODELS:
        p = model_download.local_path(name, md)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        Path(p).write_bytes(b"x")
    ok, failed = model_download.download_required(md)
    assert ok is True
    assert failed is None
