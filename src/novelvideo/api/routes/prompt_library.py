"""[aipdd fork delta] 提示词库接口：列出 / 修改 / 恢复默认内置提示词。

覆盖值存 runtime_settings（与模型网关配置同一个 settings.db），
生成链路通过 ``novelvideo.catalog.prompt_library`` 读取生效值。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from novelvideo.catalog import prompt_library
from novelvideo.api.auth import get_api_user, require_owner

router = APIRouter(
    prefix="/prompt-library",
    dependencies=[Depends(get_api_user)],
)


class PromptOverrideUpdate(BaseModel):
    """提示词覆盖请求体；与内置默认一致时等价于恢复默认。"""

    value: str = Field(default="", description="覆盖后的提示词文本。")


def _payload() -> dict[str, object]:
    return {
        "revision": prompt_library.revision(),
        "groups": list(prompt_library.GROUP_ORDER),
        "items": prompt_library.list_prompt_items(),
    }


@router.get("")
async def list_prompt_library():
    """返回全部登记提示词（含内置默认、生效值与是否被覆盖）。"""
    return JSONResponse({"ok": True, "data": _payload()})


@router.put("/{key}", dependencies=[Depends(require_owner)])
async def update_prompt_library_item(key: str, body: PromptOverrideUpdate):
    """保存一条提示词覆盖（全局生效，仅 owner）。"""
    try:
        prompt_library.set_prompt_override(key, body.value)
    except KeyError:
        raise HTTPException(status_code=404, detail="未知的提示词 key") from None
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return JSONResponse({"ok": True, "data": _payload()})


@router.delete("/{key}", dependencies=[Depends(require_owner)])
async def reset_prompt_library_item(key: str):
    """删除一条覆盖，恢复内置默认（全局生效，仅 owner）。"""
    try:
        prompt_library.reset_prompt_override(key)
    except KeyError:
        raise HTTPException(status_code=404, detail="未知的提示词 key") from None
    return JSONResponse({"ok": True, "data": _payload()})