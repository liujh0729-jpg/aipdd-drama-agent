"""LLM attribution context and provider instrumentation.

This module stays in the engine side so provider monkey patches can run in
CE and EE processes without importing EE/control-plane modules.
"""

from __future__ import annotations

import contextvars
import logging
from dataclasses import fields, is_dataclass
from functools import cache
from typing import Any, Optional

logger = logging.getLogger("novelvideo.llm_instrumentation")

_PROJECT_CTX: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "novelvideo_project_id", default=None
)
_USER_CTX: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "novelvideo_llm_user_id", default=None
)
_RESOURCE_KIND_CTX: contextvars.ContextVar[str] = contextvars.ContextVar(
    "novelvideo_llm_resource_kind", default=""
)
_BILLING_METADATA_CTX: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "novelvideo_billing_metadata", default={}
)
_MODEL_CALL_INSTRUMENTATION_ACTIVE: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "st_model_call_instrumentation_active",
    default=False,
)

_ALLOWED_RESOURCE_KINDS = frozenset(
    {"portrait", "sketch", "render", "video", "tts", "script", "ingest"}
)
_PROVIDER_REQUEST_ID_HEADER_NAMES = (
    "x-request-id",
    "x-requestid",
    "request-id",
    "request_id",
    "x-newapi-request-id",
    "newapi-request-id",
    "x-oneapi-request-id",
    "oneapi-request-id",
    "x-goog-request-id",
)

_pydantic_ai_openai_trace_patched = False
_agent_run_patched = False
_litellm_hook_installed = False
_litellm_acompletion_patched = False


def set_project_context(project_id: Optional[str]) -> None:
    _PROJECT_CTX.set(project_id or None)


def get_project_context() -> Optional[str]:
    return _PROJECT_CTX.get()


def get_llm_user_context() -> Optional[str]:
    return _USER_CTX.get()


def get_resource_kind_context() -> str:
    return _RESOURCE_KIND_CTX.get()


def get_billing_metadata_context() -> dict[str, Any]:
    return dict(_BILLING_METADATA_CTX.get() or {})


def clear_llm_usage_context() -> None:
    _USER_CTX.set(None)
    set_project_context(None)
    _RESOURCE_KIND_CTX.set("")
    _BILLING_METADATA_CTX.set({})


def set_llm_usage_context(
    user_id: Optional[str],
    project_id: Optional[str] = None,
    resource_kind: str = "",
    billing_metadata: Optional[dict[str, Any]] = None,
) -> None:
    _USER_CTX.set(user_id)
    set_project_context(project_id)
    kind = resource_kind if resource_kind in _ALLOWED_RESOURCE_KINDS else ""
    _RESOURCE_KIND_CTX.set(kind)
    _BILLING_METADATA_CTX.set(dict(billing_metadata or {}))


def set_model_call_instrumentation_active(active: bool = True):
    return _MODEL_CALL_INSTRUMENTATION_ACTIVE.set(active)


def reset_model_call_instrumentation_active(token) -> None:
    _MODEL_CALL_INSTRUMENTATION_ACTIVE.reset(token)


def _extract_model_name(agent: object) -> str:
    try:
        m = getattr(agent, "model", None)
        if m is None:
            return ""
        for attr in ("model_name", "name", "_model_name", "model"):
            val = getattr(m, attr, None)
            if isinstance(val, str) and val:
                return val.strip()
        s = str(m).strip()
        return s if s else ""
    except Exception:
        return ""


def _first_nonempty_str(*values: object) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _header_value(headers: object, *names: str) -> str:
    getter = getattr(headers, "get", None)
    for name in names:
        value = getter(name) if callable(getter) else None
        found = _first_nonempty_str(value)
        if found:
            return found
    if isinstance(headers, dict):
        lowered = {str(key).lower(): value for key, value in headers.items()}
        for name in names:
            found = _first_nonempty_str(lowered.get(name.lower()))
            if found:
                return found
    return ""


def _attach_openai_response_trace(parsed: object, http_response: object) -> None:
    headers = getattr(http_response, "headers", None)
    if headers is None:
        return
    try:
        header_map = dict(headers)
    except Exception:
        header_map = headers
    try:
        setattr(parsed, "_response_headers", header_map)
    except Exception as exc:
        logger.debug("openai response headers attach failed: %s", exc)
    request_id = _header_value(header_map, *_PROVIDER_REQUEST_ID_HEADER_NAMES)
    if not request_id:
        return
    try:
        current = getattr(parsed, "_request_id", None)
        if not current:
            setattr(parsed, "_request_id", request_id)
    except Exception as exc:
        logger.debug("openai request id attach failed: %s", exc)


def _extract_provider_ids(response_obj: object) -> tuple[str, str, str]:
    request_id = ""
    task_id = ""
    response_id = ""

    def _extract_from_one(obj: object) -> tuple[str, str, str]:
        req = _first_nonempty_str(
            getattr(obj, "request_id", None), getattr(obj, "_request_id", None)
        )
        task = _first_nonempty_str(getattr(obj, "task_id", None), getattr(obj, "taskId", None))
        resp = _first_nonempty_str(
            getattr(obj, "id", None),
            getattr(obj, "response_id", None),
            getattr(obj, "provider_response_id", None),
        )
        if isinstance(obj, dict):
            resp = _first_nonempty_str(
                resp, obj.get("id"), obj.get("response_id"), obj.get("provider_response_id")
            )
            req = _first_nonempty_str(req, obj.get("request_id"), obj.get("requestId"))
            task = _first_nonempty_str(task, obj.get("task_id"), obj.get("taskId"))
        for details in (getattr(obj, "provider_details", None), getattr(obj, "metadata", None)):
            if isinstance(details, dict):
                req = _first_nonempty_str(req, details.get("request_id"), details.get("requestId"))
                task = _first_nonempty_str(task, details.get("task_id"), details.get("taskId"))
                resp = _first_nonempty_str(
                    resp, details.get("response_id"), details.get("provider_response_id")
                )
        response_headers = getattr(obj, "_response_headers", None)
        req = _first_nonempty_str(
            req, _header_value(response_headers, *_PROVIDER_REQUEST_ID_HEADER_NAMES)
        )
        hidden = getattr(obj, "_hidden_params", None)
        if isinstance(hidden, dict):
            headers = hidden.get("headers") or hidden.get("response_headers") or {}
            req = _first_nonempty_str(
                req, _header_value(headers, *_PROVIDER_REQUEST_ID_HEADER_NAMES)
            )
            req = _first_nonempty_str(req, hidden.get("request_id"), hidden.get("requestId"))
        return req, task, resp

    try:
        request_id, task_id, response_id = _extract_from_one(response_obj)
        for attr in ("response",):
            nested = getattr(response_obj, attr, None)
            if nested is None:
                continue
            if callable(nested):
                try:
                    nested = nested()
                except TypeError:
                    continue
            nested_req, nested_task, nested_resp = _extract_from_one(nested)
            request_id = _first_nonempty_str(request_id, nested_req)
            task_id = _first_nonempty_str(task_id, nested_task)
            response_id = _first_nonempty_str(response_id, nested_resp)
        for method in ("new_messages", "all_messages"):
            messages_fn = getattr(response_obj, method, None)
            if not callable(messages_fn):
                continue
            try:
                messages = messages_fn()
            except Exception:
                continue
            for message in reversed(list(messages or [])):
                nested_req, nested_task, nested_resp = _extract_from_one(message)
                request_id = _first_nonempty_str(request_id, nested_req)
                task_id = _first_nonempty_str(task_id, nested_task)
                response_id = _first_nonempty_str(response_id, nested_resp)
                if request_id and response_id:
                    break
    except Exception:
        pass
    return request_id, task_id, response_id


def _strip_model_provider_prefix(model: str) -> str:
    value = (model or "").strip()
    for prefix in ("openai/", "custom/", "google/", "gemini/"):
        if value.startswith(prefix):
            return value[len(prefix) :]
    return value


@cache
def _normalization_aliases() -> tuple[str, ...]:
    """归一化比对照：功能位登记表里的内部别名（``DC-*``），不读环境变量。

    [aipdd fork delta] 这里以前逐个 ``os.environ.get("X_MODEL")`` 取「配置值」来并名字，
    而那些 per-feature env 已全部退出模型解析链（模板里也删了）——留着会让人误以为还能
    用 env 指模型。本函数的职责只是把 ``DC-x-LLM-v2`` 这类带后缀的调用名并回槽位别名，
    让用量统计按功能位聚合，登记表就是这些别名的唯一真源。
    """
    from novelvideo.model_slots import SLOTS

    return tuple(sorted({str(slot.alias).strip() for slot in SLOTS.values() if slot.alias}))


def _normalize_recorded_model_name(model: str) -> str:
    raw = (model or "").strip()
    if not raw:
        return ""
    plain = _strip_model_provider_prefix(raw)
    for alias in _normalization_aliases():
        alias_plain = _strip_model_provider_prefix(alias)
        if not alias_plain:
            continue
        if plain == alias_plain or plain.startswith(f"{alias_plain}-"):
            return alias_plain
    return plain


def _extract_litellm_usage(kwargs: dict, response_obj: object) -> tuple[int, int, str]:
    in_tok = 0
    out_tok = 0
    model = ""
    try:
        usage = getattr(response_obj, "usage", None) or {}
        if isinstance(usage, dict):
            in_tok = int(usage.get("prompt_tokens") or 0)
            out_tok = int(usage.get("completion_tokens") or 0)
        else:
            in_tok = int(getattr(usage, "prompt_tokens", 0) or 0)
            out_tok = int(getattr(usage, "completion_tokens", 0) or 0)
        model = (kwargs or {}).get("model", "") or getattr(response_obj, "model", "") or ""
    except Exception:
        pass
    return in_tok, out_tok, _normalize_recorded_model_name(model)


def _json_log_value(value: object, *, depth: int = 0) -> object:
    """Convert provider inputs/results to JSON-safe diagnostic values."""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        size_bytes = value.nbytes if isinstance(value, memoryview) else len(value)
        return {"type": "bytes", "size_bytes": size_bytes}
    binary_data = getattr(value, "data", None)
    if isinstance(binary_data, (bytes, bytearray, memoryview)):
        size_bytes = (
            binary_data.nbytes
            if isinstance(binary_data, memoryview)
            else len(binary_data)
        )
        return {
            "type": type(value).__name__,
            "media_type": str(getattr(value, "media_type", "") or ""),
            "size_bytes": size_bytes,
        }
    if depth >= 8:
        return {"type": type(value).__name__, "truncated": True}
    if isinstance(value, dict):
        return {
            str(key): _json_log_value(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_json_log_value(item, depth=depth + 1) for item in value]
    if is_dataclass(value) and not isinstance(value, type):
        return {
            "type": type(value).__name__,
            "fields": {
                field.name: _json_log_value(
                    getattr(value, field.name), depth=depth + 1
                )
                for field in fields(value)
            },
        }
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        try:
            return _json_log_value(model_dump(mode="python"), depth=depth + 1)
        except Exception:
            pass
    output = getattr(value, "output", None)
    if output is not None:
        return {"output": _json_log_value(output, depth=depth + 1)}
    rendered = str(value)
    if "BinaryContent(data=b" in rendered or "BinaryImage(data=b" in rendered:
        return {
            "type": type(value).__name__,
            "omitted": True,
            "reason": "nested_binary_repr",
        }
    return rendered


def _provider_http_status(exc: BaseException) -> int | None:
    """Extract only a normalized HTTP status from a provider exception."""
    candidates = (
        exc,
        getattr(exc, "response", None),
        getattr(exc, "http_response", None),
    )
    for candidate in candidates:
        if candidate is None:
            continue
        for attribute in ("status_code", "status"):
            value = getattr(candidate, attribute, None)
            try:
                status = int(value)
            except (TypeError, ValueError):
                continue
            if 100 <= status <= 599:
                return status
    return None


def _failure_log_metadata(
    exc: BaseException, response: object = None
) -> dict[str, object]:
    message = str(exc)
    metadata: dict[str, object] = {
        "error_message": message,
        "response_payload": {
            "status": "failed",
            "error_type": type(exc).__name__,
            "error_message": message,
            "response": _json_log_value(response),
        },
    }
    provider_http_status = _provider_http_status(exc)
    if provider_http_status is not None:
        metadata["provider_http_status"] = provider_http_status
    return metadata


def _hook_exception(kwargs: object) -> BaseException:
    error = kwargs.get("exception") if isinstance(kwargs, dict) else None
    if isinstance(error, BaseException):
        return error
    return RuntimeError(str(error or "model call failed"))


async def _log_model_call_failure(
    exc: BaseException, response: object = None
) -> None:
    """Record why a provider call failed; never gates execution."""
    from novelvideo.ports import update_current_model_call_log

    metadata = _failure_log_metadata(exc, response)
    try:
        await update_current_model_call_log(
            response_payload=metadata.get("response_payload"),
            error_message=str(metadata.get("error_message") or ""),
        )
    except Exception:  # noqa: BLE001
        return


async def _forward_agent_usage(agent: object, result: object) -> None:
    user_id = _USER_CTX.get()
    if not user_id:
        return
    project_id = _PROJECT_CTX.get()
    resource_kind = _RESOURCE_KIND_CTX.get()
    model = _normalize_recorded_model_name(_extract_model_name(agent))
    request_id, task_id, response_id = _extract_provider_ids(result)
    meta = {
        "response_id": response_id,
        "response_payload": _json_log_value(result),
    }
    from novelvideo.ports import get_usage_meter

    meter = get_usage_meter()
    try:
        usage_fn = getattr(result, "usage", None)
        usage = usage_fn() if callable(usage_fn) else None
        if usage is not None:
            in_tok = (
                getattr(usage, "input_tokens", None)
                or getattr(usage, "request_tokens", None)
                or 0
            )
            out_tok = (
                getattr(usage, "output_tokens", None)
                or getattr(usage, "response_tokens", None)
                or 0
            )
            await meter.record_llm_tokens(
                user_id=user_id,
                input_tokens=int(in_tok or 0),
                output_tokens=int(out_tok or 0),
                model=model,
                project_id=project_id,
                resource_kind=resource_kind,
            )
    except Exception as e:
        logger.debug("forward_agent_usage failed: %s", e)
    await meter.bump_model_call(
        user_id=user_id,
        model=model,
        project_id=project_id,
        resource_kind=resource_kind,
        provider_request_id=request_id,
        provider_task_id=task_id,
        metadata=meta,
    )


def _install_pydantic_ai_openai_trace_patch() -> None:
    global _pydantic_ai_openai_trace_patched
    if _pydantic_ai_openai_trace_patched:
        return
    try:
        from pydantic_ai.models.openai import OpenAIChatModel
    except Exception:
        return
    try:
        from openai._response import APIResponse, AsyncAPIResponse

        original_async_parse = AsyncAPIResponse.parse
        original_sync_parse = APIResponse.parse

        async def _patched_async_parse(self, *args, **kwargs):
            parsed = await original_async_parse(self, *args, **kwargs)
            _attach_openai_response_trace(parsed, getattr(self, "http_response", None))
            return parsed

        def _patched_sync_parse(self, *args, **kwargs):
            parsed = original_sync_parse(self, *args, **kwargs)
            _attach_openai_response_trace(parsed, getattr(self, "http_response", None))
            return parsed

        AsyncAPIResponse.parse = _patched_async_parse  # type: ignore[method-assign]
        APIResponse.parse = _patched_sync_parse  # type: ignore[method-assign]
    except Exception as exc:
        logger.debug("OpenAI SDK response parse patch failed: %s", exc)
    original_process_response = OpenAIChatModel._process_response

    def _patched_process_response(self, response):
        request_id = ""
        try:
            request_id = (
                str(getattr(response, "_request_id", "") or "").strip()
                or str(getattr(response, "request_id", "") or "").strip()
                or _header_value(
                    getattr(response, "_response_headers", None), *_PROVIDER_REQUEST_ID_HEADER_NAMES
                )
            )
        except Exception:
            request_id = ""
        result = original_process_response(self, response)
        if request_id:
            try:
                details = dict(getattr(result, "provider_details", None) or {})
                details.setdefault("request_id", request_id)
                result.provider_details = details
            except Exception as exc:
                logger.debug("pydantic-ai request id attach failed: %s", exc)
        return result

    OpenAIChatModel._process_response = _patched_process_response  # type: ignore[method-assign]
    _pydantic_ai_openai_trace_patched = True


def _install_agent_run_patch() -> None:
    global _agent_run_patched
    if _agent_run_patched:
        return
    try:
        from pydantic_ai import Agent
    except Exception:
        return
    original_run = Agent.run

    async def _tracked_run(self, *args, **kwargs):
        # [aipdd fork delta] WP-D: 额度预约已拆除，这里只剩「本次调用归谁记日志」
        # 的所有权标记与失败 / 成功的观测面写入。
        token = _MODEL_CALL_INSTRUMENTATION_ACTIVE.set(True)
        try:
            result = await original_run(self, *args, **kwargs)
        except BaseException as exc:
            await _log_model_call_failure(exc)
            raise
        finally:
            _MODEL_CALL_INSTRUMENTATION_ACTIVE.reset(token)
        try:
            await _forward_agent_usage(self, result)
        except Exception:
            pass
        return result

    Agent.run = _tracked_run  # type: ignore[assignment]
    _agent_run_patched = True


async def _forward_litellm_success(kwargs: dict, response_obj: object) -> None:
    user_id = _USER_CTX.get()
    if not user_id:
        return
    in_tok, out_tok, model = _extract_litellm_usage(kwargs, response_obj)
    project_id = _PROJECT_CTX.get()
    resource_kind = _RESOURCE_KIND_CTX.get()
    request_id, task_id, response_id = _extract_provider_ids(response_obj)
    meta = {
        "response_id": response_id,
        "response_payload": _json_log_value(response_obj),
    }
    from novelvideo.ports import get_usage_meter

    meter = get_usage_meter()
    if in_tok > 0 or out_tok > 0:
        try:
            await meter.record_llm_tokens(
                user_id=user_id,
                input_tokens=in_tok,
                output_tokens=out_tok,
                model=model,
                project_id=project_id,
                resource_kind=resource_kind,
            )
        except Exception as e:
            logger.debug("litellm usage emit failed: %s", e)
    await meter.bump_model_call(
        user_id=user_id,
        model=model,
        project_id=project_id,
        resource_kind=resource_kind,
        provider_request_id=request_id,
        provider_task_id=task_id,
        metadata=meta,
    )


def _patch_litellm_acompletion(litellm_module: object) -> None:
    global _litellm_acompletion_patched
    if _litellm_acompletion_patched:
        return
    original_acompletion = getattr(litellm_module, "acompletion", None)
    if not callable(original_acompletion):
        return

    async def _tracked_acompletion(*args, **kwargs):
        if _MODEL_CALL_INSTRUMENTATION_ACTIVE.get() or not _USER_CTX.get():
            return await original_acompletion(*args, **kwargs)
        model = str(kwargs.get("model") or (args[0] if args else "") or "").strip()
        if not model:
            return await original_acompletion(*args, **kwargs)
        token = _MODEL_CALL_INSTRUMENTATION_ACTIVE.set(True)
        try:
            response = await original_acompletion(*args, **kwargs)
        except BaseException as exc:
            await _log_model_call_failure(exc)
            raise
        finally:
            _MODEL_CALL_INSTRUMENTATION_ACTIVE.reset(token)
        call_kwargs = dict(kwargs)
        call_kwargs.setdefault("model", model)
        try:
            await _forward_litellm_success(call_kwargs, response)
        except Exception as exc:
            logger.debug("litellm acompletion success emit failed: %s", exc)
        return response

    setattr(litellm_module, "acompletion", _tracked_acompletion)
    _litellm_acompletion_patched = True


def _install_litellm_hook() -> None:
    global _litellm_hook_installed
    if _litellm_hook_installed:
        return
    try:
        import litellm  # type: ignore[import-not-found]
        from litellm.integrations.custom_logger import CustomLogger  # type: ignore[import-not-found]
    except Exception:
        return

    class _SupertaleUsageLogger(CustomLogger):
        def _extract(self, kwargs: dict, response_obj: object) -> tuple[int, int, str]:
            return _extract_litellm_usage(kwargs, response_obj)

        async def _forward_success(self, kwargs, response_obj) -> None:
            if _MODEL_CALL_INSTRUMENTATION_ACTIVE.get():
                return
            await _forward_litellm_success(kwargs, response_obj)

        async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
            if _MODEL_CALL_INSTRUMENTATION_ACTIVE.get():
                return
            await _log_model_call_failure(
                _hook_exception(kwargs), response_obj
            )

        def log_failure_event(self, kwargs, response_obj, start_time, end_time):
            import asyncio

            if _MODEL_CALL_INSTRUMENTATION_ACTIVE.get():
                return
            coro = _log_model_call_failure(
                _hook_exception(kwargs), response_obj
            )
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                loop.create_task(coro)
            else:
                asyncio.run(coro)

        async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
            await self._forward_success(kwargs, response_obj)

        def log_success_event(self, kwargs, response_obj, start_time, end_time):
            import asyncio

            coro = self._forward_success(kwargs, response_obj)
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = None
            if loop is not None:
                loop.create_task(coro)
            else:
                asyncio.run(coro)

    hook = _SupertaleUsageLogger()
    try:
        existing = list(getattr(litellm, "callbacks", None) or [])
        if not any(isinstance(item, _SupertaleUsageLogger) for item in existing):
            existing.append(hook)
            litellm.callbacks = existing
        _patch_litellm_acompletion(litellm)
        _litellm_hook_installed = True
    except Exception as e:
        logger.debug("litellm hook install failed: %s", e)


def install_provider_instrumentation() -> None:
    _install_pydantic_ai_openai_trace_patch()
    _install_agent_run_patch()
    _install_litellm_hook()
