"""脚本生成入口。

2.0 当前默认走“逐行剧本模式”：
- 一行一个 beat
- 保留原行文本
- LLM 只补 2.0 beat 元数据

旧的 screenplay-first 复杂链路已退出默认主路径。
本模块保留原有导入路径，只作为 literal workflow 的兼容薄壳。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from novelvideo.workflows.literal_script_writing import LiteralScriptWritingWorkflow

if TYPE_CHECKING:
    from novelvideo.cognee import CogneeStore


class ScriptWritingWorkflow(LiteralScriptWritingWorkflow):
    """向后兼容的脚本工作流名称。

    现在等价于 LiteralScriptWritingWorkflow。
    """


def create_script_writing_workflow(
    cognee_store: "CogneeStore",
    visual_style: str = "",
    genre: str = "",
    story_setting: str = "",
    spine_template: str = "drama",
) -> ScriptWritingWorkflow:
    """创建脚本生成 workflow。

    兼容旧入口名，内部直接返回逐行剧本模式实例。
    """
    del visual_style, genre, story_setting
    audio_type_mode = "narrated" if spine_template == "narrated" else "literal"
    return ScriptWritingWorkflow(
        cognee_store=cognee_store,
        # 逐行模式最终要调用 persist_narration_script()，该接口在 CogneeStore 上。
        sqlite_store=cognee_store,
        output_dir=getattr(cognee_store, "output_dir", ""),
        audio_type_mode=audio_type_mode,
    )


__all__ = [
    "ScriptWritingWorkflow",
    "create_script_writing_workflow",
]
