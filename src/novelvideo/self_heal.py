# SPDX-License-Identifier: Elastic-2.0
# Copyright (c) 2026 ClaymoreLab
"""[aipdd fork delta] 环境与代码自愈：默认文本大模型参与诊断 + 白名单修复。

面向「用户调用 MCP 工具失败时」的兜底链路：

1. 收集**有界、脱敏**的证据：系统自检、环境待办、运行日志尾部、失败工具的处理函数
   源码、异常堆栈附近的代码片段；
2. 用已配置的默认文本模型（``FREEZONE_TEXT_WRITER_MODEL`` 槽位）做一次非流式分析，
   要求只回 JSON（诊断 / 证据 / 修复建议 / 是否需人工）；
3. ``mode="repair"`` 时只执行白名单动作：安装可自动装的环境项、**唯一匹配**的源码
   字符串替换（备份 + 语法校验 + 失败回滚）、创建缺失的数据目录；
4. 模型不可用或返回不可解析时降级：仍返回只读诊断与规则化建议，绝不让自愈本身崩掉。

有意不做的事：不执行 shell、不装 pip/npm 包、不改密钥、不删除文件、不碰
``src/novelvideo/security`` 与鉴权模块；macOS 签名包内拒绝改代码（会破坏签名）。
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_MAX_PROMPT_CHARS = 28_000
_MAX_CONTEXT_CHARS = 42_000
_MAX_LOG_FILES = 4
_MAX_LOG_LINES = 60
_MAX_LOG_BYTES = 64 * 1024
_MAX_LOG_TAIL_CHARS = 3_000
_MAX_SOURCE_CHARS = 6_000
_MAX_SNIPPET_CHARS = 3_000
_MAX_PATCHES = 3
_MAX_PATCH_FILE_BYTES = 512 * 1024
_MAX_NEW_CHARS = 20_000
_ANALYSIS_MAX_TOKENS = 2_400
_ANALYSIS_TIMEOUT_SECONDS = 120.0

_PATCH_PREFIXES = ("src/novelvideo/", ".hermes/plugins/")
_PATCH_DENY_PREFIXES = (
    "src/novelvideo/security/",
    "src/novelvideo/api/auth.py",
    "src/novelvideo/self_heal.py",
)

_CATEGORIES = {
    "environment",
    "configuration",
    "model_gateway",
    "code",
    "network",
    "storage",
    "permission",
    "unknown",
}

_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|access[_-]?token|refresh[_-]?token|secret|password|passwd|authorization|credential)",
    re.I,
)
_SECRET_VALUE_RES = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-]{8,}"),
)
_INLINE_SECRET_RE = re.compile(
    r"(?i)((?:api[_-]?key|token|secret|password|passwd|authorization)\s*[:=]\s*[\"']?)([^\s\"',}]{6,})"
)
_FRAME_RE = re.compile(r'File "([^"]+)",\s*line\s+(\d+)')


class SelfHealError(RuntimeError):
    """自愈分析链路（模型 / 解析）不可用；调用方据此降级。"""


def _repo_root() -> Path:
    """源码根：环境变量 > 含 pyproject+uv.lock 的祖先 > 含 src/novelvideo 的祖先。

    桌面安装版可能从 ``resources/app`` 或 ``resources/python/.../site-packages``
    两种副本导入本模块，不能只用 ``parents[2]`` 推断。
    """

    override = os.environ.get("NOVELVIDEO_PROJECT_ROOT", "").strip()
    if override:
        return Path(override).resolve()
    here = Path(__file__).resolve()
    candidates = list(here.parents)
    for parent in candidates:
        if (parent / "pyproject.toml").exists() and (parent / "uv.lock").exists():
            return parent
    for parent in candidates:
        if (parent / "src" / "novelvideo").is_dir():
            return parent
    return here.parents[2]


def _truncate(value: Any, limit: int) -> str:
    text = str(value if value is not None else "")
    return text if len(text) <= limit else f"{text[:limit]}…[truncated]"


def _coerce_bool(value: Any) -> bool:
    """严格布尔：``"false"`` / ``"0"`` 不能被 Python 的真值规则当成 True。"""

    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def redact_text(value: Any) -> str:
    """抹掉常见的密钥形态：``sk-``、Bearer、``api_key=...``。"""

    text = str(value if value is not None else "")
    for pattern in _SECRET_VALUE_RES:
        text = pattern.sub("***", text)
    text = _INLINE_SECRET_RE.sub(lambda m: f"{m.group(1)}***", text)
    return text


def _redact_obj(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            if _SECRET_KEY_RE.search(str(key)):
                out[str(key)] = "***" if item not in (None, "") else item
            else:
                out[str(key)] = _redact_obj(item)
        return out
    if isinstance(value, list):
        return [_redact_obj(item) for item in value]
    if isinstance(value, str):
        return redact_text(value)
    return value


def _packaged_kind() -> str:
    root = _repo_root().as_posix()
    if sys.platform == "darwin" and ".app/Contents/" in root:
        return "macos-app"
    if Path(root).name == "app" and Path(root).parent.name == "resources":
        return "windows-installed"
    return ""


def _tail(path: Path, lines: int, max_bytes: int) -> str:
    with path.open("rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - max_bytes))
        raw = fh.read()
    text = raw.decode("utf-8", errors="replace")
    return "\n".join(text.splitlines()[-lines:])


def _collect_diagnostics() -> dict[str, Any]:
    try:
        from novelvideo import diagnostics

        return diagnostics.run_diagnostics()
    except Exception as exc:  # noqa: BLE001 - 自检失败只降级，不拖垮自愈
        return {
            "sections": [],
            "summary": {},
            "error": f"{type(exc).__name__}: {exc}",
        }


def _collect_env_plan() -> dict[str, Any]:
    try:
        from novelvideo import env_setup_install

        plan = env_setup_install.build_plan()
    except Exception as exc:  # noqa: BLE001
        return {"items": [], "summary": {}, "error": f"{type(exc).__name__}: {exc}"}
    pending: list[dict[str, Any]] = []
    for item in plan.get("items", []):
        if not isinstance(item, dict) or item.get("status") == "ready":
            continue
        pending.append(
            {
                "id": str(item.get("id") or ""),
                "status": str(item.get("status") or ""),
                "auto_installable": bool(item.get("auto_installable")),
                "optional": bool(item.get("optional")),
                "detail": _truncate(redact_text(item.get("detail") or ""), 240) or None,
                "guide": _truncate(redact_text(item.get("guide") or ""), 300) or None,
            }
        )
    return {"items": pending[:20], "summary": plan.get("summary", {})}


def _collect_failing_items(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for section in report.get("sections", []):
        if not isinstance(section, dict):
            continue
        for item in section.get("items", []):
            if not isinstance(item, dict) or item.get("level") not in ("fail", "warn"):
                continue
            rows.append(
                {
                    "section": str(section.get("id") or ""),
                    "id": str(item.get("id") or ""),
                    "level": str(item.get("level") or ""),
                    "detail": _truncate(redact_text(item.get("detail") or ""), 240) or None,
                }
            )
    return rows[:30]


def _collect_logs() -> list[dict[str, str]]:
    from novelvideo import config

    # api.log 直接落在 RUNTIME_DIR 根下（见 novelvideo.api.__init__），轮转文件
    # 为 api.log.1；部分部署另把日志放 RUNTIME_DIR/logs。两处都要看。
    runtime = Path(config.RUNTIME_DIR)
    candidates: list[Path] = []
    for folder in (runtime, runtime / "logs"):
        try:
            candidates.extend(path for path in folder.glob("*.log*") if path.is_file())
        except OSError:
            continue
    try:
        files = sorted(
            candidates,
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )[:_MAX_LOG_FILES]
    except OSError:
        return []
    rows: list[dict[str, str]] = []
    for path in files:
        try:
            tail = _tail(path, _MAX_LOG_LINES, _MAX_LOG_BYTES)
        except OSError:
            continue
        if not tail.strip():
            continue
        rows.append(
            {
                "file": path.name,
                "tail": _truncate(redact_text(tail), _MAX_LOG_TAIL_CHARS),
            }
        )
    return rows


def _collect_tool_context(tool_name: str) -> dict[str, Any]:
    if not tool_name:
        return {}
    try:
        import inspect

        from novelvideo.chat import dramaclaw_tools as dt

        entries = getattr(dt, "TOOLS", ())
    except Exception as exc:  # noqa: BLE001
        return {"name": tool_name, "error": f"{type(exc).__name__}: {exc}"}
    for name, schema, handler in entries:
        if name != tool_name:
            continue
        try:
            source = inspect.getsource(handler)
        except Exception:  # noqa: BLE001 - 内建/动态处理器取不到源码不算错
            source = ""
        return {
            "name": name,
            "description": _truncate(redact_text(schema.get("description") or ""), 600),
            "source": _truncate(redact_text(source), _MAX_SOURCE_CHARS) or None,
        }
    return {"name": tool_name, "found": False}


def _collect_traceback(error: str) -> list[dict[str, Any]]:
    if not error:
        return []
    root = _repo_root().resolve()
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for match in _FRAME_RE.finditer(error):
        if len(rows) >= 3:
            break
        raw_path, raw_line = match.group(1), match.group(2)
        try:
            line_no = int(raw_line)
        except ValueError:
            continue
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = root / candidate
        try:
            resolved = candidate.resolve()
            rel = resolved.relative_to(root).as_posix()
        except (OSError, ValueError):
            continue
        if (rel, line_no) in seen or resolved.suffix != ".py" or not resolved.is_file():
            continue
        seen.add((rel, line_no))
        try:
            lines = resolved.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        start = max(1, line_no - 12)
        end = min(len(lines), line_no + 12)
        snippet = "\n".join(f"{idx}: {lines[idx - 1]}" for idx in range(start, end + 1))
        rows.append(
            {
                "file": rel,
                "line": line_no,
                "snippet": _truncate(redact_text(snippet), _MAX_SNIPPET_CHARS),
            }
        )
    return rows


def build_context(
    *,
    symptom: str,
    tool_name: str = "",
    error: str = "",
    scope: str = "auto",
) -> dict[str, Any]:
    """收集有界证据；每一路都独立容错，任何一路失败都不阻断其余证据。"""

    include_env = scope in ("auto", "environment")
    include_code = scope in ("auto", "code")
    context: dict[str, Any] = {
        "symptom": _truncate(redact_text(symptom), 2_000),
        "tool_name": tool_name or None,
        "error": _truncate(redact_text(error), 6_000) or None,
        "scope": scope,
        "platform": sys.platform,
        "python": sys.version.split()[0],
        "packaged": _packaged_kind() or None,
        "repo_root": _repo_root().as_posix(),
        "diagnostics": {},
        "environment_plan": {},
        "logs": [],
        "tool": {},
        "traceback_frames": [],
    }
    if include_env:
        report = _collect_diagnostics()
        context["diagnostics"] = {
            "summary": report.get("summary", {}),
            "attention": _collect_failing_items(report),
            "error": report.get("error"),
        }
        context["environment_plan"] = _collect_env_plan()
        context["logs"] = _collect_logs()
    if include_code:
        context["tool"] = _collect_tool_context(tool_name)
        context["traceback_frames"] = _collect_traceback(error)
    text = json.dumps(context, ensure_ascii=False, default=str)
    if len(text) > _MAX_CONTEXT_CHARS:
        context["logs"] = [
            {"file": row["file"], "tail": _truncate(row["tail"], 800)}
            for row in context.get("logs", [])
        ]
        if context.get("tool", {}).get("source"):
            context["tool"]["source"] = _truncate(context["tool"]["source"], 1_500)
        text = json.dumps(context, ensure_ascii=False, default=str)
    return context


def resolve_text_model(explicit: str = "") -> str:
    """默认文本模型：显式指定 > 用户绑定槽位（不吞未绑定异常）。"""

    name = str(explicit or "").strip()
    if name:
        return name
    from novelvideo.config import get_newapi_text_model_name
    from novelvideo.official_defaults import DEFAULT_FREEZONE_TEXT_WRITER_MODEL

    return get_newapi_text_model_name(
        "FREEZONE_TEXT_WRITER_MODEL",
        DEFAULT_FREEZONE_TEXT_WRITER_MODEL,
    )


def _system_prompt() -> str:
    return (
        "你是 AIPDD 短剧平台的「环境与代码自愈分析器」。你只能依据用户提供的诊断证据推理，"
        "不得编造未出现在证据里的工具名、接口、文件名或日志。\n"
        "只输出一个 JSON 对象，不要 Markdown 代码围栏、不要额外解释。字段：\n"
        '{"diagnosis": "中文根因判断", "category": "environment|configuration|model_gateway|code|network|storage|permission|unknown", '
        '"confidence": 0.0, "evidence": ["引用证据的短句"], '
        '"repairs": [{"type": "...", "reason": "..."}], '
        '"needs_human": false, "next_steps": ["中文下一步"], "reply": "给用户看的中文结论（2-4 句）"}\n'
        "允许的 repair 只有三种：\n"
        '1) {"type":"install_env_item","item":"<环境计划里的 id>","reason":"..."} —— 仅限状态为 installable 且 auto_installable=true 的项；\n'
        '2) {"type":"patch_file","path":"src/novelvideo/xxx.py","old":"<文件中恰好出现一次的原文>","new":"<替换后的完整片段>","reason":"..."} —— '
        "仅在证据明确指向具体代码缺陷时使用；old 必须是可唯一匹配的原文，不得包含密钥；\n"
        '3) {"type":"ensure_dir","path":"<STATE_DIR/RUNTIME_DIR/OUTPUT_DIR 下的路径>","reason":"..."}。\n'
        "禁止建议执行 shell、安装 pip/npm 包、删除文件、修改密钥或鉴权代码。"
        "证据不足时 repairs 必须为空且 needs_human=true。最多给 3 条 repairs。"
    )


def _user_prompt(symptom: str, context: dict[str, Any]) -> str:
    payload = {
        "symptom": symptom,
        "context": context,
        "task": (
            "判断这次失败的直接原因，区分「环境缺依赖 / 配置没配 / 模型网关不通 / 代码缺陷 / 网络 / 权限」。"
            "先基于证据定位；如果证据不足就明确说不足。需要修复时只给白名单动作。"
        ),
    }
    text = json.dumps(payload, ensure_ascii=False, default=str)
    if len(text) <= _MAX_PROMPT_CHARS:
        return text
    # 直接截断序列化 JSON 会把证据切成半截，模型拿到的是非法结构。
    # 按信息密度降级：日志只留文件名、源码片段删掉、堆栈只留位置。
    compact = dict(context)
    compact["logs"] = [str(row.get("file") or "") for row in context.get("logs") or []]
    compact["tool"] = {
        key: value
        for key, value in (context.get("tool") or {}).items()
        if key != "source"
    }
    compact["traceback_frames"] = [
        {"file": row.get("file"), "line": row.get("line")}
        for row in context.get("traceback_frames") or []
    ]
    diagnostics = dict(context.get("diagnostics") or {})
    diagnostics["attention"] = list(diagnostics.get("attention") or [])[:12]
    compact["diagnostics"] = diagnostics
    plan = dict(context.get("environment_plan") or {})
    plan["items"] = list(plan.get("items") or [])[:10]
    compact["environment_plan"] = plan
    payload["context"] = compact
    text = json.dumps(payload, ensure_ascii=False, default=str)
    return _truncate(text, _MAX_PROMPT_CHARS)


def _ask_model(*, model: str, system: str, user: str) -> str:
    import httpx

    from novelvideo.shared import openai_compat

    api_key, gateway_base = openai_compat.resolve_credentials()
    base_url = openai_compat.normalize_base_url(gateway_base)
    if not base_url:
        raise SelfHealError("公共网关地址未配置（设置 → 模型与渠道）")
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": _ANALYSIS_MAX_TOKENS,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    try:
        with httpx.Client(
            timeout=openai_compat.api_timeout(_ANALYSIS_TIMEOUT_SECONDS)
        ) as client:
            response = client.post(
                openai_compat.chat_completions_url(base_url),
                headers=openai_compat.auth_headers(api_key),
                json=payload,
            )
    except Exception as exc:  # noqa: BLE001 - 网关不可达统一转自愈降级
        raise SelfHealError(f"调用默认文本模型失败：{type(exc).__name__}: {exc}") from exc
    if response.status_code >= 400:
        raise SelfHealError(
            openai_compat.error_message(
                int(response.status_code),
                response.text,
                prefix="自愈分析模型网关",
            )
        )
    try:
        data = response.json() if response.content else {}
    except ValueError as exc:
        raise SelfHealError(f"自愈分析模型返回非 JSON 响应：{exc}") from exc
    text = openai_compat.first_message_text(data if isinstance(data, dict) else {})
    if not text:
        raise SelfHealError("默认文本模型没有返回内容")
    return text


def parse_analysis(text: str) -> dict[str, Any]:
    cleaned = str(text or "").strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
    except ValueError:
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if not match:
            raise SelfHealError(f"模型没有返回可解析的 JSON：{_truncate(cleaned, 300)}") from None
        try:
            parsed = json.loads(match.group(0))
        except ValueError as exc:
            raise SelfHealError(f"模型返回的 JSON 无法解析：{exc}") from exc
    if not isinstance(parsed, dict):
        raise SelfHealError("模型返回的顶层结构不是 JSON 对象")
    return parsed


def _string_list(value: Any, limit: int, item_chars: int = 300) -> list[str]:
    if not isinstance(value, list):
        return []
    rows: list[str] = []
    for item in value[:limit]:
        text = redact_text(item).strip()
        if text:
            rows.append(_truncate(text, item_chars))
    return rows


def normalize_analysis(raw: dict[str, Any]) -> dict[str, Any]:
    category = str(raw.get("category") or "unknown").strip().lower()
    if category not in _CATEGORIES:
        category = "unknown"
    try:
        confidence = float(raw.get("confidence"))
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    repairs: list[dict[str, Any]] = []
    for item in raw.get("repairs") or []:
        if isinstance(item, dict) and item.get("type"):
            repairs.append(_redact_obj(item))
    return {
        "diagnosis": _truncate(redact_text(raw.get("diagnosis") or ""), 2_000),
        "category": category,
        "confidence": confidence,
        "evidence": _string_list(raw.get("evidence"), 8),
        "repairs": repairs[:5],
        "needs_human": bool(raw.get("needs_human")),
        "next_steps": _string_list(raw.get("next_steps"), 8),
        "reply": _truncate(redact_text(raw.get("reply") or ""), 1_500),
    }


def _degraded_analysis(
    *, reason: str, context: dict[str, Any]
) -> dict[str, Any]:
    attention = context.get("diagnostics", {}).get("attention") or []
    evidence = [
        f"{row.get('section')}.{row.get('id')}: {row.get('detail') or row.get('level')}"
        for row in attention[:6]
    ]
    pending = context.get("environment_plan", {}).get("items") or []
    # 降级根因就是模型网关，必须先给出这条，环境待办只是附加线索。
    steps = ["检查设置 → 模型与渠道的网关地址与密钥，并确认默认文本模型已绑定"]
    steps.extend(
        f"补装环境项 {row['id']}（{row['status']}）" for row in pending if row.get("auto_installable")
    )
    steps.extend(
        f"按指引处理 {row['id']}" for row in pending if not row.get("auto_installable")
    )
    return {
        "diagnosis": f"默认文本模型不可用，已降级为只读诊断：{reason}",
        "category": "model_gateway",
        "confidence": 0.0,
        "evidence": evidence,
        "repairs": [],
        "needs_human": True,
        "next_steps": steps[:8],
        "reply": (
            "我已收集环境、日志与失败上下文，但默认文本模型当前不可用，无法完成智能分析。"
            f"原因：{reason}。请先在「设置 → 模型与渠道」确认文本模型已绑定且网关可达，再重试自愈。"
        ),
    }


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _write_text_atomic(path: Path, text: str) -> None:
    # 先写临时文件再 os.replace：进程中途被杀时原文件保持完整，
    # 不会留下半截源码（自愈改的正是正在运行的源码）。
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _backup_file(path: Path, rel: str, source: str) -> Path:
    from novelvideo import config

    # 时间戳只到秒：同一秒内两次修补会互相覆盖备份，追加随机后缀保唯一。
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    backup = Path(config.STATE_DIR) / "self_heal" / "backups" / stamp / rel
    backup.parent.mkdir(parents=True, exist_ok=True)
    _write_text_atomic(backup, source)
    return backup


def _repair_patch(repair: dict[str, Any]) -> dict[str, Any]:
    raw_path = str(repair.get("path") or "").strip().replace("\\", "/")
    # 只剥开头的 "./"，不能用 lstrip("./")：那会把 ".hermes/..." 的点一起吃掉。
    while raw_path.startswith("./"):
        raw_path = raw_path[2:]
    if not raw_path:
        return {"status": "rejected", "reason": "patch_file 缺少 path"}
    if Path(raw_path).is_absolute() or ".." in Path(raw_path).parts:
        return {"status": "rejected", "reason": "path 必须是仓库内相对路径"}
    if _packaged_kind() == "macos-app":
        return {
            "status": "rejected",
            "reason": "macOS 安装包内修改源码会破坏代码签名，需在源码仓库修复后重新签名打包",
        }
    root = _repo_root().resolve()
    path = (root / raw_path).resolve()
    if not _is_within(path, root):
        return {"status": "rejected", "reason": "path 解析后越出仓库根目录"}
    try:
        resolved_rel = path.relative_to(root).as_posix()
    except ValueError:
        return {"status": "rejected", "reason": "path 解析后越出仓库根目录"}
    # 路径比较统一大小写不敏感：Windows/macOS 文件系统不区分大小写，
    # 否则 "src/novelvideo/SECURITY/x.py" 会绕过 deny 前缀。
    rel_key = resolved_rel.casefold()
    if not any(rel_key.startswith(prefix.casefold()) for prefix in _PATCH_PREFIXES):
        return {"status": "rejected", "reason": "只允许修改 src/novelvideo/ 与 .hermes/plugins/ 下的文件"}
    if any(rel_key.startswith(prefix.casefold()) for prefix in _PATCH_DENY_PREFIXES):
        return {"status": "rejected", "reason": "该文件属于安全/鉴权边界，禁止自愈修改"}
    if path.suffix != ".py" or not path.is_file():
        return {"status": "rejected", "reason": "只允许修补已存在的 .py 文件"}
    try:
        size = path.stat().st_size
    except OSError as exc:
        return {"status": "failed", "reason": f"读取文件失败：{exc}"}
    if size > _MAX_PATCH_FILE_BYTES:
        return {"status": "rejected", "reason": "文件超过 512KB，拒绝自动修改"}

    old = str(repair.get("old") or "")
    new = str(repair.get("new") or "")
    if not old.strip() or not new.strip():
        return {"status": "rejected", "reason": "patch_file 的 old/new 都不能为空"}
    if len(new) > _MAX_NEW_CHARS:
        return {"status": "rejected", "reason": "new 片段超过 20000 字符"}
    try:
        with path.open("r", encoding="utf-8", newline="") as fh:
            source = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        return {"status": "failed", "reason": f"读取源码失败：{exc}"}
    occurrences = source.count(old)
    if occurrences != 1:
        return {
            "status": "rejected",
            "reason": f"old 片段在文件中出现 {occurrences} 次（必须恰好 1 次）",
        }
    patched = source.replace(old, new, 1)
    try:
        compile(patched, str(path), "exec")
    except SyntaxError as exc:
        return {
            "status": "rejected",
            "reason": f"替换后语法校验失败：{exc.msg}（第 {exc.lineno} 行）",
        }
    try:
        backup = _backup_file(path, raw_path, source)
    except OSError as exc:
        return {"status": "failed", "reason": f"备份失败，未修改文件：{exc}"}
    try:
        _write_text_atomic(path, patched)
        with path.open("r", encoding="utf-8", newline="") as fh:
            if fh.read() != patched:
                raise OSError("写入校验不一致")
    except Exception as exc:  # noqa: BLE001 - 写坏必须回滚
        try:
            _write_text_atomic(path, source)
        except OSError:
            return {
                "status": "failed",
                "reason": f"写入失败且回滚失败，原文件备份在 {backup}：{exc}",
                "backup": str(backup),
            }
        return {"status": "failed", "reason": f"写入失败，已回滚：{exc}", "backup": str(backup)}
    return {
        "status": "applied",
        "path": raw_path,
        "backup": str(backup),
        "bytes_before": len(source.encode("utf-8")),
        "bytes_after": len(patched.encode("utf-8")),
        "restart_required": True,
    }


def _repair_install(repair: dict[str, Any], *, allow_heavy: bool) -> dict[str, Any]:
    from novelvideo import env_setup_install

    item = str(repair.get("item") or "").strip()
    if not item:
        return {"status": "rejected", "reason": "install_env_item 缺少 item"}
    if item not in env_setup_install.AUTO_INSTALLABLE_IDS:
        return {"status": "rejected", "reason": f"环境项 {item} 不支持自动安装"}
    if not allow_heavy:
        return {
            "status": "needs_confirm",
            "reason": f"安装环境项 {item} 耗时较长，需显式传 allow_heavy=true 才执行",
            "item": item,
        }
    data = env_setup_install.install_item(item)
    result = _redact_obj(data)
    if isinstance(result, dict) and result.get("manual"):
        return {
            "status": "needs_manual",
            "item": item,
            "guide": result.get("guide"),
            "result": result,
        }
    if isinstance(result, dict) and result.get("started"):
        # 安装是异步启动的：此刻只保证「已开始」，不能声称已装好。
        return {"status": "started", "item": item, "result": result}
    return {"status": "applied", "item": item, "result": result}


def _repair_ensure_dir(repair: dict[str, Any]) -> dict[str, Any]:
    from novelvideo import config

    raw = str(repair.get("path") or "").strip()
    if not raw:
        return {"status": "rejected", "reason": "ensure_dir 缺少 path"}
    roots = [Path(config.STATE_DIR).resolve(), Path(config.RUNTIME_DIR).resolve(), Path(config.OUTPUT_DIR).resolve()]
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = roots[0] / candidate
    resolved = candidate.resolve()
    if not any(_is_within(resolved, root) for root in roots):
        return {"status": "rejected", "reason": "ensure_dir 只允许 STATE_DIR / RUNTIME_DIR / OUTPUT_DIR 内路径"}
    try:
        resolved.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return {"status": "failed", "reason": f"创建目录失败：{exc}"}
    return {"status": "applied", "path": str(resolved)}


def apply_repairs(
    repairs: list[dict[str, Any]],
    *,
    mode: str,
    allow_heavy: bool,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    applied = 0
    for repair in repairs[:5]:
        rtype = str(repair.get("type") or "").strip()
        reason = _truncate(redact_text(repair.get("reason") or ""), 400)
        entry: dict[str, Any] = {"type": rtype, "reason": reason or None}
        if mode != "repair":
            entry.update(
                {
                    "status": "proposed",
                    "detail": "mode=diagnose：仅作为建议，未执行（用户确认后用 mode=repair 执行）",
                }
            )
            results.append(entry)
            continue
        if applied >= _MAX_PATCHES:
            entry.update({"status": "skipped", "detail": "单次最多应用 3 项修复"})
            results.append(entry)
            continue
        try:
            if rtype == "patch_file":
                outcome = _repair_patch(repair)
            elif rtype == "install_env_item":
                outcome = _repair_install(repair, allow_heavy=allow_heavy)
            elif rtype == "ensure_dir":
                outcome = _repair_ensure_dir(repair)
            else:
                outcome = {"status": "rejected", "reason": f"不支持的修复类型：{rtype or '(empty)'}"}
        except Exception as exc:  # noqa: BLE001 - 单项修复失败不影响其余项
            outcome = {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"}
        if outcome.get("status") == "applied":
            applied += 1
        entry.update(_redact_obj(outcome))
        results.append(entry)
    return results


def _audit(entry: dict[str, Any]) -> str:
    audit_id = uuid.uuid4().hex[:16]
    record = {
        "id": audit_id,
        "at": datetime.now(timezone.utc).isoformat(),
        **entry,
    }
    try:
        from novelvideo import config

        path = Path(config.STATE_DIR) / "self_heal" / "audit.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > 2 * 1024 * 1024:
            path.replace(path.with_suffix(".jsonl.1"))
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(_redact_obj(record), ensure_ascii=False, default=str) + "\n")
    except Exception:  # noqa: BLE001 - 审计写失败不影响自愈结果
        pass
    return audit_id


def _context_summary(context: dict[str, Any]) -> dict[str, Any]:
    diagnostics = context.get("diagnostics") or {}
    plan = context.get("environment_plan") or {}
    tool = context.get("tool") or {}
    return {
        "diagnostics": {
            "summary": diagnostics.get("summary") or {},
            "attention": (diagnostics.get("attention") or [])[:12],
            "error": diagnostics.get("error"),
        },
        "environment_plan": {
            "summary": plan.get("summary") or {},
            "pending": (plan.get("items") or [])[:12],
            "error": plan.get("error"),
        },
        "logs": [row.get("file") for row in context.get("logs") or []],
        "tool_source_included": bool(tool.get("source")),
        "traceback_frames": [
            {"file": row.get("file"), "line": row.get("line")}
            for row in context.get("traceback_frames") or []
        ],
        "platform": context.get("platform"),
        "packaged": context.get("packaged"),
    }


def _agent_instruction(
    *, degraded: bool, repair_results: list[dict[str, Any]]
) -> str:
    statuses = {str(row.get("status") or "") for row in repair_results}
    parts = [
        "把 analysis.reply 用自然中文转述给用户，不要直接倾倒原始 JSON。",
    ]
    if degraded:
        parts.append(
            "本次是降级只读诊断：明确告诉用户默认文本模型当前不可用、原因见 "
            "degraded_reason，并引导到「设置 → 模型与渠道」配好后再试。"
        )
    if "proposed" in statuses:
        parts.append(
            "repairs 里 status=proposed 的条目只是建议，尚未执行；先说明建议，"
            "再问用户是否要修复，得到同意后才用 mode='repair' 重试。"
        )
    if "needs_confirm" in statuses:
        parts.append(
            "有修复项卡在 needs_confirm（通常是较重的环境安装）：先向用户确认，"
            "同意后带 allow_heavy=true 重试一次。"
        )
    if "needs_manual" in statuses:
        parts.append(
            "有修复项卡在 needs_manual：当前环境无法自动安装，把该条的 guide 原样"
            "转述给用户，不要声称已经装好。"
        )
    if "started" in statuses:
        parts.append(
            "有修复项是 status=started：安装已异步开始但尚未完成，告诉用户稍后可用"
            "环境检查确认结果，不要声称已经装好。"
        )
    if statuses & {"applied", "failed", "rejected"}:
        parts.append(
            "如实汇报每条修复的结果；有 applied 且 restart_required=true 时，"
            "提醒用户重启 AIPDD（桌面版退出并重新打开）。"
        )
    parts.append("同一个错误最多自愈一次，不要反复调用本工具。")
    return " ".join(parts)


def run_self_heal(
    *,
    symptom: str,
    tool_name: str = "",
    error: str = "",
    scope: str = "auto",
    mode: str = "diagnose",
    model: str = "",
    allow_heavy: bool = False,
) -> dict[str, Any]:
    """诊断（并可选修复）一次失败；模型不可用时降级返回只读诊断。"""

    symptom = str(symptom or "").strip()
    if not symptom:
        raise ValueError("symptom is required：请描述失败现象或粘贴错误原文")
    mode = str(mode or "diagnose").strip().lower()
    if mode not in ("diagnose", "repair"):
        raise ValueError("mode must be 'diagnose' or 'repair'")
    scope = str(scope or "auto").strip().lower()
    if scope not in ("auto", "environment", "code"):
        raise ValueError("scope must be 'auto', 'environment' or 'code'")

    started = time.time()
    context = build_context(
        symptom=symptom,
        tool_name=str(tool_name or "").strip(),
        error=str(error or "").strip(),
        scope=scope,
    )
    resolved_model = ""
    degraded_reason = ""
    analysis: dict[str, Any] | None = None
    try:
        resolved_model = resolve_text_model(model)
    except Exception as exc:  # noqa: BLE001 - 槽位未绑定/目录缺失 → 降级
        degraded_reason = f"默认文本模型不可用：{exc}"
    if resolved_model:
        try:
            raw_text = _ask_model(
                model=resolved_model,
                system=_system_prompt(),
                user=_user_prompt(symptom, context),
            )
            analysis = normalize_analysis(parse_analysis(raw_text))
        except Exception as exc:  # noqa: BLE001 - 模型/解析失败 → 降级
            degraded_reason = str(exc)
    degraded = analysis is None
    if analysis is None:
        analysis = _degraded_analysis(reason=degraded_reason or "未知原因", context=context)

    repair_results = apply_repairs(
        analysis.get("repairs") or [],
        mode=mode,
        allow_heavy=_coerce_bool(allow_heavy),
    )
    restart_required = any(
        bool(item.get("restart_required")) for item in repair_results
    )
    context_summary = _context_summary(context)
    audit_id = _audit(
        {
            "symptom": _truncate(symptom, 500),
            "tool_name": str(tool_name or "") or None,
            "mode": mode,
            "scope": scope,
            "model": resolved_model or None,
            "degraded": degraded,
            "degraded_reason": _truncate(degraded_reason, 500) or None,
            "diagnosis": analysis.get("diagnosis"),
            "category": analysis.get("category"),
            "confidence": analysis.get("confidence"),
            "needs_human": analysis.get("needs_human"),
            "repairs": [
                {
                    "type": row.get("type"),
                    "status": row.get("status"),
                    "path": row.get("path"),
                    "item": row.get("item"),
                }
                for row in repair_results
            ],
            "elapsed_ms": int((time.time() - started) * 1000),
        }
    )
    return {
        "ok": True,
        "mode": mode,
        "scope": scope,
        "tool_name": str(tool_name or "") or None,
        "model": resolved_model or None,
        "degraded": degraded,
        "degraded_reason": _truncate(degraded_reason, 800) or None,
        "analysis": analysis,
        "repairs": repair_results,
        "restart_required": restart_required,
        "restart_hint": (
            "代码补丁已保存，重启 AIPDD 服务后生效（桌面版请退出并重新打开应用）。"
            if restart_required
            else None
        ),
        "context": context_summary,
        "agent_instruction": _agent_instruction(
            degraded=degraded, repair_results=repair_results
        ),
        "audit_id": audit_id,
        "elapsed_ms": int((time.time() - started) * 1000),
    }
