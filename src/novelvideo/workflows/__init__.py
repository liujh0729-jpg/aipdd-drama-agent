"""NovelVideo Workflows 模块（Cognee 版）。"""

from novelvideo.workflows.script_writing import (
    ScriptWritingWorkflow,
    create_script_writing_workflow,
)
from novelvideo.workflows.literal_script_writing import (
    LiteralScriptWritingWorkflow,
    create_literal_script_writing_workflow,
)

__all__ = [
    "ScriptWritingWorkflow",
    "create_script_writing_workflow",
    "LiteralScriptWritingWorkflow",
    "create_literal_script_writing_workflow",
]
