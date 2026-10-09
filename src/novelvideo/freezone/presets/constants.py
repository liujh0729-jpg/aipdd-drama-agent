"""包级常量：媒体扩展名、宽高比枚举、默认图片模型（B2 拆包自单文件 presets.py）。"""

from __future__ import annotations


IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}
VIDEO_EXTS = {".mp4", ".mov", ".webm"}
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg"}
TEXT_EXTS = {".json", ".txt", ".md"}
# [aipdd fork delta req40] 预设不再钉死某个图源键：留空 = 跟随「设置 › 模型」里的默认
# 图模型（渠道目录内），用户也可以在节点上改选目录里的其它图像模型。
FREEZONE_PRESET_DEFAULT_IMAGE_MODEL = ""
FREEZONE_PRESET_IMAGE_ASPECT_RATIOS = (
    "1:1",
    "16:9",
    "9:16",
    "4:3",
    "3:4",
    "3:2",
    "2:3",
    "4:5",
    "5:4",
    "21:9",
)
