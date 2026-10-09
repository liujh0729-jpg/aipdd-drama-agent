"""包级常量：图片尺寸词表、宽高比、重试/轮询阈值、媒体输入 TTL（B3 拆包自单文件 grid_generation.py）。"""

import logging


_VALID_IMAGE_SIZES = {"512", "1K", "2K", "4K"}
_OPENROUTER_IMAGE_CAPABILITY_CACHE: dict[str, tuple[bool, str]] = {}
_OPENAI_VALID_QUALITIES = {"low", "medium", "high", "auto"}
_OPENAI_MIN_PIXELS = 655_360
_OPENAI_MAX_PIXELS = 8_294_400
_OPENAI_MAX_EDGE = 3840
_OPENAI_MAX_RATIO = 3.0
_HUIMENG_IMAGE_POLL_INTERVAL_SECONDS = 2.0
_HUIMENG_IMAGE_MAX_POLLS = 290
HUIMENG_IMAGE2_SINGLE_CELL_SELECTION = "huimeng_gpt_image2"
HUIMENG_IMAGE2_SINGLE_CELL_REASON = "huimeng-image-2-1k-only"
SINGLE_CELL_RENDER_MODE_KEY = "1x1_2-3"
SINGLE_CELL_RENDER_MODE_BY_ASPECT = {
    "1:1": "1x1_1-1",
    "9:16": "1x1_9-16",
    "16:9": "1x1_16-9",
}
logger = logging.getLogger(__name__)
NEWAPI_MEDIA_INPUT_MIN_TTL_SECONDS = 2 * 60 * 60
