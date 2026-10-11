"""Transient media relay for LLM-visible image URLs."""

from __future__ import annotations

import uuid
import base64
import hashlib
import io
import inspect
import logging
import re
import mimetypes
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

AI_REFERENCE_JPEG_QUALITY = 95
IMAGE_TRANSFORM_AI_REFERENCE_JPEG = "ai_reference_jpeg"


class ServiceEgressDenied(RuntimeError):
    """Stable denial for an invalid storage service boundary."""

    code = "ORG_SERVICE_EGRESS_DENIED"

    def __init__(self, reason: str | None = None) -> None:
        super().__init__("organization service egress is denied")
        # Which boundary refused, as a fixed slug — never interpolated request
        # data. The denial is raised from five places that need five different
        # fixes (a missing port registration is not a malformed object key),
        # and the caller has no other way to tell them apart.
        self.reason = reason


class ServiceOperationNotReplayable(RuntimeError):
    """Raised when a durable service operation was already claimed."""

    code = "ORG_SERVICE_OPERATION_NOT_REPLAYABLE"


class ServiceInvocationFailed(RuntimeError):
    """Secret-free failure after a claimed storage invocation."""

    code = "ORG_SERVICE_INVOCATION_FAILED"

    def __init__(self) -> None:
        super().__init__("service operation failed")


@dataclass(frozen=True, slots=True)
class StorageRelayIdentity:
    """Secret-free identity binding one relay service to one tenant project."""

    credential_id: str
    credential_version: int
    organization_id: str
    project_id: str
    allowed_source_hosts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name in ("credential_id", "organization_id", "project_id"):
            if type(getattr(self, name)) is not str or not getattr(self, name).strip():
                raise ValueError(f"{name} is required")
        if type(self.credential_version) is not int or self.credential_version < 1:
            raise ValueError("credential_version must be positive")
        if type(self.allowed_source_hosts) is not tuple or any(
            type(host) is not str or not host.strip() or "/" in host
            for host in self.allowed_source_hosts
        ):
            raise ValueError("allowed_source_hosts must contain host names")


def validate_tenant_source_url(
    value: str,
    *,
    context,
    identity: StorageRelayIdentity,
) -> str:
    """Validate a remote source without fetching it or exposing its URL."""

    from novelvideo.egress_context import TrustedEgressContext

    parsed = urlparse(str(value or "").strip())
    try:
        port = parsed.port
    except ValueError:
        raise ServiceEgressDenied("source-url") from None
    allowed_hosts = (
        {host.strip().lower() for host in identity.allowed_source_hosts}
        if type(identity) is StorageRelayIdentity
        else set()
    )
    if (
        type(context) is not TrustedEgressContext
        or not context.is_organization
        or type(identity) is not StorageRelayIdentity
        or identity.organization_id != context.billing_principal.id
        or identity.project_id != context.project_id
        or parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.hostname.lower() not in allowed_hosts
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
    ):
        raise ServiceEgressDenied("source-url")
    return str(value).strip()


class MediaRelayConfigError(RuntimeError):
    """Raised when the media relay is not configured for URL input."""


class AliyunOSSRelay:
    """Upload transient bytes to Aliyun OSS and return a short-lived signed URL."""

    def __init__(
        self,
        *,
        endpoint: str,
        bucket_name: str,
        access_key_id: str,
        access_key_secret: str,
    ) -> None:
        missing = [
            name
            for name, value in {
                "OSS_RELAY_ENDPOINT": endpoint,
                "OSS_RELAY_BUCKET": bucket_name,
                "OSS_RELAY_AK": access_key_id,
                "OSS_RELAY_SK": access_key_secret,
            }.items()
            if not str(value or "").strip()
        ]
        if missing:
            raise MediaRelayConfigError(
                "OSS media relay config missing: " + ", ".join(missing)
            )

        try:
            import oss2
        except ImportError as exc:
            raise MediaRelayConfigError(
                "oss2 is not installed; install project dependencies before using media relay"
            ) from exc

        self._bucket = oss2.Bucket(
            oss2.Auth(access_key_id, access_key_secret),
            f"https://{endpoint.strip()}",
            bucket_name.strip(),
        )

    def upload_bytes(
        self,
        data: bytes,
        *,
        ext: str = "png",
        ttl: int = 1800,
        resource_type: str = "image",
        object_key: str | None = None,
    ) -> str:
        if not data:
            raise ValueError("cannot relay empty media bytes")
        ext = _normalize_ext(ext)
        key = object_key or (
            f"relay/{datetime.now(timezone.utc):%Y%m%d}/{uuid.uuid4().hex}.{ext}"
        )
        if not _is_safe_object_key(key):
            raise ServiceEgressDenied("object-key")
        self._bucket.put_object(key, data)
        return self._bucket.sign_url("GET", key, int(ttl), slash_safe=True)

    def upload_file(self, path: str | Path, *, ttl: int = 1800) -> str:
        file_path = Path(path)
        return self.upload_bytes(
            file_path.read_bytes(),
            ext=file_path.suffix.lstrip(".") or "png",
            ttl=ttl,
        )


class S3Relay:
    """[aipdd fork delta] Upload transient bytes to AWS S3 and return a short-lived presigned URL.

    与 :class:`AliyunOSSRelay` 同构：凭据齐备才构造，懒导入 boto3（只在选 aws_s3 档时才
    需要），``upload_bytes`` 先 ``put_object`` 再 ``generate_presigned_url`` 拿限时直链。
    区域无签名不可用，视作必填；``endpoint_url`` 仅自建 S3 兼容网关才显式传。
    """

    def __init__(
        self,
        *,
        region: str,
        bucket_name: str,
        access_key_id: str,
        access_key_secret: str,
        endpoint_url: str = "",
        session_token: str = "",
    ) -> None:
        missing = [
            name
            for name, value in {
                "AWS_S3_REGION": region,
                "AWS_S3_BUCKET": bucket_name,
                "AWS_S3_ACCESS_KEY_ID": access_key_id,
                "AWS_S3_SECRET_ACCESS_KEY": access_key_secret,
            }.items()
            if not str(value or "").strip()
        ]
        if missing:
            raise MediaRelayConfigError(
                "S3 media relay config missing: " + ", ".join(missing)
            )

        try:
            import boto3
        except ImportError as exc:
            raise MediaRelayConfigError(
                "boto3 is not installed; install project dependencies before using S3 media relay"
            ) from exc

        client_kwargs: dict[str, str] = {
            "region_name": region.strip(),
            "aws_access_key_id": access_key_id.strip(),
            "aws_secret_access_key": access_key_secret.strip(),
        }
        if str(endpoint_url or "").strip():
            client_kwargs["endpoint_url"] = endpoint_url.strip()
        if str(session_token or "").strip():
            client_kwargs["aws_session_token"] = session_token.strip()
        self._region = region.strip()
        self._bucket_name = bucket_name.strip()
        self._client = boto3.client("s3", **client_kwargs)

    def upload_bytes(
        self,
        data: bytes,
        *,
        ext: str = "png",
        ttl: int = 1800,
        resource_type: str = "image",
        object_key: str | None = None,
    ) -> str:
        if not data:
            raise ValueError("cannot relay empty media bytes")
        ext = _normalize_ext(ext)
        key = object_key or (
            f"relay/{datetime.now(timezone.utc):%Y%m%d}/{uuid.uuid4().hex}.{ext}"
        )
        if not _is_safe_object_key(key):
            raise ServiceEgressDenied("object-key")
        self._client.put_object(Bucket=self._bucket_name, Key=key, Body=data)
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket_name, "Key": key},
            ExpiresIn=int(ttl),
        )

    def upload_file(self, path: str | Path, *, ttl: int = 1800) -> str:
        file_path = Path(path)
        return self.upload_bytes(
            file_path.read_bytes(),
            ext=file_path.suffix.lstrip(".") or "png",
            ttl=ttl,
        )


class CloudinaryRelay:
    """Upload transient bytes to Cloudinary and return its secure delivery URL."""

    def __init__(
        self,
        *,
        cloud_name: str,
        api_key: str,
        api_secret: str,
        folder: str = "",
    ) -> None:
        missing = [
            name
            for name, value in {
                "CLOUDINARY_RELAY_CLOUD_NAME": cloud_name,
                "CLOUDINARY_RELAY_API_KEY": api_key,
                "CLOUDINARY_RELAY_API_SECRET": api_secret,
            }.items()
            if not str(value or "").strip()
        ]
        if missing:
            raise MediaRelayConfigError(
                "Cloudinary media relay config missing: " + ", ".join(missing)
            )

        self._cloud_name = cloud_name.strip()
        self._api_key = api_key.strip()
        self._api_secret = api_secret.strip()
        self._folder = str(folder or "").strip().strip("/")

    def upload_bytes(
        self,
        data: bytes,
        *,
        ext: str = "png",
        ttl: int = 1800,
        resource_type: str = "image",
        object_key: str | None = None,
    ) -> str:
        if not data:
            raise ValueError("cannot relay empty media bytes")

        import httpx

        ext = _normalize_ext(ext)
        resource_type = str(resource_type or "image").strip().lower()
        if resource_type not in {"image", "video", "raw"}:
            raise ValueError(f"unsupported Cloudinary resource type: {resource_type}")
        filename = f"{uuid.uuid4().hex}.{ext}"
        content_type = mimetypes.types_map.get(f".{ext}", "application/octet-stream")
        payload = {"folder": self._folder} if self._folder else {}
        if object_key is not None:
            if not _is_safe_object_key(object_key):
                raise ServiceEgressDenied("object-key")
            payload["public_id"] = object_key.rsplit(".", 1)[0]
        url = (
            f"https://api.cloudinary.com/v1_1/{self._cloud_name}/"
            f"{resource_type}/upload"
        )
        try:
            with httpx.Client(timeout=60.0) as client:
                response = client.post(
                    url,
                    data=payload,
                    files={"file": (filename, data, content_type)},
                    auth=(self._api_key, self._api_secret),
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            detail = _cloudinary_error_detail(exc.response)
            suffix = f": {detail}" if detail else ""
            raise MediaRelayConfigError(
                "Cloudinary media relay upload failed "
                f"(HTTP {exc.response.status_code}){suffix}"
            ) from exc
        except httpx.HTTPError as exc:
            raise MediaRelayConfigError(
                f"Cloudinary media relay upload failed: {exc}"
            ) from exc

        result = response.json()
        secure_url = str(result.get("secure_url") or result.get("url") or "").strip()
        if not secure_url:
            raise MediaRelayConfigError("Cloudinary media relay upload returned no URL")
        return secure_url

    def upload_file(self, path: str | Path, *, ttl: int = 1800) -> str:
        file_path = Path(path)
        return self.upload_bytes(
            file_path.read_bytes(),
            ext=file_path.suffix.lstrip(".") or "png",
            ttl=ttl,
        )


# ---------------------------------------------------------------------------
# [aipdd fork delta req44] AIPDD 数字资产云：渠道 Key 即上传凭据
# ---------------------------------------------------------------------------

AIPDD_CLOUD_DEFAULT_BUCKET = "aipdd"
AIPDD_CLOUD_DEFAULT_PREFIX = "assets/dramaclaw"
AIPDD_CLOUD_UPLOAD_PATH = "/oss/upload"
AIPDD_CLOUD_ASSET_PATH = "/digital_asset"


def _site_origin(base_url: str) -> str:
    """把渠道 baseUrl 还原成 ``scheme://host``（上传口只挂在站点根）。

    实测 ``/v1/oss/upload`` 是 404，``/v1`` 之类的前缀必须先剥掉再用。
    """
    raw = str(base_url or "").strip()
    if not raw:
        return ""
    parsed = urlparse(raw if "://" in raw else f"https://{raw}")
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme}://{parsed.netloc}"


def resolve_aipdd_cloud_target(
    *,
    saved_base_url: str = "",
    saved_api_key: str = "",
) -> tuple[str, str]:
    """解析数字资产中转的 ``(站点根, API Key)``，解析不到回空串。

    优先级：设置页显式填写 > ``AIPDD_CLOUD_BASE_URL`` / ``AIPDD_CLOUD_API_KEY``
    env 引导 > 已连接的 AIPDD 渠道（渠道自己的 ``X-API-Key`` 就是上传凭据）。
    站点地址一律从配置/渠道推导，源码里不写死（见 req41 静态守卫）。
    """
    from novelvideo import config

    base = str(saved_base_url or "").strip() or str(
        getattr(config, "AIPDD_CLOUD_BASE_URL", "") or ""
    ).strip()
    key = str(saved_api_key or "").strip() or str(
        getattr(config, "AIPDD_CLOUD_API_KEY", "") or ""
    ).strip()
    if base and key:
        return _site_origin(base), key

    try:
        from novelvideo.model_gateway_settings import get_newapi_provider_channels

        channels = get_newapi_provider_channels()
    except Exception:  # pragma: no cover - 设置库不可读时按「未配置」处理
        logger.warning("AIPDD 数字资产云：读取渠道配置失败", exc_info=True)
        channels = []
    for channel in channels:
        provider = str(channel.get("provider") or "").strip().lower()
        if "aipdd" not in provider:
            continue
        # 只缺一半时也能跑：填了 Key 就用渠道的地址，只填了地址就用渠道的 Key。
        origin = _site_origin(base) or _site_origin(str(channel.get("baseUrl") or ""))
        resolved_key = key or str(channel.get("upstreamKey") or "").strip()
        if origin and resolved_key:
            return origin, resolved_key
    return "", ""


def _aipdd_response_detail(response) -> str:
    """尽量从错误响应里抠出一句人话（HTML 页面只截前缀）。"""
    text = str(getattr(response, "text", "") or "").strip()
    try:
        body = response.json()
    except Exception:
        return text[:200]
    if isinstance(body, dict):
        message = str(body.get("message") or body.get("error") or "").strip()
        if message:
            return message[:200]
    return text[:200]


def _unwrap_aipdd_envelope(body: object) -> dict:
    """拆开平台信封取 ``data``；成功的 ``code`` 是 **0**（不是 200）。

    参考实现按 ``code !== 200`` 判失败，对现在的后端会把成功当失败，因此两种成功码
    都收；非 JSON（例如渠道地址被前端 SPA 页面接管）单独报可执行的错。
    """
    if not isinstance(body, dict):
        raise MediaRelayConfigError("AIPDD 数字资产云响应结构无法识别")
    if body.get("code") not in (0, 200):
        raise MediaRelayConfigError(
            "AIPDD 数字资产云上传失败"
            f"（code={body.get('code')}）：{str(body.get('message') or '')[:200]}"
        )
    data = body.get("data")
    return data if isinstance(data, dict) else {}


class AipddCloudRelay:
    """[aipdd fork delta req44] 上传素材到 AIPDD 数字资产云，返回永久公网直链。

    实测（2026-10-07）：``POST {站点根}/oss/upload``（multipart ``file`` + 可选
    ``bucket_name`` / ``prefix``，头 ``X-API-Key`` 即渠道密钥）回
    ``{"code": 0, "data": {"url": "https://…", "is_private": false}}``；拿到的直链无签名、
    支持 Range、长期可读。这是「把本地素材交给上游抓取」唯一免额外凭据的通路 ——
    ``/v1/videos`` 不收 multipart（实测一律 500）、火山 MediaKit 只给 ``mediakit://``、
    data URL 上游受理但不校验（会静默产出与原视频无关的片子），都已在 req42/43 排除。
    """

    provider_name = "aipdd_cloud"

    def __init__(
        self,
        *,
        origin: str = "",
        api_key: str = "",
        bucket: str = "",
        prefix: str = "",
        register_asset: bool = False,
        saved_base_url: str = "",
        saved_api_key: str = "",
    ) -> None:
        if not str(origin or "").strip() or not str(api_key or "").strip():
            origin, api_key = resolve_aipdd_cloud_target(
                saved_base_url=saved_base_url, saved_api_key=saved_api_key
            )
        if not origin or not api_key:
            raise MediaRelayConfigError(
                "AIPDD 参考媒体中转未就绪：请在「设置 · 图床/媒体存储」填写 AIPDD "
                "API Key（或在「设置 → 模型与渠道」连接一条 AIPDD 渠道，它的 Key 就是"
                "上传凭据）；也可用 AIPDD_CLOUD_BASE_URL / AIPDD_CLOUD_API_KEY 引导。"
            )
        # 调用方可能直接拿渠道地址（带 /v1）传进来：上传口只认站点根，这里统一洗一遍。
        self._origin = (_site_origin(str(origin)) or str(origin)).rstrip("/")
        self._api_key = str(api_key)
        self._bucket = str(bucket or "").strip() or AIPDD_CLOUD_DEFAULT_BUCKET
        self._prefix = (
            str(prefix or "").strip().strip("/") or AIPDD_CLOUD_DEFAULT_PREFIX
        )
        self._register_asset = bool(register_asset)

    def _prefix_for(self, object_key: str | None) -> str:
        """调用方给了 object_key 就沿用它的目录，文件名仍由平台生成。"""
        if object_key is None:
            return self._prefix
        if not _is_safe_object_key(object_key):
            raise ServiceEgressDenied("object-key")
        parent = str(object_key).rsplit("/", 1)[0].strip("/") if "/" in object_key else ""
        return parent or self._prefix

    def upload_bytes(
        self,
        data: bytes,
        *,
        ext: str = "png",
        ttl: int = 1800,
        resource_type: str = "image",
        object_key: str | None = None,
    ) -> str:
        if not data:
            raise ValueError("cannot relay empty media bytes")

        import httpx

        ext = _normalize_ext(ext)
        resource_type = str(resource_type or "image").strip().lower()
        filename = f"{uuid.uuid4().hex}.{ext}"
        content_type = (
            mimetypes.types_map.get(f".{ext}")
            or _DEFAULT_MIME_BY_RESOURCE.get(resource_type)
            or "application/octet-stream"
        )
        url = f"{self._origin}{AIPDD_CLOUD_UPLOAD_PATH}"
        try:
            with httpx.Client(
                timeout=httpx.Timeout(
                    connect=15.0, read=300.0, write=300.0, pool=15.0
                )
            ) as client:
                response = client.post(
                    url,
                    headers={"X-API-Key": self._api_key},
                    data={"bucket_name": self._bucket, "prefix": self._prefix_for(object_key)},
                    files={"file": (filename, data, content_type)},
                )
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise MediaRelayConfigError(
                "AIPDD 数字资产云上传失败"
                f"（HTTP {exc.response.status_code}）：{_aipdd_response_detail(exc.response)}"
            ) from exc
        except httpx.HTTPError as exc:
            raise MediaRelayConfigError(f"AIPDD 数字资产云上传失败：{exc}") from exc

        try:
            body = response.json()
        except ValueError as exc:
            raise MediaRelayConfigError(
                "AIPDD 数字资产云返回的不是 JSON（渠道地址可能被站点页面接管）："
                f"{_aipdd_response_detail(response)}"
            ) from exc
        payload = _unwrap_aipdd_envelope(body)
        public_url = str(payload.get("url") or "").strip()
        if not public_url.startswith(("http://", "https://")):
            raise MediaRelayConfigError("AIPDD 数字资产云上传未返回公网直链")
        if self._register_asset:
            self.register_asset(
                public_url,
                name=str(payload.get("filename") or filename),
                resource_type=resource_type,
            )
        return public_url

    def register_asset(
        self, url: str, *, name: str, resource_type: str = "image"
    ) -> str:
        """把直链登记成平台数字资产（best-effort，失败不影响出片）。

        ``/digital_asset`` 实测接受任意 ``type`` 字符串并原样回显，这里按素材类型传；
        默认不启用，避免一次任务里几十张参考图把资产库灌满。
        """
        import httpx

        body = {
            "type": str(resource_type or "image").strip().lower() or "image",
            "url": str(url or "").strip(),
            "name": str(name or "").strip(),
            "labels": ["dramaclaw"],
            "isOpen": False,
        }
        try:
            with httpx.Client(
                timeout=httpx.Timeout(
                    connect=15.0, read=60.0, write=60.0, pool=15.0
                )
            ) as client:
                response = client.post(
                    f"{self._origin}{AIPDD_CLOUD_ASSET_PATH}",
                    headers={"X-API-Key": self._api_key, "Content-Type": "application/json"},
                    json=body,
                )
                response.raise_for_status()
                payload = _unwrap_aipdd_envelope(response.json())
        except Exception as exc:  # noqa: BLE001 - 登记失败绝不阻断出片
            logger.warning("AIPDD 数字资产登记失败（已忽略）：%s", exc)
            return ""
        return str(payload.get("id") or "")

    def upload_file(self, path: str | Path, *, ttl: int = 1800) -> str:
        file_path = Path(path)
        ext = file_path.suffix.lstrip(".") or "png"
        return self.upload_bytes(
            file_path.read_bytes(),
            ext=ext,
            ttl=ttl,
            # 登记类型必须跟真实素材走，否则视频会被登成图片资产。
            resource_type=_resource_type_for_ext(ext),
        )


class LocalImageRelay:
    """[aipdd fork delta] 「本地存储」档的参考媒体中转替身。

    参考图片不外传：把字节直接内联为 base64 data URL 返回，随请求下发给
    LocalNewAPI（newapi/aipdd）——与 huimeng 通道 ``local_file_to_data_url`` 的
    做法一致，图生图/图生视频在本地档可用。

    参考视频/音频本档自己发不出去：实测上游会受理带 ``video_url`` data URL 的
    请求却不去拉素材，产出的就是一段与原视频无关的片子，所以这两类必须在本地档
    也经「AIPDD 数字资产云」中转成公网直链（上传凭据就是设置里填的 AIPDD API
    Key，未填时回落已连接的 AIPDD 渠道）。解析不到凭据就直接报错，不再静默内联。

    心法（用户口径）：**能不用云就不用云**。只有调用方明确声明 ``resource_type``
    为 video/audio（即那个必须拿公网直链的 omni 参考素材通路）才去上传；其余入口
    （如不声明类型的 ``upload_file``）一律维持内联，绝不无谓地把素材外传。
    """

    def __init__(
        self,
        *,
        saved_base_url: str = "",
        saved_api_key: str = "",
        aipdd_prefix: str = "",
        aipdd_register_asset: bool = False,
    ) -> None:
        # [aipdd fork delta req44] 本地档的视频/音频出口：惰性构造，避免没配 Key 的
        # 纯图片用户在只走内联时也去读一遍渠道表。
        self._saved_base_url = str(saved_base_url or "")
        self._saved_api_key = str(saved_api_key or "")
        self._aipdd_prefix = str(aipdd_prefix or "")
        self._aipdd_register_asset = bool(aipdd_register_asset)
        self._aipdd_relay: AipddCloudRelay | None = None
        self._aipdd_resolved = False

    def _aipdd(self) -> AipddCloudRelay | None:
        """本地档可用的 AIPDD 中转；凭据解析不到时回 ``None``（由调用方决定报错口径）。"""
        if not self._aipdd_resolved:
            self._aipdd_resolved = True
            origin, api_key = resolve_aipdd_cloud_target(
                saved_base_url=self._saved_base_url,
                saved_api_key=self._saved_api_key,
            )
            if origin and api_key:
                self._aipdd_relay = AipddCloudRelay(
                    origin=origin,
                    api_key=api_key,
                    prefix=self._aipdd_prefix,
                    register_asset=self._aipdd_register_asset,
                )
        return self._aipdd_relay

    def upload_bytes(
        self,
        data: bytes,
        *,
        ext: str = "png",
        ttl: int = 1800,
        resource_type: str = "image",
        object_key: str | None = None,
    ) -> str:
        if not data:
            raise ValueError("cannot relay empty media bytes")
        if resource_type in {"video", "audio"}:
            relay = self._aipdd()
            if relay is None:
                # 文案按真实素材说：上游为了适配 Cloudinary 只认 image/video/raw，
                # 参考音频传进来的 resource_type 其实是 "video"，拿它拼文案会误导用户。
                kind = (
                    "参考音频"
                    if _resource_type_for_ext(ext) == "audio"
                    else "参考视频"
                )
                raise MediaRelayConfigError(
                    f"本地存储档下{kind}无法内联下发：实测上游受理带 data URL 的请求"
                    "但不去拉素材，产出的内容与源素材无关。请在「设置 · 图床/媒体存储」"
                    "填写 AIPDD API Key（或在「设置 → 模型与渠道」连接一条 AIPDD 渠道，"
                    "它的 Key 就是上传凭据），由数字资产云转出公网直链；也可改用"
                    "阿里云 OSS / Cloudinary 档。"
                )
            return relay.upload_bytes(
                data,
                ext=ext,
                ttl=ttl,
                resource_type=resource_type,
                object_key=object_key,
            )
        return _bytes_to_data_url(data, ext, resource_type=resource_type)

    def upload_file(self, path: str | Path, *, ttl: int = 1800) -> str:
        file_path = Path(path)
        return self.upload_bytes(
            file_path.read_bytes(),
            ext=file_path.suffix.lstrip(".") or "png",
            ttl=ttl,
        )


def _effective_media_relay_config():
    """Resolve the effective media relay config from process config/env.

    Shared by :func:`get_media_relay` and :func:`media_relay_is_local`. Not
    cached so credential rotation / test monkeypatching is observed immediately.
    """
    from novelvideo import config
    from novelvideo.model_gateway_settings import get_effective_media_relay_config

    return get_effective_media_relay_config(
        env_provider=getattr(config, "MEDIA_RELAY_PROVIDER", ""),
        env_ttl_seconds=getattr(config, "MEDIA_RELAY_TTL_SECONDS", 1800),
        env_endpoint=getattr(config, "OSS_RELAY_ENDPOINT", ""),
        env_bucket=getattr(config, "OSS_RELAY_BUCKET", ""),
        env_access_key_id=getattr(config, "OSS_RELAY_AK", ""),
        env_access_key_secret=getattr(config, "OSS_RELAY_SK", ""),
        env_cloud_name=getattr(config, "CLOUDINARY_RELAY_CLOUD_NAME", ""),
        env_cloudinary_api_key=getattr(config, "CLOUDINARY_RELAY_API_KEY", ""),
        env_cloudinary_api_secret=getattr(config, "CLOUDINARY_RELAY_API_SECRET", ""),
        env_cloudinary_folder=getattr(config, "CLOUDINARY_RELAY_FOLDER", ""),
        env_aipdd_api_key=getattr(config, "AIPDD_CLOUD_API_KEY", ""),
        env_aipdd_base_url=getattr(config, "AIPDD_CLOUD_BASE_URL", ""),
        env_s3_region=getattr(config, "AWS_S3_REGION", ""),
        env_s3_bucket=getattr(config, "AWS_S3_BUCKET", ""),
        env_s3_access_key_id=getattr(config, "AWS_S3_ACCESS_KEY_ID", ""),
        env_s3_access_key_secret=getattr(config, "AWS_S3_SECRET_ACCESS_KEY", ""),
        env_s3_endpoint_url=getattr(config, "AWS_S3_ENDPOINT_URL", ""),
        env_s3_session_token=getattr(config, "AWS_S3_SESSION_TOKEN", ""),
    )


def media_relay_is_local() -> bool:
    """[aipdd fork delta] 当前媒体存储是否为「本地存储」档位。

    [aipdd fork delta req44] 「本地」从此只代表「参考图片不外传」：参考视频/音频
    仍必须经 AIPDD 数字资产云转公网直链，所以调用方不能再把本档读成「完全没有云侧出口」。
    """
    return _effective_media_relay_config().provider == "local"


def get_media_relay() -> (
    AliyunOSSRelay | S3Relay | CloudinaryRelay | LocalImageRelay | AipddCloudRelay
):
    """Build the configured media relay.

    The relay is intentionally not cached so tests can monkeypatch config and
    failed or rotated credentials are not hidden behind process-local state.
    """
    relay_config = _effective_media_relay_config()
    provider = relay_config.provider
    aipdd_saved_base_url = str(getattr(relay_config, "aipdd_base_url", "") or "")
    aipdd_saved_api_key = str(getattr(relay_config, "aipdd_api_key", "") or "")
    aipdd_prefix = str(getattr(relay_config, "aipdd_prefix", "") or "")
    aipdd_register_asset = bool(getattr(relay_config, "aipdd_register_asset", False))
    if provider == "local":
        # [aipdd fork delta] 本地存储档：参考图片内联为 base64 data URL 随请求下发
        # 给 LocalNewAPI，无需云端中转即可图生图/图生视频。
        # [aipdd fork delta req44] 参考视频/音频不在这个可用面里：本档必须携带 AIPDD
        # API Key，由数字资产云转出公网直链（详见 LocalImageRelay 注释）。
        return LocalImageRelay(
            saved_base_url=aipdd_saved_base_url,
            saved_api_key=aipdd_saved_api_key,
            aipdd_prefix=aipdd_prefix,
            aipdd_register_asset=aipdd_register_asset,
        )
    if provider == "aipdd_cloud":
        # [aipdd fork delta req44] 数字资产云档：凭据就是设置里填的 AIPDD API Key
        # （未填时回落已连接的 AIPDD 渠道自身的 Key）。
        return AipddCloudRelay(
            bucket=relay_config.bucket,
            prefix=aipdd_prefix,
            register_asset=aipdd_register_asset,
            saved_base_url=aipdd_saved_base_url,
            saved_api_key=aipdd_saved_api_key,
        )
    if provider == "cloudinary":
        return CloudinaryRelay(
            cloud_name=relay_config.cloud_name,
            api_key=relay_config.cloudinary_api_key,
            api_secret=relay_config.cloudinary_api_secret,
            folder=relay_config.cloudinary_folder,
        )
    if provider == "aws_s3":
        # [aipdd fork delta] AWS S3 档：与阿里云 OSS 并行的对象存储中转，凭据齐备才构造。
        return S3Relay(
            region=str(getattr(relay_config, "s3_region", "") or ""),
            bucket_name=str(getattr(relay_config, "s3_bucket", "") or ""),
            access_key_id=str(getattr(relay_config, "s3_access_key_id", "") or ""),
            access_key_secret=str(getattr(relay_config, "s3_access_key_secret", "") or ""),
            endpoint_url=str(getattr(relay_config, "s3_endpoint_url", "") or ""),
            session_token=str(getattr(relay_config, "s3_session_token", "") or ""),
        )
    if provider != "aliyun_oss":
        raise MediaRelayConfigError(
            f"unsupported MEDIA_RELAY_PROVIDER: {provider or '-'}"
        )

    return AliyunOSSRelay(
        endpoint=relay_config.endpoint,
        bucket_name=relay_config.bucket,
        access_key_id=relay_config.access_key_id,
        access_key_secret=relay_config.access_key_secret,
    )


def _default_media_relay_ttl_seconds() -> int:
    from novelvideo import config
    from novelvideo.model_gateway_settings import get_effective_media_relay_config

    return get_effective_media_relay_config(
        env_provider=getattr(config, "MEDIA_RELAY_PROVIDER", ""),
        env_ttl_seconds=getattr(config, "MEDIA_RELAY_TTL_SECONDS", 1800),
        env_endpoint=getattr(config, "OSS_RELAY_ENDPOINT", ""),
        env_bucket=getattr(config, "OSS_RELAY_BUCKET", ""),
        env_access_key_id=getattr(config, "OSS_RELAY_AK", ""),
        env_access_key_secret=getattr(config, "OSS_RELAY_SK", ""),
        env_cloud_name=getattr(config, "CLOUDINARY_RELAY_CLOUD_NAME", ""),
        env_cloudinary_api_key=getattr(config, "CLOUDINARY_RELAY_API_KEY", ""),
        env_cloudinary_api_secret=getattr(config, "CLOUDINARY_RELAY_API_SECRET", ""),
        env_cloudinary_folder=getattr(config, "CLOUDINARY_RELAY_FOLDER", ""),
    ).ttl_seconds


def media_relay_ttl_seconds(*, minimum: int = 0) -> int:
    """Return the configured relay TTL without going below a caller's safety floor."""

    return max(int(minimum), _default_media_relay_ttl_seconds())


def upload_image_bytes(
    data: bytes,
    *,
    ext: str = "png",
    ttl: int | None = None,
    image_transform: str | None = None,
) -> str:
    return upload_media_bytes(
        data,
        ext=ext,
        ttl=ttl,
        resource_type="image",
        image_transform=image_transform,
    )


_DEFAULT_MIME_BY_RESOURCE = {
    "image": "image/png",
    "video": "video/mp4",
    "audio": "audio/mpeg",
    "raw": "application/octet-stream",
}


def _bytes_to_data_url(data: bytes, ext: str, *, resource_type: str = "image") -> str:
    """[aipdd fork delta] 把本地参考媒体字节内联为 base64 data URL。

    LocalNewAPI（newapi/aipdd）接受 data URL 作为**图片**输入（与 image_generator
    的 Seedream/SeedEdit 及 huimeng 通道的 local_file_to_data_url 一致），因此「本地
    存储」档无需 OSS/Cloudinary 也能图生图/图生视频。**视频/音频不在本可用面里**
    （req42/44 实测：上游受理带 data URL 的请求却不去拉素材）：``LocalImageRelay``
    已经把这两类拦去 AIPDD 中转，不会走到这里。

    mime 优先按扩展名推断（mp3→audio/mpeg、mp4→video/mp4），未知时回落到
    resource_type 的常见类型；图片扩展名异常时强制回落 image/png，与旧图生图行为保持一致。
    """
    ext = _normalize_ext(ext)
    mime = (
        mimetypes.types_map.get(f".{ext}")
        or _DEFAULT_MIME_BY_RESOURCE.get(resource_type)
    )
    if resource_type == "image" and not (mime and mime.startswith("image/")):
        mime = "image/png"
    if not mime:
        mime = "application/octet-stream"
    return "data:" + mime + ";base64," + base64.b64encode(data).decode("ascii")


def upload_media_bytes(
    data: bytes,
    *,
    ext: str = "png",
    ttl: int | None = None,
    resource_type: str = "image",
    image_transform: str | None = None,
) -> str:
    ttl_seconds = int(ttl if ttl is not None else _default_media_relay_ttl_seconds())
    data, ext = _apply_image_transform(data, ext=ext, image_transform=image_transform)
    return get_media_relay().upload_bytes(
        data,
        ext=ext,
        ttl=ttl_seconds,
        resource_type=resource_type,
    )


def upload_image_file(path: str | Path, *, ttl: int | None = None) -> str:
    """Upload a local image file to the relay and return a short-lived URL."""
    ttl_seconds = int(ttl if ttl is not None else _default_media_relay_ttl_seconds())
    return get_media_relay().upload_file(path, ttl=ttl_seconds)


async def relay_tenant_image_bytes(
    data: bytes,
    *,
    object_id: str,
    context,
    identity: StorageRelayIdentity,
    operations,
    relay=None,
    ext: str = "png",
    ttl: int = 1800,
) -> str:
    """Relay bytes through a tenant-bound service identity and durable claim."""

    from novelvideo.egress_context import TrustedEgressContext
    from novelvideo.ports.egress_operations import (
        HandleKind,
        OperationSpec,
        OperationState,
        canonical_request_digest,
        record_unknown_outcome,
    )

    if (
        type(context) is not TrustedEgressContext
        or not context.is_organization
        or type(identity) is not StorageRelayIdentity
        or identity.organization_id != context.billing_principal.id
        or identity.project_id != context.project_id
        or not data
        or type(object_id) is not str
        or not object_id.strip()
    ):
        raise ServiceEgressDenied("identity")

    normalized_ext = _normalize_ext(ext)
    object_digest = hashlib.sha256(object_id.encode("utf-8")).hexdigest()
    # Inside `relay/` because that is the whole of what the relay credential is
    # granted: the same bucket answers a HEAD under `relay/` with 404 NoSuchKey
    # and the identical HEAD under `tenants/` with 403 AccessDenied. The tenant
    # and project segments stay — they are why the key was ever structured —
    # they just live under the prefix the credential can reach.
    object_key = (
        f"relay/tenants/{identity.organization_id}/"
        f"projects/{identity.project_id}/"
        f"objects/{object_digest}.{normalized_ext}"
    )
    request_digest = canonical_request_digest(
        {
            "content_digest": hashlib.sha256(data).hexdigest(),
            "object_digest": object_digest,
            "ext": normalized_ext,
            "ttl": int(ttl),
        }
    )
    claim = await operations.claim(
        spec=OperationSpec(
            organization_id=identity.organization_id,
            project_id=identity.project_id,
            root_task_id=context.root_task_id,
            # One relay is one billable egress, and one envelope may relay many
            # objects, so the operation key has to name the object too — the
            # envelope alone would make the second object collide with the
            # first and be rejected as a conflicting claim. object_digest is
            # derived from object_id, so a retry recomputes the same key.
            business_task_id=f"{context.envelope_id}:{object_digest}",
            capability="storage.media.relay",
            credential_id=identity.credential_id,
            credential_version=identity.credential_version,
            request_digest=request_digest,
            handle_kind=HandleKind.NONE,
        )
    )
    if not claim.won or claim.operation.state is not OperationState.DISPATCHING:
        raise ServiceOperationNotReplayable("service operation already claimed")

    try:
        adapter = relay or get_media_relay()
        result = adapter.upload_bytes(
            data,
            ext=normalized_ext,
            ttl=int(ttl),
            object_key=object_key,
        )
        if inspect.isawaitable(result):
            result = await result
    except Exception:
        await record_unknown_outcome(
            operations, claim=claim, capability="storage.media.relay"
        )
        raise ServiceInvocationFailed() from None
    # `completed` 只能来自 `accepted`（`0039:294-338` 的 definer 如此判），先前从
    # `dispatching` 直接跳 completed 在真库上必抛 P0001；之所以一直是绿的，只因替身
    # 没有状态机。expected_version 要跟着 accepted 的返回走：版本已经 +1。
    accepted = await operations.mark_accepted(
        operation_id=claim.operation.operation_id,
        transition_token=claim.transition_token,
        expected_version=claim.operation.version,
        provider_job_id=None,
    )
    await operations.mark_completed(
        operation_id=accepted.operation_id,
        transition_token=claim.transition_token,
        expected_version=accepted.version,
        result_ref=None,
    )
    return str(result)


async def relay_tenant_image_bytes_from_context(
    data: bytes,
    *,
    object_id: str,
    context: object,
    ext: str = "png",
    ttl: int = 1800,
) -> str:
    """Relay tenant image bytes using only verified context and registered authority."""

    from novelvideo.egress_context import TrustedEgressContext
    from novelvideo.ports import get_egress_operation_port

    if type(context) is not TrustedEgressContext or not context.is_organization:
        raise ServiceEgressDenied("context")

    try:
        operations = get_egress_operation_port()
    except Exception:
        raise ServiceEgressDenied("operation-port") from None
    if not all(
        callable(getattr(operations, method, None))
        for method in ("claim", "mark_completed", "mark_unknown")
    ):
        raise ServiceEgressDenied("operation-port")

    identity = StorageRelayIdentity(
        credential_id="svc-media-relay",
        credential_version=1,
        organization_id=context.billing_principal.id,
        project_id=context.project_id,
    )
    return await relay_tenant_image_bytes(
        data,
        object_id=object_id,
        context=context,
        identity=identity,
        operations=operations,
        ext=ext,
        ttl=ttl,
    )


def ensure_image_url(reference: str | Path, *, ttl: int | None = None) -> str:
    """Return a remote URL for an image reference.

    - http/https URLs are already model-visible and are returned unchanged.
    - data:image/...;base64 references are uploaded to the relay.
    - local files are uploaded to the relay.

    This keeps newAPI/HuiMeng image calls from receiving local paths or data URLs
    when the upstream channel requires fetchable URL inputs.
    """
    value = str(reference or "").strip()
    if not value:
        raise ValueError("image reference is empty")
    if _is_remote_url(value):
        return value
    data_url_match = _DATA_IMAGE_URL_RE.match(value)
    if data_url_match:
        ext = _normalize_ext(data_url_match.group("ext"))
        try:
            data = base64.b64decode(data_url_match.group("data"), validate=True)
        except Exception as exc:
            raise ValueError("invalid base64 image data URL") from exc
        return upload_image_bytes(data, ext=ext, ttl=ttl)

    path = Path(value).expanduser()
    if not path.exists() or not path.is_file():
        raise ValueError(f"image reference is not a URL or local file: {value}")
    return upload_image_file(path, ttl=ttl)


def _apply_image_transform(
    data: bytes,
    *,
    ext: str,
    image_transform: str | None,
) -> tuple[bytes, str]:
    if not image_transform:
        return data, ext
    if image_transform == IMAGE_TRANSFORM_AI_REFERENCE_JPEG:
        return _normalize_ai_reference_image(data, ext=ext)
    raise ValueError(f"unsupported image_transform: {image_transform}")


def _normalize_ai_reference_image(
    data: bytes, *, ext: str = "png"
) -> tuple[bytes, str]:
    original_ext = _normalize_ext(ext)

    try:
        from PIL import Image, ImageOps, UnidentifiedImageError

        with Image.open(io.BytesIO(data)) as img:
            original_format = (img.format or original_ext or "").upper() or "UNKNOWN"
            original_mode = img.mode
            original_size = img.size
            img = ImageOps.exif_transpose(img)
            if img.mode in {"RGBA", "LA"} or (
                img.mode == "P" and "transparency" in img.info
            ):
                background = Image.new("RGB", img.size, (255, 255, 255))
                alpha = img.convert("RGBA").getchannel("A")
                background.paste(img.convert("RGBA"), mask=alpha)
                img = background
            elif img.mode != "RGB":
                img = img.convert("RGB")

            normalized_size = img.size
            buffer = io.BytesIO()
            img.save(
                buffer,
                format="JPEG",
                quality=AI_REFERENCE_JPEG_QUALITY,
                optimize=True,
            )
            normalized = buffer.getvalue()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        logger.info(
            "DramaClawAPI reference image normalize skipped: ext=%s bytes=%d error=%s",
            original_ext,
            len(data),
            exc,
        )
        return data, original_ext

    logger.info(
        "DramaClawAPI reference image normalized: %s %dx%d %s %.1fKB -> "
        "JPEG %dx%d RGB %.1fKB q=%d",
        original_format,
        original_size[0],
        original_size[1],
        original_mode,
        len(data) / 1024,
        normalized_size[0],
        normalized_size[1],
        len(normalized) / 1024,
        AI_REFERENCE_JPEG_QUALITY,
    )
    return normalized, "jpg"


_DATA_IMAGE_URL_RE = re.compile(
    r"^data:image/(?P<ext>[a-zA-Z0-9.+-]+);base64,(?P<data>.+)$",
    re.DOTALL,
)


def _is_remote_url(value: str) -> bool:
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _is_safe_object_key(value: str) -> bool:
    if not value or value.startswith(("/", ".")) or ".." in value.split("/"):
        return False
    return all(
        part and re.fullmatch(r"[A-Za-z0-9._-]+", part) for part in value.split("/")
    )


def _normalize_ext(ext: str) -> str:
    ext = (ext or "png").strip().lower().lstrip(".")
    if ext in {"jpeg", "pjpeg"}:
        return "jpg"
    if ext == "svg+xml":
        return "svg"
    return ext or "png"


# [aipdd fork delta req44] 按扩展名推素材类型：本地档只有判定为 video/audio 才会
# 走 AIPDD 中转，靠调用方自觉传 resource_type 等于给「静默内联视频」留了后门。
_VIDEO_EXTS = {"mp4", "mov", "webm", "mkv", "avi", "m4v", "flv", "wmv", "ts"}
_AUDIO_EXTS = {"mp3", "wav", "m4a", "aac", "flac", "ogg", "opus", "amr", "wma"}


def _resource_type_for_ext(ext: str) -> str:
    """从文件扩展名推 ``image`` / ``video`` / ``audio``（未知归为 image）。"""
    normalized = _normalize_ext(ext)
    if normalized in _VIDEO_EXTS:
        return "video"
    if normalized in _AUDIO_EXTS:
        return "audio"
    return "image"


def _cloudinary_error_detail(response: object) -> str:
    try:
        payload = response.json()  # type: ignore[attr-defined]
    except Exception:
        payload = None
    if isinstance(payload, dict):
        error = payload.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or "").strip()
            if message:
                return message[:500]
    try:
        return str(response.text or "").strip()[:500]  # type: ignore[attr-defined]
    except Exception:
        return ""
