# ruff: noqa: F401
"""视频生成器包（B1 拆包：单文件 video_generator.py 4,376 行 → 本包）。

保持 novelvideo.generators.video_generator 的导入路径、公开/私有符号面与
monkeypatch 打点面完全不变：本 __init__ 全量 re-export 子模块符号，并保留原
模块对外部依赖（ports / media_relay / cancel / ...）的引用面。

子模块：
- egress         出网脱敏、子进程、模型调用记录
- models         数据类型与抽象基类
- backend_utils  后端解析 / 提示词翻译 / newAPI 工具
- backends/*     各后端生成器（mock / seedance / seedance2 / grok / huimeng / newapi / comfyui）
- factory        create_video_generator
"""

from novelvideo.authz.authz_retry import retry_authz_read
from novelvideo.egress_context import ambient_egress_context
from novelvideo.i18n_message import lmsg
from novelvideo.ports import (
    get_usage_meter,
    get_video_result_delivery,
    update_current_model_call_log,
)
from novelvideo.ports.authz import AuthzError, detach_authz_error
from novelvideo.ports.video_delivery import (
    ArchivedVideoSource,
    VideoDeliveryError,
    VideoDeliveryReceipt,
)
from novelvideo.shared.billing_errors import find_billing_error
from novelvideo.shared.provider_costs import is_definite_no_cost_http_rejection
from novelvideo.shared.provider_errors import (
    classify_provider_video_task_error,
    provider_video_error_code_from_text,
    provider_video_error_message_from_text,
    provider_video_task_error_message,
)
from novelvideo.shared.upstream_quota_errors import is_upstream_quota_error
from novelvideo.storage.media_relay import (
    IMAGE_TRANSFORM_AI_REFERENCE_JPEG,
    media_relay_ttl_seconds,
    upload_media_bytes,
)
from novelvideo.task_backend.cancel import TaskCancelled, TaskTimedOut
from novelvideo.task_backend.envelope import RunningTaskAuthorityIndeterminate
from novelvideo.task_backend.subprocesses import run_project_subprocess
from novelvideo.usage.video_request_usage import (
    record_video_request,
    update_video_request_status,
)

from .backend_utils import (
    LOCALAPI_DISABLED_VIDEO_MODELS,
    LOCALAPI_MAINLINE_SEEDANCE2_MODELS,
    LOCALAPI_VIDEO_BACKEND_PREFIX,
    LOCALAPI_VIDEO_DISPLAY_LABELS,
    _coerce_video_backend_value,
    _is_loopback_server_address,
    _seedance2_config_mapping,
    _seedance2_duration_from_config,
    _server_address_host,
    newapi_video_backend_options,
    parse_newapi_video_backend,
    translate_prompt_to_english,
)
from .backends.comfyui import ComfyUIVideoGenerator
from .backends.grok import GrokVideoGenerator
from .backends.huimeng import HuimengVideoGenerator
from .backends.mock import MockVideoGenerator
from .backends.newapi import NewApiVideoError, NewApiVideoGenerator
from .backends.seedance import SeedanceVideoGenerator
from .backends.seedance2 import ShotReference, Seedance2VideoGenerator
from .egress import (
    LOCALAPI_MEDIA_INPUT_MIN_TTL_SECONDS,
    LOCALAPI_VIDEO_HTTP_TIMEOUT_SECONDS,
    VideoEgressError,
    _POST_ACCEPT_AUTHZ_MAX_RETRIES,
    _POST_ACCEPT_AUTHZ_RETRY_BASE_SECONDS,
    _POST_ACCEPT_AUTHZ_RETRY_CAP_SECONDS,
    _POST_ACCEPT_AUTHZ_RETRY_RANDOM,
    _POST_ACCEPT_AUTHZ_RETRY_SLEEP,
    _VIDEO_EGRESS_ERROR_MESSAGES,
    _deny_direct_organization_video,
    _direct_video_context_denied,
    _record_video_model_call,
    _run_video_subprocess,
    _safe_video_error_code,
    _video_egress_context,
    logger,
)
from .factory import create_video_generator
from .models import (
    VideoBackend,
    VideoGenResult,
    VideoGenStatus,
    VideoGeneratorBase,
)
