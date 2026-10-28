#!/usr/bin/env python3
"""Ratchet guard for committed env templates.

Each env template key must be read by the runtime surface that owns the
template, or be explicitly covered by an allowlist entry in this file.

[aipdd fork delta] 槽位登记表 ``_SLOT_ALIASES`` 不再是「运行期真的会读」的来源：
``model_slots.resolve_slot`` 已完全不读进程环境，槽位名只是内部键。所以模板里再写
``X_MODEL=DC-x-LLM`` 一律判死键 —— 模型绑定的真源是「设置 → 模型与渠道」+ 渠道目录。
"""

from __future__ import annotations

import argparse
import ast
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_DEAD_KEYS = 0
BASELINE_MISSING_KEYS = 0
ENV_KEY_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=")
TEMPLATE_ENV_KEY_RE = re.compile(r"^\s*#?\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=")
CONFIG_KEY_RE = re.compile(r"^[A-Z][A-Z0-9_]*_[A-Z0-9_]+$")
SHELL_ENV_REF_RE = re.compile(
    r"\$(?:\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)(?:[^}]*)\}|(?P<plain>[A-Za-z_][A-Za-z0-9_]*))"
)
DYNAMIC_ENV_ALLOWLIST: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"^ST_PROJECT_(?:MIN|MAX|USER_MAX)_ACTIVE_"
            r"(?:DEFAULT|VIDEO|WORLD|FFMPEG)_TASKS$"
        ),
        "src/novelvideo/task_backend/limits.py builds per-lane task limit names dynamically.",
    ),
    (
        re.compile(r"^EMBEDDING_BATCH_SIZE$"),
        "Written to os.environ in src/novelvideo/cognee/config.py for Cognee to consume; "
        "no direct runtime read.",
    ),
)
THIRD_PARTY_ENV_ALLOWLIST: tuple[tuple[re.Pattern[str], str], ...] = ()
COMMON_REVERSE_ENV_ALLOWLIST: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"^(?:GITHUB_SHA|OFFICIAL_CATALOG_OSS_(?:ACCESS_KEY_ID|ACCESS_KEY_SECRET|BUCKET|ENDPOINT|PREFIX))$"
        ),
        "Official media catalog publishing CI input, not application runtime configuration.",
    ),
    (re.compile(r"^ST_EDITION$"), "Launcher/test gate env, not operator template config."),
    (
        re.compile(r"^ST_ORG_(?:EGRESS_MODE|GATEWAY_API_KEY|GATEWAY_BASE_URL)$"),
        "Process-local handoff written by the parent when it launches a model child "
        "(task_backend/subprocesses.build_model_child_env); never operator-set, and "
        "setting it externally has no effect because the parent rebuilds the child env.",
    ),
    (re.compile(r"^DRAMACLAW_CE_ROOT$"), "Audit script discovery override, not runtime app config."),
    (
        re.compile(
            r"^(?:PROJECT_ID|PROJECT_DIR|PROJECT_DIR_FILE|DIRS_FILE|TASK_FILE|TASK_ID|LANE|MODE|"
            r"FIXTURE_DIR|LOG_DIR|M05_.*|FOSS_ONLY_.*|NOVEL_FIXTURE|STEP_TIMEOUT|MODE_UPPER|"
            r"PROVIDER_(?:CHECK|HAS_KEY|KEY_ENV|KEY_INFO)|ST_ACCEPT_PORT|SERVER_PID|"
            r"RENDER_IMG|VIDEO_DIR|VITE_DIRECTOR_VIEWER_URL)$"
        ),
        "Acceptance-script scratch env passed inside test commands.",
    ),
    (re.compile(r"^(?:ST_API_COVERAGE_FILE|PYTEST_ADDOPTS|PYTEST_CURRENT_TEST)$"), "Pytest runner env."),
    (
        re.compile(r"^I18N_(?:API_KEY|BASE_URL|MODEL)$"),
        "Developer-only bulk-translation pipeline (scripts/i18n_translate.py) endpoint "
        "override; a repo-maintenance CLI that also accepts --base-url/--api-key/--model "
        "flags, never runtime app or operator template configuration.",
    ),
    (re.compile(r"^(?:LANG|LC_ALL)$"), "Process locale env, not app configuration."),
    (
        re.compile(r"^(?:TERM_PROGRAM)$"),
        "Process/desktop env read by the Mac one-click scripts (scripts/mac_common.sh) "
        "to decide whether it may close the Terminal window on success; informational only.",
    ),
    (
        re.compile(r"^(?:BASH_SOURCE|ROOT_DIR)$"),
        "Shell-local variable in startup/dev scripts (start-ce.sh etc.), not external env config.",
    ),
    (
        re.compile(r"^(?:MODEL|LLM|EMBEDDING)_(?:PROVIDER|MODEL|NAME|API_KEY|BASE_URL|ENDPOINT|TIMEOUT|THINKING_LEVEL|DIMENSIONS|API_VERSION)$"),
        "Legacy/generic model adapter env; current operator contract uses LOCALAPI_/COGNEE_ keys.",
    ),
    (
        re.compile(r"^COGNEE_(?:LLM|EMBEDDING)_(?:API_KEY|API_VERSION|ENDPOINT|TIMEOUT)$"),
        "Low-level Cognee adapter override; current template documents provider/model/dim controls.",
    ),
    (
        re.compile(r"^COGNEE_LOG_FILE$"),
        "Internal dependency guard: DramaClaw disables Cognee's private file handler and "
        "routes dependency logs through host-process logging; this is not operator configuration.",
    ),
    (
        re.compile(r"^DA2_.*$"),
        "DA-2 depth model loading toggle (pano_sharp.build_da2_model), internal model-loading flag.",
    ),
    (
        re.compile(
            r"^(?:OPENAI|OPENROUTER|GEMINI|GOOGLE|GOOGLE_AI|DASHSCOPE|ARK|HUIMENGI|XAI|"
            r"VOLCENGINE|COMFYUI|COSYVOICE|FISH|EDGE_TTS|NANOBANANA|HUIMENG|"
            r"SKETCH_GATE|SKETCH_EDIT|VOXEL|PANO|SOG|STAGE_COLLISION|BACKUP_OSS)_.*$"
        ),
        "Optional legacy/provider-specific integration env outside the curated LocalAPI template.",
    ),
    (
        re.compile(
            r"^(?:REDIS_URL|FFMPEG_PATH|LOGFIRE_TOKEN|"
            r"OTEL_EXPORTER_OTLP_TRACES_ENDPOINT|ENABLE_BACKEND_ACCESS_CONTROL)$"
        ),
        "Third-party/runtime integration env, not part of the curated operator template.",
    ),
    (
        re.compile(
            r"^(?:CHARACTER_IMAGE|DIRECTOR_CONTROL|DIRECTOR_SCREENWRITER|FREEZONE_IMAGE_REVERSE_PROMPT|GLOBAL_VIDEO|"
            r"KEYFRAME_PROMPT|SCENE_ASSET|SCENE_360|SEEDANCE|SEEDREAM|SEEDEDIT|VIDEO_PROMPT|"
            r"TTS|MIGRATE_LEGACY|ML_SHARP|KEEP_RAW|DOWNLOAD_VIA_OSS|STATIC_VIA_OSS|"
            r"DISABLE_RENDER_PLAN|GRID_MODE|JR_ERROR_LOG|VIDEO_RESOLUTION|"
            r"SCENE_SPATIAL_CONTRACT|BLOCK_WORLD_|"
            r"OSS_|SUPERTALE_|DRAMACLAW_|HERMES_|CLAUDE_|CODEX_|SUPERPOWER_).*"
        ),
        "Legacy/internal feature flag or integration env outside the current public template contract.",
    ),
    (
        re.compile(r"^(?:BACKUP_ENV_NAME|BACKUP_STAGE_DIR|BACKUP_SYNC_OUTPUT)$"),
        "Optional backup integration env outside the current LocalAPI-first template contract.",
    ),
    (
        re.compile(
            r"^(?:NOVELVIDEO_AGENT_DATA_DIR|NOVELVIDEO_API_HOST|NOVELVIDEO_API_PORT|NOVELVIDEO_API_URL|NOVELVIDEO_DATA_ROOT|"
            r"NOVELVIDEO_API_WORKERS|NOVELVIDEO_API_TIMEOUT|NOVELVIDEO_API_READY_TIMEOUT|"
            r"NOVELVIDEO_API_GRACEFUL_SHUTDOWN_TIMEOUT|"
            r"NOVELVIDEO_TIMEOUT|"
            r"NOVELVIDEO_RUNTIME_DIR|NOVELVIDEO_STATE_DIR|NOVELVIDEO_TASK_STARTING_TIMEOUT|"
            r"NOVELVIDEO_UI_HOST|NOVELVIDEO_UI_PORT|NOVELVIDEO_WORKERS|"
            r"NOVELVIDEO_VERBOSE|NOVELVIDEO_ENABLE_LOGFIRE|"
            r"NOVELVIDEO_LOGFIRE_SERVICE|ST_CANVAS_LOCK_TIMING|ST_HERMES_.*|"
            r"ST_LITESTREAM_ENABLED|ST_LOCAL_USERNAME|"
            r"ST_PROJECT_TASK_TIMEOUT_S|ST_SPLAT_TRANSFORM_BIN)$"
        ),
        "Local runtime/dev override with code default; not required in committed templates.",
    ),
)
CE_REVERSE_ENV_ALLOWLIST: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(r"^ST_MEDIA_ARCHIVE_COPY_ENABLED$"),
        "EE-only archive copy switch checked by shared CE delivery code; "
        "CE does not expose it as operator configuration.",
    ),
    (
        re.compile(r"^LOCALAPI_(?:API_KEY|BASE_URL)$"),
        "EE deployment credentials read by shared CE/EE gateway code; CE dynamic "
        "credentials live in settings.db and intentionally omit these variables.",
    ),
    (
        re.compile(
            r"^LOCALAPI_(?:SQL_DSN|SQLITE_PATH|ADMIN_USERNAME)$"
        ),
        "Advanced LocalAPI provisioner overrides; CE launchers and the self-hosted compose "
        "supply managed SQLite values, so these are intentionally absent from the public template.",
    ),
    (
        re.compile(
            r"^(?:ST_CONTROL_PLANE_DSN|ST_REDIS_URL|ST_TASK_BACKEND|ST_COOKIE_SECURE|ST_WORKER_.*)$"
        ),
        "EE/control-plane env read by shared CE code but intentionally absent from CE template.",
    ),
    (
        re.compile(r"^(?:BLOCK_WORLD_.+|ST_SPLAT_TRANSFORM_BIN)$"),
        "Optional world/3DGS feature override with code default/fallback "
        "(director_world block_world_builder falls back to MODEL_* keys; "
        "stage_asset resolves splat-transform binary by default); not required in base template.",
    ),
    # [aipdd fork delta] fork 引入的内部/调优类 env：均有代码默认值或探测性兜底，
    # 不进公开模板（避免把内部实现细节变成运维契约）。
    (
        re.compile(r"^AIPDD_VIDEO_API_HOST_HINT$"),
        "Internal video API host hint probed by the fork video_generator protocol "
        "detection (configurable, but fork-internal routing detail), not operator config.",
    ),
    (
        re.compile(r"^MINIMAX_VIDEO_API_HOST_HINT$"),
        "Internal MiniMax V2 video host hint probed by the same fork protocol "
        "detection (configurable, but fork-internal routing detail), not operator config.",
    ),
    (
        re.compile(r"^DB_ENCRYPTION_KEY$"),
        "Reserved AES-GCM field-encryption key read by utils/crypto.py; the fork has no "
        "wired caller yet, so it is not part of the operator template contract.",
    ),
    (
        re.compile(r"^NOVELVIDEO_PROJECT_ROOT$"),
        "World-install source-root override with code-side path probing fallback "
        "(world_install._project_root), internal layout detail rather than operator config.",
    ),
    (
        re.compile(r"^SCENE_HEADER_AUTOGEN_MAX_ATTEMPTS$"),
        "Scene-header autogen retry tuning (default 4 in utils/scene_header_autogen.py); "
        "internal tuning knob with code default, not required in base template.",
    ),
    (
        re.compile(r"^DOUYIN_CDP_(?:BIN|PORT|PROFILE)$"),
        "[aipdd fork delta] Host-side helper env read only by "
        "scripts/douyin-cdp-chrome.sh, which launches the customer's already installed Chrome "
        "with --remote-debugging-port for Douyin copywriting extraction. The application "
        "contract is ST_CHROME_CDP_URL, so these launcher knobs are not operator template keys.",
    ),
    (
        re.compile(r"^DOUYIN_WEB_COOKIE$"),
        "[aipdd fork delta] Douyin web login Cookie read by freezone/douyin_web_api.py only as "
        "the non-CE fallback: under CE the single source of truth is settings.db (saved from "
        "the Settings dialog's Douyin Cookie page), so the credential is intentionally kept out "
        "of the public template to avoid an env/DB dual source.",
    ),
    (
        re.compile(r"^VECTCUT_.*$"),
        "[aipdd fork delta] Optional VectCut editing service env (services/vectcut): the "
        "service is standalone (scripts/start-vectcut.sh / compose vectcut profile) and "
        "documents its env in services/vectcut/README-AIPDD.md, not the public template.",
    ),
    (
        re.compile(r"^WECHAT_(?:APPID|SECRET)$"),
        "[aipdd fork delta] Optional WeChat Official Account publish credentials read by "
        "gzh/publish.py only as the lowest-priority fallback (config.json > "
        "~/.wechat_publish.json > env). The operator contract is the project config.json / "
        "settings, so these per-deployment secrets stay out of the public template to avoid "
        "an env/config dual source.",
    ),
    # [aipdd fork delta] 本地 Agent 工具 / 桌面形态的进程级 env：由 compose、桌面
    # sidecar 或 OS 外壳注入，均有代码默认值或自动探测兜底，不是运维要填的模板键。
    (
        re.compile(r"^AIPDD_DRAMA_MCP_(?:CONTAINER|TRANSPORT)$"),
        "[aipdd fork delta] Local Agent tool bridge env read by agent/local_tools.py: "
        "compose injects the docker-exec container name and the transport defaults to "
        "/.dockerenv auto-detection, so operators do not set these in the template.",
    ),
    (
        re.compile(r"^APP_RESOURCE_PATH$"),
        "[aipdd fork delta] Bundled desktop resource root injected by the Tauri sidecar "
        "(desktop/src-tauri/src/env.rs) and probed by freezone/motion_render/chrome.py; "
        "absent in the web form, so it is not part of the public template contract.",
    ),
    (
        re.compile(r"^AGENT_BUILTIN_SKILLS_DIR$"),
        "[aipdd fork delta] Builtin agent skills directory override read by agent/skills.py "
        "with a code-side bundled-path fallback, so it is an internal override rather than "
        "operator configuration.",
    ),
    (
        re.compile(r"^(?:COMSPEC|LOCALAPPDATA|PYTHONPATH)$"),
        "OS/shell-provided process env (Windows command interpreter, per-user app data, "
        "interpreter import path) forwarded to child processes by agent/mcp.py and "
        "chat/hermes_pool.py; supplied by the host, never part of the app template.",
    ),
    (
        re.compile(r"^(?:HTTP_PROXY|HTTPS_PROXY)$"),
        "Standard OS/CI proxy env forwarded by the faceswap installers "
        "(faceswap_install.py / model_download.py) so downloads honor a corporate proxy; "
        "provided by the environment, not a template key.",
    ),
    (
        re.compile(r"^LOCALAPI_(?:API_KEY|BASE_URL)$"),
        "Self-hosted gateway credentials read by shared CE/EE code and the subprocess "
        "allowlist; CE dynamic credentials live in settings.db, so like LOCALAPI_* these "
        "variables are intentionally omitted from the public template.",
    ),
)
SUPERTALE_REVERSE_ENV_ALLOWLIST: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            r"^(?:BASH_SOURCE|API_HOST|API_PORT|API_LOG|CELERY_APP|CELERY_BIN|CELERY_CONCURRENCY|"
            r"CELERY_LOG|CELERY_LOGLEVEL|CELERY_PING_TIMEOUT|CELERY_PROC_PATTERN|CELERY_QUEUES|"
            r"CE_APP|CE_PORT|CE_START_TIMEOUT|CE_WHEEL_PORT|DATA_ROOT|DEV_RUN_DIR|DIST_DIR|"
            r"EE_PORT|FE_DIR|FE_PID_FILE|FORCE_FRESH|LOG_FILE|PID_FILE|PROJECT_ROOT|"
            r"PROXY_LOG_FILE|PROXY_PID_FILE|PROXY_PORT|PROXY_TARGET_FILE|PYTHON_PATH|"
            r"ROOT_DIR|RUN_DIR|SCRIPT_DIR|SMOKE_LOG|STAMP_FILE|START_TIMEOUT|"
            r"WHEEL_CURRENT|WITH_DEPS|WITH_FE|WORKER_ID)$"
        ),
        "Shell-local variable assigned inside SuperTale2 startup/dev scripts, not external env config.",
    ),
    (
        re.compile(
            r"^(?:M(?:02|03|04|06|07|09)_.*|DEFAULT_EE_USERNAME|DEFAULT_EE_PASSWORD|"
            r"EXPECTED_RATCHET|GRANT_ID|MIN_PASSED|POOL_ID|PROBE_ID|SHAPES_FILE|"
            r"ST_BASELINE_.*)$"
        ),
        "Acceptance/smoke scratch env used only by bounded verification scripts.",
    ),
    (
        re.compile(r"^GRAPH_DATABASE_(?:URL|USERNAME|PASSWORD)$"),
        "One-off character-tag maintenance script Neo4j override, not runtime operator config.",
    ),
    (
        re.compile(r"^(?:ST_DEV_.*|ST_SMOKE_(?:BASE|LOG_FILE)|ST_TAIL_.*|TAIL_.*|TARGETED_TESTS|VENV_DIR)$"),
        "Local dc/smoke developer-tool knob, not deployed runtime config.",
    ),
    (
        re.compile(r"^SUPERTALE2_.*$"),
        "Incident drill client env for local/manual probes, not service runtime config.",
    ),
    (
        re.compile(r"^ST_CELERY_(?:APP|BIN|LOGLEVEL|PING_TIMEOUT|PROC_PATTERN)$"),
        "Celery launcher implementation override used by local scripts, not service settings.",
    ),
)


@dataclass(frozen=True)
class TemplateFinding:
    template: str
    key: str
    reason: str

    def describe(self) -> str:
        return f"{self.template}: {self.key} ({self.reason})"


@dataclass(frozen=True)
class EnvReport:
    label: str
    template_keys: dict[str, set[str]]
    documented_template_keys: dict[str, set[str]]
    static_keys: set[str]
    allowed_keys: dict[str, str]
    dead_findings: list[TemplateFinding]
    allowed_missing_keys: dict[str, str]
    missing_findings: list[TemplateFinding]

    @property
    def dead_keys(self) -> set[str]:
        return {finding.key for finding in self.dead_findings}

    @property
    def missing_keys(self) -> set[str]:
        return {finding.key for finding in self.missing_findings}

    @property
    def ok(self) -> bool:
        return (
            len(self.dead_findings) <= BASELINE_DEAD_KEYS
            and len(self.missing_findings) <= BASELINE_MISSING_KEYS
        )


@dataclass(frozen=True)
class EnvReaderFunction:
    positional_params: tuple[str, ...]
    reader_params: frozenset[str]
    vararg_param: str | None = None


@dataclass(frozen=True)
class EnvReaderCall:
    function: EnvReaderFunction
    bound_receiver: bool = False


@dataclass(frozen=True)
class EnvFunctionDef:
    key: str
    function: ast.FunctionDef | ast.AsyncFunctionDef
    class_name: str | None = None
    os_aliases: frozenset[str] = frozenset()
    environ_aliases: frozenset[str] = frozenset()
    getenv_aliases: frozenset[str] = frozenset()


def _read_template_keys(path: Path, root: Path) -> tuple[str, set[str]]:
    if not path.exists():
        return (path.relative_to(root).as_posix(), set())
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = ENV_KEY_RE.match(line)
        if match:
            keys.add(match.group(1))
    return (path.relative_to(root).as_posix(), keys)


def _read_documented_template_keys(path: Path, root: Path) -> tuple[str, set[str]]:
    if not path.exists():
        return (path.relative_to(root).as_posix(), set())
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = TEMPLATE_ENV_KEY_RE.match(line)
        if match:
            keys.add(match.group(1))
    return (path.relative_to(root).as_posix(), keys)


def _is_config_key_like(key: str) -> bool:
    return bool(CONFIG_KEY_RE.match(key))


def _iter_scan_files(roots: list[Path]) -> list[Path]:
    files: list[Path] = []
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            files.append(root)
            continue
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or ".venv" in path.parts:
                continue
            files.append(path)
    return sorted(set(files))


def _constant_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


def _import_aliases(tree: ast.AST) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    os_aliases = {"os"}
    environ_aliases: set[str] = set()
    getenv_aliases: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os":
                    os_aliases.add(alias.asname or alias.name)
        elif isinstance(node, ast.ImportFrom) and node.module == "os":
            for alias in node.names:
                imported_as = alias.asname or alias.name
                if alias.name == "environ":
                    environ_aliases.add(imported_as)
                elif alias.name == "getenv":
                    getenv_aliases.add(imported_as)
    return frozenset(os_aliases), frozenset(environ_aliases), frozenset(getenv_aliases)


def _is_os_environ(
    node: ast.AST,
    os_aliases: frozenset[str],
    environ_aliases: frozenset[str],
) -> bool:
    if isinstance(node, ast.Name) and node.id in environ_aliases:
        return True
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "environ"
        and isinstance(node.value, ast.Name)
        and node.value.id in os_aliases
    )


def _is_os_getenv_call(
    node: ast.Call,
    os_aliases: frozenset[str],
    getenv_aliases: frozenset[str],
) -> bool:
    if isinstance(node.func, ast.Name) and node.func.id in getenv_aliases:
        return True
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr == "getenv"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id in os_aliases
    )


def _is_os_environ_method_call(
    node: ast.Call,
    method_names: set[str],
    os_aliases: frozenset[str],
    environ_aliases: frozenset[str],
) -> bool:
    return (
        isinstance(node.func, ast.Attribute)
        and node.func.attr in method_names
        and _is_os_environ(node.func.value, os_aliases, environ_aliases)
    )


def _reader_function_call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    return None


def _reader_function_for_call(
    node: ast.AST,
    reader_functions: dict[str, EnvReaderFunction],
    current_class: str | None,
) -> EnvReaderCall | None:
    bare_name = _reader_function_call_name(node)
    if bare_name is not None:
        function = reader_functions.get(bare_name)
        return EnvReaderCall(function) if function is not None else None
    if (
        current_class is not None
        and isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in {"self", "cls"}
    ):
        function = reader_functions.get(f"{current_class}.{node.attr}")
        return EnvReaderCall(function, bound_receiver=True) if function is not None else None
    return None


def _reader_positional_param(
    call: EnvReaderCall,
    index: int,
) -> str | None:
    callee = call.function
    positional_index = index
    if (
        call.bound_receiver
        and callee.positional_params
        and callee.positional_params[0] in {"self", "cls"}
    ):
        positional_index += 1
    if positional_index < len(callee.positional_params):
        return callee.positional_params[positional_index]
    return callee.vararg_param


def _iter_function_defs(tree: ast.AST) -> list[EnvFunctionDef]:
    os_aliases, environ_aliases, getenv_aliases = _import_aliases(tree)
    functions: list[EnvFunctionDef] = []
    for node in getattr(tree, "body", []):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions.append(
                EnvFunctionDef(
                    key=node.name,
                    function=node,
                    os_aliases=os_aliases,
                    environ_aliases=environ_aliases,
                    getenv_aliases=getenv_aliases,
                )
            )
        elif isinstance(node, ast.ClassDef):
            for member in node.body:
                if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    functions.append(
                        EnvFunctionDef(
                            key=f"{node.name}.{member.name}",
                            function=member,
                            class_name=node.name,
                            os_aliases=os_aliases,
                            environ_aliases=environ_aliases,
                            getenv_aliases=getenv_aliases,
                        )
                    )
    return functions


def _direct_env_name_arg(
    node: ast.Call,
    os_aliases: frozenset[str],
    environ_aliases: frozenset[str],
    getenv_aliases: frozenset[str],
) -> ast.AST | None:
    if not node.args:
        return None
    if _is_os_getenv_call(node, os_aliases, getenv_aliases) or _is_os_environ_method_call(
        node,
        {"get", "setdefault"},
        os_aliases,
        environ_aliases,
    ):
        return node.args[0]
    return None


def _env_reader_params_for_function(
    function: ast.FunctionDef | ast.AsyncFunctionDef,
    reader_functions: dict[str, EnvReaderFunction],
    current_class: str | None,
    os_aliases: frozenset[str],
    environ_aliases: frozenset[str],
    getenv_aliases: frozenset[str],
) -> set[str]:
    positional_params = tuple(arg.arg for arg in function.args.posonlyargs + function.args.args)
    keyword_params = positional_params + tuple(arg.arg for arg in function.args.kwonlyargs)
    params = set(keyword_params)
    if function.args.vararg is not None:
        params.add(function.args.vararg.arg)
    loop_aliases: dict[str, str] = {}
    for node in ast.walk(function):
        if (
            isinstance(node, ast.For)
            and isinstance(node.target, ast.Name)
            and isinstance(node.iter, ast.Name)
            and node.iter.id in params
        ):
            loop_aliases[node.target.id] = node.iter.id

    reader_params: set[str] = set()
    for node in ast.walk(function):
        if isinstance(node, ast.Call):
            direct_arg = _direct_env_name_arg(
                node,
                os_aliases,
                environ_aliases,
                getenv_aliases,
            )
            if isinstance(direct_arg, ast.Name) and direct_arg.id in params:
                reader_params.add(direct_arg.id)
            elif isinstance(direct_arg, ast.Name) and direct_arg.id in loop_aliases:
                reader_params.add(loop_aliases[direct_arg.id])

            reader_call = _reader_function_for_call(node.func, reader_functions, current_class)
            if reader_call is None:
                continue
            for index, arg in enumerate(node.args):
                callee_param = _reader_positional_param(reader_call, index)
                if callee_param is None:
                    continue
                if callee_param not in reader_call.function.reader_params:
                    continue
                if isinstance(arg, ast.Name) and arg.id in params:
                    reader_params.add(arg.id)
                elif isinstance(arg, ast.Name) and arg.id in loop_aliases:
                    reader_params.add(loop_aliases[arg.id])
            for keyword in node.keywords:
                if keyword.arg not in reader_call.function.reader_params:
                    continue
                if isinstance(keyword.value, ast.Name) and keyword.value.id in params:
                    reader_params.add(keyword.value.id)
                elif isinstance(keyword.value, ast.Name) and keyword.value.id in loop_aliases:
                    reader_params.add(loop_aliases[keyword.value.id])
        elif isinstance(node, ast.Subscript) and _is_os_environ(
            node.value,
            os_aliases,
            environ_aliases,
        ):
            if isinstance(node.slice, ast.Name) and node.slice.id in params:
                reader_params.add(node.slice.id)
            elif isinstance(node.slice, ast.Name) and node.slice.id in loop_aliases:
                reader_params.add(loop_aliases[node.slice.id])
        elif isinstance(node, ast.Compare) and isinstance(node.left, ast.Name):
            if node.left.id in params and any(isinstance(op, ast.In) for op in node.ops):
                if any(
                    _is_os_environ(comparator, os_aliases, environ_aliases)
                    for comparator in node.comparators
                ):
                    reader_params.add(node.left.id)
            elif node.left.id in loop_aliases and any(isinstance(op, ast.In) for op in node.ops):
                if any(
                    _is_os_environ(comparator, os_aliases, environ_aliases)
                    for comparator in node.comparators
                ):
                    reader_params.add(loop_aliases[node.left.id])
    return reader_params


def _collect_env_reader_functions(py_files: list[Path]) -> dict[str, EnvReaderFunction]:
    functions: list[EnvFunctionDef] = []
    for path in py_files:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            continue
        functions.extend(_iter_function_defs(tree))

    reader_functions: dict[str, EnvReaderFunction] = {}
    for entry in functions:
        function = entry.function
        if entry.key in reader_functions:
            continue
        reader_functions[entry.key] = EnvReaderFunction(
            positional_params=tuple(
                arg.arg for arg in function.args.posonlyargs + function.args.args
            ),
            reader_params=frozenset(),
            vararg_param=function.args.vararg.arg if function.args.vararg else None,
        )

    changed = True
    while changed:
        changed = False
        next_reader_params: dict[str, set[str]] = {
            name: set(function.reader_params)
            for name, function in reader_functions.items()
        }
        for entry in functions:
            next_reader_params[entry.key].update(
                _env_reader_params_for_function(
                    entry.function,
                    reader_functions,
                    entry.class_name,
                    entry.os_aliases,
                    entry.environ_aliases,
                    entry.getenv_aliases,
                )
            )
        for name, reader_params in next_reader_params.items():
            existing = reader_functions[name]
            merged = frozenset(reader_params)
            if merged != existing.reader_params:
                reader_functions[name] = EnvReaderFunction(
                    positional_params=existing.positional_params,
                    reader_params=merged,
                    vararg_param=existing.vararg_param,
                )
                changed = True

    return {
        name: function
        for name, function in reader_functions.items()
        if function.reader_params
    }


def _literal_iter_values(node: ast.AST) -> set[str]:
    if not isinstance(node, (ast.Tuple, ast.List, ast.Set)):
        return set()
    values = {_constant_string(element) for element in node.elts}
    return {value for value in values if value}


class _PythonEnvKeyVisitor(ast.NodeVisitor):
    def __init__(
        self,
        reader_functions: dict[str, EnvReaderFunction],
        os_aliases: frozenset[str],
        environ_aliases: frozenset[str],
        getenv_aliases: frozenset[str],
    ) -> None:
        self.keys: set[str] = set()
        self.reader_functions = reader_functions
        self._literal_loop_aliases: list[dict[str, set[str]]] = []
        self._literal_name_scopes: list[dict[str, set[str]]] = [{}]
        self._class_stack: list[str] = []
        self.os_aliases = os_aliases
        self.environ_aliases = environ_aliases
        self.getenv_aliases = getenv_aliases

    @property
    def _current_class(self) -> str | None:
        return self._class_stack[-1] if self._class_stack else None

    def _alias_values(self, name: str) -> set[str]:
        for aliases in reversed(self._literal_loop_aliases):
            values = aliases.get(name)
            if values is not None:
                return values
        for aliases in reversed(self._literal_name_scopes):
            values = aliases.get(name)
            if values is not None:
                return values
        return set()

    def _add_key_or_alias(self, node: ast.AST) -> None:
        key = _constant_string(node)
        if key:
            self.keys.add(key)
        elif isinstance(node, ast.Name):
            self.keys.update(self._alias_values(node.id))

    def _visit_for(self, node: ast.For | ast.AsyncFor) -> None:
        values = _literal_iter_values(node.iter)
        if isinstance(node.target, ast.Name) and values:
            self.visit(node.target)
            self.visit(node.iter)
            self._literal_loop_aliases.append({node.target.id: values})
            for child in node.body:
                self.visit(child)
            self._literal_loop_aliases.pop()
            for child in node.orelse:
                self.visit(child)
            return
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._class_stack.append(node.name)
        self._literal_name_scopes.append({})
        self.generic_visit(node)
        self._literal_name_scopes.pop()
        self._class_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._literal_name_scopes.append({})
        self.generic_visit(node)
        self._literal_name_scopes.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._literal_name_scopes.append({})
        self.generic_visit(node)
        self._literal_name_scopes.pop()

    def visit_Assign(self, node: ast.Assign) -> None:
        values = _literal_iter_values(node.value)
        constant_value = _constant_string(node.value)
        if constant_value:
            values = {constant_value}
        for target in node.targets:
            if isinstance(target, ast.Name):
                if values:
                    self._literal_name_scopes[-1][target.id] = values
                else:
                    self._literal_name_scopes[-1].pop(target.id, None)
        self.generic_visit(node)

    def visit_For(self, node: ast.For) -> None:
        self._visit_for(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_for(node)

    def visit_Call(self, node: ast.Call) -> None:
        direct_arg = _direct_env_name_arg(
            node,
            self.os_aliases,
            self.environ_aliases,
            self.getenv_aliases,
        )
        if direct_arg is not None:
            self._add_key_or_alias(direct_arg)
        reader_call = _reader_function_for_call(
            node.func,
            self.reader_functions,
            self._current_class,
        )
        if reader_call is not None:
            for index, arg in enumerate(node.args):
                callee_param = _reader_positional_param(reader_call, index)
                if callee_param is None:
                    continue
                if callee_param in reader_call.function.reader_params:
                    self._add_key_or_alias(arg)
            for keyword in node.keywords:
                if keyword.arg in reader_call.function.reader_params:
                    self._add_key_or_alias(keyword.value)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:
        if _is_os_environ(
            node.value,
            self.os_aliases,
            self.environ_aliases,
        ) and not isinstance(node.ctx, ast.Store):
            self._add_key_or_alias(node.slice)
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        if any(isinstance(op, ast.In) for op in node.ops):
            if any(
                _is_os_environ(comparator, self.os_aliases, self.environ_aliases)
                for comparator in node.comparators
            ):
                self._add_key_or_alias(node.left)
        self.generic_visit(node)


def _python_env_keys(path: Path, reader_functions: dict[str, EnvReaderFunction]) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return set()

    os_aliases, environ_aliases, getenv_aliases = _import_aliases(tree)
    visitor = _PythonEnvKeyVisitor(
        reader_functions,
        os_aliases,
        environ_aliases,
        getenv_aliases,
    )
    visitor.visit(tree)
    return visitor.keys


def _script_env_keys(path: Path) -> set[str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        return set()

    keys: set[str] = set()
    for raw_line in lines:
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        for match in SHELL_ENV_REF_RE.finditer(line.split("#", 1)[0]):
            key = match.group("braced") or match.group("plain")
            if _is_config_key_like(key):
                keys.add(key)
    return keys


def collect_static_env_keys(scan_roots: list[Path]) -> set[str]:
    keys: set[str] = set()
    files = _iter_scan_files(scan_roots)
    py_files = [path for path in files if path.suffix == ".py"]
    reader_functions = _collect_env_reader_functions(py_files)
    for path in files:
        if path.suffix == ".py":
            keys.update(_python_env_keys(path, reader_functions))
        else:
            keys.update(_script_env_keys(path))
    return keys


def _allowlist_reason(key: str) -> str | None:
    for pattern, reason in DYNAMIC_ENV_ALLOWLIST + THIRD_PARTY_ENV_ALLOWLIST:
        if pattern.match(key):
            return reason
    return None


def _missing_allowlist_reason(
    key: str,
    reverse_allowlist: tuple[tuple[re.Pattern[str], str], ...],
) -> str | None:
    for pattern, reason in (
        THIRD_PARTY_ENV_ALLOWLIST + COMMON_REVERSE_ENV_ALLOWLIST + reverse_allowlist
    ):
        if pattern.match(key):
            return reason
    return None


def analyze_templates(
    *,
    label: str,
    root: Path,
    template_paths: list[Path],
    scan_roots: list[Path],
    reverse_allowlist: tuple[tuple[re.Pattern[str], str], ...] = (),
) -> EnvReport:
    root = root.resolve()
    template_keys = dict(_read_template_keys(path.resolve(), root) for path in template_paths)
    documented_template_keys = dict(
        _read_documented_template_keys(path.resolve(), root) for path in template_paths
    )
    static_keys = collect_static_env_keys([path.resolve() for path in scan_roots])
    allowed_keys: dict[str, str] = {}
    dead_findings: list[TemplateFinding] = []
    allowed_missing_keys: dict[str, str] = {}
    missing_findings: list[TemplateFinding] = []
    all_documented_template_keys = (
        set().union(*documented_template_keys.values()) if documented_template_keys else set()
    )

    for template, keys in sorted(template_keys.items()):
        for key in sorted(keys):
            if key in static_keys:
                continue
            reason = _allowlist_reason(key)
            if reason:
                allowed_keys[key] = reason
                continue
            dead_findings.append(
                TemplateFinding(template=template, key=key, reason="no runtime read")
            )

    for key in sorted(static_keys):
        if key in all_documented_template_keys:
            continue
        reason = _missing_allowlist_reason(key, reverse_allowlist)
        if reason:
            allowed_missing_keys[key] = reason
            continue
        missing_findings.append(
            TemplateFinding(
                template="<runtime>",
                key=key,
                reason="runtime read missing from env template",
            )
        )

    return EnvReport(
        label=label,
        template_keys=template_keys,
        documented_template_keys=documented_template_keys,
        static_keys=static_keys,
        allowed_keys=allowed_keys,
        dead_findings=dead_findings,
        allowed_missing_keys=allowed_missing_keys,
        missing_findings=missing_findings,
    )


def analyze_ce_repo(root: Path) -> EnvReport:
    root = root.resolve()
    return analyze_templates(
        label="dramaclaw-ce",
        root=root,
        template_paths=[root / ".env.example"],
        scan_roots=[root / "src", root / "scripts"],
        reverse_allowlist=CE_REVERSE_ENV_ALLOWLIST,
    )


def analyze_supertale_repo(root: Path, ce_root: Path) -> EnvReport:
    root = root.resolve()
    ce_root = ce_root.resolve()
    return analyze_templates(
        label="SuperTale2",
        root=root,
        template_paths=[root / ".env.example", root / ".env.control-plane.example"],
        scan_roots=[root / "src", root / "scripts", ce_root / "src"],
        reverse_allowlist=SUPERTALE_REVERSE_ENV_ALLOWLIST,
    )


def _is_ce_root(path: Path) -> bool:
    return (path / "src" / "novelvideo").exists()


def _discover_ce_root(root: Path) -> Path | None:
    env_root = os.environ.get("DRAMACLAW_CE_ROOT", "").strip()
    candidates = []
    if env_root:
        candidates.append(Path(env_root).expanduser())
    candidates.extend(
        [
            root / "dc" / "dramaclaw-ce",
            root.parent / "dc" / "dramaclaw-ce",
            root.parent / "dramaclaw-ce",
        ]
    )
    for candidate in candidates:
        if _is_ce_root(candidate):
            return candidate
    return None


def _ce_root_error(root: Path) -> str:
    return (
        "SuperTale2 env audit requires dramaclaw-ce source for CE runtime keys. "
        "Pass --ce-root /path/to/dramaclaw-ce, set DRAMACLAW_CE_ROOT, "
        f"or checkout dramaclaw-ce under {root / 'dc' / 'dramaclaw-ce'}."
    )


def _print_report(report: EnvReport) -> None:
    total_keys = sum(len(keys) for keys in report.template_keys.values())
    print(
        "Env config ratchet: "
        f"{report.label}, dead_baseline={BASELINE_DEAD_KEYS}, "
        f"missing_baseline={BASELINE_MISSING_KEYS}, templates={len(report.template_keys)}, "
        f"keys={total_keys}, static_refs={len(report.static_keys)}, "
        f"allowlisted={len(report.allowed_keys)}, dead={len(report.dead_findings)}, "
        f"missing_allowlisted={len(report.allowed_missing_keys)}, "
        f"missing={len(report.missing_findings)}"
    )
    if report.allowed_keys:
        print("\nAllowlisted env keys:")
        for key, reason in sorted(report.allowed_keys.items()):
            print(f"  {key}: {reason}")
    if report.dead_findings:
        print("\nDead env template keys:")
        for finding in report.dead_findings:
            print(f"  {finding.describe()}")
    if report.allowed_missing_keys:
        print("\nAllowlisted missing env reads:")
        for key, reason in sorted(report.allowed_missing_keys.items()):
            print(f"  {key}: {reason}")
    if report.missing_findings:
        print("\nRuntime env reads missing from templates:")
        for finding in report.missing_findings:
            print(f"  {finding.describe()}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=REPO_ROOT)
    parser.add_argument("--ce-root", type=Path)
    parser.add_argument("--mode", choices=("auto", "ce", "supertale"), default="auto")
    args = parser.parse_args(argv)

    root = args.root.resolve()
    mode = args.mode
    if mode == "auto":
        mode = "supertale" if (root / ".env.control-plane.example").exists() else "ce"

    if mode == "ce":
        report = analyze_ce_repo(root)
    else:
        ce_root = args.ce_root.resolve() if args.ce_root else _discover_ce_root(root)
        if ce_root is None or not _is_ce_root(ce_root):
            print(_ce_root_error(root), file=sys.stderr)
            return 2
        report = analyze_supertale_repo(root, ce_root)

    _print_report(report)
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
