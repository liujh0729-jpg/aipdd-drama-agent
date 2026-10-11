"""Per-user Hermes workspace initialization.

Owns one job: idempotently materialize ``state/{user}/.hermes/`` to be a
working HERMES_HOME — with sandbox-friendly tmpdir, repo-pinned skill
softlinks, a starter config.yaml, and an empty compatibility .env file.

Kept separate from chat_service.py so the latter stays small. Designed to be
safe to call on every HermesPool.spawn() (cheap when already initialized).
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
from pathlib import Path

import yaml

_log = logging.getLogger(__name__)

DRAMACLAW_ROOT = Path(__file__).resolve().parents[3]
STATE_ROOT = DRAMACLAW_ROOT / "state"
# `vectcut` 是独立剪辑服务（services/vectcut）的调用技能，默认随 dramaclaw 一起分发。
DEFAULT_HERMES_SKILLS = {"dramaclaw", "vectcut"}
DEFAULT_HERMES_PLUGINS = {"dramaclaw"}
DEFAULT_HERMES_TOOLSETS = {"hermes-acp"}
_warned_repo_state_fallback = False


_DEFAULT_HERMES_MODEL = "DC-hermes-LLM"
_DRAMACLAW_HERMES_PROVIDER_NAME = "dramaclaw"
_DRAMACLAW_HERMES_PROVIDER = f"custom:{_DRAMACLAW_HERMES_PROVIDER_NAME}"
_DRAMACLAW_HERMES_KEY_ENV = "NEWAPI_API_KEY"
_DEFAULT_HERMES_MODEL_API_MODE = "chat_completions"
_DEFAULT_HERMES_MODEL_CONTEXT_LENGTH = "131072"

_CONFIG_YAML_TEMPLATE = """# AIPDD-managed hermes config.
# Toolset whitelist enforces L1 defense (no direct file write / shell).
#
# Edit with care; this file may be regenerated.
#
# Model routes through the selected NewAPI gateway (OpenAI-compatible), unified
# with the video/image generators. The endpoint is non-secret workspace config;
# AIPDD injects the key into the worker process as NEWAPI_API_KEY.

custom_providers:
  - name: dramaclaw
    base_url: {base_url}
    key_env: NEWAPI_API_KEY
    api_mode: {api_mode}

model:
  default: {model}
  provider: custom:dramaclaw
  context_length: {context_length}   # skip the slow cold-start context-length probe

enabled_toolsets:
  - hermes-acp         # Repo plugins exposed through ACP
  - memory             # hermes built-in cross-session memory

plugins:
  enabled:
    - dramaclaw

display:
  tool_progress: verbose
  tool_progress_command: true

# Tools disabled at L1 so a sandbox bypass is layered with "no tool to misuse":
disabled_toolsets:
  - bash
  - shell
  - terminal
  - subprocess
  - file_write
  - file_read         # We allow read by sandbox; disable agent-side tool too
  - edit
  - write
  - read
  - glob
  - grep
"""


_DEFAULT_ENV_TEMPLATE = """# AIPDD-managed Hermes workspace.
# Model credentials are injected into the worker process and are never written
# here. Do not duplicate model keys in this file.
"""


def _state_root() -> Path:
    configured = os.environ.get("NOVELVIDEO_STATE_DIR", "").strip()
    if configured:
        return Path(configured).expanduser()
    global _warned_repo_state_fallback
    if not _warned_repo_state_fallback:
        _warned_repo_state_fallback = True
        _log.warning(
            "NOVELVIDEO_STATE_DIR is not set; Hermes workspace falls back to %s",
            DRAMACLAW_ROOT / "state",
        )
    return DRAMACLAW_ROOT / "state"


def _root_value(*names: str) -> str:
    """Read the first non-empty value among ``names`` from root .env then env."""
    env_path = DRAMACLAW_ROOT / ".env"
    try:
        root_values = _parse_env_assignments(env_path.read_text(encoding="utf-8"))
    except OSError:
        root_values = {}
    for name in names:
        value = (root_values.get(name) or os.environ.get(name, "")).strip()
        if value:
            return value
    return ""


def _effective_newapi_gateway() -> tuple[str, str]:
    """Return effective NewAPI ``(api_key, base_url)`` for Hermes.

    CE resolves the UI-selected gateway from settings.db. EE has no CE settings
    database and resolves its deployment-level NEWAPI_API_KEY / NEWAPI_BASE_URL
    from the environment -- the address is deployment data, never a code
    constant ([aipdd fork delta req41]).
    """
    from novelvideo.model_gateway_settings import get_effective_newapi_config

    gateway = get_effective_newapi_config(
        official_api_key=os.environ.get("NEWAPI_API_KEY", ""),
    )
    return gateway.api_key, gateway.base_url


def _newapi_base_url() -> str:
    return _effective_newapi_gateway()[1]


def effective_gateway_fingerprint() -> str:
    """Return a non-secret fingerprint of the gateway used by new Hermes workers."""
    api_key, base_url = _effective_newapi_gateway()
    material = f"{base_url}\n{api_key}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def effective_gateway_credentials() -> tuple[str, str]:
    """Return the NewAPI credentials injected into a newly spawned worker."""
    return _effective_newapi_gateway()


def _hermes_model_default() -> str:
    """Hermes / 导演聊天的默认模型：只读「设置 → 模型与渠道」里 ``HERMES_MODEL`` 这一功能位。

    [aipdd fork delta] 以前从 root ``.env`` / 进程环境取 ``HERMES_MODEL``：设置页换了绑定，
    写进 worker ``config.yaml`` 的还是环境里那个旧名字，聊天的模型和面板显示的对不上。
    现在走同一条槽位解析链（槽位绑定 → 同类已绑位 → 渠道目录首项）；这是写配置文件的
    展示路径，解析不到就回内部别名，绝不在这上面抛 ``ModelSlotNotConfigured``。
    ``HERMES_MODEL_API_MODE`` / ``HERMES_MODEL_CONTEXT_LENGTH`` 仍是部署参数，照旧读 env。
    """
    try:
        from novelvideo.config import get_newapi_text_model_name

        resolved = str(
            get_newapi_text_model_name("HERMES_MODEL", _DEFAULT_HERMES_MODEL) or ""
        ).strip()
        if resolved:
            return resolved
    except Exception:  # noqa: BLE001 - 工作区初始化不能因为一个模型名解析失败而崩
        _log.debug("hermes model slot unresolved, using internal alias", exc_info=True)
    return _DEFAULT_HERMES_MODEL


def _hermes_model_api_mode() -> str:
    return _root_value("HERMES_MODEL_API_MODE") or _DEFAULT_HERMES_MODEL_API_MODE


def _hermes_model_context_length() -> str:
    raw = _root_value("HERMES_MODEL_CONTEXT_LENGTH")
    if not raw:
        return _DEFAULT_HERMES_MODEL_CONTEXT_LENGTH
    try:
        value = int(raw)
    except ValueError:
        _log.warning("invalid HERMES_MODEL_CONTEXT_LENGTH=%r, using default", raw)
        return _DEFAULT_HERMES_MODEL_CONTEXT_LENGTH
    return str(value) if value > 0 else _DEFAULT_HERMES_MODEL_CONTEXT_LENGTH


def _default_config_yaml() -> str:
    return _CONFIG_YAML_TEMPLATE.format(
        model=_hermes_model_default(),
        base_url=_newapi_base_url(),
        api_mode=_hermes_model_api_mode(),
        context_length=_hermes_model_context_length(),
    )

_DEFAULT_SOUL_MD = (
    "你是 AIPDD 画布。不要自称 Hermes Agent，不要提 Nous Research，"
    "也不要主动解释底层代理框架。自我介绍时只回答“我是 AIPDD 画布”，"
    "不要附加“AIPDD 的小说转视频创作助手”之类的头衔或职能描述。"
    "你应当直接、清晰、务实，优先帮助用户完成 "
    "AIPDD 项目进度查询、任务管理、剧本、配音、图片、视频生成与交付相关工作。\n"
)

# [aipdd fork delta] Previous brand text (虾导 era) kept verbatim so existing
# workspaces are migrated in place instead of re-seeded.
_PREV_SOUL_MD = (
    "你是虾导。不要自称 Hermes Agent，不要提 Nous Research，"
    "也不要主动解释底层代理框架。自我介绍时只回答“我是虾导”，"
    "不要附加“DramaClaw 的小说转视频创作助手”之类的头衔或职能描述。"
    "你应当直接、清晰、务实，优先帮助用户完成 "
    "DramaClaw 项目进度查询、任务管理、剧本、配音、图片、视频生成与交付相关工作。"
)

_DEFAULT_MEMORY_MD = """AIPDD 画布在 AIPDD 会话中面向用户自称“AIPDD 画布”，不要自称 Hermes Agent，不要提 Nous Research 或底层代理框架。自我介绍时只回答“我是 AIPDD 画布”，不要附加“AIPDD 的小说转视频创作助手”之类的头衔或职能描述。
§
AIPDD 管理的会话中 `terminal` 被禁用（在 config.yaml disabled_toolsets 中），curl 等 shell 命令会被直接拒绝。调用 AIPDD API 时应使用已启用的 `hermes-acp` toolset 中的 AIPDD 插件工具，不要用 curl。
"""

_OLD_SOUL_PREFIX = (
    "You are Hermes Agent, an intelligent AI assistant created by Nous Research. "
    "You are helpful, knowledgeable, and direct. You assist users with a wide range "
    "of tasks including answering questions, writing and editing code, analyzing "
    "information, creative work, and executing actions via your tools. You "
    "communicate clearly, admit uncertainty when appropriate, and prioritize being "
    "genuinely useful over being verbose unless otherwise directed below. Be targeted "
    "and efficient in your exploration and investigations."
)

_OLD_IDENTITY_MEMORY_LINE = (
    "虾导在 DramaClaw 会话中面向用户自称“虾导”，不要自称 Hermes Agent，"
    "不要提 Nous Research 或底层代理框架。用户问“你是谁 / 你叫什么 / "
    "你是什么助手 / 介绍一下你自己”时，直接回答“我是虾导，DramaClaw "
    "的小说转视频创作助手。”"
)

# [aipdd fork delta] 虾导 era text (previous default) for in-place migration.
_PREV_IDENTITY_MEMORY_LINE = (
    "虾导在 DramaClaw 会话中面向用户自称“虾导”，不要自称 Hermes Agent，"
    "不要提 Nous Research 或底层代理框架。自我介绍时只回答“我是虾导”，"
    "不要附加“DramaClaw 的小说转视频创作助手”之类的头衔或职能描述。"
)

_IDENTITY_MEMORY_LINE = (
    "AIPDD 画布在 AIPDD 会话中面向用户自称“AIPDD 画布”，不要自称 Hermes Agent，"
    "不要提 Nous Research 或底层代理框架。自我介绍时只回答“我是 AIPDD 画布”，"
    "不要附加“AIPDD 的小说转视频创作助手”之类的头衔或职能描述。"
)

_OLD_MEMORY_LINE = (
    "DramaClaw 管理的 Hermes 会话中 `terminal` 被禁用（在 config.yaml "
    "disabled_toolsets 中），curl 等 shell 命令会被直接拒绝。调用 DramaClaw API "
    "时应使用已启用的 `dramaclaw` 插件 toolset 提供的内置 HTTP 工具，不要用 curl。"
)

# [aipdd fork delta] 虾导 era text (previous default) for in-place migration.
_PREV_MEMORY_LINE = (
    "DramaClaw 管理的虾导会话中 `terminal` 被禁用（在 config.yaml "
    "disabled_toolsets 中），curl 等 shell 命令会被直接拒绝。调用 DramaClaw API "
    "时应使用已启用的 `hermes-acp` toolset 中的 DramaClaw 插件工具，不要用 curl。"
)

_NEW_MEMORY_LINE = (
    "AIPDD 管理的会话中 `terminal` 被禁用（在 config.yaml "
    "disabled_toolsets 中），curl 等 shell 命令会被直接拒绝。调用 AIPDD API "
    "时应使用已启用的 `hermes-acp` toolset 中的 AIPDD 插件工具，不要用 curl。"
)

_OLD_SOUL_IDENTITY_TEXT = (
    "你是虾导，DramaClaw 的小说转视频创作助手。用户问“你是谁 / 你叫什么 / "
    "你是什么助手 / 介绍一下你自己”时，直接回答“我是虾导，"
    "DramaClaw 的小说转视频创作助手。”"
)


def ensure_user_hermes_workspace(username: str) -> Path:
    """Create / refresh per-user HERMES_HOME. Idempotent and cheap.

    Layout under ``state/{username}/.hermes/``:
        config.yaml         L1 toolset whitelist (overwritten only if missing)
        .env                compatibility file (model credentials are not stored)
        tmp/                per-user TMPDIR (sandbox writable)
        skills/
            _user/          per-user / hermes-learned skills (writable)
            <name>/         softlink → repo .hermes/skills/<name>

    Returns the HERMES_HOME path (caller passes as ``HERMES_HOME`` env var).
    """
    home = _state_root() / username / ".hermes"
    home.mkdir(parents=True, exist_ok=True)
    try:
        home.chmod(0o700)
    except OSError:
        pass  # filesystem may not support (e.g. some mounts)

    # per-user TMPDIR (sandbox profile only allows write here)
    tmp_dir = home / "tmp"
    tmp_dir.mkdir(exist_ok=True)
    try:
        tmp_dir.chmod(0o700)
    except OSError:
        pass

    # skills layout
    skills_dir = home / "skills"
    skills_dir.mkdir(exist_ok=True)
    (skills_dir / "_user").mkdir(exist_ok=True)
    _materialize_skill_links(skills_dir)

    # plugins layout
    plugins_dir = home / "plugins"
    plugins_dir.mkdir(exist_ok=True)
    _materialize_plugin_links(plugins_dir)

    # hermes config (only write if missing — user may have customized)
    config_yaml = home / "config.yaml"
    if not config_yaml.exists():
        config_yaml.write_text(_default_config_yaml(), encoding="utf-8")
    _ensure_default_plugin_enabled(config_yaml)
    _ensure_default_toolsets_enabled(config_yaml)
    _ensure_model_config_from_env(config_yaml)
    _ensure_model_gateway_config(config_yaml)
    _ensure_identity_context(home)

    # Keep an empty compatibility file for Hermes. Model credentials are
    # injected only into each worker process.
    env_file = home / ".env"
    if not env_file.exists():
        env_file.write_text(_DEFAULT_ENV_TEMPLATE, encoding="utf-8")
        try:
            env_file.chmod(0o600)
        except OSError:
            pass

    return home


def _parse_env_assignments(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key.startswith("export "):
            key = key.removeprefix("export ").strip()
        if key:
            values[key] = value.strip().strip('"').strip("'")
    return values


def _ensure_identity_context(home: Path) -> None:
    """Keep user-visible assistant identity consistent across all workspaces."""
    soul_file = home / "SOUL.md"
    try:
        if soul_file.exists():
            text = soul_file.read_text(encoding="utf-8")
            if _OLD_SOUL_PREFIX in text:
                text = text.replace(_OLD_SOUL_PREFIX, _DEFAULT_SOUL_MD.strip(), 1)
            elif _PREV_SOUL_MD in text:
                # [aipdd fork delta] 虾导 era text → AIPDD brand, in place.
                text = text.replace(_PREV_SOUL_MD, _DEFAULT_SOUL_MD.strip(), 1)
            elif "你是 AIPDD 画布" not in text:
                text = _DEFAULT_SOUL_MD.rstrip() + "\n\n" + text
            text = text.replace(_OLD_SOUL_IDENTITY_TEXT, "你是 AIPDD 画布。")
            soul_file.write_text(text.rstrip() + "\n", encoding="utf-8")
        else:
            soul_file.write_text(_DEFAULT_SOUL_MD, encoding="utf-8")
    except OSError:
        _log.warning("failed to ensure hermes SOUL.md at %s", soul_file)

    memories_dir = home / "memories"
    try:
        memories_dir.mkdir(exist_ok=True)
        memory_file = memories_dir / "MEMORY.md"
        if memory_file.exists():
            text = memory_file.read_text(encoding="utf-8")
            text = text.replace(_OLD_IDENTITY_MEMORY_LINE, _IDENTITY_MEMORY_LINE)
            text = text.replace(_PREV_IDENTITY_MEMORY_LINE, _IDENTITY_MEMORY_LINE)
            text = text.replace(_OLD_MEMORY_LINE, _NEW_MEMORY_LINE)
            text = text.replace(_PREV_MEMORY_LINE, _NEW_MEMORY_LINE)
            if _IDENTITY_MEMORY_LINE not in text:
                text = _IDENTITY_MEMORY_LINE + "\n§\n" + text.lstrip()
            memory_file.write_text(text.rstrip() + "\n", encoding="utf-8")
        else:
            memory_file.write_text(_DEFAULT_MEMORY_MD, encoding="utf-8")
    except OSError:
        _log.warning("failed to ensure hermes MEMORY.md under %s", memories_dir)


def _materialize_skill_links(skills_dir: Path) -> None:
    """Create / refresh symlinks from skills_dir/<name> → repo-pinned skills.

    The source of truth is ``DramaClaw/.hermes/skills/`` so a fresh checkout
    has the same Hermes skills on every machine.

    Idempotent: stale links to dirs that no longer exist in the source are
    removed; new skills are added; existing real directories are left alone.
    """
    src_skills = DRAMACLAW_ROOT / ".hermes" / "skills"
    if not src_skills.is_dir():
        _log.info(
            "hermes skills source not found at %s — skipping skill links",
            src_skills,
        )
        return

    allowed = {
        name.strip()
        for name in os.environ.get(
            "ST_HERMES_SKILLS",
            ",".join(sorted(DEFAULT_HERMES_SKILLS)),
        ).split(",")
        if name.strip()
    }
    want = {
        p.name: p.resolve()
        for p in src_skills.iterdir()
        if p.is_dir() and (not allowed or p.name in allowed)
    }

    # Add / refresh links
    for name, target in want.items():
        if name.startswith("_"):
            continue  # reserve `_user` for hermes-learned
        link = skills_dir / name
        if link.is_symlink():
            try:
                if link.resolve() == target:
                    continue
                link.unlink()  # stale → recreate
            except OSError:
                continue
        elif link.exists():
            # User-installed real dir with same name; do not clobber.
            _log.warning(
                "skill name collision at %s (not a symlink); leaving as-is",
                link,
            )
            continue
        try:
            link.symlink_to(target)
        except OSError as e:
            _log.warning("failed to link %s → %s: %s", link, target, e)

    # Remove stale symlinks (skill removed from repo mirror)
    for entry in skills_dir.iterdir():
        if entry.name == "_user" or not entry.is_symlink():
            continue
        if entry.name not in want:
            try:
                entry.unlink()
            except OSError:
                pass


def _ensure_default_plugin_enabled(config_yaml: Path) -> None:
    """Non-destructively add the repo default plugin block to legacy configs."""
    try:
        text = config_yaml.read_text(encoding="utf-8")
    except OSError:
        return
    if "plugins:" in text:
        return
    plugin_names = "\n".join(f"    - {name}" for name in sorted(DEFAULT_HERMES_PLUGINS))
    addition = f"\nplugins:\n  enabled:\n{plugin_names}\n"
    try:
        config_yaml.write_text(text.rstrip() + addition + "\n", encoding="utf-8")
    except OSError:
        return


def _ensure_default_toolsets_enabled(config_yaml: Path) -> None:
    """Non-destructively add repo default toolsets to legacy configs."""
    try:
        text = config_yaml.read_text(encoding="utf-8")
    except OSError:
        return
    original_text = text
    text = _migrate_acp_toolsets(text)
    missing = [
        name
        for name in sorted(DEFAULT_HERMES_TOOLSETS)
        if not re.search(rf"(?m)^  - {re.escape(name)}(?:\s*(?:#.*)?)?$", text)
    ]
    if not missing:
        if text == original_text:
            return
        try:
            config_yaml.write_text(text.rstrip() + "\n", encoding="utf-8")
        except OSError:
            return
        return
    if "enabled_toolsets:" not in text:
        addition = "enabled_toolsets:\n" + "".join(f"  - {name}\n" for name in missing)
        new_text = text.rstrip() + "\n\n" + addition
    else:
        new_text = re.sub(
            r"(?m)^enabled_toolsets:\s*$",
            lambda m: m.group(0) + "\n" + "".join(f"  - {name}\n" for name in missing).rstrip(),
            text,
            count=1,
        )
        if new_text == text:
            return
    try:
        config_yaml.write_text(new_text.rstrip() + "\n", encoding="utf-8")
    except OSError:
        return


def _migrate_acp_toolsets(text: str) -> str:
    """Collapse legacy plugin-specific toolsets into the ACP toolset."""
    if "enabled_toolsets:" not in text:
        return text
    legacy = DEFAULT_HERMES_PLUGINS
    lines = text.splitlines()
    out: list[str] = []
    in_toolsets = False
    inserted_acp = False
    saw_legacy = False
    saw_acp = False
    for line in lines:
        if re.match(r"^enabled_toolsets:\s*$", line):
            in_toolsets = True
            out.append(line)
            continue
        if in_toolsets:
            match = re.match(r"^(\s*)-\s*([^\s#]+)(.*)$", line)
            if match and len(match.group(1)) >= 2:
                name = match.group(2)
                if name in legacy:
                    saw_legacy = True
                    continue
                if name == "hermes-acp":
                    saw_acp = True
                out.append(line)
                continue
            if saw_legacy and not saw_acp and not inserted_acp:
                out.append("  - hermes-acp")
                inserted_acp = True
            in_toolsets = False
        out.append(line)
    if in_toolsets and saw_legacy and not saw_acp and not inserted_acp:
        out.append("  - hermes-acp")
    return "\n".join(out)


def _ensure_model_gateway_config(config_yaml: Path) -> None:
    """Reconcile the managed NewAPI provider without persisting its secret.

    Hermes 0.18 resolves ``custom_providers[].key_env`` from the subprocess
    environment. Existing workspaces are normalized lazily on their next spawn,
    so releases need no separate workspace migration.
    """
    try:
        text = config_yaml.read_text(encoding="utf-8")
    except OSError:
        return
    try:
        config = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        _log.warning("failed to parse hermes config yaml at %s", config_yaml)
        return
    if not isinstance(config, dict):
        return
    model = config.get("model")
    if not isinstance(model, dict):
        model = {}
        config["model"] = model
    changed = False
    desired_model = {
        "default": _hermes_model_default(),
        "provider": _DRAMACLAW_HERMES_PROVIDER,
        "context_length": int(_hermes_model_context_length()),
    }
    for key, value in desired_model.items():
        if model.get(key) != value:
            model[key] = value
            changed = True
    for secret_or_legacy_key in ("api_key", "api", "base_url"):
        if secret_or_legacy_key in model:
            model.pop(secret_or_legacy_key, None)
            changed = True

    providers = config.get("custom_providers")
    if not isinstance(providers, list):
        providers = []
        config["custom_providers"] = providers
        changed = True
    managed_provider = next(
        (
            item
            for item in providers
            if isinstance(item, dict)
            and str(item.get("name") or "").strip().lower()
            == _DRAMACLAW_HERMES_PROVIDER_NAME
        ),
        None,
    )
    if managed_provider is None:
        managed_provider = {"name": _DRAMACLAW_HERMES_PROVIDER_NAME}
        providers.append(managed_provider)
        changed = True
    desired_provider = {
        "name": _DRAMACLAW_HERMES_PROVIDER_NAME,
        "base_url": _newapi_base_url(),
        "key_env": _DRAMACLAW_HERMES_KEY_ENV,
        "api_mode": _hermes_model_api_mode(),
    }
    for key, value in desired_provider.items():
        if managed_provider.get(key) != value:
            managed_provider[key] = value
            changed = True
    for secret_key in ("api_key", "api"):
        if secret_key in managed_provider:
            managed_provider.pop(secret_key, None)
            changed = True
    if not changed:
        return
    try:
        config_yaml.write_text(_dump_hermes_config_yaml(config), encoding="utf-8")
    except OSError:
        _log.warning("failed to sync managed model gateway into %s", config_yaml)


class _IndentedSafeDumper(yaml.SafeDumper):
    def increase_indent(self, flow: bool = False, indentless: bool = False):
        return super().increase_indent(flow, False)


def _dump_hermes_config_yaml(config: dict) -> str:
    return yaml.dump(
        config,
        Dumper=_IndentedSafeDumper,
        allow_unicode=True,
        sort_keys=False,
    )


def _ensure_model_config_from_env(config_yaml: Path) -> None:
    """Apply explicit Hermes model *deployment parameter* env overrides to existing config.yaml.

    [aipdd fork delta] ``HERMES_MODEL`` / ``DRAMACLAW_HERMES_MODEL`` 的模型名覆盖已删：模
    型名由「设置 → 模型与渠道」的 ``HERMES_MODEL`` 功能位决定（下面的 _ensure_model_gateway_config
    就按那条写 ``default``，env 再写一次只会互相盖）。API mode 与 context length 是网关能力
    参数、不是模型选择，继续读 env。
    """
    overrides: dict[str, object] = {}
    api_mode = _root_value("HERMES_MODEL_API_MODE")
    if api_mode:
        overrides["api_mode"] = api_mode
    context_length = _root_value("HERMES_MODEL_CONTEXT_LENGTH")
    if context_length:
        overrides["context_length"] = int(_hermes_model_context_length())
    if not overrides:
        return
    try:
        text = config_yaml.read_text(encoding="utf-8")
    except OSError:
        return
    try:
        config = yaml.safe_load(text) or {}
    except yaml.YAMLError:
        _log.warning("failed to parse hermes config yaml at %s", config_yaml)
        return
    if not isinstance(config, dict):
        return
    config_model = config.setdefault("model", {})
    if not isinstance(config_model, dict):
        config_model = {}
        config["model"] = config_model
    changed = False
    for key, value in overrides.items():
        if config_model.get(key) != value:
            config_model[key] = value
            changed = True
    if not changed:
        return
    try:
        config_yaml.write_text(_dump_hermes_config_yaml(config), encoding="utf-8")
    except OSError:
        _log.warning("failed to apply hermes model env overrides to %s", config_yaml)


def _materialize_plugin_links(plugins_dir: Path) -> None:
    """Create / refresh symlinks from plugins_dir/<name> → repo-pinned plugins."""
    src_plugins = DRAMACLAW_ROOT / ".hermes" / "plugins"
    if not src_plugins.is_dir():
        _log.info(
            "hermes plugins source not found at %s — skipping plugin links",
            src_plugins,
        )
        return

    allowed = {
        name.strip()
        for name in os.environ.get(
            "ST_HERMES_PLUGINS",
            ",".join(sorted(DEFAULT_HERMES_PLUGINS)),
        ).split(",")
        if name.strip()
    }
    want = {
        p.name: p.resolve()
        for p in src_plugins.iterdir()
        if p.is_dir() and (not allowed or p.name in allowed)
    }

    for name, target in want.items():
        if name.startswith("_"):
            continue
        link = plugins_dir / name
        if link.is_symlink():
            try:
                if link.resolve() == target:
                    continue
                link.unlink()
            except OSError:
                continue
        elif link.exists():
            _log.warning(
                "plugin name collision at %s (not a symlink); leaving as-is",
                link,
            )
            continue
        try:
            link.symlink_to(target)
        except OSError as e:
            _log.warning("failed to link %s → %s: %s", link, target, e)

    for entry in plugins_dir.iterdir():
        if not entry.is_symlink():
            continue
        if entry.name not in want:
            try:
                entry.unlink()
            except OSError:
                pass


__all__ = [
    "effective_gateway_credentials",
    "effective_gateway_fingerprint",
    "ensure_user_hermes_workspace",
]
