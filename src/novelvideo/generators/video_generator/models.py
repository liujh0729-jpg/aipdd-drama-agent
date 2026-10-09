"""视频生成数据类型与抽象基类（B1 拆包自单文件 video_generator.py）。"""

from abc import ABC
from dataclasses import dataclass
from enum import Enum
from typing import Optional

class VideoGenStatus(Enum):
    """视频生成状态。"""

    PENDING = "pending"
    PROCESSING = "processing"
    DONE = "done"
    FAILED = "failed"


class VideoBackend(Enum):
    """视频生成后端。"""

    SEEDANCE_FAST = "seedance_fast"  # Seedance 1.0 Pro Fast
    SEEDANCE_PRO = "seedance_pro"  # Seedance 1.5 Pro 有声
    SEEDANCE_PRO_SILENT = "seedance_pro_silent"  # Seedance 1.5 Pro 无声
    SEEDANCE_2 = "seedance_2"  # Seedance 2.0（v2.0 主力）
    COMFYUI = "comfyui"  # Claymore 1.0
    LTX23 = "ltx23"  # Lightricks LTX-Video 2.3 22B
    GROK_720 = "grok_720"  # xAI Grok Imagine Video 720p


@dataclass
class VideoGenResult:
    """视频生成结果。"""

    status: VideoGenStatus
    video_url: Optional[str] = None
    video_path: Optional[str] = None
    last_frame_url: Optional[str] = None
    last_frame_path: Optional[str] = None
    task_id: Optional[str] = None
    provider_task_id: Optional[str] = None
    error: Optional[str] = None
    duration_seconds: float = 0.0


class VideoGeneratorBase(ABC):
    """视频生成器基类。

    定义 Image-to-Video 生成的标准接口。
    """

    async def generate(
        self,
        image_path: Optional[str],
        prompt: str,
        output_path: str,
        aspect_ratio: str = "16:9",
        duration: float = 5.0,
        poll_interval: float = 5.0,
        max_polls: int = 60,
    ) -> VideoGenResult:
        """完整生成流程：提交 + 轮询 + 下载。

        Args:
            image_path: 首帧图像路径
            prompt: 动作描述
            output_path: 输出视频路径
            aspect_ratio: 宽高比
            duration: 目标时长（秒）
            poll_interval: 轮询间隔（秒）
            max_polls: 最大轮询次数

        Returns:
            生成结果
        """
        # 默认实现，子类可覆盖
        raise NotImplementedError("Subclass should implement generate()")
