"""请求路径同步 LLM 生成的总墙钟超时兜底（llm_deadline）回归测试。

C3/A7 把这些同步 LLM 端点的出网身份归因修毕后，#2 剩下的是一条可靠性债：整轮
生成（多轮往返）在请求进程内没有总墙钟上限，卡住的上游会无限期占住 worker。本测试
钉住兜底语义：默认宽松、只在病态挂起触发、超时转成干净的 504、正常路径逐字节不变。
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi import HTTPException

from novelvideo.api import llm_deadline


def test_deadline_default_when_env_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LLM_REQUEST_DEADLINE_SECONDS", raising=False)
    assert llm_deadline.llm_request_deadline_seconds() == 900.0


@pytest.mark.parametrize("raw", ["", "  ", "abc", "0", "-5", "0"])
def test_deadline_falls_back_on_invalid_or_nonpositive(
    monkeypatch: pytest.MonkeyPatch, raw: str
) -> None:
    # 非法/非正值一律回落宽松默认——绝不能把兜底设成 0 或负数把正常生成全误杀。
    monkeypatch.setenv("LLM_REQUEST_DEADLINE_SECONDS", raw)
    assert llm_deadline.llm_request_deadline_seconds() == 900.0


def test_deadline_honours_valid_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_REQUEST_DEADLINE_SECONDS", "12.5")
    assert llm_deadline.llm_request_deadline_seconds() == 12.5


@pytest.mark.asyncio
async def test_deadline_passes_through_fast_path() -> None:
    # 正常生成远低于上限：CM 透明放行，不改返回值/行为。
    async with llm_deadline.llm_request_deadline("改写生成"):
        result = await asyncio.sleep(0, result="ok")
    assert result == "ok"


@pytest.mark.asyncio
async def test_deadline_converts_stall_to_504(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_REQUEST_DEADLINE_SECONDS", "0.05")

    with pytest.raises(HTTPException) as exc:
        async with llm_deadline.llm_request_deadline("故事优化"):
            await asyncio.sleep(5)
    assert exc.value.status_code == 504
    assert "故事优化" in str(exc.value.detail)
    assert "超时" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_deadline_reraises_non_timeout_exception() -> None:
    # 非超时的业务异常原样透传，交给调用点既有 except 处理，兜底不吞。
    class _Boom(RuntimeError):
        pass

    with pytest.raises(_Boom):
        async with llm_deadline.llm_request_deadline("剧本生成"):
            raise _Boom()
