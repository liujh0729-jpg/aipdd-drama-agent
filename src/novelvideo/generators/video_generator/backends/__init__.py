# ruff: noqa: F401
"""视频生成后端集合（B1 拆包自单文件 video_generator.py）。"""

from .comfyui import ComfyUIVideoGenerator
from .grok import GrokVideoGenerator
from .huimeng import HuimengVideoGenerator
from .mock import MockVideoGenerator
from .newapi import NewApiVideoError, NewApiVideoGenerator
from .seedance import SeedanceVideoGenerator
from .seedance2 import ShotReference, Seedance2VideoGenerator
