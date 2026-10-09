from __future__ import annotations

import io
import re
import sys
import types
from pathlib import Path

import pytest
from PIL import Image

from novelvideo import config
from novelvideo.model_gateway_settings import save_media_relay_config
from novelvideo.storage import media_relay


class FakeAuth:
    def __init__(self, ak: str, sk: str) -> None:
        self.ak = ak
        self.sk = sk


class FakeBucket:
    instances: list["FakeBucket"] = []

    def __init__(self, auth: FakeAuth, endpoint: str, bucket_name: str) -> None:
        self.auth = auth
        self.endpoint = endpoint
        self.bucket_name = bucket_name
        self.puts: list[tuple[str, bytes]] = []
        self.signs: list[tuple[str, str, int, bool]] = []
        FakeBucket.instances.append(self)

    def put_object(self, key: str, data: bytes):
        self.puts.append((key, data))
        return object()

    def sign_url(self, method: str, key: str, ttl: int, slash_safe: bool = True) -> str:
        self.signs.append((method, key, ttl, slash_safe))
        return f"https://relay.test/{key}?signed=1"


@pytest.fixture(autouse=True)
def fake_oss2(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("ST_EDITION", "ce")
    monkeypatch.delenv("ST_CONTROL_PLANE_DSN", raising=False)
    FakeBucket.instances.clear()
    fake_module = types.SimpleNamespace(Auth=FakeAuth, Bucket=FakeBucket)
    monkeypatch.setitem(sys.modules, "oss2", fake_module)


def test_aliyun_oss_relay_uploads_under_relay_prefix_and_signs_get_url() -> None:
    relay = media_relay.AliyunOSSRelay(
        endpoint="oss-cn-chengdu.aliyuncs.com",
        bucket_name="claymore-llm-relay",
        access_key_id="ak",
        access_key_secret="sk",
    )

    url = relay.upload_bytes(b"image-bytes", ext="PNG", ttl=1800)

    bucket = FakeBucket.instances[0]
    assert bucket.endpoint == "https://oss-cn-chengdu.aliyuncs.com"
    assert bucket.bucket_name == "claymore-llm-relay"
    key, data = bucket.puts[0]
    assert data == b"image-bytes"
    assert re.match(r"^relay/\d{8}/[0-9a-f]{32}\.png$", key)
    assert bucket.signs == [("GET", key, 1800, True)]
    assert url == f"https://relay.test/{key}?signed=1"


def test_aliyun_oss_relay_requires_credentials() -> None:
    with pytest.raises(media_relay.MediaRelayConfigError) as exc_info:
        media_relay.AliyunOSSRelay(
            endpoint="oss-cn-chengdu.aliyuncs.com",
            bucket_name="claymore-llm-relay",
            access_key_id="",
            access_key_secret="sk",
        )

    assert "OSS_RELAY_AK" in str(exc_info.value)


def test_media_relay_ttl_seconds_applies_floor_without_shortening_config(monkeypatch) -> None:
    monkeypatch.setattr(media_relay, "_default_media_relay_ttl_seconds", lambda: 1800)
    assert media_relay.media_relay_ttl_seconds(minimum=7200) == 7200

    monkeypatch.setattr(media_relay, "_default_media_relay_ttl_seconds", lambda: 10800)
    assert media_relay.media_relay_ttl_seconds(minimum=7200) == 10800


def test_get_media_relay_uses_saved_runtime_oss_config(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(config, "MEDIA_RELAY_PROVIDER", "aliyun_oss")
    monkeypatch.setattr(config, "MEDIA_RELAY_TTL_SECONDS", 1800)
    monkeypatch.setattr(config, "OSS_RELAY_ENDPOINT", "env.endpoint")
    monkeypatch.setattr(config, "OSS_RELAY_BUCKET", "env-bucket")
    monkeypatch.setattr(config, "OSS_RELAY_AK", "env-ak")
    monkeypatch.setattr(config, "OSS_RELAY_SK", "env-sk")
    save_media_relay_config(
        provider="aliyun_oss",
        ttl_seconds=900,
        endpoint="db.endpoint",
        bucket="db-bucket",
        access_key_id="db-ak",
        access_key_secret="db-sk",
    )

    relay = media_relay.get_media_relay()
    url = relay.upload_bytes(b"image-bytes", ext="png", ttl=900)

    bucket = FakeBucket.instances[0]
    assert bucket.endpoint == "https://db.endpoint"
    assert bucket.bucket_name == "db-bucket"
    assert bucket.auth.ak == "db-ak"
    assert bucket.auth.sk == "db-sk"
    assert bucket.signs[0][2] == 900
    assert url.startswith("https://relay.test/relay/")


def test_get_media_relay_uses_saved_runtime_cloudinary_config(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(config, "MEDIA_RELAY_PROVIDER", "aliyun_oss")
    monkeypatch.setattr(config, "MEDIA_RELAY_TTL_SECONDS", 1800)
    monkeypatch.setattr(config, "OSS_RELAY_ENDPOINT", "env.endpoint")
    monkeypatch.setattr(config, "OSS_RELAY_BUCKET", "env-bucket")
    monkeypatch.setattr(config, "OSS_RELAY_AK", "env-ak")
    monkeypatch.setattr(config, "OSS_RELAY_SK", "env-sk")
    monkeypatch.setattr(config, "CLOUDINARY_RELAY_CLOUD_NAME", "")
    monkeypatch.setattr(config, "CLOUDINARY_RELAY_API_KEY", "")
    monkeypatch.setattr(config, "CLOUDINARY_RELAY_API_SECRET", "")
    monkeypatch.setattr(config, "CLOUDINARY_RELAY_FOLDER", "relay")
    save_media_relay_config(
        provider="cloudinary",
        ttl_seconds=900,
        cloud_name="demo-cloud",
        cloudinary_api_key="api-key",
        cloudinary_api_secret="api-secret",
        cloudinary_folder="dramaclaw-relay",
    )

    relay = media_relay.get_media_relay()

    assert isinstance(relay, media_relay.CloudinaryRelay)
    assert relay._cloud_name == "demo-cloud"
    assert relay._api_key == "api-key"
    assert relay._api_secret == "api-secret"
    assert relay._folder == "dramaclaw-relay"


# ---------------------------------------------------------------------------
# [aipdd fork delta] AWS S3 档：零网络，桩掉 boto3.client 验 put_object + presign
# ---------------------------------------------------------------------------


class FakeS3Client:
    instances: list["FakeS3Client"] = []

    def __init__(self, service_name: str = "", **kwargs) -> None:
        self.service_name = service_name
        self.kwargs = kwargs
        self.puts: list[dict[str, object]] = []
        self.presigns: list[tuple[str, dict, int]] = []
        FakeS3Client.instances.append(self)

    def put_object(self, *, Bucket: str, Key: str, Body: bytes):
        self.puts.append({"Bucket": Bucket, "Key": Key, "Body": Body})
        return {}

    def generate_presigned_url(
        self, method: str, *, Params: dict, ExpiresIn: int
    ) -> str:
        self.presigns.append((method, Params, ExpiresIn))
        return (
            f"https://{Params['Bucket']}.s3.test/{Params['Key']}"
            f"?signed=1&expires={ExpiresIn}"
        )


@pytest.fixture
def fake_boto3(monkeypatch: pytest.MonkeyPatch):
    FakeS3Client.instances.clear()
    fake_module = types.SimpleNamespace(client=FakeS3Client)
    monkeypatch.setitem(sys.modules, "boto3", fake_module)
    return FakeS3Client


def test_aws_s3_relay_requires_credentials() -> None:
    with pytest.raises(media_relay.MediaRelayConfigError) as exc_info:
        media_relay.S3Relay(
            region="",
            bucket_name="user-relay",
            access_key_id="ak",
            access_key_secret="sk",
        )

    assert "AWS_S3_REGION" in str(exc_info.value)


def test_aws_s3_relay_uploads_under_relay_prefix_and_presigns_get(
    fake_boto3,
) -> None:
    relay = media_relay.S3Relay(
        region="us-east-1",
        bucket_name="user-relay",
        access_key_id="ak",
        access_key_secret="sk",
    )

    url = relay.upload_bytes(b"image-bytes", ext="PNG", ttl=1800)

    client = fake_boto3.instances[0]
    assert client.kwargs["region_name"] == "us-east-1"
    assert client.kwargs["aws_access_key_id"] == "ak"
    assert client.kwargs["aws_secret_access_key"] == "sk"
    # endpoint_url / session token 只在显式配置时才进 client kwargs。
    assert "endpoint_url" not in client.kwargs
    assert "aws_session_token" not in client.kwargs
    put = client.puts[0]
    assert put["Bucket"] == "user-relay"
    assert put["Body"] == b"image-bytes"
    key = str(put["Key"])
    assert re.match(r"^relay/\d{8}/[0-9a-f]{32}\.png$", key)
    method, params, expires = client.presigns[0]
    assert method == "get_object"
    assert params == {"Bucket": "user-relay", "Key": key}
    assert expires == 1800
    assert url == f"https://user-relay.s3.test/{key}?signed=1&expires=1800"


def test_aws_s3_relay_rejects_unsafe_object_key(fake_boto3) -> None:
    relay = media_relay.S3Relay(
        region="us-east-1",
        bucket_name="user-relay",
        access_key_id="ak",
        access_key_secret="sk",
    )

    with pytest.raises(media_relay.ServiceEgressDenied):
        relay.upload_bytes(b"x", object_key="relay/../../etc/passwd")


def test_get_media_relay_uses_saved_runtime_s3_config(
    monkeypatch, tmp_path, fake_boto3
) -> None:
    monkeypatch.setattr(config, "STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(config, "MEDIA_RELAY_PROVIDER", "aws_s3")
    monkeypatch.setattr(config, "MEDIA_RELAY_TTL_SECONDS", 1800)
    monkeypatch.setattr(config, "AWS_S3_REGION", "env-region")
    monkeypatch.setattr(config, "AWS_S3_BUCKET", "env-bucket")
    monkeypatch.setattr(config, "AWS_S3_ACCESS_KEY_ID", "env-ak")
    monkeypatch.setattr(config, "AWS_S3_SECRET_ACCESS_KEY", "env-sk")
    monkeypatch.setattr(config, "AWS_S3_ENDPOINT_URL", "")
    monkeypatch.setattr(config, "AWS_S3_SESSION_TOKEN", "")
    save_media_relay_config(
        provider="aws_s3",
        ttl_seconds=900,
        s3_region="db-region",
        s3_bucket="db-bucket",
        s3_access_key_id="db-ak",
        s3_access_key_secret="db-sk",
    )

    relay = media_relay.get_media_relay()
    assert isinstance(relay, media_relay.S3Relay)
    url = relay.upload_bytes(b"image-bytes", ext="png", ttl=900)

    client = fake_boto3.instances[0]
    # CE 下 db 为唯一真源：走的是 db-*，不是 env-*。
    assert client.kwargs["region_name"] == "db-region"
    assert client.kwargs["aws_access_key_id"] == "db-ak"
    assert client.puts[0]["Bucket"] == "db-bucket"
    assert client.presigns[0][2] == 900
    assert url.startswith("https://db-bucket.s3.test/relay/")


@pytest.mark.parametrize(
    ("ext", "resource_type", "expected_path", "expected_content_type"),
    [
        ("PNG", "image", "/image/upload", "image/png"),
        ("mp3", "video", "/video/upload", "audio/mpeg"),
        ("mp4", "video", "/video/upload", "video/mp4"),
        ("pdf", "raw", "/raw/upload", "application/pdf"),
    ],
)
def test_cloudinary_relay_uploads_bytes_with_basic_auth(
    monkeypatch: pytest.MonkeyPatch,
    ext: str,
    resource_type: str,
    expected_path: str,
    expected_content_type: str,
) -> None:
    calls: list[dict[str, object]] = []

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, str]:
            return {"secure_url": "https://res.cloudinary.com/demo/image/upload/abc.png"}

    class FakeClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url, *, data, files, auth):
            calls.append({"url": url, "data": data, "files": files, "auth": auth})
            return FakeResponse()

    import httpx

    monkeypatch.setattr(httpx, "Client", FakeClient)
    relay = media_relay.CloudinaryRelay(
        cloud_name="demo-cloud",
        api_key="api-key",
        api_secret="api-secret",
        folder="dramaclaw-relay",
    )

    url = relay.upload_bytes(
        b"image-bytes",
        ext=ext,
        ttl=900,
        resource_type=resource_type,
    )

    assert url == "https://res.cloudinary.com/demo/image/upload/abc.png"
    assert str(calls[0]["url"]).endswith(expected_path)
    assert calls[0]["data"] == {"folder": "dramaclaw-relay"}
    assert calls[0]["auth"] == ("api-key", "api-secret")
    filename, data, content_type = calls[0]["files"]["file"]
    assert filename.endswith(f".{ext.lower()}")
    assert data == b"image-bytes"
    assert content_type == expected_content_type


def test_cloudinary_relay_surfaces_safe_error_detail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    request = httpx.Request(
        "POST", "https://api.cloudinary.com/v1_1/demo-cloud/video/upload"
    )
    response = httpx.Response(
        400,
        request=request,
        json={"error": {"message": "Invalid video file"}},
    )

    class FakeClient:
        def __init__(self, *, timeout: float) -> None:
            self.timeout = timeout

        def __enter__(self) -> "FakeClient":
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, *args, **kwargs):
            return response

    monkeypatch.setattr(httpx, "Client", FakeClient)
    relay = media_relay.CloudinaryRelay(
        cloud_name="demo-cloud",
        api_key="api-key",
        api_secret="api-secret",
    )

    with pytest.raises(media_relay.MediaRelayConfigError) as exc_info:
        relay.upload_bytes(b"audio-bytes", ext="mp3", resource_type="video")

    assert str(exc_info.value) == (
        "Cloudinary media relay upload failed (HTTP 400): Invalid video file"
    )
    assert "api-secret" not in str(exc_info.value)


class CaptureRelay:
    def __init__(self) -> None:
        self.uploaded_bytes: list[tuple[bytes, str, int]] = []
        self.uploaded_files: list[tuple[Path, int]] = []

    def upload_bytes(
        self,
        data: bytes,
        *,
        ext: str = "png",
        ttl: int = 1800,
        resource_type: str = "image",
    ) -> str:
        self.uploaded_bytes.append((data, ext, ttl))
        return f"https://relay.test/bytes.{ext}?ttl={ttl}"

    def upload_file(self, path: str | Path, *, ttl: int = 1800) -> str:
        file_path = Path(path)
        self.uploaded_files.append((file_path, ttl))
        return f"https://relay.test/{file_path.name}?ttl={ttl}"


def test_ensure_image_url_returns_remote_url_without_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    relay = CaptureRelay()
    monkeypatch.setattr(media_relay, "get_media_relay", lambda: relay)

    url = media_relay.ensure_image_url("https://example.test/a.png", ttl=60)

    assert url == "https://example.test/a.png"
    assert relay.uploaded_bytes == []
    assert relay.uploaded_files == []


def test_ensure_image_url_uploads_data_url(monkeypatch: pytest.MonkeyPatch) -> None:
    relay = CaptureRelay()
    monkeypatch.setattr(media_relay, "get_media_relay", lambda: relay)

    url = media_relay.ensure_image_url("data:image/jpeg;base64,aGVsbG8=", ttl=60)

    assert url == "https://relay.test/bytes.jpg?ttl=60"
    assert relay.uploaded_bytes == [(b"hello", "jpg", 60)]
    assert relay.uploaded_files == []


def test_upload_image_bytes_defaults_to_raw_upload(monkeypatch: pytest.MonkeyPatch) -> None:
    relay = CaptureRelay()
    monkeypatch.setattr(media_relay, "get_media_relay", lambda: relay)

    url = media_relay.upload_image_bytes(b"raw-png-bytes", ext="png", ttl=60)

    assert url == "https://relay.test/bytes.png?ttl=60"
    assert relay.uploaded_bytes == [(b"raw-png-bytes", "png", 60)]


def test_upload_image_bytes_can_normalize_ai_reference_jpeg(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay = CaptureRelay()
    monkeypatch.setattr(media_relay, "get_media_relay", lambda: relay)
    source = Image.new("RGBA", (32, 16), (255, 0, 0, 255))
    source_buf = io.BytesIO()
    source.save(source_buf, format="PNG")

    url = media_relay.upload_image_bytes(
        source_buf.getvalue(),
        ext="png",
        ttl=60,
        image_transform=media_relay.IMAGE_TRANSFORM_AI_REFERENCE_JPEG,
    )

    assert url == "https://relay.test/bytes.jpg?ttl=60"
    assert len(relay.uploaded_bytes) == 1
    uploaded_bytes, ext, ttl = relay.uploaded_bytes[0]
    assert ext == "jpg"
    assert ttl == 60
    uploaded_image = Image.open(io.BytesIO(uploaded_bytes))
    assert uploaded_image.format == "JPEG"
    assert uploaded_image.mode == "RGB"
    assert uploaded_image.size == (32, 16)


def test_ensure_image_url_uploads_local_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    relay = CaptureRelay()
    monkeypatch.setattr(media_relay, "get_media_relay", lambda: relay)
    image_path = tmp_path / "portrait.png"
    image_path.write_bytes(b"png-bytes")

    url = media_relay.ensure_image_url(image_path, ttl=60)

    assert url == "https://relay.test/portrait.png?ttl=60"
    assert relay.uploaded_bytes == []
    assert relay.uploaded_files == [(image_path, 60)]


def test_local_relay_inlines_reference_images_only(monkeypatch) -> None:
    """[aipdd fork delta req44] 本地存储档只内联参考**图片**。

    历史：ap-minimax-h3「全能参考」曾把图/音/视频统一内联为 data URL（更早的逻辑甚
    至对非 image 直接抛「切换到 OSS/Cloudinary」）。实测上游会受理带 ``video_url`` data
    URL 的请求却不去拉素材，产出的是一段与原视频无关的片子：req42 加了提交前闸门，
    req44 把这类输入改走 AIPDD 数字资产云转公网直链，解析不到凭据就报错，不再静默内联。
    """
    monkeypatch.setattr(
        media_relay, "resolve_aipdd_cloud_target", lambda **kwargs: ("", "")
    )
    relay = media_relay.LocalImageRelay()

    image = relay.upload_bytes(b"PNG", ext="png", resource_type="image")
    assert image.startswith("data:image/png;base64,")

    # 音频在生成器里也可能被标成 resource_type=video：口径一样，都不许静默内联。
    with pytest.raises(media_relay.MediaRelayConfigError, match="AIPDD API Key"):
        relay.upload_bytes(b"AUDIO", ext="mp3", resource_type="video")
    with pytest.raises(media_relay.MediaRelayConfigError, match="AIPDD API Key"):
        relay.upload_bytes(b"VIDEO", ext="mp4", resource_type="video")


def test_get_media_relay_local_wires_aipdd_delegate(monkeypatch) -> None:
    # [aipdd fork delta req44] 本地档仍把图片内联，但要看得到 AIPDD 中转凭据：
    # get_media_relay 必须把设置库里的 Key/地址递给委派用的数字资产云。
    monkeypatch.setattr(
        media_relay,
        "_effective_media_relay_config",
        lambda: types.SimpleNamespace(
            provider="local",
            aipdd_base_url="https://gw.example/v1",
            aipdd_api_key="sk-aipdd",
            aipdd_prefix="assets/x",
            aipdd_register_asset=False,
        ),
    )

    relay = media_relay.get_media_relay()

    assert isinstance(relay, media_relay.LocalImageRelay)
    assert relay.upload_bytes(b"x", ext="png").startswith("data:image/png;base64,")
    delegate = relay._aipdd()
    assert isinstance(delegate, media_relay.AipddCloudRelay)
    # 上传口只挂在站点根（/v1/oss/upload 实测 404）。
    assert delegate._origin == "https://gw.example"
    assert delegate._api_key == "sk-aipdd"
    assert delegate._prefix == "assets/x"
