"""Mock 视频生成器（B1 拆包自单文件 video_generator.py）。

_run_video_subprocess 经包命名空间（_video_generator.）延迟解析，保证 tests 对
novelvideo.generators.video_generator._run_video_subprocess 的 monkeypatch 打点生效。
"""

import os

from novelvideo.generators import video_generator as _video_generator
from novelvideo.task_backend.cancel import TaskCancelled, TaskTimedOut
from novelvideo.video_encoder import video_encode_args

from ..models import VideoGenResult, VideoGenStatus, VideoGeneratorBase

class MockVideoGenerator(VideoGeneratorBase):
    """模拟视频生成器（测试用）。

    使用 FFmpeg 将静态图片转换为带 Ken Burns 效果的视频，
    用于开发阶段验证完整流程。

    示例:
        >>> generator = MockVideoGenerator()
        >>> result = await generator.generate(
        ...     image_path="frame.png",
        ...     prompt="character smiling",
        ...     output_path="output.mp4",
        ...     duration=5.0
        ... )
    """

    def __init__(self, width: int = 1920, height: int = 1080, fps: int = 30):
        """初始化模拟生成器。

        Args:
            width: 视频宽度
            height: 视频高度
            fps: 帧率
        """
        self.width = width
        self.height = height
        self.fps = fps

    async def generate(
        self,
        image_path: str,
        prompt: str,
        output_path: str,
        aspect_ratio: str = "16:9",
        duration: float = 5.0,
        **kwargs,
    ) -> VideoGenResult:
        """生成测试视频：首帧图像 + Ken Burns 效果。

        Args:
            image_path: 首帧图像路径
            prompt: 动作描述（叠加到视频上作为调试信息）
            output_path: 输出视频路径
            aspect_ratio: 宽高比
            duration: 视频时长（秒）

        Returns:
            生成结果
        """
        try:
            # 确保输出目录存在
            os.makedirs(os.path.dirname(output_path), exist_ok=True)

            # 计算帧数
            frames = int(duration * self.fps)

            # 清理 prompt 中的特殊字符（避免 FFmpeg 命令行问题）
            safe_prompt = (
                prompt[:40].replace("'", "").replace('"', "").replace(":", " ")
            )

            # 使用 FFmpeg 生成视频
            # 1. Ken Burns 缩放效果
            # 2. 叠加 prompt 文字（调试用）
            video_filter = (
                f"scale=8000:-1,"
                f"zoompan=z='min(zoom+0.0008,1.15)':d={frames}:"
                f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                f"s={self.width}x{self.height}:fps={self.fps},"
                f"drawtext=text='{safe_prompt}':"
                f"fontsize=20:fontcolor=white@0.7:x=20:y=20"
            )

            cmd = [
                "ffmpeg",
                "-y",
                "-loop",
                "1",
                "-i",
                image_path,
                "-vf",
                video_filter,
                "-t",
                str(duration),
                *video_encode_args(preset="fast"),
                "-pix_fmt",
                "yuv420p",
                "-an",  # 无音频
                output_path,
            ]

            result = _video_generator._run_video_subprocess(cmd)

            if result.returncode != 0:
                return VideoGenResult(
                    status=VideoGenStatus.FAILED,
                    error=f"FFmpeg error: {result.stderr[:500]}",
                )

            return VideoGenResult(
                status=VideoGenStatus.DONE,
                video_path=output_path,
                duration_seconds=duration,
            )

        except (TaskCancelled, TaskTimedOut):
            raise
        except Exception as e:
            return VideoGenResult(
                status=VideoGenStatus.FAILED,
                error=str(e),
            )
